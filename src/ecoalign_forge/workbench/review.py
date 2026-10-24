"""Append-only, optimistic human decisions. Never rewrite a machine checkpoint."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ecoalign_forge.policy.compiler import compile_policy, validate_final
from ecoalign_forge.policy.models import PolicyPack
from ecoalign_forge.runtime.journal import Journal, connect, transaction
from ecoalign_forge.schemas.kernel import (
    CandidateEvaluation,
    Contract,
    SourceText,
    canonical,
    digest,
)


class ReviewConflictError(ValueError):
    """A stale client must reload the current decision before submitting again."""


class ReviewDecision(Contract):
    operation_id: str = Field(min_length=1, max_length=160)
    case_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0, strict=True)
    action: Literal["accept", "correct", "abstain", "exclude"]
    reviewer: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=10000)
    corrected: CandidateEvaluation | None = None
    # Additional source/template families are additive; batches cannot be ungrouped.
    family_ids: tuple[str, ...] = ()
    error_type: Literal["none", "false_positive", "false_negative", "other"] = "none"

    @model_validator(mode="after")
    def valid_decision(self):
        if not self.reviewer.strip() or not self.reason.strip() or not self.operation_id.strip():
            raise ValueError("Reviewer, reason and operation ID cannot be blank")
        if (self.action == "correct") != (self.corrected is not None):
            raise ValueError("Only a correction supplies corrected evaluation")
        if len(set(self.family_ids)) != len(self.family_ids) or any(
            not f.strip() or len(f) > 160 for f in self.family_ids
        ):
            raise ValueError("Family IDs must be nonempty, unique and at most 160 characters")
        return self


def read_reviews(run_dir: Path) -> list[dict]:
    path = Path(run_dir) / "reviews.sqlite3"
    if not path.exists():
        return []
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        records = [json.loads(r[0]) for r in db.execute("SELECT value FROM reviews ORDER BY seq")]
        previous: dict[str, dict] = {}
        for record in records:
            cid = record["decision"]["case_id"]
            parent = previous.get(cid)
            if digest({k: v for k, v in record.items() if k != "review_id"}) != record["review_id"]:
                raise ValueError("Review record checksum mismatch")
            if record["parent_id"] != (parent["review_id"] if parent else None) or record[
                "revision"
            ] != (parent["revision"] + 1 if parent else 1):
                raise ValueError("Broken review revision chain")
            previous[cid] = record
        return records
    finally:
        db.close()


def _case(j: Journal, case_id: str, history: list[dict], *, row=None, pairs=None) -> dict:
    row = row or next((c for c in j.cases() if c["id"] == case_id), None)
    if row is None:
        raise ValueError("Unknown case ID")
    manifest = j.get("manifest")
    stages = {
        r["stage"]: json.loads(r["value"])
        for r in j.db.execute("SELECT * FROM stages WHERE case_id=?", (case_id,))
    }
    revisions = [r for r in history if r["decision"]["case_id"] == case_id]
    binding = digest(
        {
            "run_id": manifest["run_id"],
            "policy_hash": manifest["policy_hash"],
            "case": row,
            "stages": stages,
        }
    )
    if any(r["case_binding"] != binding for r in revisions):
        raise ValueError("Machine case changed after human review; refusing stale lineage")
    latest = revisions[-1] if revisions else None
    machine_final = (stages.get("gate") or {}).get("final")
    effective = latest["final"] if latest else machine_final
    return dict(
        case=row,
        stages=stages,
        manifest=manifest,
        case_binding=binding,
        history=revisions,
        latest=latest,
        revision=latest["revision"] if latest else 0,
        effective_final=effective,
        machine_pairs=pairs
        if pairs is not None
        else [p for p in j.pairs() if p["source_case_id"] == case_id],
    )


def read_case(run_dir: Path, case_id: str) -> dict:
    j = Journal.readonly(run_dir)
    try:
        # A consistent snapshot, with no migration or write to the old journal.
        j.db.execute("BEGIN")
        return _case(j, case_id, read_reviews(run_dir))
    finally:
        j.close()


def snapshot_run(run_dir: Path) -> dict:
    """Freeze a stopped/paused run and its review revisions for dataset construction."""
    j = Journal.readonly(run_dir)
    try:
        with j.owner():
            j.db.execute("BEGIN")
            history = read_reviews(run_dir)
            reviews_by_case, pairs_by_case = defaultdict(list), defaultdict(list)
            for revision in history:
                reviews_by_case[revision["decision"]["case_id"]].append(revision)
            for pair in j.pairs():
                pairs_by_case[pair["source_case_id"]].append(pair)
            from ecoalign_forge.engine.kernel import SynthesisKernel

            return dict(
                manifest=j.get("manifest"),
                summary=SynthesisKernel()._snapshot(j, persist=False),
                cases=[
                    _case(j, c["id"], reviews_by_case[c["id"]], row=c, pairs=pairs_by_case[c["id"]])
                    for c in j.cases()
                ],
                reviews=history,
            )
    finally:
        j.close()


def submit_review(run_dir: Path, decision: ReviewDecision) -> dict:
    """A committed correction must pass the same evidence and rule gate as chosen."""
    j = Journal.readonly(run_dir)
    db = None
    try:
        with j.owner():
            j.db.execute("BEGIN")
            view = _case(j, decision.case_id, read_reviews(run_dir))
            if view["case"]["source"] is None:
                raise ValueError("Case has no source to review")
            if view["case"]["state"] in {"in_progress", "unattempted"}:
                raise ValueError("Wait for the case's machine stages to finish")
            db = connect(Path(run_dir) / "reviews.sqlite3")
            db.execute("""CREATE TABLE IF NOT EXISTS reviews(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT UNIQUE NOT NULL,
                case_id TEXT NOT NULL, revision INTEGER NOT NULL, value TEXT NOT NULL,
                UNIQUE(case_id, revision))""")
            with transaction(db):
                old = db.execute(
                    "SELECT value FROM reviews WHERE operation_id=?", (decision.operation_id,)
                ).fetchone()
                if old:
                    value = json.loads(old[0])
                    if value["decision"] != decision.model_dump(mode="json"):
                        raise ReviewConflictError(
                            "Operation ID was already used for another decision"
                        )
                    return value
                if view["revision"] != decision.expected_revision:
                    raise ReviewConflictError(
                        f"Stale revision {decision.expected_revision}; current is {view['revision']}"
                    )
                final = None
                if decision.action in {"accept", "correct"}:
                    if decision.action == "correct":
                        candidate = decision.corrected
                        assert candidate is not None  # enforced by ReviewDecision
                    else:
                        if not view["effective_final"]:
                            raise ValueError("No acceptable current decision; provide a correction")
                        candidate = CandidateEvaluation.model_validate(
                            view["effective_final"]["evaluation"]
                        )
                    compiled = compile_policy(PolicyPack.model_validate(view["manifest"]["policy"]))
                    if compiled.sha256 != view["manifest"]["policy_hash"]:
                        raise ValueError("Policy snapshot hash mismatch")
                    gate = validate_final(
                        compiled,
                        SourceText.model_validate(view["case"]["source"]),
                        candidate,
                        "corrected" if decision.action == "correct" else "passed",
                    )
                    if gate.status != "accepted" or gate.final is None:
                        raise ValueError(
                            "Correction rejected by final gate: " + ", ".join(gate.reasons)
                        )
                    final = gate.final.model_dump(mode="json")
                from ecoalign_forge.engine.kernel import code_identity

                record = dict(
                    schema_version=1,
                    created_at=datetime.now(UTC).isoformat(),
                    revision=view["revision"] + 1,
                    decision=decision.model_dump(mode="json"),
                    case_binding=view["case_binding"],
                    policy_hash=view["manifest"]["policy_hash"],
                    run_id=view["manifest"]["run_id"],
                    parent_id=view["latest"]["review_id"] if view["latest"] else None,
                    final=final,
                    code=code_identity(),
                    reviewer_identity="self_declared_local",
                )
                record["review_id"] = digest(record)
                db.execute(
                    "INSERT INTO reviews(operation_id,case_id,revision,value) VALUES(?,?,?,?)",
                    (
                        decision.operation_id,
                        decision.case_id,
                        record["revision"],
                        canonical(record),
                    ),
                )
                return record
    finally:
        if db is not None:
            db.close()
        j.close()
