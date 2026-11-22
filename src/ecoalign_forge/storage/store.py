"""DataStore — JSONL file storage for pipeline outputs."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from ecoalign_forge.config import settings
from ecoalign_forge.schemas.dpo import DPO_Pair
from ecoalign_forge.schemas.pipeline import PipelineRun

logger = logging.getLogger(__name__)


class DataStore:
    """Simple JSONL-based storage for DPO pairs and pipeline artifacts."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or settings.datasets_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_dpo_pairs(self, pairs: list[DPO_Pair], run_id: str) -> str:
        """Save DPO pairs to a JSONL file (atomic write). Returns the file path."""
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"dpo_pairs_{run_id[:8]}_{timestamp}.jsonl"
        filepath = self.base_dir / filename

        # 原子写入：先写临时文件，成功后 rename，避免中断时产生截断文件
        tmp_path = filepath.with_suffix(".jsonl.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                for pair in pairs:
                    f.write(json.dumps(pair.model_dump(mode="json"), ensure_ascii=False) + "\n")
            tmp_path.rename(filepath)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        return str(filepath)

    def load_dpo_pairs(self, filepath: str) -> list[DPO_Pair]:
        """Load DPO pairs from a JSONL file. Skips corrupted lines with warning."""
        pairs = []
        with open(filepath, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    pairs.append(DPO_Pair.model_validate_json(line))
                except Exception as e:
                    logger.warning(f"跳过 {filepath} 第 {line_num} 行（损坏）: {e}")
        return pairs

    def save_run(self, run: PipelineRun, path: Path | None = None) -> None:
        """追加一条管道运行记录到 JSONL。"""
        filepath = path or self.base_dir.parent / "runs.jsonl"
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(run.model_dump(), ensure_ascii=False, default=str) + "\n")

    def list_runs(self, path: Path | None = None) -> list[dict]:
        """读取所有管道运行记录。损坏行跳过并 warning。"""
        filepath = path or self.base_dir.parent / "runs.jsonl"
        if not filepath.exists():
            return []
        runs: list[dict] = []
        with open(filepath, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(f"跳过 runs.jsonl 第 {line_num} 行（损坏）: {e}")
        return runs

    def list_datasets(self) -> list[dict]:
        """List all saved datasets."""
        datasets = []
        for f in sorted(self.base_dir.glob("*.jsonl"), reverse=True):
            try:
                stat = f.stat()
            except FileNotFoundError:
                continue  # 竞态删除，跳过
            with open(f, encoding="utf-8") as fh:
                line_count = sum(1 for line in fh if line.strip())
            datasets.append(
                {
                    "filename": f.name,
                    "path": str(f),
                    "size_kb": round(stat.st_size / 1024, 1),
                    "samples": line_count,
                    "created": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                }
            )
        return datasets
