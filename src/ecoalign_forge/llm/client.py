"""LLM Client — 基于 LiteLLM acompletion 的异步封装，内置重试机制。

包含两层独立的重试：
- LLM API 层：`generate()` 用 `@retry` 装饰，捕获 `LLMError`（网络/API 错误）
- 解析校验层：`generate_validated()` 用 `AsyncRetrying` 捕获 `SchemaValidationError`
  （JSON 错误 / Pydantic 校验错误），重试时会重新发起整次 LLM 调用
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TypeVar

from litellm import acompletion
from tenacity import (
    AsyncRetrying,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ecoalign_forge.config import settings
from ecoalign_forge.exceptions import (
    AgentError,
    LLMError,
    ParseRetryExhaustedError,
    SchemaValidationError,
)

logger = logging.getLogger(__name__)

# LLM API 层重试参数：必须硬编码，因为 @retry 装饰器在导入期固化这些值，
# 一旦装饰器表达式求值就无法再变。改这里需要重启进程。
_RETRY_MAX_ATTEMPTS = 3
_RETRY_WAIT_MAX = 30

# 解析校验层重试参数：从 settings 读取，可通过 .env 调节。
# 这些常量在 generate_validated 的函数体内被引用（运行时查找模块全局），
# 因此 monkeypatch 仍然有效——测试套件的 _fast_backoff fixture 即依赖此点。
_PARSE_RETRY_MAX_ATTEMPTS = settings.parse_max_retries
_PARSE_RETRY_WAIT_MIN = settings.parse_retry_wait_min
_PARSE_RETRY_WAIT_MAX = settings.parse_retry_wait_max

# 全局 reasoning_effort（gpt-5.x / o1 / o3 等支持 reasoning 的模型）。
# 每次 generate() 调用会自动把该值作为 reasoning_effort kwarg 透传给 litellm，
# 除非调用方显式传入同名参数覆盖。
_DEFAULT_REASONING_EFFORT = settings.llm_reasoning_effort


T = TypeVar("T")


class LLMClient:
    """异步 LLM 客户端，基于 LiteLLM，支持 100+ 供应商。"""

    def __init__(
        self, default_model: str = "openai/gpt-4o-mini", *, allow_network: bool = True
    ) -> None:
        self.default_model = default_model
        self.allow_network = allow_network

    @retry(
        stop=stop_after_attempt(_RETRY_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=_RETRY_WAIT_MAX),
        retry=retry_if_exception_type(LLMError),
        before_sleep=lambda rs: logger.warning(
            f"LLM 调用失败 (第 {rs.attempt_number} 次)，重试中..."
        ),
        reraise=True,
    )
    async def generate(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> str:
        """单次异步 LLM 调用，内置指数退避重试。

        始终使用 `stream=True` 模式累积 chunks——部分网关（如 codex-for.me）
        只接受流式请求。对下游调用透明：返回值仍是完整字符串。

        额外的 `**kwargs`（例如 `reasoning_effort="high"`、`top_p`、
        `response_format`）会直接透传给 litellm.acompletion，便于支持
        provider-specific 参数而无需修改签名。
        """
        if not self.allow_network:
            raise AgentError("LLMClient", "Network model calls are disabled in demo/mock mode")
        # 注入全局 reasoning_effort（若 settings 配置了且调用方未显式指定）
        if _DEFAULT_REASONING_EFFORT and "reasoning_effort" not in kwargs:
            kwargs["reasoning_effort"] = _DEFAULT_REASONING_EFFORT
        # 让 litellm 丢弃 provider 不支持的参数（例如 gpt-5.x 不接受 temperature != 1），
        # 使 agent 层的 temperature 设置对非 reasoning 模型仍然生效。按调用传入而非设置
        # 全局 litellm.drop_params：全局开关会在导入本模块时静默改变同进程内所有 litellm
        # 调用（包括 runtime 内核显式要求 drop_params=False 的路径）。
        kwargs.setdefault("drop_params", True)

        try:
            stream = await acompletion(
                model=model or self.default_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                **kwargs,
            )
            chunks: list[str] = []
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    chunks.append(delta)
            return "".join(chunks)
        except LLMError:
            # 已经是 LLMError，直接重新抛出供 tenacity 重试
            raise
        except Exception as e:
            raise LLMError(f"LLM 调用失败: {e}") from e

    async def batch_generate(
        self,
        prompts: list[list[dict]],
        model: str | None = None,
        max_concurrent: int = 5,
        **kwargs,
    ) -> list[str]:
        """Batch async generation with semaphore-based concurrency control."""
        sem = asyncio.Semaphore(max_concurrent)

        async def _call(msgs: list[dict]) -> str:
            async with sem:
                return await self.generate(msgs, model=model, **kwargs)

        results = await asyncio.gather(
            *[_call(m) for m in prompts],
            return_exceptions=True,
        )

        outputs = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"LLM call failed: {r}")
                outputs.append("")
            else:
                outputs.append(r)
        return outputs

    async def generate_validated(
        self,
        messages: list[dict],
        parser: Callable[[str], T],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> T:
        """LLM 调用 + parser 解析的原子操作。

        当 `parser` 抛出 `SchemaValidationError` 时（例如 JSON 不合法或 Pydantic
        校验失败），会按指数退避重新发起整次 LLM 调用——这是有意义的重试策略，
        因为 LLM 在不同采样温度/随机种子下可能产出合法响应。

        重试**只**捕获 `SchemaValidationError`；`LLMError`（网络/API 错误）由
        内层 `generate()` 自身的 `@retry` 装饰器处理，不会被这里二次重试。

        Args:
            messages: OpenAI 风格的消息列表
            parser: 同步解析器，输入原始字符串，输出目标对象；失败必须抛
                `SchemaValidationError`（或其子类）
            model: 可选模型覆盖
            temperature: 采样温度
            max_tokens: 最大生成 token

        Returns:
            parser 解析后的对象

        Raises:
            ParseRetryExhaustedError: 解析重试用尽
            LLMError: 内层 LLM 调用最终失败（不在解析重试范围内）
        """
        attempts = 0

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(_PARSE_RETRY_MAX_ATTEMPTS),
                wait=wait_exponential(
                    multiplier=1,
                    min=_PARSE_RETRY_WAIT_MIN,
                    max=_PARSE_RETRY_WAIT_MAX,
                ),
                retry=retry_if_exception_type(SchemaValidationError),
                reraise=True,
            ):
                with attempt:
                    attempts += 1
                    raw = await self.generate(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                    try:
                        return parser(raw)
                    except SchemaValidationError as e:
                        logger.warning(f"解析失败 (第 {attempts} 次)，重试中: {e}")
                        raise
        except SchemaValidationError as e:
            # reraise=True：tenacity 用尽重试后直接抛出最后一次原始异常
            raise ParseRetryExhaustedError(attempts, e) from e

    async def batch_generate_validated(
        self,
        prompts: list[list[dict]],
        parser: Callable[[str], T],
        *,
        model: str | None = None,
        max_concurrent: int = 5,
        **kwargs,
    ) -> list[T | None]:
        """批量带校验生成，每条 prompt 独立重试。

        与 `batch_generate` 行为一致：失败位置返回 `None`（而非中断整个 batch）。
        失败原因可能是：
        - 内层 LLM 调用持续失败（`LLMError`）
        - 解析重试用尽（`ParseRetryExhaustedError`）

        Args:
            prompts: 每个元素是一组消息（一次 LLM 调用）
            parser: 同步解析器，同 `generate_validated`
            model: 可选模型覆盖
            max_concurrent: 信号量并发上限
            **kwargs: 透传给 `generate_validated`（例如 temperature）

        Returns:
            与输入等长的列表；失败位置为 `None`
        """
        sem = asyncio.Semaphore(max_concurrent)

        async def _one(msgs: list[dict]) -> T | None:
            async with sem:
                try:
                    return await self.generate_validated(
                        messages=msgs, parser=parser, model=model, **kwargs
                    )
                except (LLMError, ParseRetryExhaustedError) as e:
                    logger.error(f"批量条目最终失败: {e}")
                    return None

        return await asyncio.gather(*[_one(p) for p in prompts])
