"""抓取 B站 UP主「合集(season)」的全部视频 → 本地 ASR → 存 transcripts/ + 入库。

用途：把妖板选手精选教学合集完整归档，供原文回查和人工核对提醒条件。
和 fetch_yaoban.py(抓全部日常动态流) 不同——本脚本只抓某个精选合集里的视频。

合集列表 API： /x/polymer/web-space/seasons_archives_list (带 cookie + wbi 签名, 翻页)
ASR：SenseVoice INT8 + Silero VAD，本机推理并缓存到 data/local_asr_cache/。

用法：
  python3 scripts/fetch_season.py list                 # 只列合集视频清单(不下载/不ASR)
  python3 scripts/fetch_season.py run                  # 列 + 逐个 ASR + 存 transcripts + 入库
  python3 scripts/fetch_season.py run --no-db          # 只存 transcripts, 不写 DB
  MID=... SEASON_ID=... python3 scripts/fetch_season.py list   # 抓别的合集
"""

import asyncio
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from stockagent.core.config import config
from stockagent.core.db import get_sessionmaker
from stockagent.core.enums import AnalysisStatus, ContentType
from stockagent.core.models import Blogger, RawContent
from stockagent.llm.local_asr import transcribe_video
from stockagent.scrapers.bilibili.scraper import BilibiliScraper, _strip_html, _wbi_sign

logging.basicConfig(level=logging.WARNING, format="%(message)s")

SEASON_API = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
MID = os.environ.get("MID", "3546957274417333")  # 妖板选手
SEASON_ID = os.environ.get("SEASON_ID", "8292379")  # 用户指定的精选合集
OUT_DIR = f"transcripts/season_{SEASON_ID}"
MIN_ASR_SEC = 40  # <40s 的引流短视频不值得 ASR


async def fetch_season_archives(scraper: BilibiliScraper, mid: str, season_id: str):
    """翻页抓合集全部视频。先试 wbi 签名，失败(-352)再裸试。返回 archive dict 列表。"""
    await scraper._fetch_wbi_keys()
    client = scraper.client
    all_arch, page = [], 1
    total = None
    while True:
        base = {
            "mid": mid,
            "season_id": season_id,
            "sort_reverse": "false",
            "page_num": str(page),
            "page_size": "30",
        }
        params = dict(base)
        if scraper._wbi_img_key and scraper._wbi_sub_key:
            params = _wbi_sign(params, scraper._wbi_img_key, scraper._wbi_sub_key)
        r = await client.get(SEASON_API, params=params)
        d = r.json()
        if d.get("code") != 0:
            # wbi 失败则裸试一次
            r = await client.get(SEASON_API, params=base)
            d = r.json()
        if d.get("code") != 0:
            print(f"  合集API错误 code={d.get('code')} msg={d.get('message')}")
            break
        data = d.get("data", {}) or {}
        arch = data.get("archives", []) or []
        all_arch.extend(arch)
        pg = data.get("page", {}) or {}
        total = pg.get("total", total)
        meta = data.get("meta", {}) or {}
        if page == 1 and meta:
            print(
                f"  合集《{meta.get('name', '?')}》total={total} desc={(meta.get('description') or '')[:60]}"
            )
        print(f"  第{page}页 {len(arch)} 条，累计 {len(all_arch)}/{total}")
        if not arch or (total is not None and len(all_arch) >= total):
            break
        page += 1
        await asyncio.sleep(3)
    return all_arch


def _fmt(ts):
    try:
        return datetime.fromtimestamp(int(ts))
    except (TypeError, ValueError):
        return None


async def do_list(scraper):
    arch = await fetch_season_archives(scraper, MID, SEASON_ID)
    print(f"\n合集共 {len(arch)} 个视频：\n")
    for i, a in enumerate(arch, 1):
        pub = _fmt(a.get("pubdate"))
        dur = a.get("duration", 0)
        print(
            f"  [{i:>2}] {pub.strftime('%Y-%m-%d') if pub else '?':10}  "
            f"{dur // 60:>2}分{dur % 60:02d}秒  {a.get('bvid')}  {a.get('title', '')[:50]}"
        )
    return arch


async def do_run(scraper, write_db: bool):
    arch = await do_list(scraper)
    os.makedirs(OUT_DIR, exist_ok=True)
    cookie = config.get("scrapers", "bilibili", "cookies", default="")

    blogger_id = None
    if write_db:
        maker = await get_sessionmaker()
        async with maker() as s:
            b = (
                await s.execute(select(Blogger).where(Blogger.platform_user_id == MID))
            ).scalar_one_or_none()
            if b:
                blogger_id = b.id
            else:
                print("  ⚠ 未找到该博主记录，本次仅存 transcripts 不写 DB")
                write_db = False

    done = skip = fail = 0
    for i, a in enumerate(arch, 1):
        bvid = a.get("bvid", "")
        title = _strip_html(a.get("title", ""))
        dur = int(a.get("duration", 0) or 0)
        pub = _fmt(a.get("pubdate"))
        fpath = f"{OUT_DIR}/{bvid}.txt"

        if os.path.exists(fpath) and os.path.getsize(fpath) > len(title) + 50:
            skip += 1
            print(f"[{i}/{len(arch)}] {title[:30]} → 已有转写，跳过")
            asr = open(fpath, encoding="utf-8").read()
        elif dur and dur < MIN_ASR_SEC:
            print(f"[{i}/{len(arch)}] {title[:30]} → {dur}s 短视频，跳过ASR")
            asr = ""
        else:
            asr_result = await transcribe_video(bvid, cookie)
            asr = str(asr_result.get("text", ""))
            if asr:
                full = f"{title}\n{asr}"
                open(fpath, "w", encoding="utf-8").write(full)
                done += 1
                print(f"[{i}/{len(arch)}] {title[:30]} → {len(asr)}字 ({dur // 60}分{dur % 60}秒)")
            else:
                fail += 1
                print(f"[{i}/{len(arch)}] {title[:30]} → ASR失败/无音频")

        if write_db and asr:
            maker = await get_sessionmaker()
            async with maker() as s:
                exists = (
                    await s.execute(
                        select(RawContent).where(
                            RawContent.blogger_id == blogger_id,
                            RawContent.content_id == bvid,
                        )
                    )
                ).scalar_one_or_none()
                text = f"{title}\n{asr}"[:8000]
                if exists:
                    if len(text) > len(exists.text or ""):
                        exists.text = text
                        exists.analysis_status = AnalysisStatus.PENDING
                else:
                    s.add(
                        RawContent(
                            blogger_id=blogger_id,
                            content_id=bvid,
                            title=title[:500],
                            text=text,
                            url=f"https://www.bilibili.com/video/{bvid}",
                            content_type=ContentType.VIDEO,
                            published_at=pub,
                            analysis_status=AnalysisStatus.PENDING,
                        )
                    )
                await s.commit()
        await asyncio.sleep(1)

    print(f"\n完成：新转写 {done}，跳过 {skip}，失败 {fail}。transcripts → {OUT_DIR}/")


async def main():
    config.load()
    scraper = BilibiliScraper()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    try:
        if cmd == "list":
            await do_list(scraper)
        elif cmd == "run":
            await do_run(scraper, write_db="--no-db" not in sys.argv)
        else:
            print(__doc__)
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
