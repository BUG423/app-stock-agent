"""10:30 盯盘邮件从数据库到模拟 SMTP 的隔离全链路测试。"""

from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

import stockagent.core.db as db
from stockagent.core.config import config
from stockagent.core.enums import (
    AnalysisStatus,
    ConfidenceLevel,
    ContentType,
    Direction,
    MonitorStatus,
    TimeHorizon,
)
from stockagent.core.models import AnalysisResult, Blogger, MonitoredStock, Notification, RawContent
from stockagent.notification.email_notifier import EmailNotifier
from stockagent.scheduler.jobs.intraday_report_job import intraday_report_job
from stockagent.scheduler.jobs.post_market_review_job import post_market_review_job


@pytest.fixture
async def report_db(tmp_path: Path, monkeypatch):
    await db.close_db()
    db._sessionmaker = None
    monkeypatch.setenv("USE_SQLITE", "1")
    monkeypatch.setenv("SQLITE_DIR", str(tmp_path))
    config.load()
    await db.init_db()
    try:
        yield
    finally:
        await db.close_db()
        db._sessionmaker = None


def _series(prices: list[float]) -> list[dict]:
    return [{"price": price, "volume": 1000} for price in prices]


async def _add_candidate(session, blogger, *, content_id, code, name, condition):
    raw = RawContent(
        blogger_id=blogger.id,
        content_id=content_id,
        title="7/20",
        text="测试内容",
        url=f"https://example.test/{content_id}",
        content_type=ContentType.VIDEO,
        published_at=datetime(2026, 7, 20, 9, 30),
        analysis_status=AnalysisStatus.COMPLETED,
        filter_status="useful",
    )
    session.add(raw)
    await session.flush()
    analysis = AnalysisResult(
        raw_content_id=raw.id,
        blogger_id=blogger.id,
        summary="测试分析",
        stock_mentions=[],
        conditions=[],
        time_horizon=TimeHorizon.NEXT_DAY,
        confidence=ConfidenceLevel.HIGH,
        direction=Direction.BULLISH,
        llm_model="test",
        llm_provider="test",
    )
    session.add(analysis)
    await session.flush()
    session.add(
        MonitoredStock(
            analysis_result_id=analysis.id,
            stock_code=code,
            stock_name=name,
            direction=Direction.BULLISH,
            reason="测试理由",
            signal_type="conditional",
            trigger_conditions=[{"description": condition}],
            monitor_date=date(2026, 7, 20),
            status=MonitorStatus.PENDING,
        )
    )


@pytest.mark.asyncio
async def test_intraday_report_db_to_smtp_is_deduplicated(report_db, monkeypatch):
    maker = await db.get_sessionmaker()
    async with maker() as session:
        blogger = Blogger(
            name="提前炒股早退休",
            platform="bilibili",
            platform_user_id="integration-test",
            profile_url="https://example.test/blogger",
            enabled=True,
        )
        session.add(blogger)
        await session.flush()
        await _add_candidate(
            session,
            blogger,
            content_id="video-1",
            code="603159",
            name="上海亚虹",
            condition="持仓观察",
        )
        await _add_candidate(
            session,
            blogger,
            content_id="video-2",
            code="603159",
            name="上海亚虹",
            condition="等待企稳",
        )
        await _add_candidate(
            session,
            blogger,
            content_id="video-3",
            code="000001",
            name="平安银行",
            condition="站稳分时线",
        )
        await session.commit()

    class FakeClient:
        def get_intraday(self, code):
            if code == "603159":
                return _series([11.0] * 10 + [10.8 - i * 0.03 for i in range(30)])
            return _series([10 + i * 0.02 for i in range(40)])

        def get_realtime_quote(self, code):
            return {"change_pct": -2.5 if code == "603159" else 1.2}

    import stockagent.market.stock_client as stock_client
    import stockagent.utils.time_utils as time_utils

    monkeypatch.setattr(stock_client, "get_stock_client", lambda: FakeClient())
    monkeypatch.setattr(time_utils, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(time_utils, "today_cst", lambda: date(2026, 7, 20))
    delivered = []

    async def fake_send_raw(_self, message):
        delivered.append(message)
        return True

    monkeypatch.setattr(EmailNotifier, "_send_raw", fake_send_raw)

    first = await intraday_report_job("integration_test")
    second = await intraday_report_job("integration_test_repeat")

    async with maker() as session:
        count = await session.scalar(select(func.count(Notification.id)))
        notification = await session.scalar(select(Notification))

    assert first == {"sent": True, "met": 1, "unmet": 1, "unknown": 0}
    # 首次汇总已将破位标的标记为 EXPIRED；重复汇总不应继续把它列为当前不满足。
    assert second == {"sent": True, "met": 1, "unmet": 0, "unknown": 0}
    assert count == 1
    assert len(delivered) == 1
    assert notification.subject == "[StockAgent][盘中盯盘] 2026-07-20｜仍满足 1｜已不满足 1"
    assert notification.body.count("<b>上海亚虹</b>") == 1
    assert "持仓观察；等待企稳" in notification.body
    assert "<b>平安银行</b>" in notification.body

    await post_market_review_job("integration_eod")
    await post_market_review_job("integration_eod_repeat")

    async with maker() as session:
        notifications = (
            (await session.execute(select(Notification).order_by(Notification.created_at)))
            .scalars()
            .all()
        )

    assert len(notifications) == 2
    assert len(delivered) == 2
    assert notifications[1].subject == (
        "[StockAgent][盘后总结] 2026-07-20｜符合 1｜未达 1｜观望 0"
    )
    assert notifications[1].body.count("上海亚虹(603159)") == 1
    assert notifications[1].body.count("平安银行(000001)") == 1
