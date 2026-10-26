"""Offline regressions for review semantics and consistent dashboard reads."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecoalign_forge.evaluation.compare import snapshot_run
from ecoalign_forge.policy.builtin import builtin_pack
from ecoalign_forge.policy.models import PolicyPack
from ecoalign_forge.runtime.journal import Journal
from ecoalign_forge.schemas.kernel import RunConfig
from tests.test_iteration_b_kernel import RecordedTransport, kernel
from tests.test_iteration_b_policy import pack_data


@pytest.mark.asyncio
@pytest.mark.parametrize("review_status", ["abstain", "failed"])
async def test_snapshot_preserves_reviewer_outcome(tmp_path, review_status):
    pack = PolicyPack.model_validate(pack_data())
    transport = RecordedTransport(
        pack, reviewer=lambda ev: dict(status=review_status, reason="fixture", corrected=None)
    )
    k = kernel(tmp_path, transport)
    result = await k.run(pack, RunConfig(execution_mode="mock", num_samples=1))
    # The legacy kernel's conservation counters intentionally stay unchanged.
    assert result["counts"]["failed"] == 1
    snapshot = snapshot_run(k.last_run_dir)
    assert snapshot["records"][0]["status"] == review_status
    assert snapshot["records"][0]["reason"] == f"review_{review_status}"


@pytest.mark.asyncio
async def test_snapshot_preserves_gate_abstain(tmp_path):
    k = kernel(tmp_path)
    await k.run(builtin_pack(), RunConfig(num_samples=5))
    records = snapshot_run(k.last_run_dir)["records"]
    assert sum(r["status"] == "abstain" for r in records) == 4
    assert sum(r["status"] == "accepted" for r in records) == 1


@pytest.mark.asyncio
async def test_dashboard_reads_one_snapshot_during_real_resume(tmp_path, monkeypatch):
    pack = PolicyPack.model_validate(pack_data())

    def interrupt(event, data):
        if event == "stage_committed" and data["stage"] == "gate":
            raise RuntimeError("controlled interruption before case commit")

    k = kernel(tmp_path, RecordedTransport(pack), interrupt)
    before = await k.run(pack, RunConfig(execution_mode="mock", num_samples=1))
    assert before["counts"]["in_progress"] == 1
    k.hook = None
    # Load the actual loader with only Streamlit's decorator substituted.
    monkeypatch.setitem(
        sys.modules, "streamlit", SimpleNamespace(cache_data=lambda **kw: lambda f: f)
    )
    spec = importlib.util.spec_from_file_location(
        "dashboard_loader_regression", Path(__file__).parents[1] / "dashboard/data_loader.py"
    )
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    original_counts = Journal.counts
    observed = []

    def interleaved_counts(journal):
        counts = original_counts(journal)
        if getattr(journal, "_readonly", False) and not observed:
            # Execute a legal writer lifecycle between dashboard SELECTs, no SQL mutation.
            observed.append(asyncio.run(k.resume(k.last_run_dir)))
        return counts

    monkeypatch.setattr(Journal, "counts", interleaved_counts)
    result = await asyncio.to_thread(loader.read_run, k.last_run_dir / "run.sqlite3")
    assert observed[0]["status"] == "completed"
    for field in ("status", "counts", "budget", "action_distribution", "review_stats"):
        assert result[field] == before[field]
    assert result["cases"][0]["state"] != "accepted"
    after = loader.read_run(k.last_run_dir / "run.sqlite3")
    assert after["status"] == "completed"
    assert after["counts"]["accepted_cases"] == 1
    assert after["cases"][0]["state"] == "accepted"
