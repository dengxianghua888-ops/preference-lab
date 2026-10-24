"""Reports bind persisted run counts and the exact reported JSONL bytes."""

from __future__ import annotations

import html
import json
from pathlib import Path

from ecoalign_forge.runtime.journal import atomic_write
from ecoalign_forge.schemas.kernel import canonical, digest, text_hash
from ecoalign_forge.workbench.dataset import verify_dataset
from ecoalign_forge.workbench.review import snapshot_run


def build_report(run_dir: Path, output_root: Path, *, dataset: Path | None = None) -> Path:
    snapshot = snapshot_run(run_dir)
    manifest = snapshot["manifest"]
    summary = snapshot["summary"]
    dataset_manifest = verify_dataset(dataset) if dataset else None
    if dataset_manifest and manifest["run_id"] not in dataset_manifest["recipe"]["run_ids"]:
        raise ValueError("Dataset does not contain the selected run")
    if dataset:
        content = (Path(dataset) / "pairs.jsonl").read_text()
    else:
        pairs = sorted(
            [p for c in snapshot["cases"] for p in c["machine_pairs"]], key=lambda p: p["pair_id"]
        )
        content = "".join(canonical(p) + "\n" for p in pairs)
    count = len(content.splitlines())
    if count != (dataset_manifest["pairs"] if dataset_manifest else summary["counts"]["dpo_pairs"]):
        raise ValueError("Report pair count mismatch")
    # Exclude local filesystem roots, credential environment, and provider error strings.
    body = dict(
        schema_version=1,
        run_id=manifest["run_id"],
        execution_mode=manifest["execution_mode"],
        code=manifest["code"],
        policy_hash=manifest["policy_hash"],
        policy_version=manifest["policy"]["version"],
        language=manifest["policy"]["language"],
        persona=manifest["config"]["persona"],
        status=summary["status"],
        pause_reason=summary["pause_reason"],
        error=summary["error"],
        machine_counts=summary["counts"],
        budget=summary["budget"],
        human_review_revisions=len(snapshot["reviews"]),
        human_reviewed_cases=sum(c["revision"] > 0 for c in snapshot["cases"]),
        dataset=dataset_manifest,
        reported_pairs=count,
        pairs_sha256=text_hash(content),
        pair_scope="selected_dataset_all_runs" if dataset else "machine_run",
        evidence_boundary="Local self-declared reviews and deterministic consistency; model quality and training not evaluated",
    )
    directory = Path(output_root) / digest(body)
    json_content = canonical(body) + "\n"
    page = """<!doctype html><html lang="zh"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EcoAlign-Forge · 运行与数据报告</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:1000px;margin:40px auto;padding:0 24px;color:#1d2633;background:#f8fafc}
h1{font-size:28px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#e9eef5;padding:20px}dt{font-weight:650}dd{margin:0 0 16px;overflow-wrap:anywhere}</style>
<h1>EcoAlign-Forge · 运行与数据报告</h1>
<p>机器处理计数与导出数据计数分别列示。复核者身份为本地自报，模型质量与训练收益未评估。</p>
<dl>"""
    for title, value in [
        ("运行", body["run_id"]),
        ("来源", body["execution_mode"]),
        ("状态", body["status"]),
        ("导出范围", body["pair_scope"]),
        ("报告 JSONL 对数", count),
        ("JSONL SHA-256", body["pairs_sha256"]),
        ("规则 SHA-256", body["policy_hash"]),
    ]:
        page += f"<dt>{html.escape(title)}</dt><dd>{html.escape(str(value))}</dd>"
    page += (
        "</dl><h2>完整计数、预算与版本</h2><pre>"
        + html.escape(json.dumps(body, ensure_ascii=False, indent=2))
        + "</pre></html>\n"
    )
    files = {"pairs.jsonl": content, "report.json": json_content, "report.html": page}
    files["SHA256SUMS"] = "".join(f"{text_hash(value)}  {name}\n" for name, value in files.items())
    for name, value in files.items():
        path = directory / name
        if path.exists() and path.read_text() != value:
            raise ValueError("Immutable report conflict")
        if not path.exists():
            atomic_write(path, value)
    return directory / "report.html"
