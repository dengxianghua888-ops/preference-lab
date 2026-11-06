"""Quota decisions use time after SQLite's potentially blocking write lock."""

from contextlib import contextmanager

import pytest

from ecoalign_forge.runtime import control
from ecoalign_forge.runtime.control import Quota, RunPausedError
from ecoalign_forge.schemas.kernel import RunConfig


def delayed_transaction(monkeypatch, *, before, after):
    clock = [before]
    original = control.transaction

    @contextmanager
    def acquire_after_wait(db):
        with original(db):
            # Model elapsed time inside BEGIN IMMEDIATE, using a real SQLite
            # transaction without scheduler-dependent sleeps in the regression.
            clock[0] = after
            yield db

    monkeypatch.setattr(control.time, "time", lambda: clock[0])
    monkeypatch.setattr(control, "transaction", acquire_after_wait)
    return clock


@pytest.mark.asyncio
async def test_deadline_expiring_during_write_lock_does_not_create_lease(tmp_path, monkeypatch):
    quota = Quota(tmp_path, RunConfig())
    delayed_transaction(monkeypatch, before=100, after=110)
    try:
        with pytest.raises(RunPausedError, match="run_deadline"):
            await quota.acquire("expired", 1, run_deadline=105)
        assert quota.db.execute("SELECT count(*) FROM leases").fetchone()[0] == 0
    finally:
        quota.close()


@pytest.mark.asyncio
async def test_lock_wait_cannot_make_a_new_active_lease_immediately_reclaimable(
    tmp_path, monkeypatch
):
    quota = Quota(tmp_path, RunConfig(max_concurrent=1, request_timeout=2))
    clock = delayed_transaction(monkeypatch, before=100, after=110)

    async def expire_waiter(_delay):
        clock[0] = 111

    monkeypatch.setattr(control.asyncio, "sleep", expire_waiter)
    try:
        await quota.acquire("active", 1, run_deadline=120)
        with pytest.raises(RunPausedError, match="run_deadline"):
            await quota.acquire("must_wait", 1, run_deadline=111)
        assert quota.db.execute("SELECT count(*) FROM leases").fetchone()[0] == 1
        assert quota.db.execute("SELECT active FROM leases").fetchone()[0] == 1
    finally:
        quota.close()


@pytest.mark.asyncio
async def test_lease_timestamps_start_when_write_lock_is_acquired(tmp_path, monkeypatch):
    quota = Quota(tmp_path, RunConfig(request_timeout=2))
    delayed_transaction(monkeypatch, before=100, after=110)
    try:
        await quota.acquire("current", 1, run_deadline=120)
        row = quota.db.execute("SELECT created,deadline,active FROM leases").fetchone()
        assert tuple(row) == (110, 113, 1)
    finally:
        quota.close()


@pytest.mark.asyncio
async def test_rolling_window_is_recomputed_after_write_lock(tmp_path, monkeypatch):
    quota = Quota(tmp_path, RunConfig(rpm=1, tpm=1))
    monkeypatch.setattr(control.time, "time", lambda: 100)
    await quota.acquire("previous", 1, run_deadline=200)
    quota.release("previous")
    delayed_transaction(monkeypatch, before=100, after=161)

    async def unnecessary_wait(_delay):
        pytest.fail("expired quota charge must not trigger another wait")

    monkeypatch.setattr(control.asyncio, "sleep", unnecessary_wait)
    try:
        await quota.acquire("next", 1, run_deadline=200)
        row = quota.db.execute("SELECT created FROM leases WHERE id='next'").fetchone()
        assert row[0] == 161
    finally:
        quota.close()
