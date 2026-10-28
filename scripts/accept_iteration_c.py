"""Deterministic C walkthrough: automated review fixtures are NOT human acceptance."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from tests.test_iteration_b_kernel import RecordedTransport
from tests.test_iteration_b_policy import pack_data

from ecoalign_forge.demo.kernel_fixtures import fixture_candidate
from ecoalign_forge.engine.kernel import SynthesisKernel, code_identity
from ecoalign_forge.policy.builtin import builtin_pack
from ecoalign_forge.policy.compiler import compile_policy
from ecoalign_forge.policy.models import PolicyPack
from ecoalign_forge.schemas.kernel import RunConfig, SourceText, canonical, text_hash
from ecoalign_forge.workbench.dataset import build_dataset, verify_dataset
from ecoalign_forge.workbench.report import build_report
from ecoalign_forge.workbench.review import ReviewDecision, snapshot_run, submit_review


def decision(run, view, action="accept", corrected=None):
    return submit_review(
        run,
        ReviewDecision(
            operation_id=f"acceptance:{view['case']['id']}:{view['revision']}:{action}",
            case_id=view["case"]["id"],
            expected_revision=view["revision"],
            action=action,
            reviewer="automated-acceptance-fixture",
            reason="Deterministic engineering fixture, not a real human review",
            corrected=corrected,
        ),
    )


async def scenarios(output):
    k = SynthesisKernel(data_dir=output / "data", datasets_dir=output / "datasets")
    result = await k.run(builtin_pack(), RunConfig())
    run = Path(result["run_dir"])
    for view in snapshot_run(run)["cases"]:
        decision(run, view, "accept" if view["effective_final"] else "abstain")
    first = build_dataset([run], output / "datasets")
    assert verify_dataset(first)["pairs"] == 1
    before = (first / "pairs.jsonl").read_bytes()
    cid = json.loads(before.splitlines()[0])["source_case_id"]
    view = next(v for v in snapshot_run(run)["cases"] if v["case"]["id"] == cid)
    decision(run, view, "abstain")
    second = build_dataset([run], output / "datasets")
    assert verify_dataset(second)["pairs"] == 0
    assert (first / "pairs.jsonl").read_bytes() == before
    report = build_report(run, output / "reports", dataset=second)
    recorded = json.loads((report.parent / "report.json").read_text())
    assert recorded["reported_pairs"] == 0
    assert recorded["pairs_sha256"] == text_hash((second / "pairs.jsonl").read_text())
    # Unreviewed copy for actual browser interaction, independent of automated reviews.
    interactive = await k.run(builtin_pack(), RunConfig())
    multilingual = {}
    for language, text in [
        ("en", "Contact raven <script>not executable</script>"),
        ("ar", "تواصل مع رافن 🐦"),
    ]:
        data = pack_data()
        data["language"] = language
        pack = PolicyPack.model_validate(data)
        transport = RecordedTransport(pack, text=text)
        mk = SynthesisKernel(
            data_dir=output / "data", datasets_dir=output / "datasets", transport=transport
        )
        r = await mk.run(pack, RunConfig(execution_mode="mock", num_samples=2, batch_size=1))
        mr = Path(r["run_dir"])
        for v in snapshot_run(mr)["cases"]:
            correction = fixture_candidate(
                compile_policy(pack), SourceText.model_validate(v["case"]["source"]), set()
            )
            decision(mr, v, "correct", correction)
        dataset = build_dataset([mr], output / "datasets")
        assert verify_dataset(dataset)["pairs"] == 1  # duplicate original text, one unique pair
        multilingual[language] = dict(
            run_id=r["run_id"], dataset=str(dataset), manifest=verify_dataset(dataset)
        )
    bad = output / "data" / "demo" / "runs" / "corrupt-fixture"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "run.sqlite3").write_bytes(b"controlled corrupt database")
    return dict(
        code=code_identity(),
        real_new_users_verified=0,
        model_calls=0,
        training=False,
        reviewed_run_id=result["run_id"],
        interactive_run_id=interactive["run_id"],
        first_dataset=str(first),
        second_dataset=str(second),
        report=str(report),
        versions=[verify_dataset(first), verify_dataset(second)],
        multilingual=multilingual,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with patch(
        "litellm.acompletion", AsyncMock(side_effect=AssertionError("external LLM forbidden"))
    ):
        result = asyncio.run(scenarios(args.output.resolve()))
    (args.output / "acceptance.json").write_text(canonical(result) + "\n")
    print(
        json.dumps(
            {
                "acceptance": str(args.output / "acceptance.json"),
                "consumer_dataset": result["first_dataset"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
