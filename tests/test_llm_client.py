"""LLMClient 测试 — Mock acompletion (streaming mode)"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ecoalign_forge.exceptions import LLMError
from ecoalign_forge.llm.client import LLMClient


async def _stream_one(text: str):
    """模拟流式返回一个 chunk，其 delta.content 是 text。"""
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = text
    yield chunk


def _always_stream(text: str):
    """返回可作为 AsyncMock.side_effect 的 async callable，每次调用生成新 stream。"""

    async def _call(**kwargs):
        return _stream_one(text)

    return _call


class TestLLMClient:
    """LLMClient 单元测试"""

    @pytest.fixture
    def client(self) -> LLMClient:
        return LLMClient(default_model="test-model")

    async def test_generate_success(self, client) -> None:
        """正常生成返回累积内容"""
        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _always_stream("测试回复")
            result = await client.generate(
                messages=[{"role": "user", "content": "你好"}],
            )

        assert result == "测试回复"
        mock_ac.assert_called_once()

    async def test_generate_uses_default_model(self, client) -> None:
        """不指定 model 时使用默认模型"""
        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _always_stream("ok")
            await client.generate(messages=[{"role": "user", "content": "test"}])

        call_kwargs = mock_ac.call_args
        assert call_kwargs.kwargs["model"] == "test-model"
        # streaming 模式下 stream=True 必须传给 acompletion
        assert call_kwargs.kwargs["stream"] is True

    async def test_generate_uses_custom_model(self, client) -> None:
        """指定 model 时覆盖默认"""
        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _always_stream("ok")
            await client.generate(
                messages=[{"role": "user", "content": "test"}],
                model="custom-model",
            )

        call_kwargs = mock_ac.call_args
        assert call_kwargs.kwargs["model"] == "custom-model"

    async def test_generate_raises_llm_error(self, client) -> None:
        """API 失败抛出 LLMError"""
        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = ConnectionError("连接失败")
            with pytest.raises(LLMError, match="LLM 调用失败"):
                await client.generate(
                    messages=[{"role": "user", "content": "test"}],
                )

    async def test_generate_passes_extra_kwargs(self, client) -> None:
        """额外 kwargs（如 reasoning_effort）应透传给 acompletion"""
        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _always_stream("ok")
            await client.generate(
                messages=[{"role": "user", "content": "test"}],
                reasoning_effort="high",
            )

        call_kwargs = mock_ac.call_args
        assert call_kwargs.kwargs.get("reasoning_effort") == "high"

    async def test_batch_generate_success(self, client) -> None:
        """批量生成"""
        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _always_stream("回复")
            results = await client.batch_generate(
                prompts=[
                    [{"role": "user", "content": "q1"}],
                    [{"role": "user", "content": "q2"}],
                ],
                max_concurrent=2,
            )

        assert len(results) == 2
        assert all(r == "回复" for r in results)

    async def test_batch_generate_partial_failure(self, client) -> None:
        """批量生成部分失败返回空字符串"""

        async def _side_effect(**kwargs):
            msgs = kwargs.get("messages", [])
            if any("fail" in m.get("content", "") for m in msgs):
                raise LLMError("持续失败")
            return _stream_one("ok")

        with patch("ecoalign_forge.llm.client.acompletion", new_callable=AsyncMock) as mock_ac:
            mock_ac.side_effect = _side_effect
            results = await client.batch_generate(
                prompts=[
                    [{"role": "user", "content": "q1"}],
                    [{"role": "user", "content": "fail-q2"}],
                    [{"role": "user", "content": "q3"}],
                ],
            )

        assert len(results) == 3
        assert results[0] == "ok"
        # 持续失败的调用返回空字符串
        assert results[1] == ""
        assert results[2] == "ok"
