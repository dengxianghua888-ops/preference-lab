"""主题模块 — CSS 常量、Plotly 布局基础配置"""

from __future__ import annotations

import copy

import plotly.graph_objects as go
import streamlit as st

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CSS – Premium Glassmorphism Theme
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@300;400;500;600;700&family=Space+Grotesk:wght@300;400;500;600;700&family=Noto+Sans+SC:wght@300;400;500;600;700;800;900&display=swap');

:root{
--bg:#0c0d14;--surf:#12131c;--elevated:#1a1b28;
--glass:rgba(255,255,255,.028);--glass-b:rgba(255,255,255,.055);--glass-h:rgba(255,255,255,.045);
--pri:#6c5ce7;--pri-l:#a29bfe;--teal:#00cec9;--rose:#fd79a8;--mint:#55efc4;
--warn:#ffeaa7;--danger:#ff7675;--sky:#74b9ff;--amber:#f0932b;
--t1:rgba(255,255,255,.92);--t2:rgba(255,255,255,.55);--t3:rgba(255,255,255,.28);
--r-sm:8px;--r-md:14px;--r-lg:20px;
}
.stApp{background:var(--bg)!important;background-image:radial-gradient(ellipse at 15% 20%,rgba(108,92,231,.06) 0%,transparent 50%),radial-gradient(ellipse at 85% 80%,rgba(0,206,201,.04) 0%,transparent 50%),radial-gradient(ellipse at 50% 50%,rgba(253,121,168,.025) 0%,transparent 40%)!important}
section[data-testid="stSidebar"]{background:rgba(12,13,20,.92)!important;backdrop-filter:blur(20px)!important;-webkit-backdrop-filter:blur(20px)!important;border-right:1px solid var(--glass-b)!important}
#MainMenu,footer,header{visibility:hidden}
.stApp{color:var(--t1)!important;font-family:'Noto Sans SC','Inter',-apple-system,sans-serif!important;-webkit-font-smoothing:antialiased}
.stApp h1,.stApp h2,.stApp h3{font-family:'Space Grotesk',sans-serif!important;font-weight:700}

div[data-testid="stMetric"]{background:var(--glass)!important;backdrop-filter:blur(24px)!important;-webkit-backdrop-filter:blur(24px)!important;border:1px solid var(--glass-b)!important;border-radius:var(--r-lg)!important;padding:22px 24px!important;transition:all .35s cubic-bezier(.4,0,.2,1);box-shadow:0 8px 32px rgba(0,0,0,.3)!important;position:relative;overflow:hidden}
div[data-testid="stMetric"]:hover{background:var(--glass-h)!important;border-color:rgba(108,92,231,.15)!important;transform:translateY(-3px);box-shadow:0 8px 32px rgba(0,0,0,.3),0 4px 20px rgba(108,92,231,.08)!important}
div[data-testid="stMetric"]::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(135deg,#6c5ce7,#a29bfe);opacity:0;transition:opacity .35s ease}
div[data-testid="stMetric"]:hover::before{opacity:1}
div[data-testid="stMetric"] label{color:var(--t2)!important;font-family:'JetBrains Mono',monospace!important;font-size:.68rem!important;text-transform:uppercase!important;letter-spacing:1.5px!important;font-weight:500!important}
div[data-testid="stMetric"] [data-testid="stMetricValue"]{color:var(--t1)!important;font-family:'Space Grotesk',sans-serif!important;font-size:2rem!important;font-weight:700!important}
div[data-testid="stMetric"] [data-testid="stMetricDelta"]>div{font-family:'JetBrains Mono',monospace!important;font-size:.72rem!important}

.stTabs [data-baseweb="tab-list"]{gap:4px;background:var(--glass);border-radius:var(--r-md);padding:4px;border:1px solid var(--glass-b);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}
.stTabs [data-baseweb="tab"]{background:transparent!important;color:var(--t2)!important;font-family:'JetBrains Mono',monospace!important;font-size:.72rem!important;text-transform:uppercase;letter-spacing:.8px;border-radius:var(--r-sm)!important;padding:10px 18px!important;transition:all .25s ease}
.stTabs [aria-selected="true"]{background:rgba(108,92,231,.12)!important;color:var(--pri)!important;border:none!important;box-shadow:0 2px 8px rgba(108,92,231,.15)}
.stTabs [data-baseweb="tab"]:hover:not([aria-selected="true"]){background:rgba(255,255,255,.03)!important;color:var(--t1)!important}

.hero{text-align:center;padding:28px 0 8px}
.hero h1{font-family:'Space Grotesk',sans-serif!important;font-size:2.6rem;font-weight:800;background:linear-gradient(135deg,#a29bfe 0%,#6c5ce7 30%,#00cec9 60%,#55efc4 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin:0;letter-spacing:-.5px;line-height:1.1}
.hero .tag{font-family:'JetBrains Mono',monospace;color:var(--t3);font-size:.72rem;letter-spacing:4px;margin-top:6px;text-transform:uppercase}
.divider{height:1px;background:linear-gradient(90deg,transparent,var(--glass-b) 20%,rgba(108,92,231,.12) 50%,var(--glass-b) 80%,transparent);margin:16px 0}
.accent{height:1px;background:linear-gradient(90deg,transparent,rgba(108,92,231,.2) 15%,rgba(0,206,201,.15) 40%,rgba(253,121,168,.12) 65%,rgba(85,239,196,.1) 85%,transparent);margin:14px 0}
.sh{font-family:'JetBrains Mono',monospace!important;font-size:.7rem!important;color:var(--t2)!important;text-transform:uppercase;letter-spacing:2.5px;padding-bottom:10px;display:flex;align-items:center;gap:10px}
.sh::before{content:'';width:20px;height:2px;background:linear-gradient(135deg,#6c5ce7,#a29bfe);border-radius:1px}
.gp{background:var(--glass);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid var(--glass-b);border-radius:var(--r-lg);padding:20px;box-shadow:0 8px 32px rgba(0,0,0,.3)}
.badge{display:inline-flex;align-items:center;gap:5px;padding:4px 12px;border-radius:20px;font-family:'JetBrains Mono',monospace;font-size:.65rem;font-weight:500;letter-spacing:.5px}
.b-run{background:rgba(85,239,196,.1);border:1px solid rgba(85,239,196,.25);color:#55efc4}
.b-done{background:rgba(108,92,231,.1);border:1px solid rgba(108,92,231,.25);color:#6c5ce7}
.b-fail{background:rgba(255,118,117,.1);border:1px solid rgba(255,118,117,.25);color:#ff7675}
.b-wait{background:rgba(255,234,167,.08);border:1px solid rgba(255,234,167,.2);color:#ffeaa7}
.pt{background:rgba(255,255,255,.04);border-radius:6px;height:8px;overflow:hidden;border:1px solid rgba(255,255,255,.03)}
.pf{height:100%;border-radius:5px;transition:width .6s cubic-bezier(.4,0,.2,1)}
.ar{display:flex;justify-content:space-between;align-items:center;padding:8px 0;font-size:.78rem;border-bottom:1px solid rgba(255,255,255,.03)}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:rgba(108,92,231,.2);border-radius:3px}
</style>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Plotly 全局布局基础配置
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_BASE_LAYOUT: dict = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(
        family="Inter, -apple-system, sans-serif",
        color="rgba(255,255,255,0.45)",
        size=11,
    ),
    margin=dict(l=40, r=20, t=35, b=40),
    hoverlabel=dict(
        bgcolor="rgba(18,19,28,0.96)",
        bordercolor="rgba(108,92,231,0.3)",
        font=dict(
            family="JetBrains Mono",
            color="rgba(255,255,255,0.85)",
            size=11,
        ),
    ),
)

# 图表通用常量
GRID = "rgba(255,255,255,0.03)"
TICK = dict(size=10, color="rgba(255,255,255,0.35)")


def apply_layout(fig: go.Figure, **overrides: object) -> None:
    """在 Plotly 图表上应用基础主题，再叠加自定义覆盖项。"""
    merged = copy.deepcopy(_BASE_LAYOUT)
    merged.update(overrides)
    fig.update_layout(**merged)


def inject_css() -> None:
    """将 Glassmorphism CSS 注入到 Streamlit 页面。"""
    st.markdown(CSS, unsafe_allow_html=True)
