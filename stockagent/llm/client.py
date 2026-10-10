"""LLM 统一调用接口 —— 管理多提供商、fallback、配置加载。"""

import logging

from stockagent.core.base_llm import LLMConfig, LLMResponse
from stockagent.core.config import config
from stockagent.core.exceptions import LLMAuthError, LLMError
from stockagent.llm.providers.openai_compatible import OpenAICompatibleClient

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM 统一客户端 —— 按优先级编排多提供商。"""

    def __init__(self) -> None:
        self._clients: dict[str, OpenAICompatibleClient] = {}
        self._primary: str = ""
        self._fallback_chain: list[str] = []
        self._init_from_config()

    def _init_from_config(self) -> None:
        """从配置加载 LLM 提供商。"""
        providers_cfg = config.get("llm", "providers", default={})
        if not providers_cfg:
            raise LLMError("未配置任何 LLM 提供商")

        llm_cfg = config.get("llm", default={})
        default_max_tokens = llm_cfg.get("max_tokens", 4096)
        default_temperature = llm_cfg.get("temperature", 0.1)
        default_timeout = llm_cfg.get("timeout_seconds", 60)

        # 按优先级排序
        sorted_providers = sorted(
            providers_cfg.items(),
            key=lambda kv: kv[1].get("priority", 99),
        )

        for name, pcfg in sorted_providers:
            api_key = pcfg.get("api_key", "")
            if not api_key:
                logger.warning(f"LLM 提供商 '{name}' 未配置 API Key，跳过")
                continue

            llm_config = LLMConfig(
                model=pcfg.get("model", ""),
                api_key=api_key,
                base_url=pcfg.get("base_url", ""),
                max_tokens=pcfg.get("max_tokens", default_max_tokens),
                temperature=pcfg.get("temperature", default_temperature),
                timeout_seconds=pcfg.get("timeout_seconds", default_timeout),
                extra={"provider_name": name},
            )
            self._clients[name] = OpenAICompatibleClient(llm_config)

        if not self._clients:
            raise LLMAuthError("没有可用的 LLM 提供商（请检查 API Key 配置）")

        # 第一个有 key 的是 primary，其余是 fallback
        names = list(self._clients.keys())
        self._primary = config.get("llm", "default_provider", default=names[0])
        if self._primary not in self._clients:
            logger.warning(f"默认提供商 '{self._primary}' 不可用，使用 '{names[0]}'")
            self._primary = names[0]
        self._fallback_chain = [n for n in names if n != self._primary]

    @property
    def primary_client(self) -> OpenAICompatibleClient:
        return self._clients[self._primary]

    @property
    def fallback_clients(self) -> list[OpenAICompatibleClient]:
        return [self._clients[n] for n in self._fallback_chain]

    async def chat(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        """调用 LLM，自动 fallback。"""
        return await self.primary_client.chat_with_fallback(
            messages=messages,
            fallback_clients=self.fallback_clients,
            response_format=response_format,
        )

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()

    def provider_name(self) -> str:
        return self._primary


# 全局单例
_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
