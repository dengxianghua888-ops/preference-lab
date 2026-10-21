"""Provider retry hints must survive classification without real model calls."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ecoalign_forge.runtime.control import (
    LiteLLMTransport,
    RequestController,
    RunPausedError,
    TransportError,
)
from tests.test_iteration_b_control import setup


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,expected",
    [
        ("120", 120),
        ("0.5", 0.5),
        ("Wed, 21 Oct 2015 07:30:00 GMT", 120),
        ("Wednesday, 21-Oct-15 07:30:00 GMT", 120),
        ("Wed Oct 21 07:30:00 2015", 120),
        ("Wed, 21 Oct 2015 07:27:00 GMT", 0),
        ("nonsense", 0),
        (None, 0),
        ("-1", 0),
        ("nan", 0),
        ("inf", 0),
    ],
)
async def test_retry_after_header(tmp_path, monkeypatch, header, expected):
    import litellm

    config, journal, quota = setup(tmp_path)
    error = RuntimeError("provider message must not be persisted")
    error.status_code = 429
    error.response = SimpleNamespace(headers={"retry-after": header})
    monkeypatch.setattr(litellm, "acompletion", AsyncMock(side_effect=error))
    monkeypatch.setattr("ecoalign_forge.runtime.control.time.time", lambda: 1445412480)
    try:
        with pytest.raises(TransportError) as raised:
            await LiteLLMTransport()({"stage": "judge", "messages": []}, "q:1", config)
        assert raised.value.kind == "transient"
        assert raised.value.retry_after == expected
        assert str(raised.value) == "transient:429"
    finally:
        quota.close()
        journal.close()


@pytest.mark.asyncio
async def test_date_retry_beyond_deadline_pauses_before_second_call(tmp_path, monkeypatch):
    import litellm

    config, journal, quota = setup(tmp_path, run_timeout=30)
    error = RuntimeError("rate limited")
    error.status_code = 429
    error.response = SimpleNamespace(headers={"retry-after": "Wed, 21 Oct 2015 07:30:00 GMT"})
    sdk = AsyncMock(side_effect=error)
    monkeypatch.setattr(litellm, "acompletion", sdk)
    monkeypatch.setattr("ecoalign_forge.runtime.control.time.time", lambda: 1445412480)
    monkeypatch.setattr("ecoalign_forge.runtime.control.asyncio.sleep", AsyncMock())
    try:
        with pytest.raises(RunPausedError, match="run_deadline"):
            await RequestController(journal, quota, config).call("q", "judge", [], [], json.loads)
        assert sdk.await_count == 1
        assert len(journal.attempts()) == 1
        assert journal.attempts()[0]["state"] == "transient"
        assert journal.budget_snapshot()["total_committed_usd"] == "0"
    finally:
        quota.close()
        journal.close()
