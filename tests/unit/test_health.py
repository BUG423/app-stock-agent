"""Health-check freshness boundary regression tests."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stockagent.core.health import _source_stale_after


def test_source_freshness_allows_first_morning_fetch_to_finish():
    timezone = ZoneInfo("Asia/Shanghai")

    assert _source_stale_after(datetime(2026, 7, 18, 7, 59, tzinfo=timezone)) == timedelta(
        hours=12
    )
    assert _source_stale_after(datetime(2026, 7, 18, 8, 0, tzinfo=timezone)) == timedelta(
        hours=12
    )
    assert _source_stale_after(datetime(2026, 7, 18, 8, 29, tzinfo=timezone)) == timedelta(
        hours=12
    )


def test_source_freshness_becomes_strict_after_morning_grace():
    timezone = ZoneInfo("Asia/Shanghai")

    assert _source_stale_after(datetime(2026, 7, 18, 8, 30, tzinfo=timezone)) == timedelta(
        minutes=30
    )
    assert _source_stale_after(datetime(2026, 7, 18, 23, 59, tzinfo=timezone)) == timedelta(
        minutes=30
    )
    assert _source_stale_after(datetime(2026, 7, 19, 0, 0, tzinfo=timezone)) == timedelta(
        hours=12
    )
