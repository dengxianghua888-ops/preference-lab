"""Moderator — 初级审核员，凭直觉对内容做 T0–T3 分级判定。

迁移到内容分发分级 ontology 后职责变化：
- 不再输出 PASS/FLAG/BLOCK 这套旧动作语言
- 改为输出 JudgeEvaluation（与 SupremeJudge 同 schema），但 prompt 故意降级
- 不看 guidelines.md，凭常识判 → 与 Judge 形成天然的弱/强对比，作为 DPO 信号源

路径 3 第二组新增：`persona` 参数支持多种弱版本 persona：
- "naive"（默认）：凭常识中庸判定
- "strict_paranoid"：过度敏感，倾向 T0/T1
- "lax_overlooker"：过度宽松，倾向 T2/T3
- "keyword_matcher"：只看显式关键字，不理解语义

不同 persona 产生不同的"错误模式"，大幅提高 DPO 配对中直接分歧
（direct_disagreement）的占比，避免 reasoning_quality 型对过度集中。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ecoalign_forge.agents.base import BaseAgent
from ecoalign_forge.exceptions import SchemaValidationError
from ecoalign_forge.llm.client import LLMClient
from ecoalign_forge.llm.prompts import MODERATOR_PERSONAS
from ecoalign_forge.schemas.chaos import ChaosCase
from ecoalign_forge.schemas.judge import JudgeEvaluation
from ecoalign_forge.schemas.policy import PolicyInput


class Moderator(BaseAgent):
    """Agent 2: 初级审核员（DPO 训练对里的 rejected 候选源）."""

    name = "Moderator"

    def __init__(
        self,
        llm: LLMClient,
        model: str | None = None,
        persona: str = "naive",
    ) -> None:
        """构造一个具有特定 persona 的 Moderator 实例。

        Args:
            llm: LLM 客户端
            model: 可选模型覆盖
            persona: 必须是 MODERATOR_PERSONAS 中的 key。不同 persona 有不同的
                system prompt 与偏差模式。默认 "naive" 保持向后兼容。
        """
        super().__init__(llm=llm, model=model)
        if persona not in MODERATOR_PERSONAS:
            raise ValueError(
                f"Unknown moderator persona: {persona!r}. "
                f"Valid: {sorted(MODERATOR_PERSONAS.keys())}"
            )
        self.persona = persona
        # 方便外部区分同类 agent 的不同实例
        self.name = f"Moderator[{persona}]"

    async def run(
        self,
        cases: list[ChaosCase],
        policy: PolicyInput,
        **kwargs: Any,
    ) -> list[JudgeEvaluation | None]:
        """对每条 case 做出初级判决。

        返回的列表与 `cases` **位置对齐**：第 i 个元素对应第 i 条 case 的初级判决，
        解析重试用尽的位置为 None（由 `batch_generate_validated` 提供该语义）。
        位置对齐是 SupremeJudge 后续按 list index 关联三个 Agent 输出的关键。
        """
        self._log(f"Moderating {len(cases)} cases as {self.persona} junior reviewer")
        system_prompt = MODERATOR_PERSONAS[self.persona]

        prompts = []
        for case in cases:
            user_prompt = f"""请对以下用户内容做出 T0/T1/T2/T3 分级判定。

## 待审核内容
\"\"\"{case.content}\"\"\"

## 你的处境提醒
你是初级审核员，没有任何专项规则手册可以查阅。请凭你对内容平台的常识和直觉
做判断。**不要假设任何特定规则编号存在**——你看不到 A-001 / B-001 这种东西。

## 输出
返回**且仅返回**一个 JSON 对象，包含 4 个字段：
- "has_stealth_marketing": bool — 你的直觉判断：这是不是私域引流软文？
- "is_ai_slop": bool — 你的直觉判断：这是不是 AI 写的低质洗稿？
- "reasoning_trace": str — 你的三步推理。**格式硬性要求**：
  * 第一步：描述你在内容中看到的特征
  * 第二步：凭直觉认为可能的违规类型；**这一步必须以这句话结尾：**
    "我还没学过规则手册，未命中任何已知规则编号。"
    （这是为了让下游系统知道你是凭直觉而非规则判决）
  * 第三步：给出定级理由
- "final_decision": str — 严格四选一："T0_Block" / "T1_Shadowban" / "T2_Normal" / "T3_Recommend"

不要任何 markdown 包裹或额外解释文字。"""

            prompts.append(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            )

        results = await self.llm.batch_generate_validated(
            prompts=prompts,
            parser=self._parse_judgment,
            model=self.model,
            temperature=0.5,  # 比 Judge 更高的温度，模拟"不那么确定"
        )

        dropped = sum(1 for r in results if r is None)
        if dropped > 0:
            self._log(f"丢弃 {dropped} 条初级判决（解析重试已用尽）")
        self._log(f"Completed {len(results) - dropped}/{len(cases)} junior moderations")
        # 保留 None 占位以保持与 cases 的位置对齐
        return results

    def _parse_judgment(self, raw: str) -> JudgeEvaluation:
        """解析 LLM 输出为 JudgeEvaluation。失败抛 SchemaValidationError 触发重试。"""
        json_text = self._extract_json(raw, mode="object")
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            raise SchemaValidationError(f"Moderator 输出无法解析为有效 JSON: {e}") from e

        try:
            return JudgeEvaluation(**data)
        except ValidationError as e:
            raise SchemaValidationError(f"Moderator 输出不符合 JudgeEvaluation Schema: {e}") from e
