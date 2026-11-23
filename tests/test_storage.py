"""存储层测试"""

from __future__ import annotations

from pathlib import Path

from ecoalign_forge.schemas.dpo import DPO_Pair
from ecoalign_forge.storage.store import DataStore


class TestDataStore:
    """DataStore JSONL 存储测试"""

    def test_save_and_load_dpo_pairs(self, tmp_path: Path) -> None:
        """保存和加载 DPO 对的完整往返"""
        store = DataStore(base_dir=tmp_path)
        pairs = [
            DPO_Pair(
                prompt="测试 prompt",
                chosen="正确回答",
                rejected="错误回答",
                chosen_score=0.9,
                rejected_score=0.3,
                preference_gap=0.6,
                dimension="violence",
                difficulty="medium",
                source_case_id="case-001",
            ),
            DPO_Pair(
                prompt="测试 prompt 2",
                chosen="好的回答",
                rejected="差的回答",
                chosen_score=0.85,
                rejected_score=0.4,
                preference_gap=0.45,
                dimension="sexual",
                difficulty="hard",
                source_case_id="case-002",
            ),
        ]
        filepath = store.save_dpo_pairs(pairs, run_id="test-run-123")
        assert Path(filepath).exists()

        loaded = store.load_dpo_pairs(filepath)
        assert len(loaded) == 2
        assert loaded[0].prompt == "测试 prompt"
        assert loaded[1].dimension == "sexual"

    def test_list_datasets(self, tmp_path: Path) -> None:
        """列出数据集"""
        store = DataStore(base_dir=tmp_path)
        store.save_dpo_pairs(
            [
                DPO_Pair(
                    prompt="p",
                    chosen="c",
                    rejected="r",
                    chosen_score=0.9,
                    rejected_score=0.1,
                    preference_gap=0.8,
                    dimension="d",
                    difficulty="easy",
                    source_case_id="c1",
                )
            ],
            run_id="run-1",
        )

        datasets = store.list_datasets()
        assert len(datasets) == 1
        assert datasets[0]["samples"] == 1

    def test_empty_directory(self, tmp_path: Path) -> None:
        """空目录返回空列表"""
        store = DataStore(base_dir=tmp_path)
        assert store.list_datasets() == []

    def test_load_preserves_pair_id(self, tmp_path: Path) -> None:
        """加载后 pair_id 保持不变"""
        store = DataStore(base_dir=tmp_path)
        original = DPO_Pair(
            prompt="p",
            chosen="c",
            rejected="r",
            chosen_score=0.8,
            rejected_score=0.2,
            preference_gap=0.6,
            dimension="d",
            difficulty="medium",
            source_case_id="s",
        )
        filepath = store.save_dpo_pairs([original], run_id="id-test")
        loaded = store.load_dpo_pairs(filepath)
        assert loaded[0].pair_id == original.pair_id

    def test_multiple_saves_list_all(self, tmp_path: Path) -> None:
        """多次保存后 list_datasets 应返回所有数据集"""
        store = DataStore(base_dir=tmp_path)
        pair = DPO_Pair(
            prompt="p",
            chosen="c",
            rejected="r",
            chosen_score=0.7,
            rejected_score=0.3,
            preference_gap=0.4,
            dimension="d",
            difficulty="easy",
            source_case_id="s",
        )
        # 使用不同的 run_id 以保证文件名不同
        store.save_dpo_pairs([pair], run_id="run-aaa")
        store.save_dpo_pairs([pair, pair], run_id="run-bbb")

        datasets = store.list_datasets()
        assert len(datasets) == 2
        # 验证样本数
        sample_counts = sorted(d["samples"] for d in datasets)
        assert sample_counts == [1, 2]
