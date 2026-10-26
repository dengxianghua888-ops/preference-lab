"""Portable synthesis with one durable identity for every request and artifact."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from ecoalign_forge.config import settings
from ecoalign_forge.demo.kernel_fixtures import FIXTURE_VERSION, DemoTransport
from ecoalign_forge.policy.compiler import compile_policy, validate_final
from ecoalign_forge.policy.models import PolicyPack
from ecoalign_forge.runtime.control import (
    Quota,
    RequestController,
    RequestFailedError,
    RunPausedError,
    budget_snapshot,
)
from ecoalign_forge.runtime.identity import environment_differences, environment_identity
from ecoalign_forge.runtime.journal import Journal, atomic_write, transaction
from ecoalign_forge.runtime.prompts import (
    TEMPLATE_VERSION,
    generation_parser,
    messages,
    parse_candidate,
    parse_review,
)
from ecoalign_forge.schemas.kernel import (
    CandidateEvaluation,
    PreferencePair,
    ReviewOutcome,
    RunConfig,
    SourceText,
    canonical,
    digest,
)

EXIT_CODES = {"completed": 0, "partial_failed": 3, "failed": 1, "cancelled": 130, "paused": 4}


def code_identity():
    package = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        h.update(str(path.relative_to(package)).encode())
        h.update(path.read_bytes())
    checkout = package.parent.parent
    commit = None
    if (checkout / ".git").exists():
        p = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True
        )
        if p.returncode == 0:
            commit = p.stdout.strip()
    return dict(source_sha256=h.hexdigest(), git_commit=commit)


class SynthesisKernel:
    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        datasets_dir: Path | None = None,
        transport=None,
        hook=None,
    ):
        self._explicit_data_dir = data_dir is not None
        self._explicit_datasets_dir = datasets_dir is not None
        self.data_dir = Path(data_dir or settings.data_dir)
        self.datasets_dir = Path(datasets_dir or settings.datasets_dir)
        self.transport = transport
        self.hook = hook
        self.last_run_dir = None

    async def run(self, policy_pack: PolicyPack, config: RunConfig | None = None):
        config = RunConfig.model_validate_json((config or RunConfig()).model_dump_json())
        compiled = compile_policy(policy_pack)
        if config.execution_mode.value == "mock" and self.transport is None:
            raise ValueError("mock execution requires an explicit fixture transport")
        if config.execution_mode.value == "demo":
            DemoTransport(compiled)
        run_id = str(uuid4())
        path = self.data_dir / config.execution_mode.value / "runs" / run_id
        self.last_run_dir = path
        j = Journal(path, hook=self.hook)
        plans = [
            dict(
                request_item_id=digest(dict(run_id=run_id, ordinal=i)),
                ordinal=i,
                batch_index=i // config.batch_size,
                generation_target=None,
            )
            for i in range(config.num_samples)
        ]
        manifest = dict(
            schema_version=2,
            storage=dict(
                data_root=str(self.data_dir.resolve()),
                datasets_root=str(self.datasets_dir.resolve()),
            ),
            run_id=run_id,
            execution_mode=config.execution_mode.value,
            fixture_version=FIXTURE_VERSION if config.execution_mode.value == "demo" else None,
            policy=json.loads(compiled.snapshot),
            policy_hash=compiled.sha256,
            config=config.model_dump(mode="json"),
            code=code_identity(),
            environment=environment_identity(),
            template_version=TEMPLATE_VERSION,
            created_at=time.time(),
        )
        try:
            with j.owner():
                j.initialize(manifest, plans)
                j.set("deadline", time.time() + config.run_timeout)
                return await self._execute(j, compiled, config)
        finally:
            j.close()

    async def resume(
        self,
        run_dir: Path,
        *,
        unknown_decisions: dict | None = None,
        max_run_cost: Decimal | None = None,
        extend_seconds: float = 0,
        allow_environment_change: bool = False,
    ):
        path = Path(run_dir)
        if not (path / "run.sqlite3").is_file():
            raise ValueError("No checkpoint at run directory")
        self.last_run_dir = path
        j = Journal(path, hook=self.hook)
        try:
            with j.owner():
                manifest = j.get("manifest")
                storage = manifest.get("storage", {})
                if not self._explicit_data_dir and storage.get("data_root"):
                    self.data_dir = Path(storage["data_root"])
                if not self._explicit_datasets_dir and storage.get("datasets_root"):
                    self.datasets_dir = Path(storage["datasets_root"])
                if storage.get("data_root") and self.data_dir.resolve() != Path(
                    storage["data_root"]
                ):
                    raise ValueError("Resume must use the original shared quota data root")
                if (
                    manifest["template_version"] != TEMPLATE_VERSION
                    or manifest["code"]["source_sha256"] != code_identity()["source_sha256"]
                ):
                    raise ValueError(
                        "Code/template differs from checkpoint; resume requires the original version"
                    )
                expected_environment = j.get("effective_environment", manifest.get("environment"))
                current_environment = environment_identity()
                differences = environment_differences(expected_environment, current_environment)
                if differences and not allow_environment_change:
                    raise ValueError(
                        "Runtime environment differs from checkpoint: "
                        + "; ".join(differences)
                        + ". Restore the original environment, start a new run, or explicitly "
                        "accept this change with allow_environment_change=True "
                        "(CLI: --allow-environment-change). This does not waive code/template checks."
                    )
                compiled = compile_policy(PolicyPack.model_validate(manifest["policy"]))
                if compiled.sha256 != manifest["policy_hash"]:
                    raise ValueError("Policy snapshot hash mismatch")
                config = RunConfig.model_validate(j.get("effective_config", manifest["config"]))
                if j.get("deadline") is None:
                    j.set("deadline", manifest["created_at"] + config.run_timeout)
                updates = {}
                if max_run_cost is not None:
                    if config.max_run_cost is None or Decimal(max_run_cost) < config.max_run_cost:
                        raise ValueError("Resume may only increase an existing budget")
                    updates["max_run_cost"] = str(max_run_cost)
                if extend_seconds < 0:
                    raise ValueError("Resume extension cannot be negative")
                if updates or extend_seconds:
                    with transaction(j.db):
                        config = RunConfig.model_validate(config.model_dump(mode="json") | updates)
                        j.set("effective_config", config.model_dump(mode="json"))
                        if extend_seconds:
                            j.set("deadline", j.get("deadline") + extend_seconds)
                        j.event(
                            "resume_config_revision",
                            dict(updates=updates, extend_seconds=extend_seconds),
                        )
                if differences:
                    with transaction(j.db):
                        j.set("effective_environment", current_environment)
                        j.event(
                            "resume_environment_revision",
                            dict(
                                previous=expected_environment,
                                current=current_environment,
                                differences=differences,
                            ),
                        )
                j.recover_inflight()
                j.resolve_unknown(unknown_decisions or {})
                if j.unknown():
                    j.set("status", "paused")
                    j.set("pause_reason", "unknown_requests")
                    return self._snapshot(j)
                if config.execution_mode.value == "mock" and self.transport is None:
                    raise ValueError("mock resume requires an explicit fixture transport")
                return await self._execute(j, compiled, config)
        finally:
            j.close()

    def _seal_batch(self, j, compiled, config, batch_index):
        key = f"batch_plan:{batch_index}"
        saved = j.get(key)
        if saved is not None:
            return saved
        cases = [c for c in j.cases() if c["batch"] == batch_index]
        coverage = {}
        if config.adaptive_sampling:
            for case in j.cases():
                if case["state"] == "accepted":
                    gate = j.stage(case["id"], "gate")
                    for dimension, label in gate["final"]["evaluation"]["labels"].items():
                        coverage[(dimension, label)] = coverage.get((dimension, label), 0) + 1
        options = [(d.id, label) for d in compiled.pack.dimensions for label in d.labels]
        plans = []
        for case in cases:
            plan = case["plan"]
            target = min(
                options,
                key=lambda x: (coverage.get(x, 0), digest([config.seed, plan["ordinal"], x])),
            )
            coverage[target] = coverage.get(target, 0) + 1
            plans.append(plan | dict(generation_target=dict(dimension=target[0], label=target[1])))
        with transaction(j.db):
            j.set(key, plans)
            for plan in plans:
                j.db.execute(
                    "UPDATE cases SET plan=? WHERE id=?", (canonical(plan), plan["request_item_id"])
                )
        return plans

    async def _execute(self, j, compiled, config):
        quota = None
        status = "running"
        error = None
        pause_reason = None
        j.set("status", status)
        try:
            quota = Quota(self.data_dir, config)
            transport = (
                DemoTransport(compiled) if config.execution_mode.value == "demo" else self.transport
            )
            controller = RequestController(j, quota, config, transport)
            batches = sorted({c["batch"] for c in j.cases()})
            for batch in batches:
                cases = [c for c in j.cases() if c["batch"] == batch]
                active = [c for c in cases if c["state"] in {"unattempted", "in_progress"}]
                if not active:
                    continue
                pause_request = j.path / "pause-request.json"
                if pause_request.exists():
                    request = json.loads(pause_request.read_text())
                    if request["run_id"] != j.get("manifest")["run_id"]:
                        raise ValueError("Pause request belongs to another run")
                    j.event("user_pause_observed", request)
                    pause_request.unlink()
                    raise RunPausedError("user_pause")
                plans = self._seal_batch(j, compiled, config, batch)
                with transaction(j.db):
                    for case in active:
                        j.set_case(case["id"], state="in_progress")
                if any(c["source"] is None for c in active):
                    prompt = messages("generator", compiled, dict(requests=plans), config)
                    try:
                        generated = await controller.call(
                            f"generation:{batch}",
                            "generator",
                            [p["request_item_id"] for p in plans],
                            prompt,
                            generation_parser(plans),
                        )
                    except RequestFailedError as exc:
                        for case in active:
                            j.commit_case(case["id"], "failed", str(exc))
                        continue
                    with transaction(j.db):
                        for item in generated:
                            j.set_case(
                                item.request_item_id,
                                source=SourceText(
                                    source_id=item.request_item_id, content=item.content
                                ),
                            )
                    j.hook("generation_committed", {"batch": batch})
                active = [
                    c for c in j.cases() if c["batch"] == batch and c["state"] == "in_progress"
                ]
                tasks = [
                    asyncio.create_task(self._case(j, controller, compiled, config, case))
                    for case in active
                ]
                try:
                    await asyncio.gather(*tasks)
                except BaseException:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
            counts = j.counts()
            # A pause arriving in the final batch needs no extra suspended work.
            if (j.path / "pause-request.json").exists():
                j.event("pause_no_remaining_work", {})
                (j.path / "pause-request.json").unlink()
            status = (
                "completed"
                if counts.failed == 0
                else "partial_failed"
                if counts.completed
                else "failed"
            )
        except RunPausedError as exc:
            status = "paused"
            pause_reason = exc.reason
        except (asyncio.CancelledError, KeyboardInterrupt):
            status = "cancelled"
            error = "user_cancelled"
        except Exception as exc:
            status = "failed"
            error = type(exc).__name__
            j.event("fatal", {"error_type": error})
        finally:
            if quota:
                quota.close()
        j.set("status", status)
        j.set("error", error)
        j.set("pause_reason", pause_reason)
        try:
            output = j.export(self.datasets_dir)
            j.set("output_path", str(output))
        except Exception as exc:
            j.set("status", "failed")
            j.set("error", "export_failed:" + type(exc).__name__)
        return self._snapshot(j)

    async def _case(self, j, controller, compiled, config, case):
        cid = case["id"]
        source = SourceText.model_validate(case["source"])
        try:
            evaluations = {}
            for stage in ["moderator", "judge"]:
                saved = j.stage(cid, stage)
                if saved is None:
                    prompt = messages(
                        stage,
                        compiled,
                        dict(source=source.model_dump(mode="json"), persona=config.persona),
                        config,
                    )
                    ev = await controller.call(
                        f"{stage}:{cid}", stage, [cid], prompt, parse_candidate
                    )
                    j.commit_stage(cid, stage, ev)
                else:
                    ev = CandidateEvaluation.model_validate(saved)
                evaluations[stage] = ev
            saved = j.stage(cid, "review")
            if saved is not None:
                review = ReviewOutcome.model_validate(saved)
            elif not config.enable_review or config.execution_mode.value == "demo":
                review = ReviewOutcome(
                    status="skipped",
                    reason="recorded_demo"
                    if config.execution_mode.value == "demo"
                    else "review_disabled",
                )
                j.commit_stage(cid, "review", review)
            else:
                prompt = messages(
                    "reviewer",
                    compiled,
                    dict(
                        source=source.model_dump(mode="json"),
                        candidate=evaluations["judge"].model_dump(mode="json"),
                    ),
                    config,
                )
                review = await controller.call(
                    f"reviewer:{cid}", "reviewer", [cid], prompt, parse_review
                )
                j.commit_stage(cid, "review", review)
            if review.status in {"failed", "abstain"}:
                j.commit_case(cid, "failed", "review_" + review.status)
                return
            final_candidate = (
                review.corrected if review.status == "corrected" else evaluations["judge"]
            )
            gate = validate_final(compiled, source, final_candidate, review.status)
            j.commit_stage(cid, "gate", gate)
            if gate.status != "accepted":
                j.commit_case(
                    cid,
                    "failed" if gate.status == "abstain" else "excluded",
                    ",".join(gate.reasons),
                )
                return
            rejected = evaluations["moderator"]
            # A malformed label vocabulary is inspectable but cannot become a training response.
            valid_rejected = (
                set(rejected.labels) == {d.id for d in compiled.pack.dimensions}
                and rejected.final_action in compiled.pack.actions
            )
            valid_rejected = valid_rejected and all(
                rejected.labels[d.id] in d.labels for d in compiled.pack.dimensions
            )
            distance = (
                sum(rejected.labels.get(k) != v for k, v in final_candidate.labels.items())
                + (rejected.final_action != final_candidate.final_action)
            ) / (len(final_candidate.labels) + 1)
            pairs = []
            if valid_rejected and distance > 0 and distance >= config.min_label_distance:
                prompt = canonical(
                    messages(
                        "judge",
                        compiled,
                        dict(source=source.model_dump(mode="json"), persona=config.persona),
                        config,
                    )
                )
                manifest = j.get("manifest")
                request_ids = [f"generation:{case['batch']}", f"moderator:{cid}", f"judge:{cid}"]
                if review.status != "skipped":
                    request_ids.append(f"reviewer:{cid}")
                responses = {
                    req: [
                        dict(
                            attempt_id=a["id"],
                            response_hash=digest(json.loads(a["result"])),
                            returned_model=json.loads(a["result"]).get("model"),
                        )
                        for a in j.attempts(req)
                        if a["result"]
                    ]
                    for req in request_ids
                }
                chosen = canonical(final_candidate)
                negative = canonical(rejected)
                pair = PreferencePair(
                    pair_id=digest([manifest["run_id"], cid, chosen, negative]),
                    prompt=prompt,
                    chosen=chosen,
                    rejected=negative,
                    source_case_id=cid,
                    label_distance=distance,
                    lineage=dict(
                        execution_mode=config.execution_mode.value,
                        fixture_version=manifest["fixture_version"],
                        source_policy_id=compiled.pack.policy_id,
                        source_policy_version=compiled.pack.version,
                        policy_hash=compiled.sha256,
                        pipeline_run_id=manifest["run_id"],
                        source_hash=source.sha256,
                        chosen_hash=digest(final_candidate),
                        rejected_hash=digest(rejected),
                        prompt_hash=digest(json.loads(prompt)),
                        template_version=TEMPLATE_VERSION,
                        code=manifest["code"],
                        review_status=review.status,
                        request_responses=responses,
                        signal="machine_label_disagreement",
                        generation_target=case["plan"]["generation_target"],
                    ),
                )
                pairs.append(pair)
            reason = (
                "machine_label_disagreement"
                if pairs
                else "invalid_candidate_vocabulary"
                if not valid_rejected
                else "below_label_distance"
                if distance > 0
                else "no_label_disagreement"
            )
            j.commit_case(cid, "accepted", reason, pairs)
        except RequestFailedError as exc:
            j.commit_case(cid, "failed", str(exc))

    def _snapshot(self, j, *, persist=True):
        counts = j.counts()
        manifest = j.get("manifest")
        finals = [j.stage(c["id"], "gate")["final"] for c in j.cases() if c["state"] == "accepted"]
        severity = [f["severity"] for f in finals if f["severity"] is not None]
        labels = {d["id"]: {v: 0 for v in d["labels"]} for d in manifest["policy"]["dimensions"]}
        actions = {a: 0 for a in manifest["policy"]["actions"]}
        for final in finals:
            actions[final["evaluation"]["final_action"]] += 1
            for key, value in final["evaluation"]["labels"].items():
                labels[key][value] += 1
        reviews = {s: 0 for s in ["passed", "corrected", "failed", "abstain", "skipped"]}
        for case in j.cases():
            review = j.stage(case["id"], "review")
            if review:
                reviews[review["status"]] += 1
        valid = reviews["passed"] + reviews["corrected"]
        result = dict(
            schema_version=2,
            run_id=manifest["run_id"],
            execution_mode=manifest["execution_mode"],
            fixture_version=manifest["fixture_version"],
            policy_hash=manifest["policy_hash"],
            language=manifest["policy"]["language"],
            status=j.get("status"),
            exit_code=EXIT_CODES.get(j.get("status"), 4),
            error=j.get("error"),
            pause_reason=j.get("pause_reason"),
            counts=counts.model_dump(),
            budget=budget_snapshot(j),
            unknown_attempts=[a["id"] for a in j.unknown()],
            label_distribution=labels,
            action_distribution=actions,
            avg_decision_severity=sum(severity) / len(severity) if severity else None,
            avg_pair_quality_heuristic=None,
            model_quality="not_evaluated",
            review_stats=reviews
            | dict(
                consistency_rate=reviews["passed"] / valid if valid else None,
                correction_rate=reviews["corrected"] / valid if valid else None,
            ),
            output_path=j.get("output_path"),
            run_dir=str(j.path),
        )
        if not persist:
            return result
        try:
            atomic_write(j.path / "summary.json", canonical(result) + "\n")
        except Exception as exc:
            j.set("status", "failed")
            j.set("error", "summary_save_failed:" + type(exc).__name__)
            result.update(status="failed", exit_code=1, error=j.get("error"))
        return result
