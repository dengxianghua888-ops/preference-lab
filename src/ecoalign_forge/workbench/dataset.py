"""Freeze reviews, deduplicate exact text, group families, publish verified versions."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import Field

from ecoalign_forge.export.sharegpt_format import to_sharegpt_dict
from ecoalign_forge.export.trl_format import to_trl_dict
from ecoalign_forge.policy.compiler import compile_policy, validate_final
from ecoalign_forge.policy.models import PolicyPack
from ecoalign_forge.runtime.journal import atomic_write
from ecoalign_forge.runtime.prompts import messages
from ecoalign_forge.schemas.kernel import (
    CandidateEvaluation,
    Contract,
    PreferencePair,
    RunConfig,
    SourceText,
    canonical,
    digest,
    text_hash,
)
from ecoalign_forge.workbench.review import snapshot_run

BUILDER_VERSION = "curated-dataset-2"


def _audit_hash(manifest):
    # Exclude only the recipe and its digest to avoid a circular hash. All audit
    # metadata and exported file checksums must participate in version identity.
    return digest({k: v for k, v in manifest.items() if k not in {"recipe", "dataset_version"}})


class DatasetConfig(Contract):
    # False is the formal curated default. True is explicitly a candidate preview.
    include_unreviewed: bool = False
    eval_fraction: float = Field(default=0.2, ge=0, lt=1)
    seed: int = Field(default=0, strict=True)


def _pair(view):
    final = view["effective_final"]
    if final is None:
        return None, "no_accepted_final"
    manifest = view["manifest"]
    compiled = compile_policy(PolicyPack.model_validate(manifest["policy"]))
    if compiled.sha256 != manifest["policy_hash"]:
        raise ValueError("Policy snapshot hash mismatch")
    source = SourceText.model_validate(view["case"]["source"])
    candidate = CandidateEvaluation.model_validate(final["evaluation"])
    gate = validate_final(compiled, source, candidate, final["review_status"])
    if (
        gate.status != "accepted"
        or gate.final is None
        or gate.final.model_dump(mode="json") != final
    ):
        raise ValueError("Saved final no longer passes the recorded gate")
    config = RunConfig.model_validate(manifest["config"])
    for role in ("moderator", "judge"):
        raw = view["stages"].get(role)
        if raw is None:
            continue
        rejected = CandidateEvaluation.model_validate(raw)
        if (
            set(rejected.labels) != set(candidate.labels)
            or rejected.final_action not in compiled.pack.actions
        ):
            continue
        if any(rejected.labels[d.id] not in d.labels for d in compiled.pack.dimensions):
            continue
        distance = (
            sum(candidate.labels[k] != rejected.labels[k] for k in candidate.labels)
            + (candidate.final_action != rejected.final_action)
        ) / (len(candidate.labels) + 1)
        if distance <= 0 or distance < config.min_label_distance:
            continue
        latest = view["latest"]
        prompt = messages(
            "judge",
            compiled,
            dict(source=source.model_dump(mode="json"), persona=config.persona),
            config,
        )
        lineage = dict(
            execution_mode=manifest["execution_mode"],
            fixture_version=manifest["fixture_version"],
            source_policy_id=compiled.pack.policy_id,
            source_policy_version=compiled.pack.version,
            policy_hash=compiled.sha256,
            pipeline_run_id=manifest["run_id"],
            source_hash=source.sha256,
            chosen_hash=digest(candidate),
            rejected_hash=digest(rejected),
            prompt_hash=digest(prompt),
            code=manifest["code"],
            review_id=latest["review_id"] if latest else None,
            review_revision=view["revision"],
            rejected_role=role,
            signal="human_reviewed_label_disagreement" if latest else "machine_label_disagreement",
            source_record_id=f"{manifest['run_id']}:{view['case']['id']}",
            original_pair_ids=[p["pair_id"] for p in view["machine_pairs"]],
        )
        return PreferencePair(
            pair_id=digest(
                [
                    BUILDER_VERSION,
                    lineage,
                    candidate.model_dump(mode="json"),
                    rejected.model_dump(mode="json"),
                ]
            ),
            prompt=canonical(prompt),
            chosen=canonical(candidate),
            rejected=canonical(rejected),
            source_case_id=view["case"]["id"],
            label_distance=distance,
            lineage=lineage,
        ), None
    return None, "no_label_disagreement"


def _groups(records):
    """Union shared hashes, generation batches and declared families in near-linear time."""
    parent = {r["record_id"]: r["record_id"] for r in records}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    owner: dict[str, str] = {}
    for record in records:
        for token in record["family_tokens"]:
            if token in owner:
                a, b = sorted((root(record["record_id"]), root(owner[token])))
                parent[b] = a
            else:
                owner[token] = record["record_id"]
    members = defaultdict(list)
    for record in records:
        members[root(record["record_id"])].append(record)
    for group in members.values():
        group_id = digest(sorted({t for r in group for t in r["family_tokens"]}))
        for record in group:
            record["group_id"] = group_id


def build_dataset(
    run_dirs,
    output_root: Path,
    config: DatasetConfig | None = None,
    *,
    selected: set[str] | None = None,
    hook=None,
) -> Path:
    """Selection keys are RUN_ID:CASE_ID. Snapshots freeze their exact review revisions."""
    from ecoalign_forge.engine.kernel import code_identity

    config = config or DatasetConfig()
    hook = hook or (lambda *_: None)
    paths = sorted({Path(p).resolve() for p in run_dirs})
    if not paths:
        raise ValueError("Select at least one run")
    snapshots = sorted((snapshot_run(p) for p in paths), key=lambda s: s["manifest"]["run_id"])
    manifests = [s["manifest"] for s in snapshots]
    if len({m["run_id"] for m in manifests}) != len(manifests):
        raise ValueError("Duplicate run identity")
    if len({m["execution_mode"] for m in manifests}) != 1:
        raise ValueError("Cannot mix execution modes")
    if len({m["policy_hash"] for m in manifests}) != 1:
        raise ValueError("Cannot mix policy snapshots")
    mode = manifests[0]["execution_mode"]
    if mode not in {"demo", "mock", "live"}:
        raise ValueError("Unknown source mode is read-only")
    records, candidates, seen = [], {}, set()
    for snapshot in snapshots:
        m = snapshot["manifest"]
        for view in snapshot["cases"]:
            case, latest = view["case"], view["latest"]
            key = f"{m['run_id']}:{case['id']}"
            seen.add(key)
            source_hash = text_hash(case["source"]["content"]) if case["source"] else None
            families = {f"batch:{m['run_id']}:{case['batch']}"}
            if source_hash:
                families.add("text:" + source_hash)
            for name in ("source_family_id", "template_family_id"):
                if case["plan"].get(name):
                    families.add("declared:" + str(case["plan"][name]))
            # Family membership is additive across review revisions, so changing an
            # annotation cannot silently separate previously linked variants.
            for rev in view["history"]:
                families.update("declared:" + f for f in rev["decision"]["family_ids"])
            record = dict(
                record_id=key,
                run_id=m["run_id"],
                case_id=case["id"],
                source=case["source"],
                source_hash=source_hash,
                machine_state=case["state"],
                machine_reason=case["reason"],
                plan=case["plan"],
                stages=view["stages"],
                reviews=view["history"],
                review_id=latest["review_id"] if latest else None,
                revision=view["revision"],
                effective_final=view["effective_final"],
                machine_pairs=view["machine_pairs"],
                family_tokens=sorted(families),
                selected=selected is None or key in selected,
                disposition="excluded",
                reason=None,
                pair_id=None,
            )
            if not record["selected"]:
                record["reason"] = "not_selected"
            elif latest and latest["decision"]["action"] in {"abstain", "exclude"}:
                record["reason"] = "human_" + latest["decision"]["action"]
            elif not latest and not config.include_unreviewed:
                record["reason"] = "not_human_reviewed"
            else:
                pair, reason = _pair(view)
                if pair:
                    candidates[key] = pair
                record["reason"] = reason
            records.append(record)
    if selected is not None and (not selected or not selected.issubset(seen)):
        raise ValueError("Selection is empty or contains unknown case IDs")
    records.sort(key=lambda r: r["record_id"])
    _groups(records)
    by_text = defaultdict(list)
    for record in records:
        if (
            record["selected"]
            and record["effective_final"]
            and record["reason"] in {None, "no_label_disagreement"}
        ):
            by_text[record["source_hash"]].append(record)
    pairs = []
    for same_text in by_text.values():
        signatures = set()
        for r in same_text:
            chosen = r["effective_final"]["evaluation"]
            signatures.add(
                digest({k: chosen[k] for k in ("labels", "rule_judgments", "final_action")})
            )
        if len(signatures) != 1:
            for r in same_text:
                r["reason"] = "duplicate_decision_conflict"
            continue
        eligible = [r for r in same_text if r["record_id"] in candidates]
        for index, r in enumerate(eligible):
            if index:
                r.update(reason="exact_duplicate", duplicate_of=eligible[0]["record_id"])
                continue
            partition = (
                "eval"
                if int(digest([config.seed, r["group_id"]])[:16], 16) / 2**64 < config.eval_fraction
                else "train"
            )
            pair = candidates[r["record_id"]]
            lineage = pair.lineage | {"split": partition, "group_id": r["group_id"]}
            pair = pair.model_copy(update={"lineage": lineage})
            r.update(
                disposition="included", reason="accepted", pair_id=pair.pair_id, split=partition
            )
            pairs.append(pair)
    pairs.sort(key=lambda p: p.pair_id)
    recipe = dict(
        builder_version=BUILDER_VERSION,
        config=config.model_dump(mode="json"),
        code=code_identity(),
        run_ids=sorted(m["run_id"] for m in manifests),
        policy_hash=manifests[0]["policy_hash"],
        source_records_hash=digest(records),
        pairs_hash=digest([p.model_dump(mode="json") for p in pairs]),
    )
    payload = dict(
        schema_version=4,
        recipe=recipe,
        execution_mode=mode,
        policy_hash=manifests[0]["policy_hash"],
        language=manifests[0]["policy"]["language"],
        data_license=manifests[0]["policy"].get("data_license"),
        pairs=len(pairs),
        selection="candidate_preview" if config.include_unreviewed else "human_reviewed",
        selected_cases=sum(r["selected"] for r in records),
        source_cases=len(records),
        unique_texts=len({r["source_hash"] for r in records if r["source_hash"] and r["selected"]}),
        exclusion_reasons=dict(
            Counter(
                r["reason"] for r in records if r["disposition"] == "excluded" and r["selected"]
            )
        ),
        reviewed_cases=sum(r["selected"] and r["review_id"] is not None for r in records),
        partitions={s: sum(p.lineage["split"] == s for p in pairs) for s in ("train", "eval")},
        source_runs=[
            {k: s["summary"][k] for k in ("run_id", "status", "counts", "budget")}
            for s in snapshots
        ],
        verification="human_decisions_and_policy_consistency_not_independent_gold",
        deduplication="exact_unicode_sha256_conflicts_excluded",
        grouping="connected_source_template_batch_families",
    )
    jsonl = lambda values: "".join(canonical(v) + "\n" for v in values)  # noqa: E731
    contents = {
        "pairs.jsonl": jsonl(pairs),
        "sources.jsonl": jsonl(records),
        "policy.json": canonical(manifests[0]["policy"]) + "\n",
    }
    for split in (None, "train", "eval"):
        subset = pairs if split is None else [p for p in pairs if p.lineage["split"] == split]
        prefix = "" if split is None else split + "/"
        contents[prefix + "trl_standard.jsonl"] = jsonl(to_trl_dict(p) for p in subset)
        contents[prefix + "trl_conversational.jsonl"] = jsonl(
            to_trl_dict(p, conversational=True) for p in subset
        )
        contents[prefix + "train_sharegpt.json"] = (
            canonical([to_sharegpt_dict(p) for p in subset]) + "\n"
        )
        contents[prefix + "dataset_info.json"] = (
            canonical(
                {
                    "ecoalign_forge_dpo": {
                        "file_name": "train_sharegpt.json",
                        "formatting": "sharegpt",
                        "ranking": True,
                        "columns": {
                            "messages": "conversations",
                            "chosen": "chosen",
                            "rejected": "rejected",
                        },
                    }
                }
            )
            + "\n"
        )
    contents["README.md"] = (
        f"# EcoAlign-Forge curated dataset\n\nMode: {mode}; language: {payload['language']}; "
        f"data license: {payload['data_license'] or 'unspecified'}.\n\n"
        f"Selection: {payload['selection']}. {len(pairs)} unique preference pairs.\n\n"
        "Human identity is self-declared locally; review is not independent gold truth. "
        "Exact text deduplication does not detect semantic near-duplicates. Declared families "
        "and generation batches stay together within this version. Eval is a development "
        "partition, not an independent holdout. Small datasets can have an empty partition.\n\n"
        "Use train/ and eval/ for partitioned consumers; root views contain all pairs. "
        "sources.jsonl preserves original text, candidates, evidence and review history. "
        "manifest.json binds all file hashes and counts. No training benefit is established.\n"
    )
    payload["pairs_sha256"] = text_hash(contents["pairs.jsonl"])
    payload["files"] = {name: text_hash(content) for name, content in contents.items()}
    recipe["audit_hash"] = _audit_hash(payload)
    version = digest(recipe)
    payload["dataset_version"] = version
    contents["manifest.json"] = canonical(payload) + "\n"
    root = Path(output_root) / mode / "curated"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / version
    # Per-dataset publication lock; readers see either no version or the full directory.
    import fcntl

    with (root / (version + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            verify_dataset(destination)
            if (destination / "manifest.json").read_text() != contents["manifest.json"]:
                raise ValueError("Immutable dataset conflict")
            return destination
        staging = root.parent / ".curated-staging"
        staging.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="building-", dir=staging))
        try:
            for name, content in contents.items():
                atomic_write(temporary / name, content)
            verify_dataset(temporary)
            hook("before_dataset_publish", {"version": version})
            os.rename(temporary, destination)
            fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            hook("dataset_published", {"version": version})
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return destination


def verify_dataset(path: Path) -> dict:
    """Fail closed on corrupt files, counts, split leaks and broken source/review links."""
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("schema_version") == 3:
        raise ValueError(
            "Legacy curated dataset has unbound audit metadata; rebuild from source runs"
        )
    if manifest.get("schema_version") != 4:
        raise ValueError("Expected a C curated dataset manifest")
    if digest(manifest["recipe"]) != manifest["dataset_version"]:
        raise ValueError("Dataset recipe hash mismatch")
    if _audit_hash(manifest) != manifest["recipe"].get("audit_hash"):
        raise ValueError("Dataset audit hash mismatch")
    for name, expected in manifest["files"].items():
        file = path / name
        if file.is_symlink() or not file.resolve().is_relative_to(path.resolve()):
            raise ValueError("Unsafe manifest file path")
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError("Dataset checksum mismatch: " + name)
    pairs = [json.loads(line) for line in (path / "pairs.jsonl").read_text().splitlines()]
    sources = [json.loads(line) for line in (path / "sources.jsonl").read_text().splitlines()]
    if (
        digest(sources) != manifest["recipe"]["source_records_hash"]
        or digest(pairs) != manifest["recipe"]["pairs_hash"]
    ):
        raise ValueError("Snapshot hash mismatch")
    if len(pairs) != manifest["pairs"] or len(sources) != manifest["source_cases"]:
        raise ValueError("Dataset count mismatch")
    if manifest["pairs_sha256"] != manifest["files"]["pairs.jsonl"]:
        raise ValueError("Pair checksum mismatch")
    compiled = compile_policy(PolicyPack.model_validate_json((path / "policy.json").read_text()))
    if (
        compiled.sha256 != manifest["policy_hash"]
        or compiled.sha256 != manifest["recipe"]["policy_hash"]
    ):
        raise ValueError("Exported policy hash mismatch")
    ids, hashes = set(), set()
    groups: dict[str, str] = {}
    by_source = {s["record_id"]: s for s in sources}
    if len(by_source) != len(sources):
        raise ValueError("Duplicate source identity")
    for pair in pairs:
        lineage = pair["lineage"]
        source = by_source[lineage["source_record_id"]]
        if pair["pair_id"] in ids or lineage["source_hash"] in hashes:
            raise ValueError("Duplicate pair or text")
        ids.add(pair["pair_id"])
        hashes.add(lineage["source_hash"])
        if source["pair_id"] != pair["pair_id"] or source["review_id"] != lineage["review_id"]:
            raise ValueError("Broken source/review lineage")
        if text_hash(source["source"]["content"]) != lineage["source_hash"]:
            raise ValueError("Source hash mismatch")
        if json.loads(pair["chosen"]) != source["effective_final"]["evaluation"]:
            raise ValueError("Chosen differs from final review")
        gate = validate_final(
            compiled,
            SourceText.model_validate(source["source"]),
            CandidateEvaluation.model_validate_json(pair["chosen"]),
            source["effective_final"]["review_status"],
        )
        if (
            gate.status != "accepted"
            or gate.final is None
            or gate.final.model_dump(mode="json") != source["effective_final"]
        ):
            raise ValueError("Exported chosen fails final gate")
        if manifest["selection"] == "human_reviewed" and not source["review_id"]:
            raise ValueError("Missing human review in curated dataset")
        group, split = lineage["group_id"], lineage["split"]
        if group in groups and groups[group] != split:
            raise ValueError("Family leaked across partitions")
        groups[group] = split
    if set(manifest["partitions"]) != {"train", "eval"} or sum(
        manifest["partitions"].values()
    ) != len(pairs):
        raise ValueError("Invalid partition counts")
    for split in (None, "train", "eval"):
        subset = pairs if split is None else [p for p in pairs if p["lineage"]["split"] == split]
        prefix = path if split is None else path / split
        if split is not None and manifest["partitions"][split] != len(subset):
            raise ValueError("Partition count mismatch")
        for name, conversational in (
            ("trl_standard.jsonl", False),
            ("trl_conversational.jsonl", True),
        ):
            rows = [json.loads(x) for x in (prefix / name).read_text().splitlines()]
            if rows != [
                to_trl_dict(PreferencePair.model_validate(p), conversational=conversational)
                for p in subset
            ]:
                raise ValueError("Consumer projection mismatch")
        if json.loads((prefix / "train_sharegpt.json").read_text()) != [
            to_sharegpt_dict(PreferencePair.model_validate(p)) for p in subset
        ]:
            raise ValueError("ShareGPT projection mismatch")
    return manifest
