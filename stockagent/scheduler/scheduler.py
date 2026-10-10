"""APScheduler 定时任务调度器。"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from stockagent.core.config import config
from stockagent.scheduler.jobs.evening_scrape import evening_analysis_job
from stockagent.scheduler.jobs.exit_monitor import exit_monitor_job
from stockagent.scheduler.jobs.health_check import health_check_job
from stockagent.scheduler.jobs.intraday_report_job import intraday_report_job
from stockagent.scheduler.jobs.intraday_watch_job import intraday_watch_job
from stockagent.scheduler.jobs.market_monitor import market_monitor_job
from stockagent.scheduler.jobs.notification_sender import send_queued_notifications
from stockagent.scheduler.jobs.post_market_review_job import post_market_review_job

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def create_scheduler() -> AsyncIOScheduler:
    """创建并配置定时任务调度器。"""
    global _scheduler

    scheduler_cfg = config.get("scheduler", default={})
    timezone = scheduler_cfg.get("timezone", "Asia/Shanghai")

    # job_defaults: 慢任务不堆积报错——重叠时跳过(max_instances=1)、错过合并成一次(coalesce)、
    # 5分钟内错过仍补跑(misfire_grace_time)。配合scraper跳过旧视频, 每轮抓取只处理新内容。
    scheduler = AsyncIOScheduler(
        timezone=timezone,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )

    # 晚间抓取分析；可每天运行以接住周末发帖。
    evening_cfg = scheduler_cfg.get("jobs", {}).get("evening_analysis", {})
    if evening_cfg.get("enabled", True):
        scheduler.add_job(
            evening_analysis_job,
            trigger=CronTrigger.from_crontab(evening_cfg.get("cron", "0 20 * * *")),
            id="evening_analysis",
            name="晚间抓取+分析",
            replace_existing=True,
            kwargs={"job_name": "evening_analysis"},
            jitter=60,
        )
        logger.info(f"已注册任务: 晚间分析 ({evening_cfg.get('cron', '0 20 * * *')})")

    # 盘中监控任务 (默认每5分钟，交易时间)
    monitor_cfg = scheduler_cfg.get("jobs", {}).get("market_monitor", {})
    if monitor_cfg.get("enabled", True):
        scheduler.add_job(
            market_monitor_job,
            trigger=CronTrigger.from_crontab(monitor_cfg.get("cron", "*/5 9-14 * * mon-fri")),
            id="market_monitor",
            name="盘中条件监控",
            replace_existing=True,
            kwargs={"job_name": "market_monitor"},
            jitter=60,
        )
        logger.info(f"已注册任务: 盘中监控 ({monitor_cfg.get('cron', '*/5 9-14 * * mon-fri')})")

    # 日间抓取+分析任务（默认每10分钟，含真人博主动态抓取）
    # 注意：不要设成每分钟——每次都会全量抓取+LLM过滤+分析，既烧 LLM 额度又易触发 B站风控。
    fetch_cfg = scheduler_cfg.get("jobs", {}).get("frequent_fetch", {})
    if fetch_cfg.get("enabled", True):
        fetch_cron = fetch_cfg.get("cron", "*/10 9-14 * * *")
        scheduler.add_job(
            evening_analysis_job,
            trigger=CronTrigger.from_crontab(fetch_cron),
            id="frequent_fetch",
            name="盘中定时抓取分析",
            replace_existing=True,
            kwargs={"job_name": "frequent_fetch"},
            jitter=60,
        )
        logger.info(f"已注册任务: 盘中抓取 ({fetch_cron})")

    # 盘中离场监控（默认每10分钟，含AI持仓评估）—— 及时提醒卖出
    exit_cfg = scheduler_cfg.get("jobs", {}).get("exit_monitor", {})
    if exit_cfg.get("enabled", True):
        exit_cron = exit_cfg.get("cron", "*/10 9-14 * * mon-fri")
        scheduler.add_job(
            exit_monitor_job,
            trigger=CronTrigger.from_crontab(exit_cron),
            id="exit_monitor",
            name="盘中离场监控",
            replace_existing=True,
            kwargs={"job_name": "exit_monitor"},
            jitter=60,
        )
        logger.info(f"已注册任务: 盘中离场监控 ({exit_cron})")

    # 盘中分时线盯盘（默认每5分钟，交易时段）—— 候选股持续踩破分时线即预警
    iw_cfg = scheduler_cfg.get("jobs", {}).get("intraday_watch", {})
    if iw_cfg.get("enabled", True):
        iw_cron = iw_cfg.get("cron", "*/5 9-14 * * mon-fri")
        scheduler.add_job(
            intraday_watch_job,
            trigger=CronTrigger.from_crontab(iw_cron),
            id="intraday_watch",
            name="盘中分时线盯盘",
            replace_existing=True,
            kwargs={"job_name": "intraday_watch"},
            jitter=30,
        )
        logger.info(f"已注册任务: 盘中分时线盯盘 ({iw_cron})")

    # 10:30 盘中汇总：同一股票合并，只发一封满足/不满足清单。
    report_cfg = scheduler_cfg.get("jobs", {}).get("intraday_report", {})
    if report_cfg.get("enabled", True):
        report_cron = report_cfg.get("cron", "30 10 * * mon-fri")
        scheduler.add_job(
            intraday_report_job,
            trigger=CronTrigger.from_crontab(report_cron),
            id="intraday_report",
            name="10:30 盘中盯盘汇总",
            replace_existing=True,
            kwargs={"job_name": "intraday_report"},
        )
        logger.info(f"已注册任务: 10:30 盘中盯盘汇总 ({report_cron})")

    # 盘后复盘（默认每交易日 15:05，汇总候选股是否符合博主预期，邮件发用户）
    eod_cfg = scheduler_cfg.get("jobs", {}).get("post_market_review", {})
    if eod_cfg.get("enabled", True):
        eod_cron = eod_cfg.get("cron", "5 15 * * mon-fri")
        scheduler.add_job(
            post_market_review_job,
            trigger=CronTrigger.from_crontab(eod_cron),
            id="post_market_review",
            name="盘后复盘",
            replace_existing=True,
            kwargs={"job_name": "post_market_review"},
            jitter=30,
        )
        logger.info(f"已注册任务: 盘后复盘 ({eod_cron})")

    # 邮件队列发送（默认每2分钟，持续运行，补发崩溃期间未发送的邮件）
    notif_cfg = scheduler_cfg.get("jobs", {}).get("notification_sender", {})
    if notif_cfg.get("enabled", True):
        notif_cron = notif_cfg.get("cron", "*/2 * * * *")
        scheduler.add_job(
            send_queued_notifications,
            trigger=CronTrigger.from_crontab(notif_cron),
            id="notification_sender",
            name="邮件队列发送",
            replace_existing=True,
            kwargs={"job_name": "notification_sender"},
        )
        logger.info(f"已注册任务: 邮件队列发送 ({notif_cron})")

    health_cfg = scheduler_cfg.get("jobs", {}).get("health_check", {})
    if health_cfg.get("enabled", True):
        health_cron = health_cfg.get("cron", "*/30 * * * *")
        scheduler.add_job(
            health_check_job,
            trigger=CronTrigger.from_crontab(health_cron),
            id="health_check",
            name="系统健康巡检",
            replace_existing=True,
            kwargs={"job_name": "health_check"},
        )
        logger.info(f"已注册任务: 系统健康巡检 ({health_cron})")

    _scheduler = scheduler
    return scheduler


async def run_scheduler() -> None:
    # 日志可视化: 不配置的话 INFO 级日志(抓取/选股/邮件/复核)全部不可见,
    # 07-07 两次故障(15:10选股崩溃/7.7盘前晚抓)都因此只能盲查。输出到 stdout 由 pm2 收集。
    import sys as _sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=_sys.stdout,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    """启动调度器（阻塞运行）。"""
    config.load()
    from stockagent.core.db import init_db

    await init_db()
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("定时任务调度器已启动")

    try:
        # 保持运行
        while True:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                # Python 3.14: CancelledError 不再被 Exception 捕获，单独处理避免进程退出
                logger.warning("主循环 sleep 被取消，自动恢复中...")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"调度器主循环异常: {e}", exc_info=True)
                # 短暂等待后继续，避免紧密循环
                await asyncio.sleep(5)
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器正在关闭...")
        scheduler.shutdown(wait=True)
        logger.info("调度器已关闭")


def get_scheduler() -> AsyncIOScheduler | None:
    """获取当前调度器实例。"""
    return _scheduler
