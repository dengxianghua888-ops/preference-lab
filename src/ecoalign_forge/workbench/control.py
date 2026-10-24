"""Cooperative pause at batch boundaries, without interrupting billed requests."""

from pathlib import Path
from uuid import uuid4

from ecoalign_forge.runtime.journal import Journal, atomic_write
from ecoalign_forge.schemas.kernel import canonical


def request_pause(run_dir: Path) -> dict:
    j = Journal.readonly(run_dir)
    try:
        if j.get("status") not in {"running", "pending"}:
            raise ValueError("Only an active run can be paused")
        value = {
            "request_id": str(uuid4()),
            "run_id": j.get("manifest")["run_id"],
            "reason": "user_pause",
        }
        atomic_write(Path(run_dir) / "pause-request.json", canonical(value) + "\n")
        return value
    finally:
        j.close()
