"""邮件策略、统一格式及盘中按股票去重测试。"""

from types import SimpleNamespace

import pytest

from stockagent.core.config import config
from stockagent.core.enums import TradeAction
from stockagent.notification.email_format import standard_subject
from stockagent.notification.email_notifier import EmailNotifier, notification_scope_id
from stockagent.scheduler.jobs.intraday_report_job import _merge_candidates, _report_html
from stockagent.scheduler.jobs.post_market_review_job import _review_html


def _candidate(record_id: str, reason: str):
    return SimpleNamespace(
        id=record_id,
        stock_code="603159",
        stock_name="上海亚虹",
        trigger_conditions=[{"description": reason}],
    )


def test_intraday_candidates_are_deduplicated_by_stock_code():
    rows = [
        (_candidate("watch-1", "持仓观察"), SimpleNamespace(name="提前炒股早退休")),
        (_candidate("watch-2", "等待企稳"), SimpleNamespace(name="提前炒股早退休")),
    ]

    merged = _merge_candidates(rows)

    assert len(merged) == 1
    assert merged[0]["code"] == "603159"
    assert merged[0]["bloggers"] == ["提前炒股早退休"]
    assert merged[0]["conditions"] == ["持仓观察", "等待企稳"]


def test_report_subjects_and_layout_are_standardized():
    assert standard_subject("盘中盯盘", "2026-07-20", "仍满足 1｜已不满足 1") == (
        "[StockAgent][盘中盯盘] 2026-07-20｜仍满足 1｜已不满足 1"
    )
    midday = _report_html(
        "2026-07-20",
        [{"name": "甲", "code": "000001", "bloggers": ["博主"], "change_pct": 1, "note": "正常", "conditions": []}],
        [{"name": "乙", "code": "000002", "bloggers": ["博主"], "change_pct": -2, "note": "破位", "conditions": []}],
        [],
    )
    eod = _review_html(
        "2026-07-20",
        {
            "total": 1,
            "graded": 1,
            "met": 1,
            "unmet": 0,
            "watch_only": 0,
            "met_rate": 100.0,
            "results": [
                {
                    "name": "甲",
                    "code": "000001",
                    "blogger": "博主",
                    "change_pct": 1.0,
                    "met": True,
                    "note": "符合",
                }
            ],
        },
    )

    for body, category in ((midday, "盘中盯盘"), (eod, "盘后总结")):
        assert "StockAgent · " + category in body
        assert "2026-07-20 · 北京时间" in body
        assert "仅供信息跟踪，不构成投资建议" in body


@pytest.mark.asyncio
async def test_buy_signal_email_is_suppressed_by_default(monkeypatch):
    if not config._loaded:
        config.load()
    monkeypatch.setitem(config._data["notification"]["email"]["policy"], "buy_signals", False)
    notifier = EmailNotifier()

    async def should_not_send(_message):
        raise AssertionError("购入信号不应调用 SMTP")

    monkeypatch.setattr(notifier, "_send_raw", should_not_send)
    signal = SimpleNamespace(
        action=TradeAction.BUY,
        stock_code="603159",
        stock_name="上海亚虹",
        current_price=12.3,
        id="signal-1",
    )

    assert await notifier.send_signal_alert(signal) is True


def test_intraday_break_scope_is_per_day_and_stock():
    first = notification_scope_id("2026-07-20", "603159")
    duplicate = notification_scope_id("2026-07-20", "603159")
    next_day = notification_scope_id("2026-07-21", "603159")

    assert first == duplicate
    assert first != next_day
    assert len(first) == 12
