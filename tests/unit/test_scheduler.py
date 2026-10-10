"""调度器交易日范围回归测试。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from stockagent.core.config import config
from stockagent.scheduler.scheduler import create_scheduler


def test_market_jobs_run_monday_to_friday_and_skip_saturday():
    if not config._loaded:
        config.load()
    scheduler = create_scheduler()
    timezone = ZoneInfo("Asia/Shanghai")
    friday_night = datetime(2026, 7, 17, 23, 0, tzinfo=timezone)

    for job_id in (
        "market_monitor",
        "exit_monitor",
        "intraday_watch",
        "intraday_report",
        "post_market_review",
    ):
        job = scheduler.get_job(job_id)
        next_run = job.trigger.get_next_fire_time(None, friday_night)

        assert next_run is not None
        assert next_run.date().isoformat() == "2026-07-20"
        assert next_run.weekday() == 0


def test_intraday_report_runs_once_at_1030():
    if not config._loaded:
        config.load()
    scheduler = create_scheduler()
    job = scheduler.get_job("intraday_report")

    assert job is not None
    assert str(job.trigger) == "cron[month='*', day='*', day_of_week='mon-fri', hour='10', minute='30']"


def test_content_fetch_and_evening_analysis_do_not_overlap_by_default():
    if not config._loaded:
        config.load()
    scheduler = create_scheduler()
    timezone = ZoneInfo("Asia/Shanghai")
    checkpoint = datetime(2026, 7, 19, 19, 55, tzinfo=timezone)

    frequent = scheduler.get_job("frequent_fetch")
    evening = scheduler.get_job("evening_analysis")
    frequent_next = frequent.trigger.get_next_fire_time(None, checkpoint)
    evening_next = evening.trigger.get_next_fire_time(None, checkpoint)

    assert frequent_next is not None and frequent_next.hour <= 19
    assert evening_next is not None and evening_next.hour >= 20
