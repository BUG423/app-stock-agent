"""爬虫插件注册表 —— 自动发现和加载平台爬虫。"""

import importlib
import logging
import pkgutil

from stockagent.core.base_scraper import AbstractScraper

logger = logging.getLogger(__name__)


class ScraperRegistry:
    """爬虫插件发现与注册中心（单例）。"""

    _instance: "ScraperRegistry | None" = None
    _scrapers: dict[str, AbstractScraper] = {}

    def __new__(cls) -> "ScraperRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def discover_plugins(self, package_name: str = "stockagent.scrapers") -> None:
        """扫描 scrapers 包，自动发现 AbstractScraper 子类并实例化。"""
        package = importlib.import_module(package_name)
        package_path = getattr(package, "__path__", [])

        for _, module_name, is_pkg in pkgutil.iter_modules(package_path):
            if not is_pkg:
                continue

            try:
                scraper_module = importlib.import_module(f"{package_name}.{module_name}.scraper")
            except ImportError as e:
                logger.debug(f"跳过模块 {module_name}（无 scraper.py）: {e}")
                continue

            for attr_name in dir(scraper_module):
                attr = getattr(scraper_module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, AbstractScraper)
                    and attr is not AbstractScraper
                ):
                    try:
                        instance = attr()
                        self._scrapers[instance.platform] = instance
                        logger.info(f"已注册爬虫: {instance.platform}")
                    except Exception as e:
                        logger.warning(f"实例化爬虫 {attr_name} 失败: {e}")

    def get(self, platform: str) -> AbstractScraper | None:
        """按平台标识获取爬虫实例。"""
        return self._scrapers.get(platform)

    def list_platforms(self) -> list[str]:
        """列出所有已注册的平台。"""
        return list(self._scrapers.keys())

    def __contains__(self, platform: str) -> bool:
        return platform in self._scrapers

    def __len__(self) -> int:
        return len(self._scrapers)


# 全局单例
scraper_registry = ScraperRegistry()
