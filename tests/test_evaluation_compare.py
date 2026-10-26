"""Offline evaluation identity and comparison safety contracts."""

import copy
import json
import socket
import sys

import pytest

from ecoalign_forge.evaluation.compare import (
    Snapshot,
    compare,
    freeze,
    load_set,
    snapshot_run,
)
from ecoalign_forge.schemas.kernel import text_hash


@pytest.fixture
def evaluation():
    return load_set(
        freeze(
            {
                "cases": [
                    {"case_id": "a", "content": "first"},
                    {"case_id": "b", "content": "second"},
                ],
                "scorer_spec": {"name": "behavior-diff", "version": 1},
            }
        )
    )


def result(evaluation, **updates):
    data = {
        "identity": evaluation.identity,
        "records": [
            {
                "case_id": c.case_id,
                "source_hash": c.source_hash,
                "status": "accepted",
                "action": "allow",
                "labels": {"risk": "low"},
                "evidence": [],
            }
            for c in evaluation.cases
        ],
    }
    data.update(updates)
    return Snapshot.model_validate(data)


def test_reordering_is_identical(evaluation):
    baseline = result(evaluation)
    reversed_set = load_set(
        freeze(evaluation.model_dump() | {"cases": list(reversed(evaluation.cases))})
    )
    assert reversed_set.identity == evaluation.identity
    reversed_result = result(evaluation, records=list(reversed(baseline.records)))
    assert compare(evaluation, baseline, reversed_result) == compare(
        reversed_set, baseline, baseline
    )


@pytest.mark.parametrize("target", ["evaluation", "snapshot"])
def test_duplicate_ids_rejected_even_identical(evaluation, target):
    with pytest.raises(ValueError, match="Duplicate case_id"):
        if target == "evaluation":
            freeze(evaluation.model_dump() | {"cases": [evaluation.cases[0]] * 2})
        else:
            result(evaluation, records=[result(evaluation).records[0]] * 2)


def test_changed_content_cannot_pair(evaluation):
    baseline = result(evaluation)
    data = baseline.model_dump()
    data["records"][0]["source_hash"] = text_hash("different")
    report = compare(evaluation, baseline, Snapshot.model_validate(data))
    assert report["coverage"]["content_matched_cases"] == 1
    assert report["cases"][0]["changes"] == []
    assert report["comparison"] == "overlap_only"
    assert "mismatched" in report["cases"][0]["explanation"][0]


def test_tampered_frozen_set_rejected(evaluation):
    frozen = freeze(evaluation.model_dump())
    frozen["evaluation_set"]["cases"][0]["content"] = "changed"
    with pytest.raises(ValueError, match="was changed"):
        load_set(frozen)


def test_scoring_reference_identity_and_source_kind(evaluation):
    data = evaluation.model_dump()
    changed = load_set(freeze(data | {"scorer_spec": {"version": 2}}))
    assert changed.identity["scorer_spec_sha256"] != evaluation.identity["scorer_spec_sha256"]
    data.update(reference_label_revision="r1", source_kind="machine_candidate")
    data["cases"][0]["reference_labels"] = {"risk": "low"}
    first = load_set(freeze(data))
    data["cases"][0]["reference_labels"]["risk"] = "high"
    second = load_set(freeze(data))
    assert first.identity["reference_labels_sha256"] != second.identity["reference_labels_sha256"]
    report = compare(evaluation, result(first), result(second))
    assert report["comparison"] == "overlap_only"
    assert report["model_quality"] == "not_evaluated"
    assert "accuracy" not in report


def test_missing_abstain_failed_are_separate(evaluation):
    baseline = result(evaluation)
    candidate = result(
        evaluation,
        records=[
            {
                "case_id": "a",
                "source_hash": evaluation.cases[0].source_hash,
                "status": "abstain",
                "reason": "external evidence unavailable",
            }
        ],
    )
    report = compare(evaluation, baseline, candidate)
    assert report["status_counts"]["candidate"] == {"abstain": 1, "missing": 1}
    assert report["coverage"] == {
        "evaluation_cases": 2,
        "content_matched_cases": 1,
        "changed_cases": 1,
        "change_rate_denominator": 1,
        "change_rate": 1.0,
    }
    assert "status" in report["cases"][0]["changes"]
    failed = candidate.model_dump()
    failed["records"][0]["status"] = "failed"
    assert compare(evaluation, baseline, Snapshot.model_validate(failed))["status_counts"][
        "candidate"
    ] == {"failed": 1, "missing": 1}


def test_legacy_is_never_given_evaluation_identity(evaluation):
    legacy = result(evaluation, identity=None)
    report = compare(evaluation, legacy, legacy)
    assert report["comparison"] == "overlap_only"
    assert len(report["identity_issues"]) == 2
    assert legacy.identity is None


def test_no_overlap_has_no_rate_and_unknown_ids_reported(evaluation):
    candidate = result(evaluation, records=[{"case_id": "outside", "status": "failed"}])
    report = compare(evaluation, result(evaluation), candidate)
    assert report["coverage"]["change_rate"] is None
    assert "outside" in report["identity_issues"][0]


def test_per_field_changes_and_budget_preserved(evaluation):
    baseline = result(evaluation, budget={"estimated_usd": "0", "unresolved_reserved_usd": "2"})
    data = baseline.model_dump()
    data["records"][0].update(action="deny", labels={"risk": "high"}, evidence=[{"quote": "first"}])
    before = copy.deepcopy(data)
    report = compare(evaluation, baseline, Snapshot.model_validate(data))
    assert report["cases"][0]["changes"] == ["action", "labels", "evidence"]
    assert report["budgets"]["baseline"] == baseline.budget
    assert data == before


def test_run_snapshot_is_read_only_and_keeps_abstain(tmp_path, monkeypatch):
    from ecoalign_forge.runtime.journal import Journal

    run = tmp_path / "run"
    journal = Journal(run)
    journal.initialize(
        {"run_id": "old", "policy_hash": "policy"},
        [
            {"request_item_id": "run-local-a", "batch_index": 0},
            {"request_item_id": "run-local-b", "batch_index": 0},
        ],
    )
    journal.set_case(
        "run-local-a", state="failed", reason="missing evidence", source={"content": "first"}
    )
    journal.commit_stage("run-local-a", "gate", {"status": "abstain", "final": None})

    # Keep the writer connection open to exercise reading committed WAL in the copy.
    def files():
        return {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}

    before = files()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network forbidden"))
    snapshot = snapshot_run(run)
    assert files() == before
    assert snapshot["identity"] is None
    assert snapshot["provenance"]["manifest"] == {"run_id": "old", "policy_hash": "policy"}
    assert snapshot["records"][0]["status"] == "abstain"
    assert snapshot["records"][1]["source_hash"] is None
    journal.close()
    before = files()
    assert snapshot_run(run) == snapshot
    assert files() == before


def test_cli_offline_and_does_not_import_kernel(tmp_path, evaluation, monkeypatch, capsys):
    from ecoalign_forge.__main__ import main

    frozen = tmp_path / "set.json"
    baseline = tmp_path / "baseline.json"
    frozen.write_text(json.dumps(freeze(evaluation.model_dump())))
    baseline.write_text(result(evaluation).model_dump_json())
    monkeypatch.setitem(sys.modules, "ecoalign_forge.engine.kernel", None)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network forbidden"))
    monkeypatch.setattr(
        sys, "argv", ["ecoalign-forge", "compare", str(frozen), str(baseline), str(baseline)]
    )
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["changed_cases"] == 0
    assert report["comparison"] == "same_evaluation_set"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["budget", "provenance", "evidence"])
def test_nonfinite_nested_snapshot_rejected(evaluation, field, value):
    data = result(evaluation).model_dump()
    if field == "evidence":
        data["records"][0]["evidence"] = [{"nested": [value]}]
    else:
        data[field] = {"nested": [{"cost": value}]}
    with pytest.raises(ValueError, match="Out of range float"):
        Snapshot.model_validate(data)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_and_numeric_overflow_rejected(tmp_path, token):
    from ecoalign_forge.evaluation.compare import read_json

    path = tmp_path / "bad.json"
    path.write_text('{"budget": {"nested": [' + token + "]}}")
    with pytest.raises(ValueError, match="Out of range float"):
        read_json(path)


def test_text_report_calls_out_unverified_overlap(evaluation):
    from ecoalign_forge.evaluation.compare import render_text

    report = compare(evaluation, result(evaluation, identity=None), result(evaluation))
    text = render_text(report)
    assert "Scope: overlap_only" in text
    assert "equivalence is not established" in text
    assert "not an improvement rate" in text
