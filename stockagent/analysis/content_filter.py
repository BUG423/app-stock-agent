"""内容过滤器 —— 使用LLM判断内容是否有投资价值。"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from stockagent.llm.client import get_llm_client
from stockagent.llm.prompts import CONTENT_FILTER_SYSTEM, CONTENT_FILTER_USER

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """过滤结果"""

    is_useful: bool  # 是否有投资价值
    reason: str  # 判断理由
    category: str  # 内容分类：recommendation/prediction/analysis/entertainment/advertisement/other


class ContentFilter:
    """内容过滤器 —— 使用LLM判断内容是否有投资价值"""

    def __init__(self):
        self._llm_client = None

    @property
    def llm_client(self):
        if self._llm_client is None:
            self._llm_client = get_llm_client()
        return self._llm_client

    async def filter_content(
        self,
        blogger_name: str,
        title: str,
        text: str,
        published_at: str = "",
        content_type: str = "unknown",
    ) -> FilterResult:
        """
        判断单条内容是否有投资价值。

        Args:
            blogger_name: 博主名称
            title: 内容标题
            text: 内容文本
            published_at: 发布时间
            content_type: 内容类型 (article/video)

        Returns:
            FilterResult: 过滤结果
        """
        # 快速关键词预检：包含股票代码或投资术语的内容直接放行
        quick_pass_keywords = [
            # 数字型：6位股票代码
            # 交易术语
            "抄底",
            "清仓",
            "建仓",
            "满仓",
            "空仓",
            "加仓",
            "减仓",
            "做T",
            "T了",
            "止盈",
            "止损",
            "吃肉",
            "落袋",
            "涨停",
            "跌停",
            "连板",
            "龙头",
            "买入",
            "卖出",
            "持仓",
            "入场",
            "上车",
            "下车",
            "启动",
            "回踩",
            "企稳",
            "突破",
            "支撑",
            "压力",
            "均线",
            "五日线",
            "十日线",
            "盘前",
            "复盘",
            "选股",
            "低吸",
            "高抛",
            "打板",
            "封板",
            "大盘",
            "指数",
            "板块",
            "反弹",
            "回调",
            "见底",
            "见顶",
            "牛市",
            "熊市",
            "震荡",
            "趋势",
            "波段",
            "半导体",
            "芯片",
            "新能源",
            "光伏",
            "医药",
            "消费",
            "科技",
        ]
        combined = f"{title} {text[:3000]}"
        quick_pass = any(kw in combined for kw in quick_pass_keywords)
        # Also check for 6-digit stock code patterns
        has_stock_code = bool(re.search(r"(?<!\d)\d{6}(?!\d)", combined))
        if quick_pass or has_stock_code:
            return FilterResult(
                is_useful=True,
                reason="关键词/股票代码预检通过，直接放行",
                category="analysis",
            )

        # 构建提示词
        messages = [
            {"role": "system", "content": CONTENT_FILTER_SYSTEM},
            {
                "role": "user",
                "content": CONTENT_FILTER_USER.format(
                    blogger_name=blogger_name,
                    title=title,
                    published_at=published_at,
                    content_type=content_type,
                    text=text[:6000],  # 限制长度，避免token过多
                ),
            },
        ]

        try:
            # 调用LLM
            response = await self.llm_client.chat(
                messages=messages,
                response_format={"type": "json_object"},
            )

            if not response.success:
                logger.error(f"内容过滤LLM调用失败: {response.error}")
                return FilterResult(
                    is_useful=True,  # 默认保留，让后续分析判断
                    reason="LLM调用失败，默认保留",
                    category="other",
                )

            # 解析结果
            import json

            try:
                result = json.loads(response.content)
                return FilterResult(
                    is_useful=result.get("is_useful", True),
                    reason=result.get("reason", ""),
                    category=result.get("category", "other"),
                )
            except json.JSONDecodeError as e:
                logger.error(f"内容过滤结果解析失败: {e}")
                return FilterResult(
                    is_useful=True,
                    reason="JSON解析失败，默认保留",
                    category="other",
                )

        except Exception as e:
            logger.error(f"内容过滤异常: {e}")
            return FilterResult(
                is_useful=True,
                reason=f"过滤异常: {str(e)[:50]}",
                category="other",
            )

    async def batch_filter(
        self,
        contents: list[dict[str, Any]],
        max_concurrent: int = 3,
    ) -> list[FilterResult]:
        """
        批量过滤内容。

        Args:
            contents: 内容列表，每个元素包含 blogger_name, title, text, published_at, content_type
            max_concurrent: 最大并发数

        Returns:
            list[FilterResult]: 过滤结果列表
        """
        sem = asyncio.Semaphore(max_concurrent)

        async def _filter_one(content: dict) -> FilterResult:
            async with sem:
                return await self.filter_content(
                    blogger_name=content.get("blogger_name", ""),
                    title=content.get("title", ""),
                    text=content.get("text", ""),
                    published_at=content.get("published_at", ""),
                    content_type=content.get("content_type", "unknown"),
                )

        tasks = [_filter_one(c) for c in contents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"过滤第{i + 1}条内容异常: {result}")
                final_results.append(
                    FilterResult(
                        is_useful=True,
                        reason=f"过滤异常: {str(result)[:50]}",
                        category="other",
                    )
                )
            else:
                final_results.append(result)

        return final_results


# 全局单例
_content_filter: ContentFilter | None = None


def get_content_filter() -> ContentFilter:
    """获取内容过滤器单例"""
    global _content_filter
    if _content_filter is None:
        _content_filter = ContentFilter()
    return _content_filter
