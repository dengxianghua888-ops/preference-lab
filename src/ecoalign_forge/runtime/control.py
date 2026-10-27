"""Shared process quota and one retry owner with durable cost accounting."""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
from datetime import UTC
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from ecoalign_forge.runtime.accounting import decimal_add, token_cost
from ecoalign_forge.runtime.journal import Journal, connect, transaction
from ecoalign_forge.runtime.prompts import BatchContractError
from ecoalign_forge.schemas.kernel import GenerationResult, RunConfig, canonical


class RunPausedError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class RequestFailedError(Exception):
    pass


class TransportError(Exception):
    def __init__(self, kind: str, code: int | None = None, retry_after: float = 0):
        super().__init__(f"{kind}:{code}")
        self.kind, self.code, self.retry_after = kind, code, retry_after


def _retry_after_seconds(value):
    """Accept delay seconds or an HTTP-date without leaking provider header text."""
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            # Obsolete HTTP asctime dates omit the zone but still mean GMT.
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            seconds = date.timestamp() - time.time()
        except (ValueError, TypeError, OverflowError, OSError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


def json_value(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [json_value(x) for x in value]
    return value


class Quota:
    """Global only within this data root/group; leases survive executor death."""

    def __init__(self, root: Path, config: RunConfig):
        root.mkdir(parents=True, exist_ok=True)
        self.db = connect(root / "quota.sqlite3")
        self.config = config
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS groups(name TEXT PRIMARY KEY,config TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS leases(id TEXT PRIMARY KEY,group_name TEXT NOT NULL,created REAL NOT NULL,
                deadline REAL NOT NULL,tokens INTEGER NOT NULL,active INTEGER NOT NULL,pid INTEGER NOT NULL);
        """)
        caps = canonical(dict(concurrent=config.max_concurrent, rpm=config.rpm, tpm=config.tpm))
        with transaction(self.db):
            old = self.db.execute(
                "SELECT config FROM groups WHERE name=?", (config.quota_group,)
            ).fetchone()
            if old and old[0] != caps:
                raise ValueError(
                    "quota_group already uses different limits; use another explicit group"
                )
            self.db.execute("INSERT OR IGNORE INTO groups VALUES(?,?)", (config.quota_group, caps))

    async def acquire(self, lease_id, tokens, run_deadline):
        if tokens > self.config.tpm:
            raise RequestFailedError("request token reservation exceeds group TPM")
        while True:
            now = time.time()
            if now >= run_deadline:
                raise RunPausedError("run_deadline")
            with transaction(self.db):
                self.db.execute("UPDATE leases SET active=0 WHERE deadline<=?", (now,))
                active = self.db.execute(
                    "SELECT count(*) FROM leases WHERE group_name=? AND active=1",
                    (self.config.quota_group,),
                ).fetchone()[0]
                count, total = self.db.execute(
                    "SELECT count(*),coalesce(sum(tokens),0) FROM leases WHERE group_name=? AND created>?",
                    (self.config.quota_group, now - 60),
                ).fetchone()
                if (
                    active < self.config.max_concurrent
                    and count < self.config.rpm
                    and total + tokens <= self.config.tpm
                ):
                    self.db.execute(
                        "INSERT INTO leases VALUES(?,?,?,?,?,1,?)",
                        (
                            lease_id,
                            self.config.quota_group,
                            now,
                            now + self.config.request_timeout + 1,
                            tokens,
                            os.getpid(),
                        ),
                    )
                    return
            await asyncio.sleep(min(0.05, max(0.001, run_deadline - now)))

    def release(self, lease_id, usage=None):
        if usage and "total_tokens" in usage:
            self.db.execute(
                "UPDATE leases SET active=0,tokens=? WHERE id=?", (usage["total_tokens"], lease_id)
            )
        else:
            self.db.execute("UPDATE leases SET active=0 WHERE id=?", (lease_id,))

    def close(self):
        self.db.close()


def budget_snapshot(journal: Journal):
    return journal.budget_snapshot()


class LiteLLMTransport:
    """SDK retries disabled; full and idle deadlines cover streamed responses."""

    async def __call__(self, payload, attempt_id, config):
        import litellm

        if config.execution_mode.value != "live":
            raise RequestFailedError("External model calls are forbidden outside live mode")
        started = time.monotonic()
        model = config.models[payload["stage"]]
        kwargs = dict(
            model=model.model,
            messages=payload["messages"],
            temperature=model.temperature,
            max_tokens=model.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            num_retries=0,
            drop_params=False,
            timeout=config.request_timeout,
        )
        if model.reasoning_effort:
            kwargs["reasoning_effort"] = model.reasoning_effort
        chunks, usage, response_model, request_id, finish = [], None, None, None, None
        stream = None
        try:
            async with asyncio.timeout(config.request_timeout):
                stream = await litellm.acompletion(**kwargs)
                iterator = stream.__aiter__()
                while True:
                    try:
                        async with asyncio.timeout(config.stream_idle_timeout):
                            chunk = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    response_model = getattr(chunk, "model", None) or response_model
                    request_id = getattr(chunk, "id", None) or request_id
                    reported = getattr(chunk, "usage", None)
                    if reported is not None:
                        data = (
                            reported.model_dump()
                            if hasattr(reported, "model_dump")
                            else dict(reported)
                        )
                        usage = {
                            k: data[k]
                            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                            if type(data.get(k)) is int and data[k] >= 0
                        }
                        if not {"prompt_tokens", "completion_tokens"}.issubset(usage):
                            usage = None
                        elif "total_tokens" not in usage:
                            usage["total_tokens"] = (
                                usage["prompt_tokens"] + usage["completion_tokens"]
                            )
                    choices = getattr(chunk, "choices", [])
                    if choices:
                        choice = choices[0]
                        delta = getattr(choice, "delta", None)
                        content = getattr(delta, "content", None)
                        if content:
                            chunks.append(content)
                        finish = getattr(choice, "finish_reason", None) or finish
                if finish is None:
                    raise TransportError("unknown")
                return GenerationResult(
                    content="".join(chunks),
                    model=response_model,
                    provider_request_id=request_id,
                    finish_reason=finish,
                    usage=usage,
                    latency_seconds=time.monotonic() - started,
                    attempt_id=attempt_id,
                )
        except asyncio.CancelledError:
            raise
        except TransportError:
            raise
        except (TimeoutError, ConnectionError) as exc:
            raise TransportError("unknown") from exc
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            if code in {400, 401, 403, 404, 422}:
                kind = "permanent"
            elif code == 429 or (isinstance(code, int) and code >= 500):
                kind = "transient"
            else:
                kind = "unknown"
            headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
            retry_after = _retry_after_seconds(headers.get("retry-after", 0))
            # Never persist arbitrary provider exception strings (they can contain credentials).
            raise TransportError(kind, code, retry_after) from exc
        finally:
            if stream is not None and hasattr(stream, "aclose"):
                # Cleanup is best effort: it must not replace a complete response or
                # the classified generation error. External cancellation still propagates.
                try:
                    async with asyncio.timeout(min(1.0, config.stream_idle_timeout)):
                        await stream.aclose()
                except Exception:
                    pass


class RequestController:
    def __init__(self, journal: Journal, quota: Quota, config: RunConfig, transport=None):
        self.journal, self.quota, self.config = journal, quota, config
        self.transport = transport or LiteLLMTransport()
        self.deadline = journal.get("deadline", time.time() + config.run_timeout)

    def reservation(self, stage, messages):
        model = self.config.models[stage]
        estimated_input = len(canonical(messages).encode()) + 256 * len(messages)
        if self.config.execution_mode.value != "live":
            return Decimal(0), 0
        price = self.config.prices[model.model]
        if estimated_input > price.max_input_tokens:
            raise RequestFailedError("Prompt exceeds explicit input token upper bound")
        cost = token_cost(
            price.max_input_tokens,
            model.max_tokens,
            price.input_per_million,
            price.output_per_million,
        )
        return cost, price.max_input_tokens + model.max_tokens

    def cost(self, stage, result):
        if self.config.execution_mode.value != "live":
            return Decimal(0)
        if not result.usage:
            return None
        price = self.config.prices[self.config.models[stage].model]
        return token_cost(
            result.usage["prompt_tokens"],
            result.usage["completion_tokens"],
            price.input_per_million,
            price.output_per_million,
        )

    async def call(self, request_id, stage, case_ids, messages, parser):
        payload = dict(stage=stage, messages=messages)
        row = self.journal.request(request_id, stage, case_ids, payload)
        if row["state"] == "skipped":
            raise RequestFailedError("explicitly_skipped_unknown_request")
        if any(
            a["resolution"] is None
            for a in self.journal.attempts(request_id)
            if a["state"] == "unknown"
        ):
            raise RunPausedError("unknown_requests")
        if row["parsed"] is not None:
            return parser(canonical(json.loads(row["parsed"])))
        existing = self.journal.attempts(request_id)
        # A complete raw response is authoritative even if the process died before parsing/cost settlement.
        saved = next((a for a in reversed(existing) if a["state"] == "response_saved"), None)
        if saved:
            result = GenerationResult.model_validate_json(saved["result"])
            value = self._parse(stage, result, parser)
            if value is not None:
                return value
        while len(self.journal.attempts(request_id)) < self.config.max_attempts:
            if time.time() >= self.deadline:
                raise RunPausedError("run_deadline")
            reservation, tokens = self.reservation(stage, messages)
            spent = Decimal(budget_snapshot(self.journal)["total_committed_usd"])
            if (
                self.config.max_run_cost is not None
                and decimal_add(spent, reservation) > self.config.max_run_cost
            ):
                raise RunPausedError("budget_exhausted")
            number = len(self.journal.attempts(request_id)) + 1
            lease_id = f"{self.journal.get('manifest')['run_id']}:{request_id}:{number}:{uuid4()}"
            self.journal.hook("before_request_intent", {"request_id": request_id})
            await self.quota.acquire(lease_id, tokens, self.deadline)
            self.journal.hook("quota_acquired", {"request_id": request_id})
            # No await between budget check and committed reservation. One event-loop owner per run.
            # Recheck after waiting for the shared quota: other coroutines may have reserved funds.
            spent = Decimal(budget_snapshot(self.journal)["total_committed_usd"])
            if (
                self.config.max_run_cost is not None
                and decimal_add(spent, reservation) > self.config.max_run_cost
            ):
                self.quota.release(lease_id)
                raise RunPausedError("budget_exhausted")
            aid = None
            result = None
            retry_wait = 0
            try:
                aid = self.journal.start_attempt(request_id, reservation)
                async with asyncio.timeout(
                    min(self.config.request_timeout, max(0.001, self.deadline - time.time()))
                ):
                    result = await self.transport(payload, aid, self.config)
                if result.attempt_id != aid:
                    raise RequestFailedError("Transport returned a different attempt identity")
                self.journal.hook("before_response_commit", {"attempt_id": aid})
                self.journal.save_response(aid, result)
                value = self._parse(stage, result, parser)
                if value is not None:
                    return value
            except (asyncio.CancelledError, TimeoutError) as exc:
                if aid:
                    self.journal.finish_attempt(aid, "unknown", error=type(exc).__name__)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise RunPausedError("unknown_requests") from exc
            except TransportError as exc:
                if exc.kind == "unknown":
                    self.journal.finish_attempt(aid, "unknown", error=str(exc))
                    raise RunPausedError("unknown_requests") from exc
                self.journal.finish_attempt(
                    aid,
                    exc.kind,
                    error=str(exc),
                    cost=Decimal(0) if exc.code in {400, 401, 403, 404, 422, 429} else None,
                )
                if exc.kind == "permanent":
                    raise RequestFailedError(str(exc)) from exc
                retry_wait = max(exc.retry_after, min(30, 2 ** (number - 1)) + random.random() / 10)
            finally:
                self.quota.release(lease_id, result.usage if result else None)
            if retry_wait:
                if time.time() + retry_wait >= self.deadline:
                    raise RunPausedError("run_deadline")
                await asyncio.sleep(retry_wait)
        raise RequestFailedError("attempt_limit_exhausted")

    def _parse(self, stage, result, parser):
        try:
            if result.finish_reason not in {"stop", "fixture"}:
                raise ValueError("incomplete_or_filtered_response")
            value = parser(result.content)
            if value is None:
                raise ValueError("parser_returned_none")
        except ValueError as exc:
            self.journal.finish_attempt(
                result.attempt_id,
                "parse_failed",
                error=str(exc) if isinstance(exc, BatchContractError) else type(exc).__name__,
                cost=self.cost(stage, result),
                usage=result.usage,
            )
            return None
        self.journal.finish_attempt(
            result.attempt_id,
            "succeeded",
            cost=self.cost(stage, result),
            usage=result.usage,
            parsed=json_value(value),
        )
        return value
