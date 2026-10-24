"""Original source, rule evidence and append-only human decisions in one workspace."""

from uuid import uuid4

import streamlit as st

from ecoalign_forge.policy.compiler import compile_policy, derive
from ecoalign_forge.policy.models import PolicyPack
from ecoalign_forge.schemas.kernel import CandidateEvaluation, Evidence, SourceText
from ecoalign_forge.workbench.review import ReviewDecision, snapshot_run, submit_review


def render_review(run_dir):
    st.subheader("样本复核")
    try:
        snapshot = snapshot_run(run_dir)
    except Exception as exc:
        st.error(f"复核数据不可用：{exc}。正在执行的运行请先在批次结束后暂停。")
        return
    status = st.selectbox("复核队列", ["待复核", "全部", "已接受", "已改判", "已弃权", "已排除"])
    actions = {"已接受": "accept", "已改判": "correct", "已弃权": "abstain", "已排除": "exclude"}
    if notice := st.session_state.pop("review_notice", None):
        st.success(notice)
    views = [
        v
        for v in snapshot["cases"]
        if v["case"]["source"]
        and (
            status == "全部"
            or (status == "待复核" and not v["latest"])
            or (v["latest"] and v["latest"]["decision"]["action"] == actions.get(status))
        )
    ]
    if not views:
        st.info("当前队列没有样本。可切换队列查看已保存的复核。")
        return
    by_id = {v["case"]["id"]: v for v in views}
    cid = st.selectbox(
        "选择样本",
        list(by_id),
        format_func=lambda k: (
            f"{by_id[k]['case']['ordinal'] + 1} · {by_id[k]['case']['source']['content'][:60]}"
        ),
    )
    view = by_id[cid]
    source = SourceText.model_validate(view["case"]["source"])
    pack = PolicyPack.model_validate(view["manifest"]["policy"])
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("#### 原始内容")
        st.code(source.content, language=None, wrap_lines=True)
        st.caption(
            f"Case {cid} · Unicode 字符数 {len(source.content)} · 复核版本 {view['revision']}"
        )
        with st.expander("原文与规则来源"):
            st.write("原文 SHA-256", source.sha256)
            st.write("规则 SHA-256", view["manifest"]["policy_hash"])
            st.json(pack.model_dump(mode="json"))
        for title, key in [("弱候选", "moderator"), ("Judge 原候选", "judge")]:
            with st.expander(title, expanded=key == "judge"):
                candidate = view["stages"].get(key)
                if candidate:
                    st.write("动作：", candidate["final_action"])
                    st.write("标签：", candidate["labels"])
                    st.write(candidate["decision_reason"])
                    for e in candidate["evidence"]:
                        st.caption(f"{e['rule_id']} · [{e['start']}, {e['end']}) · {e['reason']}")
                        st.code(e["quote"], language=None, wrap_lines=True)
                else:
                    st.info("该阶段没有已保存的候选。")
        with st.expander("机器复核与门槛诊断"):
            st.json({k: view["stages"].get(k) for k in ("review", "gate")})
    with right:
        st.markdown("#### 保存人工决定")
        if view["latest"]:
            st.write(
                f"当前：{view['latest']['decision']['action']} · {view['latest']['decision']['reviewer']}"
            )
            st.write(view["latest"]["decision"]["reason"])
        if view["effective_final"]:
            st.write("当前最终动作：", view["effective_final"]["evaluation"]["final_action"])
            st.write("当前标签：", view["effective_final"]["evaluation"]["labels"])
        _decision_form(run_dir, view, source, pack)
    with st.expander(f"复核记录与改判差异 · {len(view['history'])} 次"):
        before = (view["stages"].get("gate") or {}).get("final")
        for revision in view["history"]:
            st.write(
                f"版本 {revision['revision']} · {revision['created_at']} · {revision['decision']['action']}"
            )
            st.write("复核者：", revision["decision"]["reviewer"])
            st.write("理由：", revision["decision"]["reason"])
            st.json(
                {
                    "before": before["evaluation"] if before else None,
                    "after": revision["final"]["evaluation"] if revision["final"] else None,
                    "review_id": revision["review_id"],
                }
            )
            before = revision["final"]
    with st.expander("完整溯源记录"):
        st.json(
            {
                "machine_pairs": view["machine_pairs"],
                "case_binding": view["case_binding"],
                "generation_plan": view["case"]["plan"],
            }
        )


def _decision_form(run_dir, view, source, pack):
    cid = view["case"]["id"]
    labels = {"接受当前判决": "accept", "改判": "correct", "弃权": "abstain", "排除": "exclude"}
    action = labels[st.radio("复核动作", list(labels), horizontal=True, key=f"action:{cid}")]
    base = (view["effective_final"] or {}).get("evaluation") or view["stages"].get("judge")
    base = base or {"rule_judgments": {}, "evidence": []}
    # Preserve displayed revision until submission; a rerun must not silently rebase.
    form_key = f"review:{run_dir.name}:{cid}:{action}"
    if form_key not in st.session_state:
        st.session_state[form_key] = {"revision": view["revision"], "operation": str(uuid4())}
    displayed = st.session_state[form_key]
    if displayed["revision"] != view["revision"]:
        st.warning("此表单基于旧复核版本。请重新载入，核对他人的修改后再提交。")
        if st.button("重新载入当前判决", key=form_key + ":reload"):
            del st.session_state[form_key]
            st.rerun()
        return
    with st.form(form_key + ":form"):
        reviewer = st.text_input("复核者名称", key=form_key + ":reviewer")
        reason = st.text_area("复核理由（必填）", key=form_key + ":reason")
        error_type = st.selectbox(
            "问题类型（人工判断）",
            ["none", "false_positive", "false_negative", "other"],
            format_func=lambda k: {
                "none": "未标记",
                "false_positive": "误杀",
                "false_negative": "漏判",
                "other": "其他",
            }[k],
        )
        families = st.text_input("关联来源族或模板族（可选，逗号分隔）")
        judgments, spans = {}, {}
        if action == "correct":
            st.caption(
                "逐条确认命中与证据，标签和最终动作由规则表计算。字符位置从 0 起，结束位置不包含。"
            )
            for rule in pack.rules:
                with st.expander(f"{rule.id} · {rule.text[:60]}"):
                    st.write(rule.text)
                    values = ["miss", "hit", "unknown"]
                    judgments[rule.id] = st.selectbox(
                        "规则判断 " + rule.id,
                        values,
                        index=values.index(base["rule_judgments"].get(rule.id, "unknown")),
                    )
                    ev = next((e for e in base["evidence"] if e["rule_id"] == rule.id), None)
                    a, b = st.columns(2)
                    start = a.number_input(
                        "起始 " + rule.id,
                        min_value=0,
                        max_value=len(source.content),
                        value=ev["start"] if ev else 0,
                    )
                    end = b.number_input(
                        "结束 " + rule.id,
                        min_value=0,
                        max_value=len(source.content),
                        value=ev["end"] if ev else len(source.content),
                    )
                    spans[rule.id] = (start, end)
        submitted = st.form_submit_button("校验并保存复核", type="primary")
    if submitted:
        try:
            corrected = None
            if action == "correct":
                compiled = compile_policy(pack)
                derived_labels, final_action, _, _ = derive(compiled, judgments)
                if final_action is None or any(v is None for v in derived_labels.values()):
                    raise ValueError("存在影响判决的未知事实；请补充证据或选择弃权。")
                evidence = []
                for rule in pack.rules:
                    if judgments[rule.id] != "hit":
                        continue
                    start, end = spans[rule.id]
                    kind = "document_scope" if rule.evidence == "document_scope" else "text_span"
                    if kind == "document_scope":
                        start, end = 0, len(source.content)
                    evidence.append(
                        Evidence(
                            rule_id=rule.id,
                            kind=kind,
                            source_id=source.source_id,
                            source_hash=source.sha256,
                            start=start,
                            end=end,
                            quote=source.content[start:end],
                            reason=reason,
                        )
                    )
                corrected = CandidateEvaluation(
                    labels=derived_labels,
                    rule_judgments=judgments,
                    evidence=tuple(evidence),
                    final_action=final_action,
                    decision_reason=reason,
                )
            decision = ReviewDecision(
                operation_id=displayed["operation"],
                case_id=cid,
                expected_revision=displayed["revision"],
                action=action,
                reviewer=reviewer,
                reason=reason,
                corrected=corrected,
                family_ids=tuple(x.strip() for x in families.split(",") if x.strip()),
                error_type=error_type,
            )
            saved = submit_review(run_dir, decision)
            del st.session_state[form_key]
            st.session_state["review_notice"] = (
                f"已保存复核版本 {saved['revision']}。已有数据集保持原样；生成下一版后生效。"
            )
            st.rerun()
        except Exception as exc:
            st.error(f"未保存：{exc}")
