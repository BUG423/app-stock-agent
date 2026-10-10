"""OpenAI-compatible LLM 客户端实现。"""

import logging
import time
from typing import Any

import httpx

from stockagent.core.base_llm import AbstractLLMClient, LLMConfig, LLMResponse

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(AbstractLLMClient):
    """通用 OpenAI-compatible API 客户端（通义千问 / DeepSeek / Kimi 等）。"""

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._circuit_open_until = 0.0
        self._circuit_reason = ""

    @property
    def provider_name(self) -> str:
        return str(self.config.extra.get("provider_name") or "")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.config.timeout_seconds),
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        """发送 OpenAI-compatible chat completion 请求。"""
        remaining = self._circuit_open_until - time.monotonic()
        if remaining > 0:
            return LLMResponse(
                content="",
                model=self.config.model,
                success=False,
                error=f"提供商熔断中（剩余{int(remaining)}秒）: {self._circuit_reason}",
                provider=self.provider_name,
            )
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }

        if response_format is not None:
            payload["response_format"] = response_format

        try:
            resp = await self.client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()

            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage", {})

            return LLMResponse(
                content=content,
                model=data.get("model", self.config.model),
                usage={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                success=True,
                provider=self.provider_name,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = e.response.headers.get("retry-after", "")
                try:
                    cooldown = max(60, min(int(retry_after), 3600))
                except (TypeError, ValueError):
                    cooldown = 1800
                self._circuit_open_until = time.monotonic() + cooldown
                self._circuit_reason = "HTTP 429 额度或频率限制"
                logger.warning(f"LLM 提供商熔断 {cooldown} 秒: {self.config.model}")
            logger.error(f"LLM API 错误 [{e.response.status_code}]: {e.response.text[:500]}")
            return LLMResponse(
                content="",
                model=self.config.model,
                success=False,
                error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                provider=self.provider_name,
            )
        except httpx.TimeoutException:
            logger.error(f"LLM 调用超时 ({self.config.timeout_seconds}s)")
            return LLMResponse(
                content="",
                model=self.config.model,
                success=False,
                error=f"请求超时 ({self.config.timeout_seconds}s)",
                provider=self.provider_name,
            )
        except Exception as e:
            logger.error(f"LLM 调用异常: {e}")
            return LLMResponse(
                content="",
                model=self.config.model,
                success=False,
                error=str(e),
                provider=self.provider_name,
            )

    async def chat_with_fallback(
        self,
        messages: list[dict[str, str]],
        fallback_clients: list[AbstractLLMClient],
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        """依次尝试主客户端和备用客户端，直到成功。"""
        # 先尝试主客户端
        response = await self.chat(messages, response_format)
        if response.success:
            return response

        logger.warning(f"主 LLM ({self.config.model}) 调用失败，尝试 fallback...")
        failures = [f"{self.provider_name or self.config.model}: {response.error or '未知错误'}"]

        # 依次尝试备用客户端
        for i, fallback in enumerate(fallback_clients):
            logger.info(f"尝试 fallback #{i + 1}: {fallback.config.model}")
            response = await fallback.chat(messages, response_format)
            if response.success:
                logger.info(f"Fallback #{i + 1} ({fallback.config.model}) 成功")
                return response
            provider = getattr(fallback, "provider_name", "") or fallback.config.model
            failures.append(f"{provider}: {response.error or '未知错误'}")

        # 全部失败时保留每个供应商的具体错误。此前这里只返回“全部失败”，
        # 导致原文降级邮件无法追溯是额度、认证、超时还是网络问题。
        return LLMResponse(
            content="",
            model="all_failed",
            success=False,
            error=("所有 LLM 提供商均调用失败: " + "；".join(failures))[:1000],
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
