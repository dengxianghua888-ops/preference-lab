"""Versioned, read-only comparison of explicitly identified evaluation records."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import Field, model_validator

from ecoalign_forge.schemas.kernel import Contract, canonical, digest, text_hash


class Case(Contract):
    case_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    reference_labels: dict[str, str] | None = None

    @property
    def source_hash(self):
        return text_hash(self.content)


class EvaluationSet(Contract):
    schema_version: Literal[1] = 1
    cases: tuple[Case, ...] = Field(min_length=1)
    scorer_spec: dict = Field(min_length=1)
    reference_label_revision: str | None = None
    source_kind: Literal["machine_candidate", "human_reviewed", "unlabeled"] = "unlabeled"

    @model_validator(mode="after")
    def unique(self):
        index(self.cases)
        if any(c.reference_labels is not None for c in self.cases) and (
            not self.reference_label_revision or self.source_kind == "unlabeled"
        ):
            raise ValueError("Reference labels require a revision and explicit source_kind")
        return self

    @property
    def identity(self):
        return {
            "eval_set_sha256": digest(
                [
                    {"case_id": c.case_id, "source_hash": c.source_hash}
                    for c in sorted(self.cases, key=lambda c: c.case_id)
                ]
            ),
            "scorer_spec_sha256": digest(self.scorer_spec),
            "reference_labels_sha256": digest(
                {
                    "revision": self.reference_label_revision,
                    "source_kind": self.source_kind,
                    "labels": {c.case_id: c.reference_labels for c in self.cases},
                }
            ),
        }


class Identity(Contract):
    eval_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scorer_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_labels_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Record(Contract):
    case_id: str = Field(min_length=1)
    source_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["accepted", "excluded", "abstain", "failed", "unattempted", "in_progress"]
    action: str | None = None
    labels: dict[str, str] | None = None
    evidence: tuple[dict, ...] | None = None
    reason: str | None = None


class Snapshot(Contract):
    schema_version: Literal[1] = 1
    identity: Identity | None = None
    provenance: dict = Field(default_factory=dict)
    budget: dict | None = None
    records: tuple[Record, ...]

    @model_validator(mode="after")
    def unique(self):
        index(self.records)
        # Pydantic's allow_inf_nan does not validate arbitrary values inside dicts.
        # Check the Python representation before JSON serialization can erase NaN.
        canonical(self.model_dump())
        return self


def index(records):
    result = {}
    for record in records:
        if record.case_id in result:
            raise ValueError(f"Duplicate case_id: {record.case_id}")
        result[record.case_id] = record
    return result


def freeze(data):
    """Recompute identities from content, never trust caller-supplied hashes."""
    evaluation = EvaluationSet.model_validate(data)
    return {"evaluation_set": evaluation.model_dump(mode="json"), "identity": evaluation.identity}


def load_set(data):
    if set(data) != {"evaluation_set", "identity"}:
        raise ValueError("Expected a frozen evaluation_set and identity")
    evaluation = EvaluationSet.model_validate(data["evaluation_set"])
    if data["identity"] != evaluation.identity:
        raise ValueError("Evaluation-set content or scoring/reference identity was changed")
    return evaluation


def compare(evaluation, baseline, candidate):
    expected = evaluation.identity
    issues = []
    sets = index(evaluation.cases)
    sides = {"baseline": baseline, "candidate": candidate}
    maps = {name: index(s.records) for name, s in sides.items()}
    for name, snapshot in sides.items():
        if snapshot.identity is None:
            issues.append(f"{name}: missing frozen evaluation identity (legacy/unbound output)")
        elif snapshot.identity.model_dump() != expected:
            issues.append(f"{name}: evaluation/scorer/reference identity mismatch")
        unknown = sorted(set(maps[name]) - set(sets))
        if unknown:
            issues.append(f"{name}: records outside evaluation set: {', '.join(unknown)}")
    rows = []
    for cid, case in sorted(sets.items()):
        left, right = maps["baseline"].get(cid), maps["candidate"].get(cid)
        problems = []
        for name, record in (("baseline", left), ("candidate", right)):
            if record is None:
                problems.append(f"{name}: missing result")
            elif record.source_hash != case.source_hash:
                problems.append(f"{name}: source content identity missing or mismatched")
                issues.append(f"{name}/{cid}: source content identity missing or mismatched")
        comparable = not problems
        changes = []
        if comparable:
            changes = [
                field
                for field in ("status", "action", "labels", "evidence", "reason")
                if getattr(left, field) != getattr(right, field)
            ]
        rows.append(
            {
                "case_id": cid,
                "source_hash": case.source_hash,
                "baseline": left.model_dump(mode="json") if left else None,
                "candidate": right.model_dump(mode="json") if right else None,
                "content_matched": comparable,
                "changes": changes,
                "explanation": problems
                or (["Changed: " + ", ".join(changes)] if changes else ["Unchanged"]),
            }
        )
    matched = sum(r["content_matched"] for r in rows)
    changed = sum(bool(r["changes"]) for r in rows)
    return {
        "schema_version": 1,
        "comparison": "same_evaluation_set" if not issues else "overlap_only",
        "comparison_notice": (
            "Both snapshots declare the frozen evaluation/scorer/reference identity."
            if not issues
            else "Only exact ID/content overlap is shown; evaluation/scorer/reference equivalence is not established."
        ),
        "identity_issues": issues,
        "identity": expected,
        "reference_source_kind": evaluation.source_kind,
        "reference_label_revision": evaluation.reference_label_revision,
        "identity_notice": "Snapshot identities are producer declarations, not proof of execution or human review.",
        "model_quality": "not_evaluated",
        "notice": "Behavior differences only; candidate labels are not human accuracy or improvements.",
        "coverage": {
            "evaluation_cases": len(sets),
            "content_matched_cases": matched,
            "changed_cases": changed,
            "change_rate_denominator": matched,
            "change_rate": changed / matched if matched else None,
        },
        "status_counts": {
            name: dict(
                sorted(
                    Counter(
                        maps[name][cid].status if cid in maps[name] else "missing" for cid in sets
                    ).items()
                )
            )
            for name in sides
        },
        "provenance": {name: s.provenance for name, s in sides.items()},
        "budgets": {name: s.budget for name, s in sides.items()},
        "budget_notice": "Run-wide reported estimates/reservations, not per-case cost or actual bills; no improvement score.",
        "cases": rows,
    }


def snapshot_run(run_dir):
    """Copy a quiescent journal before opening SQLite: never create WAL/SHM in the run.

    Legacy run-local IDs remain run-local. No evaluation identity is manufactured.
    """
    from ecoalign_forge.runtime.journal import Journal

    run_dir = Path(run_dir)
    if not (run_dir / "run.sqlite3").is_file():
        raise ValueError("No checkpoint at run directory")

    def signature():
        return {
            p.name: (p.stat().st_size, p.stat().st_mtime_ns)
            for p in (run_dir / "run.sqlite3", run_dir / "run.sqlite3-wal")
            if p.exists()
        }

    with TemporaryDirectory() as temporary:
        before = signature()
        for name in before:
            shutil.copyfile(run_dir / name, Path(temporary) / name)
        if signature() != before:
            raise ValueError("Run changed during snapshot; stop its writer and retry")
        journal = Journal.readonly(temporary)
        try:
            manifest = journal.get("manifest") or {}
            records = []
            for case in journal.cases():
                gate = journal.stage(case["id"], "gate") or {}
                review = journal.stage(case["id"], "review") or {}
                final = gate.get("final") or {}
                evaluation = final.get("evaluation") or {}
                source = case["source"]
                records.append(
                    Record(
                        case_id=case["id"],
                        source_hash=text_hash(source["content"]) if source else None,
                        status=(
                            "abstain"
                            if gate.get("status") == "abstain" or review.get("status") == "abstain"
                            else case["state"]
                        ),
                        action=evaluation.get("final_action"),
                        labels=evaluation.get("labels"),
                        evidence=evaluation.get("evidence"),
                        reason=case["reason"],
                    )
                )
            return Snapshot(
                records=tuple(records),
                budget=journal.budget_snapshot(),
                provenance={
                    "adapter": "legacy-journal-v1",
                    "run_dir": str(run_dir.resolve()),
                    "manifest": manifest,
                    "identity_origin": "unavailable_in_legacy_run",
                },
            ).model_dump(mode="json")
        finally:
            journal.close()


def read_json(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # Also catches numeric overflow (e.g. 1e999), not only NaN/Infinity tokens.
    canonical(data)
    return data


def render_text(report):
    """Compact human-readable counterpart to the full JSON audit report."""
    coverage = report["coverage"]
    lines = [
        "EcoAlign offline behavior comparison",
        "Scope: " + report["comparison"],
        report["comparison_notice"],
        report["notice"],
        report["identity_notice"],
        f"Coverage: {coverage['content_matched_cases']}/{coverage['evaluation_cases']} cases have matching content on both sides.",
        f"Changed: {coverage['changed_cases']}/{coverage['change_rate_denominator']} content-matched cases (not an improvement rate).",
    ]
    lines.extend("Identity warning: " + issue for issue in report["identity_issues"])
    for name in ("baseline", "candidate"):
        counts = ", ".join(f"{key}={value}" for key, value in report["status_counts"][name].items())
        lines.append(f"{name}: {counts}")
    for row in report["cases"]:
        left, right = row["baseline"], row["candidate"]
        left_state = left["status"] if left else "missing"
        right_state = right["status"] if right else "missing"
        lines.append(
            f"- {row['case_id']}: {left_state} -> {right_state}; {'; '.join(row['explanation'])}"
        )
        for name, record in (("baseline", left), ("candidate", right)):
            if record:
                lines.append(
                    f"  {name}: action={record['action']!r}, labels={record['labels']!r}, reason={record['reason']!r}"
                )
    lines.append(report["budget_notice"])
    for name, budget in report["budgets"].items():
        lines.append(f"{name} budget: " + json.dumps(budget, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)
