"""数据导出模块的单元测试。"""

import json

import pytest

from ecoalign_forge.export.sharegpt_format import (
    export_dataset_info,
    export_sharegpt,
    to_sharegpt_dict,
)
from ecoalign_forge.export.trl_format import export_trl, to_trl_dict
from ecoalign_forge.schemas.dpo import DPO_Pair

# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


@pytest.fixture
def sample_pairs() -> list[DPO_Pair]:
    return [
        DPO_Pair(
            prompt="Moderate this content",
            chosen='{"final_decision": "T0_Block"}',
            rejected='{"final_decision": "T2_Normal"}',
            chosen_score=1.0,
            rejected_score=0.2,
            preference_gap=0.8,
            dimension="stealth_marketing",
            difficulty="hard",
            source_case_id="case-001",
        ),
        DPO_Pair(
            prompt="Moderate this other content",
            chosen='{"final_decision": "T2_Normal"}',
            rejected='{"final_decision": "T3_Recommend"}',
            chosen_score=0.4,
            rejected_score=0.0,
            preference_gap=0.4,
            dimension="ai_slop",
            difficulty="medium",
            source_case_id="case-002",
        ),
    ]


# ──────────────────────────────────────────────────────────────
# TRL 格式
# ──────────────────────────────────────────────────────────────


class TestTRLFormat:
    def test_basic_conversion(self, sample_pairs):
        record = to_trl_dict(sample_pairs[0])
        assert "prompt" in record
        assert "chosen" in record
        assert "rejected" in record
        # 基础模式不含扩展字段
        assert "chosen_rating" not in record

    def test_with_metadata(self, sample_pairs):
        record = to_trl_dict(sample_pairs[0], include_metadata=True)
        assert record["chosen_rating"] == 1.0
        assert record["rejected_rating"] == 0.2
        assert record["preference_gap"] == 0.8
        assert record["dimension"] == "stealth_marketing"

    def test_export_trl_file(self, sample_pairs, tmp_path):
        out = export_trl(sample_pairs, tmp_path / "train.jsonl")
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["prompt"] == "Moderate this content"


# ──────────────────────────────────────────────────────────────
# ShareGPT 格式
# ──────────────────────────────────────────────────────────────


class TestShareGPTFormat:
    def test_basic_conversion(self, sample_pairs):
        record = to_sharegpt_dict(sample_pairs[0])
        assert len(record["conversations"]) == 1
        assert record["conversations"][0]["from"] == "human"
        assert record["chosen"]["from"] == "gpt"
        assert record["rejected"]["from"] == "gpt"

    def test_export_sharegpt_file(self, sample_pairs, tmp_path):
        out = export_sharegpt(sample_pairs, tmp_path / "train_sharegpt.json")
        assert out.exists()
        data = json.loads(out.read_text())
        assert len(data) == 2

    def test_dataset_info(self, tmp_path):
        out = export_dataset_info(tmp_path, dataset_name="test_dpo")
        assert out.exists()
        info = json.loads(out.read_text())
        assert "test_dpo" in info
        assert info["test_dpo"]["ranking"] is True
        assert info["test_dpo"]["formatting"] == "sharegpt"
