"""Collect a month of "提前炒股早退休" posts and preserve OCR evidence.

This script intentionally stops before declaring a brokerage account authentic. It
collects timestamped source material and deterministic OCR number candidates for a
separate market-price and position-continuity audit.

Usage:
    PYTHONPATH=. python3 scripts/audit_early_retirement.py --days 35
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stockagent.analysis.ocr_stock_resolver import extract_ocr_market_evidence
from stockagent.core.config import config
from stockagent.llm.ocr import ocr_bilibili_video
from stockagent.scrapers.bilibili.scraper import BilibiliScraper

UID = "3707010912618582"
BLOGGER_NAME = "提前炒股早退休"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def _write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    os.replace(temporary, path)


async def collect(args: argparse.Namespace) -> dict[str, Any]:
    config.load()
    cutoff = datetime.now() - timedelta(days=args.days)
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output

    scraper = BilibiliScraper()
    try:
        posts = await scraper._scrape_videos_via_dynamic(UID, args.max_items)
    finally:
        await scraper.close()

    posts = [
        post
        for post in posts
        if post.published_at is not None
        and post.published_at.replace(tzinfo=None) >= cutoff
    ]
    posts.sort(key=lambda post: post.published_at or datetime.min)

    payload: dict[str, Any] = {
        "blogger": BLOGGER_NAME,
        "uid": UID,
        "collected_at": datetime.now(),
        "cutoff": cutoff,
        "requested_days": args.days,
        "post_count": len(posts),
        "posts": [],
    }
    cookie = config.get("scrapers", "bilibili", "cookies", default="")

    for index, post in enumerate(posts, start=1):
        row: dict[str, Any] = {
            "content_id": post.content_id,
            "content_type": post.content_type,
            "published_at": post.published_at,
            "title": post.title,
            "url": post.url,
            "description_text": post.text,
            "metadata": post.metadata,
        }
        if args.ocr and post.content_type == "video":
            result = await ocr_bilibili_video(
                post.content_id,
                cookie_string=cookie,
                cover_url=str(post.metadata.get("cover") or ""),
            )
            ocr_text = str(result.get("text") or "").strip()
            row["ocr"] = {
                "success": bool(result.get("success")),
                "text": ocr_text,
                "stock_codes": result.get("stock_codes") or [],
                "frame_count": int(result.get("frame_count") or 0),
                "cache_hit": bool(result.get("cache_hit")),
                "error": str(result.get("error") or ""),
                "market_evidence": extract_ocr_market_evidence(ocr_text),
            }
        payload["posts"].append(row)
        _write_snapshot(output, payload)
        status = "OCR" if row.get("ocr", {}).get("success") else "metadata"
        published = post.published_at.strftime("%Y-%m-%d %H:%M")
        print(f"[{index:02d}/{len(posts):02d}] {published} {post.content_id} {status} {post.title}")

    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=35, help="History window in calendar days")
    parser.add_argument("--max-items", type=int, default=80, help="Maximum dynamic items to fetch")
    parser.add_argument(
        "--output",
        default=f"data/audits/early_retirement_{datetime.now():%Y%m%d}.json",
        help="JSON evidence output path",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_false",
        dest="ocr",
        help="Collect post metadata without downloading videos",
    )
    parser.set_defaults(ocr=True)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    payload = asyncio.run(collect(parse_args()))
    print(f"完成：{payload['post_count']} 条动态")


if __name__ == "__main__":
    main()
