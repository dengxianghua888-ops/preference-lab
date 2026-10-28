"""Persisted-run navigation extended with local review and immutable datasets."""

from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.data_loader import list_runs, read_run


def render_kernel(mode):
    # Operate: native controls, existing palette, compact readable working surface.
    st.markdown(
        """<style>
    .block-container{padding-top:2rem;max-width:1500px}
    .stApp{background-image:none!important}
    .stApp header{visibility:visible!important;background:transparent!important}
    .stApp h1,.stApp h2,.stApp h3{font-family:inherit!important}
    .stApp [data-testid="stIconMaterial"]{font-family:"Material Symbols Rounded"!important}
    div[data-testid="stMetric"]{background:transparent!important;border:0!important;
      box-shadow:none!important;backdrop-filter:none!important;padding:8px 0!important}
    div[data-testid="stMetric"]:hover{transform:none!important}
    div[data-testid="stMetric"]::before{display:none}
    .stApp [data-testid="stCaptionContainer"] p,.stApp [data-testid="stCaptionContainer"] span{color:#bac2d5!important}
    .stApp [data-testid="stCaptionContainer"]{opacity:1!important}
    button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid #a29bfe!important}
    @media(max-width:700px){.block-container{padding:1rem}h1{font-size:1.7rem!important}}
    @media(prefers-reduced-motion:reduce){*{transition:none!important}}
    </style>""",
        unsafe_allow_html=True,
    )
    st.title("EcoAlign-Forge")
    st.caption("可复核的数据工作台 · 本地复核者身份为自报 · 模型质量与训练收益未评估")
    page = st.sidebar.radio("工作区", ["运行", "样本复核", "数据集"])
    st.sidebar.caption("连接状态：未检测。编辑期间不自动刷新。")
    runs = list_runs(mode)
    if not runs:
        st.info(f"{mode} 暂无新内核运行。先运行下面的预录 Demo，再选择 demo 来源。")
        st.code("python -m ecoalign_forge run --demo --num-samples 5", language="bash")
        return
    selected = st.sidebar.selectbox("运行记录", runs, format_func=lambda p: p.parent.name)
    if st.sidebar.button("刷新已保存数据"):
        st.rerun()
    try:
        run = read_run(selected)
    except Exception as exc:
        st.error(f"读取运行失败：{type(exc).__name__}。请检查所选 run 的数据库或选择另一条记录。")
        return
    st.caption(f"来源 {run['execution_mode']} · {run['language']} · Run {run['run_id']}")
    if mode in {"demo", "mock"}:
        st.info("当前为受控演示数据；复核操作会真实保存，但不构成真实模型或真人质量验收。")
    if page == "运行":
        _run_page(run, selected.parent, runs)
    elif page == "样本复核":
        from dashboard.review_view import render_review

        render_review(selected.parent)
    else:
        from dashboard.dataset_view import render_datasets

        render_datasets(mode, runs, selected)


def _run_page(run, run_dir, paths):
    st.subheader(f"运行状态：{run['status']}")
    if run["pause_reason"]:
        st.warning("暂停原因：" + run["pause_reason"])
    if run["error"]:
        st.error(run["error"])
    counts = run["counts"]
    for col, key, label in zip(
        st.columns(4),
        ["completed", "accepted_cases", "excluded_cases", "dpo_pairs"],
        ["机器阶段完成", "规则门槛接受", "规则门槛排除", "机器偏好对"],
        strict=True,
    ):
        col.metric(label, counts[key])
    labels, actions, diagnostics = st.tabs(["维度标签", "最终动作", "计数与诊断"])
    with labels:
        dimension = st.selectbox("维度", list(run["label_distribution"]))
        values = run["label_distribution"][dimension]
        st.bar_chart(
            pd.DataFrame({"标签": values.keys(), "数量": values.values()}).set_index("标签")
        )
    with actions:
        values = run["action_distribution"]
        st.bar_chart(
            pd.DataFrame({"动作": values.keys(), "数量": values.values()}).set_index("动作")
        )
    with diagnostics:
        st.json(counts)
        st.dataframe(run["cases"], hide_index=True, width="stretch")
        st.json(run["review_stats"])
    with st.expander("预算、模型与规则配置"):
        st.json(run["budget"])
        from ecoalign_forge.runtime.journal import Journal

        j = Journal.readonly(run_dir)
        try:
            manifest = j.get("manifest")
        finally:
            j.close()
        st.write("规则 SHA-256", run["policy_hash"])
        st.json(manifest["config"])
        st.caption("费用为价格表估算；实际账单未知。默认单个 naive persona，可通过配置切换。")
    if run["status"] in {"running", "pending"} and st.button("完成当前批次后暂停"):
        from ecoalign_forge.workbench.control import request_pause

        try:
            request_pause(run_dir)
            st.success("暂停请求已保存；当前批次结束后停止调度。刷新可查看状态。")
        except Exception as exc:
            st.error(str(exc))
    if run["status"] in {"paused", "cancelled", "failed", "partial_failed"}:
        _resume(run, run_dir)
    if st.button("生成此运行报告"):
        from ecoalign_forge.workbench.report import build_report

        try:
            report = build_report(run_dir, run_dir / "reports")
            st.session_state["run_report"] = str(report)
        except Exception as exc:
            st.error(f"报告生成失败：{exc}")
    report_path = st.session_state.get("run_report")
    if report_path and Path(report_path).is_relative_to(run_dir):
        st.download_button(
            "下载 HTML 报告",
            Path(report_path).read_bytes(),
            file_name="run-report.html",
            mime="text/html",
            on_click="ignore",
        )
        st.caption("报告目录还保存匹配的 report.json、pairs.jsonl 与 SHA256SUMS。")
    with st.expander("同来源的运行列表"):
        rows = []
        for path in paths:
            try:
                r = read_run(path)
                rows.append(
                    dict(
                        run_id=r["run_id"],
                        status=r["status"],
                        completed=r["counts"]["completed"],
                        pairs=r["counts"]["dpo_pairs"],
                    )
                )
            except Exception as exc:
                rows.append(dict(run_id=path.parent.name, status="读取失败：" + type(exc).__name__))
        st.dataframe(rows, hide_index=True, width="stretch")


def _resume(run, run_dir):
    if run["execution_mode"] == "mock":
        st.info("mock 恢复需要 Python 入口显式注入原受控替身；页面不会代替它调用真实模型。")
        return
    import asyncio
    from decimal import Decimal

    from ecoalign_forge.engine.kernel import SynthesisKernel

    with st.expander("恢复运行", expanded=bool(run["unknown_attempts"])):
        st.caption("恢复使用原版本、规则与模型。未决请求必须逐项明确选择，重试保留原不确定费用。")
        choices = {}
        for attempt in run["unknown_attempts"]:
            choice = st.selectbox(
                f"未决请求 {attempt}", ["保持暂停", "重试", "跳过"], key="unknown:" + attempt
            )
            if choice != "保持暂停":
                choices[attempt] = "retry" if choice == "重试" else "skip"
        budget = st.text_input("新的金额上限 USD（留空保留）")
        extension = st.number_input("延长期限（秒）", min_value=0, value=0)
        ready = not run["unknown_attempts"] or len(choices) == len(run["unknown_attempts"])
        if st.button("按上述选择恢复", disabled=not ready):
            try:
                with st.spinner("恢复中，已保存的成功请求将本地重放…"):
                    result = asyncio.run(
                        SynthesisKernel().resume(
                            run_dir,
                            unknown_decisions=choices,
                            max_run_cost=Decimal(budget) if budget else None,
                            extend_seconds=extension,
                        )
                    )
                st.write("恢复结果：", result["status"])
            except Exception as exc:
                st.error(f"恢复失败：{exc}")
