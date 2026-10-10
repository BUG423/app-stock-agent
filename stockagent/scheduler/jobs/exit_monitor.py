"""盘中离场监控定时任务 —— 盯持仓, 触发即及时提醒卖出并平仓。"""

import logging

from stockagent.engine.exit_monitor import run_exit_monitor
from stockagent.market.trading_calendar import is_trading_time

logger = logging.getLogger(__name__)


async def exit_monitor_job(job_name: str = "exit_monitor") -> None:
    if not is_trading_time():
        logger.debug(f"[定时任务] {job_name} 非交易时间, 跳过")
        return
    try:
        stats = await run_exit_monitor()
        if stats.get("sold", 0) > 0:
            logger.info(f"[定时任务] {job_name} 卖出 {stats['sold']} 笔")
    except Exception as e:
        logger.error(f"[定时任务] {job_name} 失败: {e}", exc_info=True)
        try:
            from stockagent.notification.email_notifier import EmailNotifier

            await EmailNotifier().send_health_alert(
                component="盘中离场监控", error=str(e)[:200], detail=f"任务: {job_name}"
            )
        except Exception:
            pass
