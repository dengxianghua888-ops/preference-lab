"""EcoAlign-Forge · 内容对齐数据合成监控大屏"""

import streamlit as st

from ecoalign_forge import __version__

# ━━ Page Config (必须是第一个 Streamlit 调用) ━━
st.set_page_config(
    page_title="EcoAlign-Forge · 内容对齐数据合成工厂",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ━━ Theme ━━
from dashboard.components.theme import inject_css

inject_css()

# ━━ Data ━━
from dashboard.data_loader import load_snapshot

mode = st.sidebar.selectbox(
    "数据来源",
    ["live", "demo", "mock", "unknown"],
    format_func=lambda x: "历史数据 (unknown)" if x == "unknown" else x,
)
storage = st.sidebar.radio("存储格式", ["新内核运行", "历史 A / unknown（只读）"])
if storage == "新内核运行":
    from dashboard.kernel_view import render_kernel

    render_kernel(mode)
    st.stop()

# Historical monitoring is read-only; only this surface may refresh automatically.
from streamlit_autorefresh import st_autorefresh

st_autorefresh(interval=5000, limit=None, key="dashboard_autorefresh")

snap = load_snapshot(mode)

# ━━ Sidebar ━━
from dashboard.components.sidebar import render_sidebar

render_sidebar(snap)

# ━━ Hero ━━
st.markdown(
    """
<div class="hero"><h1>EcoAlign-Forge</h1><div class="tag">内容对齐数据合成 · 实时监控大屏</div></div>
<div class="accent"></div>
""",
    unsafe_allow_html=True,
)

st.caption(f"执行来源：{snap.execution_mode.value} · 当前指标运行：{snap.run_id or '无'}")
if snap.load_error:
    st.error(snap.load_error)
    st.stop()
if snap.total_cases == 0:
    st.info("该来源暂无判决数据。可在命令行执行 --demo 生成预录演示。")

# ━━ KPI Cards ━━
from dashboard.components.kpi_cards import render_kpi_cards

render_kpi_cards(snap)

st.markdown('<div class="accent"></div>', unsafe_allow_html=True)

# ━━ Chart Tabs ━━
from dashboard.components.attack_chart import render_attack_distribution
from dashboard.components.quality_chart import render_quality_analysis
from dashboard.components.radar_chart import render_radar
from dashboard.components.timeline_chart import render_timeline

tab_radar, tab_tl, tab_atk, tab_ql = st.tabs(
    [
        "◎ 拦截率雷达",
        "◸ 合成趋势",
        "⚔ 攻击策略分布",
        "◈ 判决与启发式",
    ]
)

with tab_radar:
    render_radar(snap)
with tab_tl:
    render_timeline(snap)
with tab_atk:
    render_attack_distribution(snap)
with tab_ql:
    render_quality_analysis(snap)

st.markdown('<div class="accent"></div>', unsafe_allow_html=True)

# ━━ Pipeline Monitor ━━
from dashboard.components.pipeline_table import render_pipeline_monitor

render_pipeline_monitor(snap)

# ━━ Footer ━━
st.markdown(
    f"""
<div style="text-align:center;padding:30px 0 12px">
    <div class="divider"></div>
    <div style="font-family:'Noto Sans SC','JetBrains Mono',monospace;color:rgba(255,255,255,.12);font-size:.6rem;letter-spacing:2px;line-height:1.8">
        EcoAlign-Forge v{__version__} · 多智能体对齐数据合成工厂<br/>
        混沌生成器 → 审核官 → 终审法官 → DPO 训练对<br/>
        © 2026 Apache 2.0
    </div>
</div>
""",
    unsafe_allow_html=True,
)
