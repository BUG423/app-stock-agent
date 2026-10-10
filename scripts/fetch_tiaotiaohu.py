"""抓取跳跳虎复盘（B站 UID 21119247）全部历史动态标题+描述入库（供原文回查）。

复用妖板的动态 API 翻页范式（不需 wbi 签名，带 cookie 即可，限频防封号）。
只存标题+描述(analysis_status=PENDING)，不做 ASR（全量 ASR 太慢；跳跳虎标题信息量已很大）。
需要转写再走 scripts/transcribe_*.py。
"""
import asyncio
import logging
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from stockagent.core.config import config
from stockagent.core.db import get_sessionmaker
from stockagent.core.enums import AnalysisStatus, ContentType
from stockagent.core.models import Blogger, RawContent
from stockagent.scrapers.bilibili.scraper import BilibiliScraper

logging.basicConfig(level=logging.WARNING)
DYNAMIC_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
UID = "21119247"


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


async def fetch_dynamics(client, mid: str, max_pages: int = 50):
    all_items, offset = [], ""
    for _ in range(max_pages):
        params = {"host_mid": mid}
        if offset:
            params["offset"] = offset
        r = await client.get(DYNAMIC_API, params=params)
        d = r.json()
        if d.get("code") != 0:
            print(f"  动态API错误: {d.get('message')}")
            break
        data = d.get("data", {})
        items = data.get("items", [])
        all_items.extend(items)
        offset = data.get("offset", "")
        print(f"  本页 {len(items)} 条，累计 {len(all_items)}")
        if not offset or not items:
            break
        await asyncio.sleep(5)
    return all_items


async def main():
    config.load()
    scraper = BilibiliScraper()
    maker = await get_sessionmaker()

    async with maker() as session:
        b = (await session.execute(
            select(Blogger).where(Blogger.platform_user_id == UID)
        )).scalar_one_or_none()
        if not b:
            b = Blogger(
                name="跳跳虎复盘", platform="bilibili", platform_user_id=UID,
                profile_url=f"https://space.bilibili.com/{UID}", enabled=True,
                notes="科技主线趋势跟踪+择时复盘博主",
            )
            session.add(b)
            await session.commit()
            await session.refresh(b)
            print(f"已添加博主: 跳跳虎复盘 (id={b.id})")
        blogger_id = b.id

    print(f"\n抓取跳跳虎所有动态（翻页，限频 5s/页）...")
    items = await fetch_dynamics(scraper.client, UID, max_pages=50)
    print(f"共 {len(items)} 条动态\n")

    saved = 0
    async with maker() as session:
        for it in items:
            mod = it.get("modules") or {}
            md = mod.get("module_dynamic") or {}
            major = md.get("major") or {}
            mtype = major.get("type", "")
            author = mod.get("module_author") or {}
            pub_ts = author.get("pub_ts")
            try:
                pub_at = datetime.fromtimestamp(int(pub_ts)) if pub_ts else None
            except (TypeError, ValueError):
                pub_at = None

            if mtype == "MAJOR_TYPE_ARCHIVE":
                a = major.get("archive", {})
                bvid = a.get("bvid", "")
                title = _strip_html(a.get("title", ""))
                desc = a.get("desc", "") or ""
                ctype = ContentType.VIDEO
                url = f"https://www.bilibili.com/video/{bvid}"
                text = f"{title}\n{desc}".strip()
            elif mtype in ("MAJOR_TYPE_OPUS", "DYNAMIC_TYPE_WORD"):
                opus = major.get("opus") or {}
                title = _strip_html(opus.get("title", ""))
                summary = opus.get("summary", {}).get("text", "") if isinstance(opus.get("summary"), dict) else ""
                desc_text = (md.get("desc") or {}).get("text", "")
                text = f"{title}\n{summary}\n{desc_text}".strip()
                bvid = it.get("id_str", "")
                ctype = ContentType.ARTICLE
                url = f"https://www.bilibili.com/opus/{bvid}"
            else:
                continue

            if not bvid:
                continue
            exists = (await session.execute(
                select(RawContent).where(
                    RawContent.blogger_id == blogger_id, RawContent.content_id == bvid
                )
            )).scalar_one_or_none()
            if exists:
                continue

            rc = RawContent(
                blogger_id=blogger_id, content_id=bvid, title=title[:500],
                text=text[:8000], url=url, content_type=ctype,
                published_at=pub_at, analysis_status=AnalysisStatus.PENDING,
            )
            session.add(rc)
            saved += 1
            print(f"  [{pub_at.strftime('%m-%d %H:%M') if pub_at else '?'}] {title[:45]}")
        await session.commit()

    print(f"\n入库 {saved} 条新内容")
    await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
