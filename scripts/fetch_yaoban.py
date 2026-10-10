"""抓取妖板选手（B站 UID 3546957274417333）的视频，用动态 API（带 cookie，限频防封号）。

动态 API /x/polymer/web-dynamic/v1/feed/space 不需 wbi 签名，带 cookie 即可，
比 wbi 视频列表接口更稳（后者对该 UID 返回 -401）。
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
UID = "3546957274417333"


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


async def fetch_dynamics(client, mid: str, max_pages: int = 3):
    """翻页抓动态（视频/图文），每页间隔 5 秒防封号。"""
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
        await asyncio.sleep(5)  # 限频防封号
    return all_items


async def main():
    config.load()
    scraper = BilibiliScraper()
    maker = await get_sessionmaker()

    # 1. 确保博主记录存在
    async with maker() as session:
        b = (await session.execute(
            select(Blogger).where(Blogger.platform_user_id == UID)
        )).scalar_one_or_none()
        if not b:
            b = Blogger(
                name="妖板选手", platform="bilibili", platform_user_id=UID,
                profile_url=f"https://space.bilibili.com/{UID}", enabled=True,
                notes="妖股/龙头/打板实盘博主",
            )
            session.add(b)
            await session.commit()
            await session.refresh(b)
            print(f"已添加博主: 妖板选手 (id={b.id})")
        blogger_id = b.id

    # 2. 动态 API 抓视频（翻所有页）
    print(f"\n抓取妖板选手所有动态（翻所有页，限频 5s/页）...")
    items = await fetch_dynamics(scraper.client, UID, max_pages=50)
    print(f"共 {len(items)} 条动态\n")

    # 3. 解析视频/图文，入库
    saved = 0
    async with maker() as session:
        for it in items:
            mod = it.get("modules", {})
            major = mod.get("module_dynamic", {}).get("major", {})
            mtype = major.get("type", "")
            author = mod.get("module_author", {})
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
                opus = major.get("opus", {})
                title = _strip_html(opus.get("title", ""))
                summary = opus.get("summary", {}).get("text", "") if isinstance(opus.get("summary"), dict) else ""
                # 纯文字动态
                desc_text = mod.get("module_dynamic", {}).get("desc", {}).get("text", "")
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
