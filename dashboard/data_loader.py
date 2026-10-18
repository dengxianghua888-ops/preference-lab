"""Read only the explicitly selected persisted source; never synthesize fallback data."""

import streamlit as st

from ecoalign_forge.schemas.execution import ExecutionMode
from ecoalign_forge.storage.dashboard_bridge import DashboardBridge, DashboardSnapshot


@st.cache_data(ttl=5)
def load_snapshot(execution_mode: str = "live") -> DashboardSnapshot:
    mode = ExecutionMode(execution_mode)
    try:
        return DashboardBridge(execution_mode=mode).get_latest_snapshot()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return DashboardSnapshot(execution_mode=mode, load_error=f"读取失败：{exc}")


def list_runs(execution_mode: str):
    from ecoalign_forge.config import settings

    if execution_mode not in {"live", "demo", "mock"}:
        return []
    return sorted(
        (settings.data_dir / execution_mode / "runs").glob("*/run.sqlite3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def read_run(path):
    from ecoalign_forge.engine.kernel import SynthesisKernel
    from ecoalign_forge.runtime.journal import Journal

    journal = Journal.readonly(path.parent)
    try:
        # All SELECTs, including the case list, must observe one committed WAL snapshot.
        journal.db.execute("BEGIN")
        result = SynthesisKernel()._snapshot(journal, persist=False)
        result["cases"] = [
            dict(case_id=c["id"], state=c["state"], reason=c["reason"]) for c in journal.cases()
        ]
        return result
    finally:
        journal.close()
