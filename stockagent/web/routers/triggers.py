"""手动触发 API 路由。"""

import logging

from fastapi import APIRouter

from stockagent.core.schemas import TriggerResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/scrape-all", response_model=TriggerResponse)
async def trigger_scrape_all():
    """手动触发抓取所有博主并分析。"""
    try:
        from stockagent.analysis.pipeline import AnalysisPipeline

        pipeline = AnalysisPipeline()
        stats = await pipeline.run()
        return TriggerResponse(
            success=True,
            message="流水线执行完成",
            details=stats,
        )
    except Exception as e:
        logger.error(f"手动触发流水线失败: {e}", exc_info=True)
        return TriggerResponse(success=False, message=str(e))


@router.post("/analyze", response_model=TriggerResponse)
async def trigger_analyze():
    """手动触发分析未处理的原始内容。"""
    # 此端点会重新分析所有 PENDING 状态的 raw_contents
    try:
        from stockagent.analysis.pipeline import AnalysisPipeline

        pipeline = AnalysisPipeline()
        stats = await pipeline.run()
        return TriggerResponse(
            success=True,
            message="分析完成",
            details=stats,
        )
    except Exception as e:
        return TriggerResponse(success=False, message=str(e))


@router.post("/monitor", response_model=TriggerResponse)
async def trigger_monitor():
    """手动触发一次条件匹配检查。"""
    try:
        from stockagent.engine.matcher import ConditionMatchingEngine

        engine = ConditionMatchingEngine()
        stats = await engine.run()
        return TriggerResponse(
            success=True,
            message="条件匹配完成",
            details=stats,
        )
    except Exception as e:
        return TriggerResponse(success=False, message=str(e))
