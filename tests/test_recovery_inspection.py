"""Evidence boundaries for read-only recovery explanations (no model calls)."""

import json
from decimal import localcontext

import pytest

from ecoalign_forge.runtime.inspection import recovery_report
from ecoalign_forge.runtime.journal import Journal
from tests.test_runtime_accounting import attempt


def test_explains_saved_failed_unknown_and_unsettled_attempts(tmp_path):
    j = Journal(tmp_path)
    j.set("manifest", {"execution_mode": "live"})
    saved = attempt(j, "saved", ".3")
    j.save_response(saved, {"content": "PRIVATE response"})
    succeeded = attempt(j, "succeeded")
    j.save_response(succeeded, {"content": "PRIVATE response"})
    j.finish_attempt(succeeded, "succeeded", cost=".1", usage={"total_tokens": 3}, parsed={})
    failed = attempt(j, "parse_failed")
    j.save_response(failed, {"content": "PRIVATE response"})
    j.finish_attempt(failed, "parse_failed", error="PRIVATE error", cost=".2")
    transient = attempt(j, "transient")
    j.finish_attempt(transient, "transient")
    rejected = attempt(j, "rejected")
    j.finish_attempt(rejected, "permanent", cost="0")
    unknown = attempt(j, "unknown")
    j.finish_attempt(unknown, "unknown")
    inflight = attempt(j, "inflight")
    report = recovery_report(j)
    assert report["counts"] == {
        "total_attempts": 7,
        "persisted_responses": 3,
        "response_replay_candidates": 1,
        "unresolved_reservations": 4,
        "unknown_remote_outcomes": 2,
        "unresolved_unknown_decisions": 1,
        "in_flight_intents": 1,
        "possibly_billable_attempts": 6,
    }
    assert report["unresolved_reserved_usd"] == "1.2"
    assert report["actual_billed_usd"] is None
    assert report["external_exactly_once_guaranteed"] is False
    by_id = {a["attempt_id"]: a for a in report["attempts"]}
    assert by_id[saved]["recovery_boundary"].startswith("saved_response")
    assert by_id[failed]["recovery_boundary"].startswith("response_preserved")
    assert by_id[succeeded]["request_parsed_persisted"] is True
    assert by_id[transient]["unresolved_reserved_usd"] == ".3"
    assert by_id[inflight]["state"] == "in_flight"
    assert "PRIVATE" not in json.dumps(report)
    assert report["unresolved_reserved_usd"] == j.budget_snapshot()["unresolved_reserved_usd"]
    j.close()


@pytest.mark.parametrize("choice", ["retry", "skip"])
def test_decision_does_not_erase_unknown_cost_or_outcome(tmp_path, choice):
    j = Journal(tmp_path)
    j.set("manifest", {"execution_mode": "live"})
    aid = attempt(j)
    j.recover_inflight()
    j.resolve_unknown({aid: choice})
    report = recovery_report(j)
    assert report["counts"]["unresolved_unknown_decisions"] == 0
    assert report["counts"]["unknown_remote_outcomes"] == 1
    assert report["counts"]["possibly_billable_attempts"] == 1
    assert report["attempts"][0]["resolution"] == choice
    assert report["unresolved_reserved_usd"] == "0.3"
    j.close()


@pytest.mark.parametrize("mode", ["demo", "mock", "unknown", None])
def test_offline_and_unknown_modes_are_not_reported_as_live_bills(tmp_path, mode):
    j = Journal(tmp_path)
    j.set("manifest", {"execution_mode": mode})
    attempt(j)
    report = recovery_report(j)
    assert report["counts"]["possibly_billable_attempts"] == 0
    expected = "no_external_charge" if mode in {"demo", "mock"} else "execution_mode_unknown"
    assert report["attempts"][0]["billing_boundary"].startswith(expected)
    j.close()


def test_readonly_wal_bytes_stable_exact_decimal_and_no_response_blob_reads(tmp_path):
    writer = Journal(tmp_path)
    writer.db.execute("PRAGMA wal_autocheckpoint=0")
    for i, value in enumerate(
        ["12345678901234567890.12345678901234567890", ".00000000000000000001"]
    ):
        aid = attempt(writer, str(i), value)
        writer.save_response(aid, {"content": "private" * 10000})
    paths = [tmp_path / name for name in ["run.sqlite3", "run.sqlite3-wal"]]
    before = {p: p.read_bytes() for p in paths}
    reader = Journal.readonly(tmp_path)
    queries = []
    reader.db.set_trace_callback(queries.append)
    reader.db.execute("BEGIN")
    with localcontext() as ctx:
        ctx.prec = 6
        report = recovery_report(reader)
    reader.db.execute("ROLLBACK")
    assert reader.db.total_changes == 0
    reader.close()
    assert report["unresolved_reserved_usd"] == "12345678901234567890.12345678901234567891"
    assert {p: p.read_bytes() for p in paths} == before
    assert not any("SELECT *" in q for q in queries)
    assert all("a.result," not in q for q in queries)
    writer.close()


@pytest.mark.asyncio
async def test_cli_live_wal_inspection_never_calls_transport_or_recovers(
    tmp_path, monkeypatch, capsys
):
    import sys

    from ecoalign_forge.__main__ import main
    from ecoalign_forge.policy.builtin import builtin_pack
    from ecoalign_forge.runtime.control import LiteLLMTransport
    from ecoalign_forge.schemas.kernel import RunConfig
    from tests.test_iteration_b_kernel import kernel

    k = kernel(tmp_path)
    await k.run(builtin_pack(), RunConfig(num_samples=1))
    writer = Journal(k.last_run_dir)
    manifest = writer.get("manifest")
    writer.set("manifest", manifest | {"execution_mode": "live"})
    aid = attempt(writer, "interrupted")
    paths = [writer.path / n for n in ["run.sqlite3", "run.sqlite3-wal"]]
    before = {p: p.read_bytes() for p in paths}

    def forbidden(*args, **kwargs):
        raise AssertionError("inspection must not call transport or recovery")

    monkeypatch.setattr(LiteLLMTransport, "__call__", forbidden)
    monkeypatch.setattr(Journal, "recover_inflight", forbidden)
    monkeypatch.setattr(sys, "argv", ["ecoalign-forge", "inspect", str(writer.path)])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    report = json.loads(output[output.index("{") :])["recovery"]
    assert report["counts"]["in_flight_intents"] == 1
    assert next(a for a in report["attempts"] if a["attempt_id"] == aid)["state"] == "in_flight"
    assert {p: p.read_bytes() for p in paths} == before
    writer.close()


def test_read_transaction_keeps_budget_and_explanations_consistent(tmp_path):
    writer = Journal(tmp_path)
    attempt(writer, "before")
    reader = Journal.readonly(tmp_path)
    reader.db.execute("BEGIN")
    budget = reader.budget_snapshot()
    attempt(writer, "after")
    report = recovery_report(reader)
    assert budget["attempts"] == report["counts"]["total_attempts"] == 1
    assert budget["unresolved_reserved_usd"] == report["unresolved_reserved_usd"]
    reader.db.execute("ROLLBACK")
    assert recovery_report(reader)["counts"]["total_attempts"] == 2
    reader.close()
    writer.close()
