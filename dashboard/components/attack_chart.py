"""攻击策略分布组件 — 柱状图展示各攻击策略的用例数量"""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go
import streamlit as st

from dashboard.components.theme import GRID, TICK, apply_layout

if TYPE_CHECKING:
    from dashboard.data_loader import DashboardSnapshot

# 攻击策略英文 → 中文映射
_STRATEGY_ZH: dict[str, str] = {
    "direct_violation": "直接攻击",
    "edge_case": "边缘场景",
    "subtle_harm": "隐性伤害",
    "jailbreak_roleplay": "越狱提示",
    "multilingual_evasion": "多语言绕过",
    "context_injection": "上下文注入",
    "gradual_escalation": "升级引导",
    "emotional_manipulation": "情感操纵",
}

# 柱状图颜色（与原始 8 色保持一致）
_BAR_COLORS: list[str] = [
    "#ff7675",
    "#e17055",
    "#fdcb6e",
    "#fd79a8",
    "#a29bfe",
    "#74b9ff",
    "#55efc4",
    "#81ecec",
]


def render_attack_distribution(snap: DashboardSnapshot) -> None:
    """渲染攻击策略分布柱状图。"""
    st.markdown(
        '<div class="sh">攻击策略分布</div>',
        unsafe_allow_html=True,
    )

    # 按固定顺序读取策略数据
    strategy_keys = list(_STRATEGY_ZH.keys())
    strategies = [_STRATEGY_ZH[k] for k in strategy_keys]
    counts = [snap.attack_strategy_counts.get(k, 0) for k in strategy_keys]

    fig = go.Figure(
        data=[
            go.Bar(
                x=strategies,
                y=counts,
                marker=dict(color=_BAR_COLORS, line=dict(width=0), cornerradius=6),
                text=counts,
                textposition="outside",
                textfont=dict(
                    family="JetBrains Mono",
                    size=11,
                    color="rgba(255,255,255,0.5)",
                ),
                hovertemplate="<b>%{x}</b><br>数量: %{y}<extra></extra>",
            )
        ]
    )
    apply_layout(
        fig,
        height=400,
        xaxis=dict(
            gridcolor=GRID,
            tickfont=dict(
                size=11,
                color="rgba(255,255,255,0.45)",
                family="Inter",
            ),
        ),
        yaxis=dict(gridcolor=GRID, tickfont=TICK),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
    st.plotly_chart(fig, width="stretch")
