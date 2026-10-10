"""每日建议汇总器 —— 聚合所有博主的分析结果，生成邮件摘要。"""

import logging
from datetime import date

from sqlalchemy import select

from stockagent.core.db import get_sessionmaker
from stockagent.core.models import AnalysisResult, Blogger, MonitoredStock, RawContent

logger = logging.getLogger(__name__)


async def get_daily_summary(
    target_date: date | None = None,
) -> dict:
    """获取指定日期的投资建议汇总。

    返回按博主分组的分析结果，用于生成每日建议邮件。
    """
    if target_date is None:
        from stockagent.utils.time_utils import today_cst

        target_date = today_cst()

    maker = await get_sessionmaker()
    async with maker() as session:
        from sqlalchemy import func

        # 查询指定日期创建的分析结果（func.date 兼容 SQLite/PostgreSQL）
        stmt = (
            select(AnalysisResult)
            .where(func.date(AnalysisResult.created_at) == target_date)
            .order_by(AnalysisResult.created_at.desc())
        )
        result = await session.execute(stmt)
        analyses = result.scalars().all()

        summary = {
            "date": str(target_date),
            "total_analyses": len(analyses),
            "bloggers": {},
            "all_stocks": [],
            "market_sentiment": "neutral",
        }

        for analysis in analyses:
            blogger = await session.get(Blogger, analysis.blogger_id)
            blogger_name = blogger.name if blogger else "未知博主"

            if blogger_name not in summary["bloggers"]:
                summary["bloggers"][blogger_name] = []

            # 获取关联的监控股票
            stmt2 = select(MonitoredStock).where(MonitoredStock.analysis_result_id == analysis.id)
            result2 = await session.execute(stmt2)
            monitored = result2.scalars().all()

            # 博主真实发文时间（取自原始内容，非分析入库时间）
            raw = await session.get(RawContent, analysis.raw_content_id)
            published_at = (
                str(raw.published_at) if raw and raw.published_at else str(analysis.created_at)
            )

            item = {
                "analysis_id": analysis.id,
                "published_at": published_at,
                "title": raw.title if raw else "",
                "summary": analysis.summary,
                "direction": analysis.direction.value if analysis.direction else "neutral",
                "confidence": analysis.confidence.value if analysis.confidence else "medium",
                "time_horizon": analysis.time_horizon.value if analysis.time_horizon else "unknown",
                "market_sentiment": analysis.market_sentiment,
                "stocks": [
                    {
                        "code": s.stock_code,
                        "name": s.stock_name or s.stock_code,
                        "direction": s.direction.value if s.direction else "neutral",
                        "reason": s.reason or "",
                        "conditions": s.trigger_conditions,
                    }
                    for s in monitored
                ],
            }
            summary["bloggers"][blogger_name].append(item)

            for s in monitored:
                summary["all_stocks"].append(
                    {
                        "code": s.stock_code,
                        "name": s.stock_name or s.stock_code,
                        "direction": s.direction.value if s.direction else "neutral",
                        "blogger": blogger_name,
                    }
                )

        # 计算整体市场情绪
        sentiments = [a.market_sentiment for a in analyses if a.market_sentiment]
        if sentiments:
            bullish = sum(1 for s in sentiments if s == "bullish")
            bearish = sum(1 for s in sentiments if s == "bearish")
            if bullish > bearish:
                summary["market_sentiment"] = "bullish"
            elif bearish > bullish:
                summary["market_sentiment"] = "bearish"
            else:
                summary["market_sentiment"] = "neutral"

        return summary
