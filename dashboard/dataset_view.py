"""Selection, immutable version publication, verification and traceable downloads."""

import io
import json
import zipfile
from collections import Counter
from pathlib import Path

import streamlit as st

from ecoalign_forge.config import settings
from ecoalign_forge.schemas.kernel import text_hash
from ecoalign_forge.workbench.dataset import DatasetConfig, build_dataset, verify_dataset
from ecoalign_forge.workbench.report import build_report
from ecoalign_forge.workbench.review import snapshot_run


def render_datasets(mode, runs, selected):
    st.subheader("数据集")
    st.caption(
        "默认仅导出已人工接受或改判的样本。精确去重后，同源族与生成批次保持在同一分区；eval 用于开发验证。"
    )
    chosen_runs = st.multiselect(
        "纳入运行（规则快照必须相同）",
        runs,
        default=[selected],
        format_func=lambda p: p.parent.name,
    )
    if not chosen_runs:
        st.info("至少选择一条运行后才能筛选和导出。")
        return
    try:
        snapshots = [snapshot_run(p.parent) for p in chosen_runs]
    except Exception as exc:
        st.error(f"读取待导出样本失败：{exc}")
        return
    if len({s["manifest"]["policy_hash"] for s in snapshots}) != 1:
        st.error("规则快照不同，请选择同一规则版本的运行。")
        return
    rows = []
    for snapshot in snapshots:
        for view in snapshot["cases"]:
            case = view["case"]
            ev = (view["effective_final"] or {}).get("evaluation") or {}
            latest = view["latest"]
            rows.append(
                dict(
                    key=f"{snapshot['manifest']['run_id']}:{case['id']}",
                    text=case["source"]["content"] if case["source"] else "（未生成）",
                    source_hash=text_hash(case["source"]["content"]) if case["source"] else None,
                    labels=ev.get("labels", {}),
                    rules=[k for k, v in ev.get("rule_judgments", {}).items() if v == "hit"],
                    review=latest["decision"]["action"] if latest else "未复核",
                    error_type=latest["decision"]["error_type"] if latest else "none",
                    families=sorted(
                        {f for rev in view["history"] for f in rev["decision"]["family_ids"]}
                    ),
                    batch=f"{snapshot['manifest']['run_id'][:8]}:{case['batch']}",
                )
            )
    with st.expander("筛选样本", expanded=True):
        a, b, c = st.columns(3)
        dimension = a.selectbox(
            "维度筛选",
            ["全部", *[d["id"] for d in snapshots[0]["manifest"]["policy"]["dimensions"]]],
        )
        labels = b.multiselect(
            "标签筛选",
            sorted({str(r["labels"].get(dimension)) for r in rows if dimension in r["labels"]}),
        )
        rules = c.multiselect("命中规则", sorted({v for r in rows for v in r["rules"]}))
        a, b, c = st.columns(3)
        problems = a.multiselect("问题类型", ["false_positive", "false_negative", "other", "none"])
        families = b.multiselect(
            "来源族 / 模板族", sorted({f for r in rows for f in r["families"]})
        )
        duplicates_only = c.checkbox("只看精确重复文本")
    counts = Counter(r["source_hash"] for r in rows if r["source_hash"])
    filtered = [
        r
        for r in rows
        if (not labels or r["labels"].get(dimension) in labels)
        and (not rules or set(rules).intersection(r["rules"]))
        and (not problems or r["error_type"] in problems)
        and (not families or set(families).intersection(r["families"]))
        and (not duplicates_only or counts.get(r["source_hash"], 0) > 1)
    ]
    st.write(f"筛选结果：{len(filtered)} 条 / {len(rows)} 条")
    st.dataframe(
        [
            {
                "内容": r["text"][:120],
                "复核": r["review"],
                "标签": str(r["labels"]),
                "问题类型": r["error_type"],
                "批次族": r["batch"],
                "Case": r["key"],
            }
            for r in filtered
        ],
        hide_index=True,
        width="stretch",
    )
    preview = st.checkbox("包含未复核机器结果（仅候选预览）")
    a, b = st.columns(2)
    fraction = a.number_input("eval 比例", min_value=0.0, max_value=0.99, value=0.2, step=0.05)
    seed = b.number_input("分区 seed", min_value=0, value=0, step=1)
    if st.button("生成不可变数据集版本", type="primary", disabled=not filtered):
        try:
            with st.spinner("正在冻结复核版本、去重、分区并校验…"):
                output = build_dataset(
                    [p.parent for p in chosen_runs],
                    settings.datasets_dir,
                    DatasetConfig(include_unreviewed=preview, eval_fraction=fraction, seed=seed),
                    selected={r["key"] for r in filtered},
                )
            st.session_state["curated:" + mode] = str(output)
            st.success("数据集版本已保存并通过文件与溯源校验。")
        except Exception as exc:
            st.error(f"未导出：{exc}")
    versions = sorted(
        (settings.datasets_dir / mode / "curated").glob("*/manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    st.markdown("#### 已保存版本")
    if not versions:
        st.info("此来源还没有经过整理的数据集版本。")
        return
    preferred = st.session_state.get("curated:" + mode)
    index = next((i for i, p in enumerate(versions) if str(p.parent) == preferred), 0)
    chosen = st.selectbox(
        "数据集版本", versions, index=index, format_func=lambda p: p.parent.name[:20]
    )
    try:
        m = verify_dataset(chosen.parent)
    except Exception as exc:
        st.error(f"数据集校验失败：{exc}。已禁用该版本的下载和报告。")
        return
    st.write(
        f"{m['selection']} · {m['pairs']} 对 · train {m['partitions']['train']} / eval {m['partitions']['eval']}"
    )
    if not m["pairs"]:
        st.warning("本版没有可配对样本。请查看排除原因；完成复核不保证一定存在偏好分歧。")
    elif 0 in m["partitions"].values():
        st.info(
            "当前有空分区。同族不拆分，小样本不保证达到目标比例；不应将此 eval 当作独立 holdout。"
        )
    st.write("排除原因：", m["exclusion_reasons"])
    st.code(m["dataset_version"], language=None)
    st.caption("manifest、JSONL 数量、SHA-256、复核引用及分区已校验。")
    with st.expander("逐对溯源"):
        pairs = [
            json.loads(line) for line in (chosen.parent / "pairs.jsonl").read_text().splitlines()
        ]
        if pairs:
            by_id = {p["pair_id"]: p for p in pairs}
            pid = st.selectbox("偏好对", list(by_id))
            pair = by_id[pid]
            sources = [
                json.loads(line)
                for line in (chosen.parent / "sources.jsonl").read_text().splitlines()
            ]
            source = next(
                s for s in sources if s["record_id"] == pair["lineage"]["source_record_id"]
            )
            st.code(source["source"]["content"], language=None, wrap_lines=True)
            st.json(
                {
                    "chosen": json.loads(pair["chosen"]),
                    "rejected": json.loads(pair["rejected"]),
                    "lineage": pair["lineage"],
                    "review_history": source["reviews"],
                }
            )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ["manifest.json", *m["files"]]:
            z.write(chosen.parent / name, name)
    st.download_button(
        "下载已校验数据集 ZIP",
        archive.getvalue(),
        file_name=f"ecoalign-{m['dataset_version'][:12]}.zip",
        mime="application/zip",
        on_click="ignore",
    )
    report_run = next((p.parent for p in runs if p.parent.name in m["recipe"]["run_ids"]), None)
    if report_run and st.button("生成本版数据报告"):
        try:
            path = build_report(
                report_run, settings.datasets_dir / mode / "reports", dataset=chosen.parent
            )
            st.session_state["dataset_report:" + m["dataset_version"]] = str(path)
        except Exception as exc:
            st.error(f"报告生成失败：{exc}")
    if report_path := st.session_state.get("dataset_report:" + m["dataset_version"]):
        st.download_button(
            "下载数据报告 HTML",
            Path(report_path).read_bytes(),
            file_name="dataset-report.html",
            mime="text/html",
            on_click="ignore",
        )
