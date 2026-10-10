"""LLM 客户端抽象基类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMConfig:
    """LLM 提供商配置。"""

    model: str
    api_key: str
    base_url: str
    max_tokens: int = 4096
    temperature: float = 0.1
    timeout_seconds: int = 60
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """LLM 调用响应。"""

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    provider: str = ""


class AbstractLLMClient(ABC):
    """LLM 客户端抽象接口（OpenAI-compatible API）。"""

    def __init__(self, config: LLMConfig):
        self.config = config

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        """
        发送对话请求。

        Args:
            messages: [{"role": "...", "content": "..."}]
            response_format: 如 {"type": "json_object"}

        Returns:
            LLMResponse
        """
        ...

    @abstractmethod
    async def chat_with_fallback(
        self,
        messages: list[dict[str, str]],
        fallback_clients: list["AbstractLLMClient"],
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        """
        依次尝试调用，失败时 fallback 到备用客户端。
        """
        ...
