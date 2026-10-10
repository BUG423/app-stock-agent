"""原始内容通知的幂等与升级回归测试。"""

from pathlib import Path

import pytest
from sqlalchemy import func, select

import stockagent.core.db as db
from stockagent.core.base_notifier import NotificationMessage
from stockagent.core.config import config
from stockagent.core.models import Notification
from stockagent.notification.email_notifier import EmailNotifier


@pytest.fixture
async def sqlite_notification_db(tmp_path: Path, monkeypatch):
    await db.close_db()
    db._sessionmaker = None
    monkeypatch.setenv("USE_SQLITE", "1")
    monkeypatch.setenv("SQLITE_DIR", str(tmp_path))
    config.load()
    monkeypatch.setitem(config._data["notification"]["email"]["policy"], "new_content", True)
    await db.init_db()
    try:
        yield
    finally:
        await db.close_db()
        db._sessionmaker = None


def _message(subject: str, body: str) -> NotificationMessage:
    return NotificationMessage(
        type="daily_advice",
        recipient="test@example.com",
        subject=subject,
        body=body,
    )


@pytest.mark.asyncio
async def test_same_raw_content_is_sent_once(sqlite_notification_db, monkeypatch):
    notifier = EmailNotifier()
    delivered = []

    async def fake_send_raw(message):
        delivered.append(message.subject)
        return True

    monkeypatch.setattr(notifier, "_send_raw", fake_send_raw)

    assert await notifier.queue_and_send(
        _message("完整分析", "full"), related_type="raw_content", related_id="raw-720"
    )
    assert await notifier.queue_and_send(
        _message("完整分析（重复）", "duplicate"),
        related_type="raw_content",
        related_id="raw-720",
        replace_pending=True,
    )

    maker = await db.get_sessionmaker()
    async with maker() as session:
        count = await session.scalar(select(func.count(Notification.id)))
        notification = await session.scalar(select(Notification))

    assert count == 1
    assert notification.sent is True
    assert delivered == ["完整分析"]


@pytest.mark.asyncio
async def test_successful_analysis_upgrades_queued_fallback(sqlite_notification_db, monkeypatch):
    notifier = EmailNotifier()
    delivered = []

    async def fake_send_raw(message):
        delivered.append((message.subject, message.body))
        return True

    monkeypatch.setattr(notifier, "_send_raw", fake_send_raw)

    assert await notifier.queue_and_send(
        _message("新内容（原文）", "fallback"),
        related_type="raw_content",
        related_id="raw-720",
        defer_send=True,
    )
    assert delivered == []

    assert await notifier.queue_and_send(
        _message("新内容", "full"),
        related_type="raw_content",
        related_id="raw-720",
        replace_pending=True,
    )

    maker = await db.get_sessionmaker()
    async with maker() as session:
        count = await session.scalar(select(func.count(Notification.id)))
        notification = await session.scalar(select(Notification))

    assert count == 1
    assert notification.sent is True
    assert notification.subject == "新内容"
    assert notification.body == "full"
    assert delivered == [("新内容", "full")]


@pytest.mark.asyncio
async def test_same_intraday_report_is_queued_once(sqlite_notification_db, monkeypatch):
    notifier = EmailNotifier()
    delivered = []

    async def fake_send_raw(message):
        delivered.append(message.subject)
        return True

    monkeypatch.setattr(notifier, "_send_raw", fake_send_raw)

    assert await notifier.queue_and_send(
        _message("10:30汇总", "first"),
        related_type="intraday_report",
        related_id="2026-07-20",
    )
    assert await notifier.queue_and_send(
        _message("10:30汇总重复", "second"),
        related_type="intraday_report",
        related_id="2026-07-20",
    )

    maker = await db.get_sessionmaker()
    async with maker() as session:
        count = await session.scalar(select(func.count(Notification.id)))

    assert count == 1
    assert delivered == ["10:30汇总"]
