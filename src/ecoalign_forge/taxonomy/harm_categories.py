"""HarmBench 对齐的危害分类体系。

参考：
- HarmBench: A Standardized Evaluation Framework (2024)
  4 功能类别 × 7 语义危害域
- OWASP LLM Top 10 (2025)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FunctionalCategory(StrEnum):
    """HarmBench 功能分类。"""

    STANDARD = "standard"
    CONTEXTUAL = "contextual"
    COPYRIGHT = "copyright"
    MULTIMODAL = "multimodal"


class SemanticDomain(StrEnum):
    """语义危害域（对齐 HarmBench + 国内内容安全场景扩展）。"""

    VIOLENCE = "violence"
    SEXUAL = "sexual_content"
    HATE_SPEECH = "hate_speech"
    MISINFORMATION = "misinformation"
    PRIVACY = "privacy_violation"
    ILLEGAL_ACTIVITY = "illegal_activity"
    SELF_HARM = "self_harm"
    # 国内特色场景扩展
    STEALTH_MARKETING = "stealth_marketing"
    AI_SLOP = "ai_slop"
    POLITICAL_SENSITIVITY = "political_sensitivity"


@dataclass(frozen=True)
class HarmCategory:
    """危害分类条目。"""

    domain: SemanticDomain
    functional_category: FunctionalCategory
    description: str
    severity: float  # [0.0, 1.0] 基础严重度
    owasp_mapping: str = ""  # OWASP LLM Top 10 对应项
    examples: tuple[str, ...] = field(default_factory=tuple)


# 核心分类体系（参考 HarmBench 510 行为）
HARM_TAXONOMY: dict[str, HarmCategory] = {
    # 国内核心场景（EcoAlign-Forge 主攻方向）
    "stealth_marketing": HarmCategory(
        domain=SemanticDomain.STEALTH_MARKETING,
        functional_category=FunctionalCategory.CONTEXTUAL,
        description="高隐蔽性私域引流：微信号、谐音字、emoji 夹带、暗号等",
        severity=0.7,
        owasp_mapping="LLM09: Misinformation",
        examples=("微信号变体", "谐音字引流", "二维码嵌入"),
    ),
    "ai_slop": HarmCategory(
        domain=SemanticDomain.AI_SLOP,
        functional_category=FunctionalCategory.STANDARD,
        description="低信息熵 AI 洗稿：套话开篇、语义重复、缺乏第一手细节",
        severity=0.5,
        owasp_mapping="LLM09: Misinformation",
        examples=("AI 套话开篇", "高度雷同内容", "稀薄列表体"),
    ),
    # HarmBench 标准域
    "violence": HarmCategory(
        domain=SemanticDomain.VIOLENCE,
        functional_category=FunctionalCategory.STANDARD,
        description="暴力或威胁内容",
        severity=0.9,
        owasp_mapping="LLM02: Insecure Output Handling",
    ),
    "hate_speech": HarmCategory(
        domain=SemanticDomain.HATE_SPEECH,
        functional_category=FunctionalCategory.STANDARD,
        description="仇恨言论、歧视性内容",
        severity=0.85,
        owasp_mapping="LLM02: Insecure Output Handling",
    ),
    "misinformation": HarmCategory(
        domain=SemanticDomain.MISINFORMATION,
        functional_category=FunctionalCategory.CONTEXTUAL,
        description="虚假信息、伪科学",
        severity=0.6,
        owasp_mapping="LLM09: Misinformation",
    ),
    "privacy_violation": HarmCategory(
        domain=SemanticDomain.PRIVACY,
        functional_category=FunctionalCategory.CONTEXTUAL,
        description="隐私泄露、个人信息暴露",
        severity=0.8,
        owasp_mapping="LLM06: Sensitive Information Disclosure",
    ),
    "jailbreak": HarmCategory(
        domain=SemanticDomain.ILLEGAL_ACTIVITY,
        functional_category=FunctionalCategory.STANDARD,
        description="越狱攻击：试图绕过安全约束",
        severity=0.95,
        owasp_mapping="LLM01: Prompt Injection",
    ),
}
