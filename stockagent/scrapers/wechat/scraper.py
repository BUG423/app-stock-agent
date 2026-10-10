"""微信公众号爬虫 —— 占位实现，后续完善。"""

import logging
from datetime import datetime

from stockagent.core.base_scraper import AbstractScraper, ScrapedContent

logger = logging.getLogger(__name__)


class WechatScraper(AbstractScraper):
    """微信公众号爬虫（待完善 —— 需要特殊方案）。"""

    platform = "wechat"

    async def scrape_user_posts(
        self,
        user_id: str,
        since: datetime | None = None,
        max_items: int = 20,
    ) -> list[ScrapedContent]:
        """抓取微信公众号文章。"""
        logger.warning("微信公众号爬虫尚未实现，返回空列表")
        # TODO: 微信公众号没有公开 API，需要以下方案之一：
        # 1. 使用搜狗微信搜索代理
        # 2. 使用第三方微信文章聚合平台
        # 3. 使用企业微信应用 + 公众号授权
        return []

    async def scrape_single_post(self, url: str) -> ScrapedContent | None:
        logger.warning("微信单篇文章抓取尚未实现")
        return None

    async def validate_credentials(self) -> bool:
        return False
