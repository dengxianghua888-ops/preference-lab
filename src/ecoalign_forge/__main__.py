"""Durable run, resume, inspect and export commands."""

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="ecoalign-forge")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("eval-freeze", help="Freeze a local evaluation-set JSON to stdout")
    freeze.add_argument("input", type=Path)
    snapshot = commands.add_parser("eval-snapshot", help="Read a stopped legacy run to stdout")
    snapshot.add_argument("run_dir", type=Path)
    compare = commands.add_parser("compare", help="Offline per-case behavior comparison to stdout")
    compare.add_argument("evaluation_set", type=Path)
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--format", choices=["json", "text"], default="json")
    run = commands.add_parser("run")
    run.add_argument("--demo", action="store_true", default=None)
    run.add_argument("--mode", choices=["demo", "live"])
    run.add_argument("--num-samples", type=int)
    run.add_argument("--config", type=Path)
    run.add_argument("--policy", type=Path)
    run.add_argument(
        "--persona", choices=["naive", "strict_paranoid", "lax_overlooker", "keyword_matcher"]
    )
    for command in [
        run,
        commands.add_parser("resume"),
        commands.add_parser("inspect"),
        commands.add_parser("export"),
    ]:
        command.add_argument("--data-dir", type=Path)
        command.add_argument("--datasets-dir", type=Path)
        if command is not run:
            command.add_argument("run_dir", type=Path)
        if command.prog.endswith("resume"):
            command.add_argument(
                "--allow-environment-change",
                action="store_true",
                help="Explicitly accept and audit changed Python/dependency versions during resume",
            )
            command.add_argument(
                "--resolve", action="append", default=[], metavar="ATTEMPT_ID=retry|skip"
            )
            command.add_argument("--max-run-cost", type=Decimal)
            command.add_argument("--extend-seconds", type=float, default=0)
    review = commands.add_parser("review", help="Read a case or append a human decision")
    review.add_argument("run_dir", type=Path)
    review.add_argument("--case-id")
    review.add_argument("--decision", type=Path)
    dataset = commands.add_parser("dataset", help="Build a curated immutable version")
    dataset.add_argument("run_dirs", nargs="+", type=Path)
    dataset.add_argument("--output-root", type=Path)
    dataset.add_argument("--include-unreviewed", action="store_true")
    dataset.add_argument("--eval-fraction", type=float, default=0.2)
    dataset.add_argument("--seed", type=int, default=0)
    dataset.add_argument("--select-file", type=Path, help="JSON list of RUN_ID:CASE_ID keys")
    verify = commands.add_parser("verify", help="Verify a curated dataset's hashes and lineage")
    verify.add_argument("dataset_dir", type=Path)
    report = commands.add_parser("report", help="Report persisted run and exact JSONL counts/hash")
    report.add_argument("run_dir", type=Path)
    report.add_argument("--output-root", type=Path, required=True)
    report.add_argument("--dataset", type=Path)
    pause = commands.add_parser("pause", help="Pause an active run after its current batch")
    pause.add_argument("run_dir", type=Path)
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("--"):
        argv = ["run", *argv]
    args = parser.parse_args(argv)

    if args.command in {"eval-freeze", "eval-snapshot", "compare"}:
        from ecoalign_forge.evaluation.compare import (
            Snapshot,
            compare,
            freeze,
            load_set,
            read_json,
            render_text,
            snapshot_run,
        )

        try:
            if args.command == "eval-freeze":
                result = freeze(read_json(args.input))
            elif args.command == "eval-snapshot":
                result = snapshot_run(args.run_dir)
            else:
                result = compare(
                    load_set(read_json(args.evaluation_set)),
                    Snapshot.model_validate(read_json(args.baseline)),
                    Snapshot.model_validate(read_json(args.candidate)),
                )
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(
            render_text(result)
            if getattr(args, "format", None) == "text"
            else json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        )
        raise SystemExit(0)

    from ecoalign_forge.engine.kernel import SynthesisKernel
    from ecoalign_forge.policy.builtin import builtin_pack
    from ecoalign_forge.policy.models import PolicyPack
    from ecoalign_forge.runtime.configuration import resolve_config
    from ecoalign_forge.runtime.journal import Journal

    try:
        if args.command == "run":
            if args.demo and args.mode == "live":
                raise ValueError("--demo conflicts with --mode live")
            config = resolve_config(
                path=args.config,
                explicit={
                    "execution_mode": "demo" if args.demo else args.mode,
                    "num_samples": args.num_samples,
                    "persona": args.persona,
                },
            )
            pack = (
                PolicyPack.model_validate_json(args.policy.read_text())
                if args.policy
                else builtin_pack()
            )
        kernel = SynthesisKernel(
            data_dir=getattr(args, "data_dir", None),
            datasets_dir=getattr(args, "datasets_dir", None),
        )
        if args.command == "run":
            result = asyncio.run(kernel.run(pack, config))
        elif args.command == "resume":
            choices = {}
            for value in args.resolve:
                key, choice = value.split("=", 1)
                if key in choices:
                    raise ValueError("Duplicate unknown attempt choice")
                choices[key] = choice
            result = asyncio.run(
                kernel.resume(
                    args.run_dir,
                    unknown_decisions=choices,
                    max_run_cost=args.max_run_cost,
                    extend_seconds=args.extend_seconds,
                    allow_environment_change=args.allow_environment_change,
                )
            )
        elif args.command == "review":
            from ecoalign_forge.workbench.review import ReviewDecision, read_case, submit_review

            if bool(args.decision) == bool(args.case_id):
                raise ValueError("Supply exactly one of --case-id or --decision")
            result = (
                submit_review(
                    args.run_dir, ReviewDecision.model_validate_json(args.decision.read_text())
                )
                if args.decision
                else read_case(args.run_dir, args.case_id)
            )
        elif args.command == "dataset":
            from ecoalign_forge.workbench.dataset import (
                DatasetConfig,
                build_dataset,
                verify_dataset,
            )

            selection = json.loads(args.select_file.read_text()) if args.select_file else None
            if selection is not None and (
                not isinstance(selection, list) or not all(isinstance(k, str) for k in selection)
            ):
                raise ValueError("Selection must be a JSON list of string keys")
            output = build_dataset(
                args.run_dirs,
                args.output_root or kernel.datasets_dir,
                DatasetConfig(
                    include_unreviewed=args.include_unreviewed,
                    eval_fraction=args.eval_fraction,
                    seed=args.seed,
                ),
                selected=set(selection) if selection is not None else None,
            )
            result = {"output_path": str(output), "manifest": verify_dataset(output)}
        elif args.command == "verify":
            from ecoalign_forge.workbench.dataset import verify_dataset

            result = verify_dataset(args.dataset_dir)
        elif args.command == "report":
            from ecoalign_forge.workbench.report import build_report

            result = {
                "report": str(build_report(args.run_dir, args.output_root, dataset=args.dataset))
            }
        elif args.command == "pause":
            from ecoalign_forge.workbench.control import request_pause

            result = request_pause(args.run_dir)
        else:
            if not (args.run_dir / "run.sqlite3").is_file():
                raise ValueError("No checkpoint at run directory")
            journal = (
                Journal.readonly(args.run_dir)
                if args.command == "inspect"
                else Journal(args.run_dir)
            )
            try:
                if args.command == "inspect":
                    from ecoalign_forge.runtime.inspection import recovery_report

                    # Both views describe one SQLite read snapshot, even with a live writer.
                    journal.db.execute("BEGIN")
                    try:
                        result = kernel._snapshot(journal, persist=False)
                        result["recovery"] = recovery_report(journal)
                    finally:
                        journal.db.execute("ROLLBACK")
                else:
                    with journal.owner():
                        result = {
                            "output_path": str(journal.export(kernel.datasets_dir)),
                            "exit_code": 0,
                        }
            finally:
                journal.close()
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(f"Pipeline failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from exc
    if "status" in result:
        print(f"Pipeline {result['status']} ({result['execution_mode']})")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(result.get("exit_code", 0) if args.command != "inspect" else 0)


if __name__ == "__main__":
    main()
