"""解析失败 + 指数退避重试机制测试。

覆盖 LLMClient.generate_validated / batch_generate_validated 的 6 个核心场景：
1. 第一次坏 JSON、第二次合法 → 成功返回，调用 2 次
2. 连续 N 次坏 JSON → 抛 ParseRetryExhaustedError，调用 N 次
3. Pydantic 校验失败也触发重试
4. LLMError 不被解析重试机制捕获（直接抛出）
5. batch_generate_validated 部分失败 → 失败位置返回 None
6. 自定义 parser 验证 SchemaValidationError 子类同样触发重试
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field, ValidationError

from ecoalign_forge.exceptions import (
    EcoAlignError,
    LLMError,
    ParseRetryExhaustedError,
    SchemaValidationError,
)
from ecoalign_forge.llm.client import LLMClient

# ──────────────────────────────────────────────────────────────
# 测试夹具
# ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """把指数退避等待时间压到接近 0，避免拖慢测试。"""
    monkeypatch.setattr("ecoalign_forge.llm.client._PARSE_RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("ecoalign_forge.llm.client._PARSE_RETRY_WAIT_MAX", 0)


@pytest.fixture
def client() -> LLMClient:
    return LLMClient(default_model="test-model")


async def _stream_one(text: str):
    """模拟 litellm 流式返回一个 chunk，其 delta.content 是 text。

    LLMClient.generate 现在始终使用 stream=True 模式，所以 mock 需要返回
    async 迭代器而非单个 response 对象。
    """
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    yield chunk


def _mock_resp(content: str):
    """每次调用都返回一个**全新**的 async 生成器，避免被迭代一次后耗尽。"""
    return _stream_one(content)


def _always_stream(text: str):
    """返回一个可作为 AsyncMock.side_effect 的 async callable，每次调用都生成新 stream。

    用于"同一条响应被重复调用 N 次"的场景（例如 test_exhausts_retries_*)。
    """

    async def _call(**kwargs):
        return _stream_one(text)

    return _call


class _Item(BaseModel):
    """测试用的简单 Pydantic 模型。"""

    name: str = Field(..., min_length=1)
    score: float = Field(..., ge=0.0, le=1.0)


def _strict_parser(raw: str) -> _Item:
    """JSON → _Item，失败抛 SchemaValidationError。"""
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SchemaValidationError(f"非法 JSON: {e}") from e
    try:
        return _Item(**data)
    except ValidationError as e:
        raise SchemaValidationError(f"Schema 校验失败: {e}") from e


# ──────────────────────────────────────────────────────────────
# 1. 第一次坏 JSON、第二次合法 → 成功返回
# ──────────────────────────────────────────────────────────────


class TestGenerateValidatedRecovery:
    """generate_validated 的恢复路径测试。"""

    async def test_recovers_after_one_bad_json(self, client: LLMClient) -> None:
        """第 1 次返回非法 JSON，第 2 次返回合法 JSON → 成功，调用 2 次"""
        good = '{"name": "ok", "score": 0.8}'
        bad = "this is not json {"

        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = [_mock_resp(bad), _mock_resp(good)]
            result = await client.generate_validated(
                messages=[{"role": "user", "content": "q"}],
                parser=_strict_parser,
            )

        assert isinstance(result, _Item)
        assert result.name == "ok"
        assert result.score == 0.8
        assert mock_ac.call_count == 2

    async def test_succeeds_first_attempt(self, client: LLMClient) -> None:
        """第 1 次就合法 → 仅调用 1 次"""
        good = '{"name": "first", "score": 0.5}'

        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.return_value = _mock_resp(good)
            result = await client.generate_validated(
                messages=[{"role": "user", "content": "q"}],
                parser=_strict_parser,
            )

        assert result.name == "first"
        assert mock_ac.call_count == 1


# ──────────────────────────────────────────────────────────────
# 2. 连续坏响应 → 抛 ParseRetryExhaustedError
# ──────────────────────────────────────────────────────────────


class TestGenerateValidatedExhaustion:
    """generate_validated 重试用尽测试。"""

    async def test_exhausts_retries_on_persistent_bad_json(self, client: LLMClient) -> None:
        """连续 3 次坏 JSON → ParseRetryExhaustedError，调用 3 次"""
        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _always_stream("garbage")
            with pytest.raises(ParseRetryExhaustedError) as exc_info:
                await client.generate_validated(
                    messages=[{"role": "user", "content": "q"}],
                    parser=_strict_parser,
                )

        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.last_error, SchemaValidationError)
        assert mock_ac.call_count == 3
        # ParseRetryExhaustedError 与 SchemaValidationError 平级，避免重试语义混淆
        assert not isinstance(exc_info.value, SchemaValidationError)
        assert isinstance(exc_info.value, EcoAlignError)

    async def test_exhausts_on_persistent_pydantic_error(self, client: LLMClient) -> None:
        """JSON 合法但 Pydantic 校验持续失败 → ParseRetryExhaustedError"""
        # name 为空字符串，min_length=1 校验会失败
        invalid_pydantic = '{"name": "", "score": 0.5}'

        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _always_stream(invalid_pydantic)
            with pytest.raises(ParseRetryExhaustedError) as exc_info:
                await client.generate_validated(
                    messages=[{"role": "user", "content": "q"}],
                    parser=_strict_parser,
                )

        assert exc_info.value.attempts == 3
        assert mock_ac.call_count == 3


# ──────────────────────────────────────────────────────────────
# 3. LLMError 不触发解析重试
# ──────────────────────────────────────────────────────────────


class TestGenerateValidatedLLMError:
    """generate_validated 与 LLMError 的边界测试。"""

    async def test_llm_error_not_caught_by_parse_retry(self, client: LLMClient) -> None:
        """内层 LLM 持续抛 ConnectionError → 由内层 generate 重试 3 次后抛 LLMError，
        外层解析重试不会再额外重试一次。
        """
        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = ConnectionError("network down")
            with pytest.raises(LLMError):
                await client.generate_validated(
                    messages=[{"role": "user", "content": "q"}],
                    parser=_strict_parser,
                )

        # 内层 LLM 重试 3 次（_RETRY_MAX_ATTEMPTS=3），外层不再叠加
        assert mock_ac.call_count == 3


# ──────────────────────────────────────────────────────────────
# 4. batch_generate_validated 部分失败
# ──────────────────────────────────────────────────────────────


class TestBatchGenerateValidated:
    """batch_generate_validated 测试。"""

    async def test_batch_partial_failure_returns_none_for_failed(self, client: LLMClient) -> None:
        """3 条 prompt 中 1 条持续返回坏 JSON → 结果为 [Item, None, Item]"""
        good_1 = '{"name": "a", "score": 0.1}'
        good_3 = '{"name": "c", "score": 0.9}'
        bad = "totally not json"

        async def _side_effect(**kwargs):
            content = kwargs["messages"][-1]["content"]
            if "fail" in content:
                return _stream_one(bad)
            if "q1" in content:
                return _stream_one(good_1)
            if "q3" in content:
                return _stream_one(good_3)
            return _stream_one(bad)

        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _side_effect
            results = await client.batch_generate_validated(
                prompts=[
                    [{"role": "user", "content": "q1"}],
                    [{"role": "user", "content": "fail-q2"}],
                    [{"role": "user", "content": "q3"}],
                ],
                parser=_strict_parser,
                max_concurrent=3,
            )

        assert len(results) == 3
        assert results[0] is not None and results[0].name == "a"
        assert results[1] is None  # 重试用尽
        assert results[2] is not None and results[2].name == "c"

    async def test_batch_all_success(self, client: LLMClient) -> None:
        """全部 prompt 一次成功 → 结果全非 None"""
        good = '{"name": "x", "score": 0.5}'

        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _always_stream(good)
            results = await client.batch_generate_validated(
                prompts=[
                    [{"role": "user", "content": "q1"}],
                    [{"role": "user", "content": "q2"}],
                ],
                parser=_strict_parser,
            )

        assert len(results) == 2
        assert all(r is not None and r.name == "x" for r in results)
        # 每条 prompt 一次性成功，acompletion 总共调用 2 次
        assert mock_ac.call_count == 2

    async def test_batch_recovers_from_transient_parse_failure(self, client: LLMClient) -> None:
        """单条 prompt 第 1 次失败、第 2 次成功 → 不会被丢弃"""
        good = '{"name": "y", "score": 0.6}'

        # 序列：bad → good
        call_index = {"i": 0}
        contents = ["bad", good]

        async def _side_effect(**kwargs):
            text = contents[call_index["i"]]
            call_index["i"] += 1
            return _stream_one(text)

        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _side_effect
            results = await client.batch_generate_validated(
                prompts=[[{"role": "user", "content": "q"}]],
                parser=_strict_parser,
            )

        assert len(results) == 1
        assert results[0] is not None
        assert results[0].name == "y"
        assert mock_ac.call_count == 2


# ──────────────────────────────────────────────────────────────
# 5. ParseRetryExhaustedError 字段断言
# ──────────────────────────────────────────────────────────────


class TestParseRetryExhaustedError:
    """ParseRetryExhaustedError 的元数据测试。"""

    def test_carries_attempt_count_and_last_error(self) -> None:
        """异常对象同时携带尝试次数与最后一次原始错误"""
        original = SchemaValidationError("根因")
        exc = ParseRetryExhaustedError(attempts=5, last_error=original)
        assert exc.attempts == 5
        assert exc.last_error is original
        assert "5" in str(exc)
        assert "根因" in str(exc)

    def test_is_subclass_of_ecoalign_error_not_schema(self) -> None:
        """与 SchemaValidationError 平级，避免重试语义混淆"""
        assert issubclass(ParseRetryExhaustedError, EcoAlignError)
        assert not issubclass(ParseRetryExhaustedError, SchemaValidationError)


# ──────────────────────────────────────────────────────────────
# 6. 非 SchemaValidationError 异常 + __cause__ 链契约
# ──────────────────────────────────────────────────────────────


class TestGenerateValidatedNonSchemaError:
    """generate_validated 的契约测试：

    - 非 SchemaValidationError 的 parser 异常应**直接传播且不重试**，
      因为 retry_if_exception_type(SchemaValidationError) 不匹配。
    - 重试用尽后 ParseRetryExhaustedError.__cause__ 必须正确指向原始异常，
      便于调试时追溯根因。
    """

    async def test_unrelated_exception_propagates_immediately(self, client: LLMClient) -> None:
        """parser 抛 KeyError 应直接传播，不触发重试"""
        good = '{"name": "ok", "score": 0.5}'

        def buggy_parser(raw: str) -> _Item:
            raise KeyError("意外异常")

        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.return_value = _mock_resp(good)
            with pytest.raises(KeyError, match="意外异常"):
                await client.generate_validated(
                    messages=[{"role": "user", "content": "q"}],
                    parser=buggy_parser,
                )

        # 关键断言：只调用 1 次，没有触发任何重试
        assert mock_ac.call_count == 1

    async def test_exhausted_error_preserves_cause_chain(self, client: LLMClient) -> None:
        """ParseRetryExhaustedError.__cause__ 应指向最后一次原始 SchemaValidationError"""
        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _always_stream("garbage")
            with pytest.raises(ParseRetryExhaustedError) as exc_info:
                await client.generate_validated(
                    messages=[{"role": "user", "content": "q"}],
                    parser=_strict_parser,
                )

        # __cause__（由 `raise ... from e` 设置）应等于 last_error
        assert exc_info.value.__cause__ is exc_info.value.last_error
        assert isinstance(exc_info.value.__cause__, SchemaValidationError)
