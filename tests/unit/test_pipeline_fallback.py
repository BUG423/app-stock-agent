"""分析服务不可用时的原文通知降级测试。"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from stockagent.analysis.pipeline import (
    _build_raw_content_email,
    _first_configured_recipient,
    _notify_raw_content_fallback,
    _persist_local_verified_direct,
)
from stockagent.core.enums import AnalysisStatus
from stockagent.core.models import AnalysisResult, MonitoredStock
from stockagent.notification import email_notifier


def test_first_configured_recipient_skips_empty_placeholder():
    notifier = SimpleNamespace(_to=["", "  ", "user@example.com", "cc@example.com"])

    assert _first_configured_recipient(notifier) == "user@example.com"


def test_raw_content_email_explicitly_avoids_trading_judgment():
    body = _build_raw_content_email("测试博主", "最新动态", "https://example.test", "原始内容")

    assert "原始内容" in body
    assert "不生成标的或交易判断" in body


class _CheckSession:
    def __init__(self, existing=None):
        self.existing = existing

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, _query):
        return self.existing

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_raw_content_fallback_queues_once_with_nonempty_recipient(monkeypatch):
    captured = {}

    class FakeNotifier:
        def __init__(self):
            self._to = ["", "user@example.com"]

        async def queue_and_send(self, message, related_type, related_id, **kwargs):
            captured.update(
                message=message,
                related_type=related_type,
                related_id=related_id,
                kwargs=kwargs,
            )
            return True

    monkeypatch.setattr(email_notifier, "EmailNotifier", FakeNotifier)
    maker = lambda: _CheckSession()  # noqa: E731
    blogger = SimpleNamespace(name="测试博主")
    content = SimpleNamespace(
        title="最新视频",
        text="OCR 原文",
        url="https://example.test/video",
    )

    sent = await _notify_raw_content_fallback(maker, blogger, content, "raw-1")

    assert sent is True
    assert captured["message"].recipient == "user@example.com"
    assert captured["related_type"] == "raw_content"
    assert captured["related_id"] == "raw-1"
    assert captured["kwargs"]["defer_send"] is True


@pytest.mark.asyncio
async def test_existing_unsent_notification_is_upgraded_to_blogger_profile():
    existing = SimpleNamespace(sent=False, subject="旧主题", body="旧正文")
    maker = lambda: _CheckSession(existing)  # noqa: E731
    blogger = SimpleNamespace(
        name="跳跳虎复盘",
        platform_user_id="21119247",
    )
    content = SimpleNamespace(
        title="7.17盘前",
        text="等待回踩五日线",
        url="https://example.test/post",
        metadata={},
    )

    sent = await _notify_raw_content_fallback(maker, blogger, content, "raw-1")

    assert sent is None
    assert existing.subject.startswith("[StockAgent][博主动态]")
    assert "跳跳舞 · 条件触发型" in existing.body
    assert "必须先满足本条原文条件" in existing.body


class _WriteSession:
    def __init__(self):
        self.added = []

    async def scalar(self, _query):
        return None

    def add(self, obj):
        if isinstance(obj, AnalysisResult):
            obj.id = "analysis-1"
        self.added.append(obj)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_local_verified_direct_persists_analysis_and_monitor_without_llm():
    session = _WriteSession()
    raw = SimpleNamespace(
        id="raw-1",
        extra_data=None,
        analysis_status=AnalysisStatus.PENDING,
    )
    blogger = SimpleNamespace(
        id="blogger-1",
        name="提前炒股早退休",
        platform_user_id="3707010912618582",
    )
    content = SimpleNamespace(
        published_at=datetime(2026, 7, 17, 14, 30),
        metadata={
            "verified_stock": {
                "code": "605028",
                "name": "世茂能源",
                "confidence": "high",
                "evidence": ["名称与价格交叉确认"],
            }
        },
    )

    result = await _persist_local_verified_direct(session, raw, blogger, content)

    assert result["mention"]["code"] == "605028"
    analysis = next(item for item in session.added if isinstance(item, AnalysisResult))
    monitored = next(item for item in session.added if isinstance(item, MonitoredStock))
    assert analysis.llm_provider == "local_evidence"
    assert analysis.stock_mentions[0]["signal_type"] == "follow_now"
    assert monitored.stock_code == "605028"
    assert monitored.signal_type == "follow_now"
    assert monitored.trigger_conditions == []
    assert raw.analysis_status == AnalysisStatus.COMPLETED
