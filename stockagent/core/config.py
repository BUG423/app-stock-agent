"""配置加载模块 —— YAML + 环境变量覆盖。"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from stockagent.core.exceptions import ConfigError

# 加载 .env 文件到环境变量
load_dotenv(Path(__file__).parent.parent.parent / ".env")


def _resolve_env(value: str) -> str:
    """解析字符串中的 ${VAR_NAME} 或 ${VAR_NAME:-default} 模式。"""
    pattern = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default if default is not None else "")

    return pattern.sub(_replace, value)


def _resolve_dict(data: dict[str, Any]) -> dict[str, Any]:
    """递归解析字典中所有字符串值里的环境变量。"""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = _resolve_env(value)
        elif isinstance(value, dict):
            result[key] = _resolve_dict(value)
        elif isinstance(value, list):
            result[key] = [_resolve_env(item) if isinstance(item, str) else item for item in value]
        else:
            result[key] = value
    return result


class Config:
    """应用配置单例。"""

    _instance: "Config | None" = None

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def load(self, config_path: str | None = None) -> None:
        """加载配置文件。"""
        if config_path is None:
            config_path = os.environ.get(
                "STOCKAGENT_CONFIG",
                str(Path(__file__).parent.parent.parent / "config" / "default.yaml"),
            )

        path = Path(config_path)
        if not path.exists():
            raise ConfigError(f"配置文件不存在: {config_path}")

        with open(path, "r", encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f)

        self._data = _resolve_dict(raw)
        self._loaded = True

    def __getattr__(self, name: str) -> Any:
        if not self._loaded:
            raise ConfigError("配置尚未加载，请先调用 Config().load()")
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._data:
            raise AttributeError(f"配置项不存在: {name}")
        return self._data[name]

    def get(self, *keys: str, default: Any = None) -> Any:
        """通过路径获取配置值，例如 config.get("llm", "providers", "tongyi_qwen")。"""
        if not self._loaded:
            raise ConfigError("配置尚未加载")
        value: Any = self._data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value

    def to_dict(self) -> dict[str, Any]:
        """返回完整配置字典。"""
        if not self._loaded:
            raise ConfigError("配置尚未加载")
        return self._data


# 全局单例
config = Config()
