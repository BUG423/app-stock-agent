"""爬虫抽象基类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ScrapedContent:
    """爬取的内容统一数据结构。"""

    platform: str
    blogger_id: int  # 数据库 blogger.id
    content_id: str
    title: str
    text: str
    url: str
    content_type: str = "unknown"  # video / article / post / image_text
    published_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AbstractScraper(ABC):
    """所有平台爬虫的基类。"""

    @property
    @abstractmethod
    def platform(self) -> str:
        """返回平台标识符，如 'bilibili', 'weibo'。"""
        ...

    @abstractmethod
    async def scrape_user_posts(
        self,
        user_id: str,
        since: datetime | None = None,
        max_items: int = 20,
    ) -> list[ScrapedContent]:
        """
        抓取用户/空间最近的帖子。

        Args:
            user_id: 平台原生用户标识符
            since: 只返回此时间之后发布的内容
            max_items: 最大抓取条数

        Returns:
            ScrapedContent 列表
        """
        ...

    @abstractmethod
    async def scrape_single_post(self, url: str) -> ScrapedContent | None:
        """抓取单条内容（按URL）。"""
        ...

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """检查爬虫 Cookie/Token 是否有效。"""
        ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any):
        pass
