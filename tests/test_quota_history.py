"""Shared quota admission should not scan or rewrite expired lease history."""

import sqlite3

import pytest

from ecoalign_forge.runtime import control
from ecoalign_forge.runtime.control import Quota, RunPausedError
from ecoalign_forge.schemas.kernel import RunConfig


@pytest.mark.asyncio
async def test_admission_work_does_not_grow_with_inactive_history(tmp_path, monkeypatch):
    quota = Quota(tmp_path, RunConfig())
    monkeypatch.setattr(control.time, "time", lambda: 10000)
    try:
        quota.db.executemany(
            "INSERT INTO leases VALUES(?,?,?,?,?,?,?)",
            [(f"old-{i}", "default" if i % 2 else "other", 1, 2, 1, 0, 1) for i in range(6000)],
        )
        steps = 0

        def progress():
            nonlocal steps
            steps += 100
            return 0

        before = quota.db.total_changes
        quota.db.set_progress_handler(progress, 100)
        await quota.acquire("new", 1, 10001)
        quota.db.set_progress_handler(None, 0)
        # A broad deterministic VM-work bound, not a scheduler/CPU timing test.
        assert steps < 10000
        assert quota.db.total_changes - before == 1
        assert quota.db.execute("SELECT count(*) FROM leases").fetchone()[0] == 6001
    finally:
        quota.close()


@pytest.mark.asyncio
async def test_expire_only_active_leases_in_the_admitting_group(tmp_path, monkeypatch):
    quota = Quota(tmp_path, RunConfig(max_concurrent=2))
    monkeypatch.setattr(control.time, "time", lambda: 100)
    try:
        quota.db.executemany(
            "INSERT INTO leases VALUES(?,?,?,?,?,?,?)",
            [
                ("finished", "default", 1, 2, 3, 0, 1),
                ("expired", "default", 1, 99, 3, 1, 1),
                ("running", "default", 1, 101, 3, 1, 1),
                ("other", "other", 1, 99, 3, 1, 1),
            ],
        )
        before = quota.db.total_changes
        await quota.acquire("new", 1, 101)
        assert quota.db.total_changes - before == 2
        assert dict(quota.db.execute("SELECT id,active FROM leases")) == {
            "finished": 0,
            "expired": 0,
            "running": 1,
            "other": 1,
            "new": 1,
        }
    finally:
        quota.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limits", [{"rpm": 1}, {"tpm": 2}])
async def test_recent_inactive_charge_still_blocks_admission(tmp_path, monkeypatch, limits):
    quota = Quota(tmp_path, RunConfig(**limits))
    clock = [100]
    monkeypatch.setattr(control.time, "time", lambda: clock[0])

    async def expire_waiter(_delay):
        clock[0] = 101

    monkeypatch.setattr(control.asyncio, "sleep", expire_waiter)
    try:
        quota.db.execute(
            "INSERT INTO leases VALUES(?,?,?,?,?,?,?)", ("recent", "default", 99, 99, 2, 0, 1)
        )
        with pytest.raises(RunPausedError, match="run_deadline"):
            await quota.acquire("blocked", 1, 101)
        assert quota.db.execute("SELECT count(*) FROM leases").fetchone()[0] == 1
    finally:
        quota.close()


def test_legacy_database_gets_idempotent_indexes_without_rewriting_rows(tmp_path):
    # A pre-change quota file with no secondary indexes remains usable.
    db = sqlite3.connect(tmp_path / "quota.sqlite3")
    db.execute(
        "CREATE TABLE leases(id TEXT PRIMARY KEY,group_name TEXT NOT NULL,created REAL NOT NULL,"
        "deadline REAL NOT NULL,tokens INTEGER NOT NULL,active INTEGER NOT NULL,pid INTEGER NOT NULL)"
    )
    db.execute("INSERT INTO leases VALUES('old','default',1,2,3,0,1)")
    db.commit()
    db.close()
    for _ in range(2):
        quota = Quota(tmp_path, RunConfig())
        try:
            indexes = {row[1] for row in quota.db.execute("PRAGMA index_list(leases)")}
            assert {"leases_active_group_deadline", "leases_group_created_tokens"} <= indexes
            assert tuple(quota.db.execute("SELECT * FROM leases").fetchone()) == (
                "old",
                "default",
                1,
                2,
                3,
                0,
                1,
            )
        finally:
            quota.close()
