"""爬虫"未处理动态类型"事件记录/读取。

把爬虫遇到的无法解析的动态类型(新类型/B站格式变更 → 疑似漏抓)落到
data/scraper_skips.json, 供 monitoring.health 汇总成 WARN, 让每日体检邮件主动暴露
"静默漏抓"(而非只落 pm2 日志)。所有写入均为防御式: 任何异常都吞掉, 绝不影响抓取主流程。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "scraper_skips.json",
)


def record_skips(source: str, skips: dict) -> None:
    """爬虫发现未处理类型时调用。source=博主uid/平台, skips={类型key: 次数}。"""
    if not skips:
        return
    try:
        events = []
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, encoding="utf-8") as f:
                events = (json.load(f) or {}).get("events", [])
        events.append(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "source": str(source),
                "skips": {str(k): int(v) for k, v in skips.items()},
            }
        )
        cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        events = [e for e in events if e.get("ts", "") >= cutoff][-200:]
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"events": events}, f, ensure_ascii=False)
        os.replace(tmp, _STATE_FILE)
    except Exception as e:  # 绝不因记录失败影响抓取
        logger.debug(f"记录漏抓事件失败(忽略): {e}")


def recent_skips(hours: float = 26.0) -> list[dict]:
    """返回最近 hours 小时内的未处理类型事件, 供 health 读取。异常返回空列表。"""
    try:
        if not os.path.exists(_STATE_FILE):
            return []
        with open(_STATE_FILE, encoding="utf-8") as f:
            events = (json.load(f) or {}).get("events", [])
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        return [e for e in events if e.get("ts", "") >= cutoff]
    except Exception:
        return []
