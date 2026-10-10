"""生产健康状态聚合：抓取新鲜度、本地媒体识别与通知队列。"""

import importlib.util
import shutil
from datetime import timedelta

from sqlalchemy import func, select

from stockagent.core.config import config
from stockagent.core.db import get_sessionmaker
from stockagent.core.models import Blogger, Notification
from stockagent.core.skip_tracker import recent_skips
from stockagent.llm.local_asr import local_asr_status
from stockagent.utils.time_utils import now_cst

_ACTIVE_SOURCE_START_HOUR = 8
_ACTIVE_SOURCE_STARTUP_GRACE_MINUTES = 30


def _source_stale_after(now) -> timedelta:
    """Return the source freshness window, allowing the first morning fetch to finish."""
    in_startup_grace = (
        now.hour == _ACTIVE_SOURCE_START_HOUR
        and now.minute < _ACTIVE_SOURCE_STARTUP_GRACE_MINUTES
    )
    active_hours = _ACTIVE_SOURCE_START_HOUR <= now.hour <= 23 and not in_startup_grace
    return timedelta(minutes=30 if active_hours else 12 * 60)


async def collect_health() -> dict:
    now = now_cst()
    issues: list[dict] = []
    maker = await get_sessionmaker()
    async with maker() as session:
        bloggers = (
            (
                await session.execute(
                    select(Blogger).where(Blogger.enabled.is_(True), Blogger.platform == "bilibili")
                )
            )
            .scalars()
            .all()
        )
        pending = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.sent.is_(False), Notification.dead_letter.is_(False)
                )
            )
        ).scalar() or 0
        dead_letters = (
            await session.execute(
                select(func.count(Notification.id)).where(Notification.dead_letter.is_(True))
            )
        ).scalar() or 0
    stale_after = _source_stale_after(now)
    sources = []
    for blogger in bloggers:
        last = blogger.last_scraped_at
        if last and last.tzinfo is None:
            age = now.replace(tzinfo=None) - last
        else:
            age = now - last if last else None
        stale = age is None or age > stale_after
        sources.append(
            {
                "name": blogger.name,
                "last_scraped_at": blogger.last_scraped_at,
                "stale": stale,
            }
        )
        if stale:
            issues.append({"code": "source_stale", "message": f"{blogger.name} 抓取已停滞"})

    skips = recent_skips(26)
    if skips:
        issues.append({"code": "unknown_dynamic", "message": f"发现{len(skips)}批未识别B站动态"})

    asr_status = local_asr_status()
    if asr_status["enabled"] and not asr_status["available"]:
        missing = "、".join(asr_status["missing"])
        issues.append({"code": "local_asr_missing", "message": f"本地ASR依赖缺失：{missing}"})
    if pending:
        issues.append({"code": "mail_pending", "message": f"有{pending}封邮件等待发送"})
    if dead_letters:
        issues.append({"code": "mail_dead", "message": f"有{dead_letters}封邮件进入死信"})

    video_ocr_cfg = config.get("scrapers", "bilibili", "video_ocr", default={})
    video_ocr_enabled = bool(
        isinstance(video_ocr_cfg, dict)
        and video_ocr_cfg.get("enabled")
        and video_ocr_cfg.get("user_ids")
    )
    rapidocr_available = all(
        importlib.util.find_spec(module) is not None
        for module in ("rapidocr", "onnxruntime", "yt_dlp")
    ) and all(shutil.which(command) is not None for command in ("ffmpeg", "ffprobe"))
    if video_ocr_enabled and not rapidocr_available:
        issues.append({"code": "rapidocr_missing", "message": "本地视频OCR依赖不完整"})

    return {
        "status": "healthy" if not issues else "degraded",
        "checked_at": now,
        "issues": issues,
        "sources": sources,
        "unknown_dynamic_batches": len(skips),
        "local_asr_enabled": asr_status["enabled"],
        "local_asr_available": asr_status["available"],
        "local_asr_model": asr_status["model_id"],
        "pending_notifications": pending,
        "dead_letter_notifications": dead_letters,
        "rapidocr_available": rapidocr_available,
        "bilibili_cookie_configured": bool(
            config.get("scrapers", "bilibili", "cookies", default="")
        ),
    }
