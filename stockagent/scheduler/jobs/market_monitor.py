"""盘中条件监控定时任务。"""

import logging

from stockagent.engine.matcher import ConditionMatchingEngine
from stockagent.market.trading_calendar import is_trading_time

logger = logging.getLogger(__name__)


async def market_monitor_job(job_name: str = "market_monitor") -> None:
    """每5分钟执行一次的条件匹配检查。"""
    if not is_trading_time():
        logger.debug(f"[定时任务] {job_name} 当前非交易时间，跳过")
        return

    logger.debug(f"[定时任务] {job_name} 开始检查...")

    try:
        engine = ConditionMatchingEngine()
        stats = await engine.run()
        if stats.get("triggered", 0) > 0:
            logger.info(f"[定时任务] {job_name} 触发 {stats['triggered']} 个信号!")
    except Exception as e:
        logger.error(f"[定时任务] {job_name} 失败: {e}", exc_info=True)
        try:
            from stockagent.notification.email_notifier import EmailNotifier

            await EmailNotifier().send_health_alert(
                component="盘中条件监控",
                error=str(e)[:200],
                detail=f"任务: {job_name}",
            )
        except Exception:
            pass
