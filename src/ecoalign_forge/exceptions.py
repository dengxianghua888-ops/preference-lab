"""Custom exception hierarchy for EcoAlign-Forge."""


class EcoAlignError(Exception):
    """Base exception for all EcoAlign-Forge errors."""


class LLMError(EcoAlignError):
    """Error during LLM API call."""


class SchemaValidationError(EcoAlignError):
    """Error parsing LLM output into Pydantic schema."""


class ParseRetryExhaustedError(EcoAlignError):
    """解析重试已用尽仍然失败。

    与 SchemaValidationError 平级——前者表示"单次解析失败，可重试"，
    后者表示"重试用尽，不可恢复"。分开继承避免上层 except
    SchemaValidationError 意外捕获已穷尽的错误导致二次重试。
    """

    def __init__(self, attempts: int, last_error: Exception) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"解析重试 {attempts} 次后仍失败: {last_error}")


class PipelineError(EcoAlignError):
    """Error during pipeline orchestration."""


class AgentError(EcoAlignError):
    """Error within an agent's execution."""

    def __init__(self, agent_name: str, message: str) -> None:
        self.agent_name = agent_name
        super().__init__(f"[{agent_name}] {message}")
