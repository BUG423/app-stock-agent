"""晚间抓取+分析定时任务。"""

import asyncio
import logging

from stockagent.analysis.pipeline import AnalysisPipeline

logger = logging.getLogger(__name__)

# APScheduler 的 max_instances 只对同一个 job ID 生效；多个任务共用本函数时，
# 仍需要这一层进程内互斥，避免同一批动态被重复抓取/分析。
_pipeline_lock = asyncio.Lock()


async def evening_analysis_job(job_name: str = "evening_analysis") -> None:
    """每日晚间执行的抓取+分析任务。"""
    if _pipeline_lock.locked():
        logger.warning("[定时任务] %s 跳过：抓取分析流水线仍在执行", job_name)
        return

    logger.info(f"[定时任务] {job_name} 开始执行...")

    async with _pipeline_lock:
        try:
            pipeline = AnalysisPipeline()
            stats = await pipeline.run()
            logger.info(f"[定时任务] {job_name} 完成: {stats}")
        except Exception as e:
            logger.error(f"[定时任务] {job_name} 失败: {e}", exc_info=True)
            try:
                from stockagent.notification.email_notifier import EmailNotifier

                await EmailNotifier().send_health_alert(
                    component="抓取分析流水线",
                    error=str(e)[:200],
                    detail=f"任务: {job_name}",
                )
            except Exception:
                pass
