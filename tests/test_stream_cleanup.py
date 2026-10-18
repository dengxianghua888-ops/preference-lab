"""Stream disposal cannot erase received responses or change failure classification."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ecoalign_forge.runtime.control import (
    LiteLLMTransport,
    RequestController,
    RequestFailedError,
    RunPausedError,
)
from tests.test_iteration_b_control import setup


class Stream:
    def __init__(self, *, error=None, finish="stop", close="error"):
        self.error, self.finish, self.close = error, finish, close
        self.sent = False
        self.closing = asyncio.Event()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.error:
            raise self.error
        if self.sent:
            raise StopAsyncIteration
        self.sent = True
        return SimpleNamespace(
            model="stub",
            id="stub-id",
            usage=dict(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content='{"ok":true}'), finish_reason=self.finish
                )
            ],
        )

    async def aclose(self):
        self.closing.set()
        try:
            if self.close == "hang":
                await asyncio.Event().wait()
            raise ConnectionError("cleanup failed")
        finally:
            self.closed = True


def install(monkeypatch, stream):
    import litellm

    call = AsyncMock(return_value=stream)
    monkeypatch.setattr(litellm, "acompletion", call)
    return call


@pytest.mark.asyncio
@pytest.mark.parametrize("close", ["error", "hang"])
async def test_complete_response_and_usage_survive_cleanup(tmp_path, monkeypatch, close):
    config, journal, quota = setup(tmp_path, stream_idle_timeout=0.01)
    stream = Stream(close=close)
    sdk = install(monkeypatch, stream)
    controller = RequestController(journal, quota, config, LiteLLMTransport())
    try:
        assert await asyncio.wait_for(controller.call("q", "judge", [], [], json.loads), 1) == {
            "ok": True
        }
        attempt = journal.attempts()[0]
        assert attempt["state"] == "succeeded"
        assert json.loads(attempt["result"])["content"] == '{"ok":true}'
        assert json.loads(attempt["usage"])["total_tokens"] == 8
        assert attempt["cost"] == "0.000011"
        assert await controller.call("q", "judge", [], [], json.loads) == {"ok": True}
        assert sdk.await_count == 1 and stream.closed
    finally:
        quota.close()
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permanent", "connection", "missing_finish", "cancel"])
async def test_generation_failure_is_not_replaced_by_close_error(tmp_path, monkeypatch, failure):
    config, journal, quota = setup(tmp_path)
    error = None
    if failure == "permanent":
        error = RuntimeError("provider error")
        error.status_code = 401
    elif failure == "connection":
        error = ConnectionError("incomplete stream")
    elif failure == "cancel":
        error = asyncio.CancelledError()
    stream = Stream(error=error, finish=None if failure == "missing_finish" else "stop")
    sdk = install(monkeypatch, stream)
    expected = (
        RequestFailedError
        if failure == "permanent"
        else asyncio.CancelledError
        if failure == "cancel"
        else RunPausedError
    )
    try:
        with pytest.raises(expected):
            await RequestController(journal, quota, config).call("q", "judge", [], [], json.loads)
        attempt = journal.attempts()[0]
        assert attempt["state"] == ("permanent" if failure == "permanent" else "unknown")
        assert attempt["result"] is None
        assert sdk.await_count == 1 and stream.closed
        if failure != "permanent":
            assert journal.budget_snapshot()["unresolved_reserved_usd"] != "0"
    finally:
        quota.close()
        journal.close()


@pytest.mark.asyncio
async def test_external_cancellation_during_cleanup_is_not_swallowed(tmp_path, monkeypatch):
    config, journal, quota = setup(tmp_path)
    stream = Stream(close="hang")
    install(monkeypatch, stream)
    task = asyncio.create_task(
        RequestController(journal, quota, config).call("q", "judge", [], [], json.loads)
    )
    try:
        await stream.closing.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert journal.attempts()[0]["state"] == "unknown"
        assert stream.closed
    finally:
        quota.close()
        journal.close()
