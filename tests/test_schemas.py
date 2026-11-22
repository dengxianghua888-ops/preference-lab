"""Schema 单元测试 — 覆盖全部 6 个 Pydantic 模型的构建、验证和序列化。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ecoalign_forge.schemas.chaos import (
    AttackStrategy,
    ChaosCase,
    Difficulty,
    ExpectedAction,
)
from ecoalign_forge.schemas.dpo import DPO_Pair
from ecoalign_forge.schemas.judge import JudgeEvaluation
from ecoalign_forge.schemas.pipeline import (
    PipelineConfig,
    PipelineResult,
    PipelineRun,
    PipelineStatus,
)
from ecoalign_forge.schemas.policy import PolicyDimension, PolicyInput

# ────────────────────────────────────────────────────────────
# 1. PolicyInput / PolicyDimension
# ────────────────────────────────────────────────────────────


class TestPolicyInput:
    """PolicyInput 及 PolicyDimension 模型测试"""

    def test_normal_construction(self) -> None:
        """正常构建含所有字段的策略"""
        dim = PolicyDimension(
            name="violence",
            description="暴力内容",
            severity_levels=["safe", "mild", "severe"],
            examples=["示例1"],
        )
        policy = PolicyInput(
            policy_id="p-1",
            name="测试策略",
            version="2.0",
            dimensions=[dim],
            language="en",
            context="仅供测试",
        )
        assert policy.policy_id == "p-1"
        assert policy.version == "2.0"
        assert policy.language == "en"
        assert len(policy.dimensions) == 1
        assert policy.dimensions[0].name == "violence"
        assert policy.context == "仅供测试"

    def test_defaults(self) -> None:
        """默认值：language='zh', version='1.0'"""
        policy = PolicyInput(
            policy_id="p-def",
            name="默认策略",
            dimensions=[PolicyDimension(name="d1", description="维度一")],
        )
        assert policy.language == "zh"
        assert policy.version == "1.0"
        assert policy.context == ""

    def test_dimension_defaults(self) -> None:
        """PolicyDimension 默认值：severity_levels 和 examples"""
        dim = PolicyDimension(name="test", description="描述")
        assert dim.severity_levels == ["safe", "mild", "moderate", "severe"]
        assert dim.examples == []

    def test_empty_dimensions_raises(self) -> None:
        """dimensions 为空列表时应触发 ValidationError（min_length=1）"""
        with pytest.raises(ValidationError):
            PolicyInput(
                policy_id="p-empty",
                name="空维度策略",
                dimensions=[],
            )

    def test_model_dump_roundtrip(self) -> None:
        """model_dump() 序列化往返验证"""
        policy = PolicyInput(
            policy_id="p-rt",
            name="往返测试",
            dimensions=[PolicyDimension(name="d", description="desc")],
        )
        data = policy.model_dump()
        restored = PolicyInput(**data)
        assert restored == policy


# ────────────────────────────────────────────────────────────
# 2. ChaosCase
# ────────────────────────────────────────────────────────────


class TestChaosCase:
    """ChaosCase 模型测试"""

    def test_normal_construction(self) -> None:
        """正常构建含全部必填字段"""
        case = ChaosCase(
            content="测试内容",
            attack_strategy="direct_violation",
            target_dimension="violence",
            difficulty="medium",
            expected_action="BLOCK",
            reasoning="测试原因",
        )
        assert case.content == "测试内容"
        assert case.attack_strategy == AttackStrategy.DIRECT
        assert case.expected_action == ExpectedAction.BLOCK

    def test_case_id_auto_generated(self) -> None:
        """不传 case_id 时应自动生成 UUID"""
        case = ChaosCase(
            content="内容",
            attack_strategy="edge_case",
            target_dimension="d",
            expected_action="PASS",
            reasoning="r",
        )
        assert case.case_id  # 非空
        assert len(case.case_id) == 36  # UUID 格式

    def test_case_id_uniqueness(self) -> None:
        """两个实例的 case_id 应不同"""
        kwargs = dict(
            content="x",
            attack_strategy="edge_case",
            target_dimension="d",
            expected_action="PASS",
            reasoning="r",
        )
        c1 = ChaosCase(**kwargs)
        c2 = ChaosCase(**kwargs)
        assert c1.case_id != c2.case_id

    @pytest.mark.parametrize(
        "strategy",
        [
            "direct_violation",
            "edge_case",
            "subtle_harm",
            "jailbreak_roleplay",
            "multilingual_evasion",
            "context_injection",
            "gradual_escalation",
            "emotional_manipulation",
        ],
    )
    def test_all_attack_strategies(self, strategy: str) -> None:
        """所有 AttackStrategy 枚举值（共 8 个）"""
        case = ChaosCase(
            content="c",
            attack_strategy=strategy,
            target_dimension="d",
            expected_action="PASS",
            reasoning="r",
        )
        assert case.attack_strategy.value == strategy

    @pytest.mark.parametrize("diff", ["easy", "medium", "hard", "extreme"])
    def test_all_difficulty_values(self, diff: str) -> None:
        """所有 Difficulty 枚举值（共 4 个）"""
        case = ChaosCase(
            content="c",
            attack_strategy="edge_case",
            target_dimension="d",
            difficulty=diff,
            expected_action="PASS",
            reasoning="r",
        )
        assert case.difficulty.value == diff

    @pytest.mark.parametrize("action", ["PASS", "FLAG", "BLOCK"])
    def test_all_expected_actions(self, action: str) -> None:
        """所有 ExpectedAction 枚举值（共 3 个）"""
        case = ChaosCase(
            content="c",
            attack_strategy="edge_case",
            target_dimension="d",
            expected_action=action,
            reasoning="r",
        )
        assert case.expected_action.value == action

    def test_metadata_default_empty_dict(self) -> None:
        """metadata 默认为空字典"""
        case = ChaosCase(
            content="c",
            attack_strategy="edge_case",
            target_dimension="d",
            expected_action="PASS",
            reasoning="r",
        )
        assert case.metadata == {}

    def test_difficulty_default_medium(self) -> None:
        """difficulty 默认为 medium"""
        case = ChaosCase(
            content="c",
            attack_strategy="edge_case",
            target_dimension="d",
            expected_action="PASS",
            reasoning="r",
        )
        assert case.difficulty == Difficulty.MEDIUM

    @pytest.mark.parametrize("field", ["content", "target_dimension", "reasoning"])
    def test_required_strings_min_length(self, field: str) -> None:
        """强校验：content / target_dimension / reasoning 不允许空字符串"""
        kwargs = dict(
            content="c",
            attack_strategy="edge_case",
            target_dimension="d",
            expected_action="PASS",
            reasoning="r",
        )
        kwargs[field] = ""
        with pytest.raises(ValidationError):
            ChaosCase(**kwargs)


# ────────────────────────────────────────────────────────────
# 3. JudgeEvaluation
# ────────────────────────────────────────────────────────────


class TestJudgeEvaluation:
    """JudgeEvaluation 模型测试（内容分发分级版）。"""

    def _kwargs(self, **overrides):
        base = dict(
            has_stealth_marketing=False,
            is_ai_slop=False,
            reasoning_trace=(
                "第一步：未发现私域引流暗号或 AI 套话\n"
                "第二步：未命中 A-* 或 B-* 任何规则\n"
                "第三步：内容合规，定级 T2_Normal"
            ),
            final_decision="T2_Normal",
        )
        base.update(overrides)
        return base

    def test_normal_construction(self) -> None:
        """正常构建：4 个必填字段全部填齐"""
        ev = JudgeEvaluation(
            **self._kwargs(
                has_stealth_marketing=True,
                is_ai_slop=False,
                final_decision="T1_Shadowban",
            )
        )
        assert ev.has_stealth_marketing is True
        assert ev.is_ai_slop is False
        assert ev.final_decision == "T1_Shadowban"
        assert "第一步" in ev.reasoning_trace

    @pytest.mark.parametrize("tier", ["T0_Block", "T1_Shadowban", "T2_Normal", "T3_Recommend"])
    def test_all_final_decision_values(self, tier: str) -> None:
        """final_decision 四个 Literal 值全部应被接受"""
        ev = JudgeEvaluation(**self._kwargs(final_decision=tier))
        assert ev.final_decision == tier

    def test_invalid_final_decision_rejected(self) -> None:
        """非法 final_decision 应触发 ValidationError"""
        with pytest.raises(ValidationError):
            JudgeEvaluation(**self._kwargs(final_decision="T4_FullBlast"))

    def test_empty_reasoning_trace_rejected(self) -> None:
        """空 reasoning_trace 触发 min_length=1 校验"""
        with pytest.raises(ValidationError):
            JudgeEvaluation(**self._kwargs(reasoning_trace=""))

    @pytest.mark.parametrize(
        "field", ["has_stealth_marketing", "is_ai_slop", "reasoning_trace", "final_decision"]
    )
    def test_missing_required_field_rejected(self, field: str) -> None:
        """任何必填字段缺失都应抛 ValidationError"""
        kwargs = self._kwargs()
        kwargs.pop(field)
        with pytest.raises(ValidationError):
            JudgeEvaluation(**kwargs)

    def test_field_descriptions_present_in_json_schema(self) -> None:
        """JSON Schema 应保留每个字段的 description（用于喂给 LLM）"""
        schema = JudgeEvaluation.model_json_schema()
        for field in ("has_stealth_marketing", "is_ai_slop", "reasoning_trace", "final_decision"):
            desc = schema["properties"][field].get("description", "")
            assert len(desc) > 20, f"{field} description 过短或缺失"

    def test_reasoning_trace_with_known_rule_id_passes(self) -> None:
        """引用合法规则编号 → 通过校验"""
        ev = JudgeEvaluation(
            **self._kwargs(
                reasoning_trace="第一步：发现 vx\n第二步：命中 A-002\n第三步：T1",
                final_decision="T1_Shadowban",
            )
        )
        assert "A-002" in ev.reasoning_trace

    def test_reasoning_trace_with_explicit_no_match_passes(self) -> None:
        """显式声明未命中 → 通过校验"""
        ev = JudgeEvaluation(
            **self._kwargs(
                reasoning_trace="第一步：内容干净\n第二步：未命中任何规则\n第三步：T2",
            )
        )
        assert "未命中" in ev.reasoning_trace

    def test_reasoning_trace_with_fabricated_rule_id_rejected(self) -> None:
        """编造的规则编号 → 拒绝"""
        with pytest.raises(ValidationError, match="未注册的规则编号"):
            JudgeEvaluation(
                **self._kwargs(
                    reasoning_trace="第一步\n第二步：命中 A-099\n第三步：T1",
                    final_decision="T1_Shadowban",
                )
            )

    def test_reasoning_trace_vague_without_opt_out_rejected(self) -> None:
        """既无规则引用也无未命中声明 → 拒绝"""
        with pytest.raises(ValidationError, match="未引用任何"):
            JudgeEvaluation(
                **self._kwargs(
                    reasoning_trace="第一步：看了一眼\n第二步：感觉还行\n第三步：T2",
                )
            )


# ────────────────────────────────────────────────────────────
# 5. DPO_Pair
# ────────────────────────────────────────────────────────────


class TestDPOPair:
    """DPO_Pair 模型测试"""

    def test_normal_construction(self) -> None:
        """正常构建"""
        pair = DPO_Pair(
            prompt="审核此内容",
            chosen="正确回答",
            rejected="错误回答",
            chosen_score=0.9,
            rejected_score=0.3,
            preference_gap=0.6,
            dimension="violence",
            difficulty="medium",
            source_case_id="case-001",
        )
        assert pair.prompt == "审核此内容"
        assert pair.chosen_score == 0.9
        assert pair.preference_gap == 0.6

    def test_pair_id_auto_generated(self) -> None:
        """pair_id 不传则自动生成 UUID"""
        pair = DPO_Pair(
            prompt="p",
            chosen="c",
            rejected="r",
            chosen_score=0.8,
            rejected_score=0.2,
            preference_gap=0.6,
            dimension="d",
            difficulty="easy",
            source_case_id="s",
        )
        assert pair.pair_id  # 非空
        assert len(pair.pair_id) == 36  # UUID 格式

    def test_pair_id_uniqueness(self) -> None:
        """两个实例的 pair_id 应不同"""
        kwargs = dict(
            prompt="p",
            chosen="c",
            rejected="r",
            chosen_score=0.8,
            rejected_score=0.2,
            preference_gap=0.6,
            dimension="d",
            difficulty="easy",
            source_case_id="s",
        )
        p1 = DPO_Pair(**kwargs)
        p2 = DPO_Pair(**kwargs)
        assert p1.pair_id != p2.pair_id

    def test_preference_gap_too_low(self) -> None:
        """preference_gap < 0 应触发 ValidationError"""
        with pytest.raises(ValidationError):
            DPO_Pair(
                prompt="p",
                chosen="c",
                rejected="r",
                chosen_score=0.5,
                rejected_score=0.5,
                preference_gap=-0.1,
                dimension="d",
                difficulty="easy",
                source_case_id="s",
            )

    def test_preference_gap_too_high(self) -> None:
        """preference_gap > 1.0 应触发 ValidationError"""
        with pytest.raises(ValidationError):
            DPO_Pair(
                prompt="p",
                chosen="c",
                rejected="r",
                chosen_score=0.5,
                rejected_score=0.5,
                preference_gap=1.5,
                dimension="d",
                difficulty="easy",
                source_case_id="s",
            )

    def test_model_dump_roundtrip(self) -> None:
        """序列化往返验证"""
        pair = DPO_Pair(
            prompt="审核内容",
            chosen="好回答",
            rejected="差回答",
            chosen_score=0.85,
            rejected_score=0.35,
            preference_gap=0.5,
            dimension="sexual",
            difficulty="hard",
            source_case_id="c-rt",
        )
        data = pair.model_dump()
        restored = DPO_Pair(**data)
        assert restored.prompt == pair.prompt
        assert restored.pair_id == pair.pair_id
        assert restored.preference_gap == pair.preference_gap


# ────────────────────────────────────────────────────────────
# 6. PipelineConfig / PipelineRun / PipelineResult
# ────────────────────────────────────────────────────────────


class TestPipelineConfig:
    """PipelineConfig 模型测试"""

    def test_defaults(self) -> None:
        """默认值验证"""
        cfg = PipelineConfig()
        assert cfg.num_samples == 10
        assert cfg.batch_size == 10
        assert cfg.max_concurrent == 5
        assert cfg.temperature == 0.7
        assert cfg.min_preference_gap == 0.2

    def test_num_samples_lower_bound(self) -> None:
        """num_samples < 1 应触发 ValidationError"""
        with pytest.raises(ValidationError):
            PipelineConfig(num_samples=0)

    def test_num_samples_upper_bound(self) -> None:
        """num_samples > 10000 应触发 ValidationError"""
        with pytest.raises(ValidationError):
            PipelineConfig(num_samples=10001)

    def test_num_samples_boundary(self) -> None:
        """num_samples 边界值 1 和 10000 应通过"""
        assert PipelineConfig(num_samples=1).num_samples == 1
        assert PipelineConfig(num_samples=10000).num_samples == 10000

    def test_batch_size_range(self) -> None:
        """batch_size 范围验证"""
        with pytest.raises(ValidationError):
            PipelineConfig(batch_size=0)
        with pytest.raises(ValidationError):
            PipelineConfig(batch_size=101)

    def test_temperature_range(self) -> None:
        """temperature 范围验证"""
        with pytest.raises(ValidationError):
            PipelineConfig(temperature=-0.1)
        with pytest.raises(ValidationError):
            PipelineConfig(temperature=2.1)


class TestPipelineRun:
    """PipelineRun 模型测试"""

    def test_auto_uuid(self) -> None:
        """run_id 自动生成 UUID"""
        run = PipelineRun()
        assert run.run_id  # 非空
        assert len(run.run_id) == 36

    def test_default_status_pending(self) -> None:
        """默认状态为 PENDING"""
        run = PipelineRun()
        assert run.status == PipelineStatus.PENDING

    @pytest.mark.parametrize("status", ["pending", "running", "completed", "failed"])
    def test_all_status_values(self, status: str) -> None:
        """所有 PipelineStatus 枚举值"""
        run = PipelineRun(status=status)
        assert run.status.value == status

    def test_default_counters(self) -> None:
        """默认计数器均为 0"""
        run = PipelineRun()
        assert run.total == 0
        assert run.completed == 0
        assert run.failed == 0
        assert run.dpo_pairs_generated == 0
        assert run.progress_pct == 0.0

    def test_optional_fields_default_none(self) -> None:
        """可选字段默认为 None"""
        run = PipelineRun()
        assert run.started_at is None
        assert run.completed_at is None
        assert run.error is None


class TestPipelineResult:
    """PipelineResult 模型测试"""

    def test_dpo_pairs_default_empty_list(self) -> None:
        """dpo_pairs 默认为空列表"""
        result = PipelineResult(
            run_id="r-1",
            total_cases=10,
            total_evaluations=10,
            total_dpo_pairs=0,
        )
        assert result.dpo_pairs == []

    def test_dimension_stats_default_empty_dict(self) -> None:
        """dimension_stats 默认为空字典"""
        result = PipelineResult(
            run_id="r-2",
            total_cases=5,
            total_evaluations=5,
            total_dpo_pairs=3,
        )
        assert result.dimension_stats == {}

    def test_default_numeric_fields(self) -> None:
        """数值字段默认值验证"""
        result = PipelineResult(
            run_id="r-3",
            total_cases=0,
            total_evaluations=0,
            total_dpo_pairs=0,
        )
        assert result.output_path == ""
        assert result.avg_quality_score == 0.0
        assert result.interception_rate == 0.0
