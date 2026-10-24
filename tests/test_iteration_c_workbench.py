"""Review decisions, filesystem exports and reports are tested through real journals."""

import json

import pytest

from ecoalign_forge.demo.kernel_fixtures import fixture_candidate
from ecoalign_forge.policy.builtin import builtin_pack
from ecoalign_forge.policy.compiler import compile_policy
from ecoalign_forge.policy.models import PolicyPack
from ecoalign_forge.runtime.journal import Journal, RunBusyError
from ecoalign_forge.schemas.kernel import RunConfig, SourceText
from ecoalign_forge.workbench.dataset import DatasetConfig, build_dataset, verify_dataset
from ecoalign_forge.workbench.review import ReviewDecision, read_case, read_reviews, submit_review
from tests.test_iteration_b_kernel import RecordedTransport, kernel
from tests.test_iteration_b_policy import pack_data


async def demo(tmp_path):
    k = kernel(tmp_path)
    await k.run(builtin_pack(), RunConfig())
    j = Journal.readonly(k.last_run_dir)
    cases = j.cases()
    j.close()
    cases.sort(key=lambda c: read_case(k.last_run_dir, c["id"])["effective_final"] is None)
    return k.last_run_dir, cases


def decide(path, cid, action="accept", **kwargs):
    revision = read_case(path, cid)["revision"]
    return submit_review(
        path,
        ReviewDecision(
            operation_id=f"{cid}:{revision}:{action}",
            case_id=cid,
            expected_revision=revision,
            reviewer="automated-test-fixture",
            reason="Controlled engineering assertion, not human acceptance",
            action=action,
            **kwargs,
        ),
    )


@pytest.mark.asyncio
async def test_review_versions_change_exports_preserve_machine_and_old_dataset(tmp_path):
    run, cases = await demo(tmp_path)
    before = (run / "run.sqlite3").read_bytes()
    root = tmp_path / "curated"
    empty = build_dataset([run], root)
    assert verify_dataset(empty)["pairs"] == 0
    for case in cases:
        decide(
            run,
            case["id"],
            "accept" if read_case(run, case["id"])["effective_final"] else "abstain",
        )
    first = build_dataset([run], root)
    assert verify_dataset(first)["pairs"] == 1
    assert build_dataset([run], root) == first
    old_content = (first / "pairs.jsonl").read_bytes()
    pair = json.loads(old_content.splitlines()[0])
    cid = pair["source_case_id"]
    decide(run, cid, "abstain")
    second = build_dataset([run], root)
    assert second != first and verify_dataset(second)["pairs"] == 0
    assert (first / "pairs.jsonl").read_bytes() == old_content
    assert (run / "run.sqlite3").read_bytes() == before
    assert read_case(run, cid)["effective_final"] is None
    assert len(read_reviews(run)) == 6


@pytest.mark.asyncio
async def test_correction_swaps_preference_and_revalidates_unicode_evidence(tmp_path):
    pack = PolicyPack.model_validate(pack_data())
    k = kernel(tmp_path, RecordedTransport(pack, text="Contact 🐦 رافن"))
    await k.run(pack, RunConfig(execution_mode="mock", num_samples=1))
    j = Journal.readonly(k.last_run_dir)
    case = j.cases()[0]
    j.close()
    source = SourceText.model_validate(case["source"])
    correct = fixture_candidate(compile_policy(pack), source, set())
    saved = decide(k.last_run_dir, case["id"], "correct", corrected=correct)
    assert saved["final"]["evaluation"]["final_action"] == "allow"
    output = build_dataset([k.last_run_dir], tmp_path / "output")
    pair = json.loads((output / "pairs.jsonl").read_text())
    assert json.loads(pair["chosen"])["final_action"] == "allow"
    assert pair["lineage"]["review_id"] == saved["review_id"]
    invalid = fixture_candidate(compile_policy(pack), source, {"CONTACT"})
    evidence = invalid.evidence[0].model_copy(update={"start": 1})
    invalid = invalid.model_copy(update={"evidence": (evidence,)})
    with pytest.raises(ValueError, match="gate"):
        decide(k.last_run_dir, case["id"], "correct", corrected=invalid)
    assert read_case(k.last_run_dir, case["id"])["revision"] == 1


@pytest.mark.asyncio
async def test_stale_review_idempotency_and_active_run_lock(tmp_path):
    run, cases = await demo(tmp_path)
    decision = ReviewDecision(
        operation_id="stable",
        case_id=cases[0]["id"],
        expected_revision=0,
        reviewer="fixture",
        reason="test",
        action="accept",
    )
    first = submit_review(run, decision)
    assert submit_review(run, decision) == first
    with pytest.raises(ValueError, match="Stale revision"):
        submit_review(run, decision.model_copy(update={"operation_id": "second"}))
    with pytest.raises(ValueError, match="already used"):
        submit_review(run, decision.model_copy(update={"reason": "changed"}))
    j = Journal.readonly(run)
    with j.owner(), pytest.raises(RunBusyError):
        submit_review(run, decision)
    j.close()
    assert len(read_reviews(run)) == 1


@pytest.mark.asyncio
async def test_dedupe_conflicts_and_batch_family_partitions(tmp_path):
    pack = PolicyPack.model_validate(pack_data())
    k = kernel(tmp_path, RecordedTransport(pack))
    await k.run(pack, RunConfig(execution_mode="mock", num_samples=2, batch_size=1))
    j = Journal.readonly(k.last_run_dir)
    cases = j.cases()
    j.close()
    for c in cases:
        decide(k.last_run_dir, c["id"], family_ids=("same-template",))
    root = tmp_path / "out"
    path = build_dataset([k.last_run_dir], root)
    m = verify_dataset(path)
    assert m["pairs"] == 1 and m["exclusion_reasons"]["exact_duplicate"] == 1
    rows = [json.loads(x) for x in (path / "sources.jsonl").read_text().splitlines()]
    assert len({r["group_id"] for r in rows}) == 1
    correction = fixture_candidate(
        compile_policy(pack), SourceText.model_validate(cases[1]["source"]), set()
    )
    decide(k.last_run_dir, cases[1]["id"], "correct", corrected=correction)
    conflict = verify_dataset(build_dataset([k.last_run_dir], root))
    assert (
        conflict["pairs"] == 0 and conflict["exclusion_reasons"]["duplicate_decision_conflict"] == 2
    )


@pytest.mark.asyncio
async def test_export_atomic_failure_retry_and_corruption(tmp_path):
    run, _ = await demo(tmp_path)
    root = tmp_path / "out"
    config = DatasetConfig(include_unreviewed=True)

    def fail(name, value):
        if name == "before_dataset_publish":
            raise OSError("injected")

    with pytest.raises(OSError, match="injected"):
        build_dataset([run], root, config, hook=fail)
    assert not list(root.glob("*/curated/*/manifest.json"))
    path = build_dataset([run], root, config)
    assert verify_dataset(path)["pairs"] == 1
    (path / "train" / "trl_standard.jsonl").write_text("corrupt")
    with pytest.raises(ValueError, match="checksum"):
        verify_dataset(path)
    with pytest.raises(ValueError, match="checksum"):
        build_dataset([run], root, config)


@pytest.mark.asyncio
async def test_no_mode_mixing_and_explicit_case_selection(tmp_path):
    run, cases = await demo(tmp_path)
    j = Journal.readonly(run)
    rid = j.get("manifest")["run_id"]
    j.close()
    selection = {f"{rid}:{cases[0]['id']}"}
    path = build_dataset(
        [run], tmp_path / "out", DatasetConfig(include_unreviewed=True), selected=selection
    )
    assert verify_dataset(path)["selected_cases"] == 1
    with pytest.raises(ValueError, match="unknown"):
        build_dataset([run], tmp_path / "out", selected={"wrong"})
    pack = PolicyPack.model_validate(pack_data())
    k = kernel(tmp_path, RecordedTransport(pack))
    await k.run(pack, RunConfig(execution_mode="mock", num_samples=1))
    with pytest.raises(ValueError, match="modes"):
        build_dataset([run, k.last_run_dir], tmp_path / "out")


@pytest.mark.parametrize(
    "field,value", [("reviewer", " "), ("reason", ""), ("family_ids", ("x", "x"))]
)
def test_invalid_decision_fields(field, value):
    args = dict(
        operation_id="a",
        case_id="case",
        expected_revision=0,
        reviewer="fixture",
        reason="ok",
        action="accept",
    )
    with pytest.raises(ValueError):
        ReviewDecision(**(args | {field: value}))


@pytest.mark.asyncio
async def test_report_real_jsonl_hash_counts_and_html_escape(tmp_path):
    from ecoalign_forge.schemas.kernel import text_hash
    from ecoalign_forge.workbench.report import build_report

    run, _ = await demo(tmp_path)
    output = build_dataset([run], tmp_path / "out", DatasetConfig(include_unreviewed=True))
    path = build_report(run, tmp_path / "reports", dataset=output)
    report = json.loads((path.parent / "report.json").read_text())
    content = (path.parent / "pairs.jsonl").read_text()
    assert report["reported_pairs"] == len(content.splitlines()) == 1
    assert report["pairs_sha256"] == text_hash(content)
    assert report["machine_counts"]["completed"] == 1
    assert report["dataset"]["dataset_version"] == output.name
    assert report["pairs_sha256"] in path.read_text()
    assert build_report(run, tmp_path / "reports", dataset=output) == path


@pytest.mark.asyncio
async def test_cooperative_pause_and_resume_keeps_completed_batch(tmp_path):
    from ecoalign_forge.workbench.control import request_pause

    pack = PolicyPack.model_validate(pack_data())
    transport = RecordedTransport(pack)
    k = kernel(tmp_path, transport)
    requested = False

    def hook(name, value):
        nonlocal requested
        if name == "case_committed" and not requested:
            request_pause(k.last_run_dir)
            requested = True

    k.hook = hook
    paused = await k.run(pack, RunConfig(execution_mode="mock", num_samples=2, batch_size=1))
    assert paused["pause_reason"] == "user_pause"
    assert paused["counts"]["completed"] == 1
    attempts = set(call[1] for call in transport.calls)
    complete = await k.resume(k.last_run_dir)
    assert complete["status"] == "completed" and complete["counts"]["completed"] == 2
    assert len(transport.calls) == len({call[1] for call in transport.calls})
    assert attempts.issubset({call[1] for call in transport.calls})


@pytest.mark.asyncio
async def test_review_tampering_and_export_format_lineage(tmp_path):
    import sqlite3

    run, cases = await demo(tmp_path)
    decide(run, cases[0]["id"])
    db = sqlite3.connect(run / "reviews.sqlite3")
    value = json.loads(db.execute("SELECT value FROM reviews").fetchone()[0])
    value["decision"]["reason"] = "tampered"
    db.execute("UPDATE reviews SET value=?", (json.dumps(value),))
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="checksum"):
        read_reviews(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before_dataset_publish", "dataset_published"])
async def test_sigkill_dataset_publication_and_replay(tmp_path, boundary):
    import os
    import subprocess
    import sys

    run, _ = await demo(tmp_path)
    script = """import os,signal,sys
from pathlib import Path
from ecoalign_forge.workbench.dataset import build_dataset,DatasetConfig
def hook(name,value):
    if name==sys.argv[3]: os.kill(os.getpid(),signal.SIGKILL)
build_dataset([Path(sys.argv[1])],Path(sys.argv[2]),DatasetConfig(include_unreviewed=True),hook=hook)
"""
    root = tmp_path / "output"
    child = subprocess.run(
        [sys.executable, "-c", script, str(run), str(root), boundary],
        env=os.environ.copy(),
        capture_output=True,
    )
    assert child.returncode == -9
    published = list(root.glob("demo/curated/*/manifest.json"))
    assert len(published) == (1 if boundary == "dataset_published" else 0)
    output = build_dataset([run], root, DatasetConfig(include_unreviewed=True))
    assert verify_dataset(output)["pairs"] == 1
    assert len(list(root.glob("demo/curated/*/manifest.json"))) == 1


@pytest.mark.asyncio
async def test_two_process_review_has_one_winner(tmp_path):
    import subprocess
    import sys

    run, cases = await demo(tmp_path)
    script = """import sys
from pathlib import Path
from ecoalign_forge.workbench.review import ReviewDecision,submit_review
from ecoalign_forge.runtime.journal import RunBusyError
try:
    submit_review(Path(sys.argv[1]),ReviewDecision(operation_id=sys.argv[3],case_id=sys.argv[2],expected_revision=0,action="accept",reviewer="subprocess-fixture",reason="race-test"))
except (ValueError,RunBusyError):
    sys.exit(3)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(run), cases[0]["id"], str(i)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(2)
    ]
    for child in children:
        child.communicate(timeout=20)
    assert sorted(child.returncode for child in children) == [0, 3]
    assert len(read_reviews(run)) == 1


@pytest.mark.asyncio
async def test_mock_resume_cannot_fall_through_to_real_provider(tmp_path):
    from ecoalign_forge.engine.kernel import SynthesisKernel

    pack = PolicyPack.model_validate(pack_data())
    k = kernel(tmp_path, RecordedTransport(pack))
    await k.run(pack, RunConfig(execution_mode="mock", num_samples=1))
    with pytest.raises(ValueError, match="explicit fixture transport"):
        await SynthesisKernel().resume(k.last_run_dir)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("selection", "candidate_preview"),
        ("exclusion_reasons", {"fabricated": 42}),
        ("source_runs", []),
        ("execution_mode", "live"),
        ("language", "fabricated"),
        ("data_license", "fabricated"),
        ("selected_cases", 42),
        ("unique_texts", 42),
        ("reviewed_cases", 42),
        ("verification", "independent_gold"),
        ("deduplication", "none"),
        ("grouping", "none"),
        ("files", {}),
    ],
)
async def test_manifest_audit_tampering_is_rejected(tmp_path, field, value):
    run, cases = await demo(tmp_path)
    decide(run, cases[0]["id"])
    output = build_dataset([run], tmp_path / "out")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest[field] != value
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="audit"):
        verify_dataset(output)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["status", "counts", "budget"])
async def test_run_audit_change_creates_new_immutable_version(tmp_path, monkeypatch, field):
    from ecoalign_forge.workbench import dataset

    run, _ = await demo(tmp_path)
    root = tmp_path / "out"
    first = build_dataset([run], root)
    before = {str(p.relative_to(first)): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    snapshot_run = dataset.snapshot_run

    def changed_snapshot(path):
        snapshot = snapshot_run(path)
        summary = snapshot["summary"]
        if field == "status":
            summary[field] = "paused"
        elif field == "counts":
            summary[field]["moderated"] += 1
        else:
            summary[field]["estimated_usd"] = "1.25"
        return snapshot

    monkeypatch.setattr(dataset, "snapshot_run", changed_snapshot)
    second = build_dataset([run], root)
    assert second != first
    assert build_dataset([run], root) == second
    assert verify_dataset(second)["source_runs"] != verify_dataset(first)["source_runs"]
    assert (second / "sources.jsonl").read_bytes() == before["sources.jsonl"]
    assert {
        str(p.relative_to(first)): p.read_bytes() for p in first.rglob("*") if p.is_file()
    } == before


@pytest.mark.asyncio
async def test_legacy_unbound_audit_requires_rebuild_without_modifying_export(tmp_path):
    from ecoalign_forge.schemas.kernel import digest

    run, _ = await demo(tmp_path)
    root = tmp_path / "out"
    output = build_dataset([run], root)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 3
    del manifest["recipe"]["audit_hash"]
    manifest["dataset_version"] = digest(manifest["recipe"])
    manifest_path.write_text(json.dumps(manifest))
    legacy = output.with_name(manifest["dataset_version"])
    output.rename(legacy)
    before = (legacy / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match=r"Legacy.*rebuild"):
        verify_dataset(legacy)
    rebuilt = build_dataset([run], root)
    assert rebuilt != legacy
    assert verify_dataset(rebuilt)["schema_version"] == 4
    assert (legacy / "manifest.json").read_bytes() == before


@pytest.mark.asyncio
async def test_run_audit_order_is_independent_of_local_paths(tmp_path):
    import shutil

    first_run, _ = await demo(tmp_path / "first")
    second_run, _ = await demo(tmp_path / "second")
    root = tmp_path / "out"
    first = build_dataset([first_run, second_run], root)
    copies = [tmp_path / "relocated" / name for name in ("z", "a")]
    for source, destination in zip(sorted([first_run, second_run]), copies, strict=True):
        shutil.copytree(source, destination)
    assert build_dataset(copies, root) == first
    assert build_dataset(list(reversed(copies)), root) == first
    runs = verify_dataset(first)["source_runs"]
    assert [run["run_id"] for run in runs] == sorted(run["run_id"] for run in runs)
