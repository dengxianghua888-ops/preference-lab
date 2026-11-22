"""AdaptiveSampler — 自适应难度采样器。

参考 Active Learning + Curriculum Learning 方法论：
- 分析已合成数据的策略覆盖分布和难度分布
- 发现欠采样的策略/难度组合
- 自动调整下一批次的 target_distribution
- 实现 Easy → Hard 渐进式课程学习

核心面试话术：
"我们不是盲目生成数据，而是分析目标模型在不同 topic/难度上的表现，
优先合成薄弱领域的数据，避免重复投入已饱和的区域。"
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from ecoalign_forge.schemas.chaos import ChaosCase, Difficulty
from ecoalign_forge.schemas.dpo import DPO_Pair

logger = logging.getLogger(__name__)

# 目标 tier 分布（课程学习：从宽松到严格渐进）
# key 与 ChaosCreator.DEFAULT_TARGET_DISTRIBUTION 的 tier 名称对齐
_CURRICULUM_SCHEDULES: dict[str, dict[str, float]] = {
    # 早期阶段：以安全内容为主建立基线
    "early": {
        "T0_Block": 0.15,
        "T1_Shadowban": 0.20,
        "T2_Normal": 0.40,
        "T3_Recommend": 0.25,
    },
    # 中期阶段：均衡分布
    "mid": {
        "T0_Block": 0.25,
        "T1_Shadowban": 0.30,
        "T2_Normal": 0.30,
        "T3_Recommend": 0.15,
    },
    # 后期阶段：侧重高风险样本
    "late": {
        "T0_Block": 0.35,
        "T1_Shadowban": 0.30,
        "T2_Normal": 0.25,
        "T3_Recommend": 0.10,
    },
}


@dataclass
class CoverageReport:
    """覆盖率分析报告。"""

    dimension_counts: dict[str, int] = field(default_factory=dict)
    difficulty_counts: dict[str, int] = field(default_factory=dict)
    strategy_counts: dict[str, int] = field(default_factory=dict)
    dimension_difficulty_matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    # 欠采样区域
    undersampled_dimensions: list[str] = field(default_factory=list)
    undersampled_difficulties: list[str] = field(default_factory=list)
    undersampled_combinations: list[tuple[str, str]] = field(default_factory=list)
    # 覆盖率分数
    coverage_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "dimension_counts": self.dimension_counts,
            "difficulty_counts": self.difficulty_counts,
            "strategy_counts": self.strategy_counts,
            "undersampled_dimensions": self.undersampled_dimensions,
            "undersampled_difficulties": self.undersampled_difficulties,
            "undersampled_combinations": [
                {"dimension": d, "difficulty": diff} for d, diff in self.undersampled_combinations
            ],
            "coverage_score": round(self.coverage_score, 4),
        }


class AdaptiveSampler:
    """自适应采样器。

    分析已有数据的覆盖分布，生成下一批次的采样策略。
    """

    def __init__(
        self,
        target_dimensions: list[str] | None = None,
        min_samples_per_cell: int = 5,
    ) -> None:
        self.target_dimensions = target_dimensions or [
            "stealth_marketing",
            "ai_slop",
        ]
        self.min_samples_per_cell = min_samples_per_cell
        self._all_difficulties = [d.value for d in Difficulty]

    def analyze_coverage(
        self,
        cases: list[ChaosCase],
        dpo_pairs: list[DPO_Pair] | None = None,
    ) -> CoverageReport:
        """分析已有数据的覆盖率。"""
        report = CoverageReport()

        # 维度分布
        report.dimension_counts = dict(Counter(c.target_dimension for c in cases))

        # 难度分布
        report.difficulty_counts = dict(Counter(c.difficulty.value for c in cases))

        # 攻击策略分布
        report.strategy_counts = dict(Counter(c.attack_strategy.value for c in cases))

        # 维度 × 难度矩阵
        for case in cases:
            dim = case.target_dimension
            diff = case.difficulty.value
            if dim not in report.dimension_difficulty_matrix:
                report.dimension_difficulty_matrix[dim] = {}
            report.dimension_difficulty_matrix[dim][diff] = (
                report.dimension_difficulty_matrix[dim].get(diff, 0) + 1
            )

        # 识别欠采样区域
        total = len(cases) if cases else 1
        avg_per_dim = total / max(len(self.target_dimensions), 1)

        for dim in self.target_dimensions:
            count = report.dimension_counts.get(dim, 0)
            if count < avg_per_dim * 0.5:
                report.undersampled_dimensions.append(dim)

        avg_per_diff = total / max(len(self._all_difficulties), 1)
        for diff in self._all_difficulties:
            count = report.difficulty_counts.get(diff, 0)
            if count < avg_per_diff * 0.3:
                report.undersampled_difficulties.append(diff)

        # 维度 × 难度组合的欠采样
        for dim in self.target_dimensions:
            for diff in self._all_difficulties:
                cell_count = report.dimension_difficulty_matrix.get(dim, {}).get(diff, 0)
                if cell_count < self.min_samples_per_cell:
                    report.undersampled_combinations.append((dim, diff))

        # 覆盖率分数 = 非空单元格 / 总单元格
        total_cells = len(self.target_dimensions) * len(self._all_difficulties)
        filled_cells = sum(
            1
            for dim in self.target_dimensions
            for diff in self._all_difficulties
            if report.dimension_difficulty_matrix.get(dim, {}).get(diff, 0) > 0
        )
        report.coverage_score = filled_cells / total_cells if total_cells > 0 else 0.0

        return report

    def suggest_distribution(
        self,
        coverage: CoverageReport,
        curriculum_stage: str = "mid",
    ) -> dict[str, float]:
        """根据覆盖率分析，建议下一批次的 tier 分布。

        Args:
            coverage: 覆盖率报告
            curriculum_stage: 课程阶段 ("early" / "mid" / "late")

        Returns:
            T0/T1/T2/T3 的目标分布
        """
        # 基础分布来自课程阶段
        base = _CURRICULUM_SCHEDULES.get(curriculum_stage, _CURRICULUM_SCHEDULES["mid"])

        # 如果有欠采样的维度，向高拦截 tier 倾斜（增加 T0/T1 比例）
        if coverage.undersampled_dimensions:
            adjusted = dict(base)
            adjusted["T0_Block"] = adjusted.get("T0_Block", 0.25) + 0.05
            adjusted["T1_Shadowban"] = adjusted.get("T1_Shadowban", 0.25) + 0.05
            # 归一化
            total = sum(adjusted.values())
            return {k: v / total for k, v in adjusted.items()}

        return dict(base)

    def suggest_focus_dimensions(self, coverage: CoverageReport) -> list[str]:
        """建议下一批次应重点关注的维度。"""
        if coverage.undersampled_dimensions:
            return coverage.undersampled_dimensions
        return self.target_dimensions

    def get_curriculum_stage(self, total_samples: int) -> str:
        """根据累计样本数判断课程阶段。"""
        if total_samples < 50:
            return "early"
        if total_samples < 200:
            return "mid"
        return "late"
