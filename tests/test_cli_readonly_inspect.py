"""The actual inspect command must not migrate an old checkpoint."""

import json
import sqlite3
import subprocess
import sys

import pytest

from ecoalign_forge.policy.builtin import builtin_pack
from ecoalign_forge.runtime.accounting import BUDGET_KEY
from ecoalign_forge.schemas.kernel import RunConfig
from tests.test_iteration_b_kernel import kernel


def legacy_checkpoint(run_dir):
    path = run_dir / "run.sqlite3"
    with sqlite3.connect(path) as db:
        for name in ["budget_insert", "budget_update", "budget_delete"]:
            db.execute(f"DROP TRIGGER {name}")
        db.execute("DELETE FROM meta WHERE key=?", (BUDGET_KEY,))
    db.close()
    # Avoid incidental WAL/SHM sidecars in the filesystem-byte preservation test.
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=DELETE")
    db.close()
    return path


@pytest.mark.asyncio
async def test_inspect_cli_preserves_legacy_checkpoint_bytes_and_schema(tmp_path):
    k = kernel(tmp_path)
    original = await k.run(builtin_pack(), RunConfig(num_samples=1))
    path = legacy_checkpoint(k.last_run_dir)
    before = path.read_bytes()
    command = [sys.executable, "-m", "ecoalign_forge", "inspect", str(k.last_run_dir)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout[result.stdout.index("{") :])
    assert snapshot["budget"] == original["budget"]
    assert snapshot["recovery"]["source"] == "persisted_journal_only"
    assert snapshot["recovery"]["external_exactly_once_guaranteed"] is False
    assert path.read_bytes() == before
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("SELECT value FROM meta WHERE key=?", (BUDGET_KEY,)).fetchone() is None
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'budget_%'"
            ).fetchall()
            == []
        )


@pytest.mark.asyncio
async def test_inspect_accepts_readonly_legacy_file(tmp_path):
    k = kernel(tmp_path)
    await k.run(builtin_pack(), RunConfig(num_samples=1))
    path = legacy_checkpoint(k.last_run_dir)
    before = path.read_bytes()
    path.chmod(0o444)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ecoalign_forge", "inspect", str(k.last_run_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert path.read_bytes() == before
    finally:
        path.chmod(0o644)


@pytest.mark.asyncio
async def test_export_keeps_writable_projection_migration(tmp_path):
    k = kernel(tmp_path)
    await k.run(builtin_pack(), RunConfig(num_samples=1))
    path = legacy_checkpoint(k.last_run_dir)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ecoalign_forge",
            "export",
            str(k.last_run_dir),
            "--datasets-dir",
            str(tmp_path / "datasets"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        assert (
            db.execute("SELECT value FROM meta WHERE key=?", (BUDGET_KEY,)).fetchone() is not None
        )


@pytest.mark.asyncio
async def test_readonly_inspect_keeps_existing_schema_version_guard(tmp_path):
    k = kernel(tmp_path)
    await k.run(builtin_pack(), RunConfig(num_samples=1))
    path = legacy_checkpoint(k.last_run_dir)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    db.close()
    before = path.read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "ecoalign_forge", "inspect", str(k.last_run_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2 and "Unsupported checkpoint schema version" in result.stderr
    assert path.read_bytes() == before
