"""微博爬虫 —— 占位实现，后续完善。"""

import logging
from datetime import datetime

from stockagent.core.base_scraper import AbstractScraper, ScrapedContent
from stockagent.core.config import config

logger = logging.getLogger(__name__)


class WeiboScraper(AbstractScraper):
    """微博爬虫（待完善 —— 需要处理反爬机制）。"""

    platform = "weibo"

    def __init__(self) -> None:
        scraper_cfg = config.get("scrapers", default={})
        weibo_cfg = scraper_cfg.get("weibo", {})
        self._cookies_str: str = weibo_cfg.get("cookies", "")

    async def scrape_user_posts(
        self,
        user_id: str,
        since: datetime | None = None,
        max_items: int = 20,
    ) -> list[ScrapedContent]:
        """抓取微博用户帖子。"""
        logger.warning("微博爬虫尚未完全实现，返回空列表")
        # TODO: 实现微博 API / 页面爬取
        # - 移动端 API: https://m.weibo.cn/api/container/getIndex?type=uid&value={user_id}
        # - 需要处理 Cookie 认证和反爬
        return []

    async def scrape_single_post(self, url: str) -> ScrapedContent | None:
        logger.warning("微博单条抓取尚未实现")
        return None

    async def validate_credentials(self) -> bool:
        return bool(self._cookies_str)
