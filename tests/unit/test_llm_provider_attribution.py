"""LLM fallback 的真实提供商审计字段测试。"""

import pytest

from stockagent.core.base_llm import LLMConfig, LLMResponse
from stockagent.llm.providers.openai_compatible import OpenAICompatibleClient


@pytest.mark.asyncio
async def test_fallback_response_keeps_actual_provider(monkeypatch):
    primary = OpenAICompatibleClient(
        LLMConfig(
            model="mimo-v2.5-pro",
            api_key="test",
            base_url="https://mimo.invalid",
            extra={"provider_name": "mimo"},
        )
    )
    fallback = OpenAICompatibleClient(
        LLMConfig(
            model="glm-4-flash",
            api_key="test",
            base_url="https://glm.invalid",
            extra={"provider_name": "glm"},
        )
    )

    async def primary_failure(*_args, **_kwargs):
        return LLMResponse(
            content="",
            model="mimo-v2.5-pro",
            success=False,
            error="quota exhausted",
            provider="mimo",
        )

    async def fallback_success(*_args, **_kwargs):
        return LLMResponse(
            content='{"summary":"ok"}',
            model="glm-4-flash",
            success=True,
            provider="glm",
        )

    monkeypatch.setattr(primary, "chat", primary_failure)
    monkeypatch.setattr(fallback, "chat", fallback_success)

    response = await primary.chat_with_fallback([], [fallback])

    assert response.success is True
    assert response.model == "glm-4-flash"
    assert response.provider == "glm"
