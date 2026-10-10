"""列出某 B站 UP 主的全部合集(season)与系列(series)，用于确认是否有其它含技巧的合集。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stockagent.core.config import config
from stockagent.scrapers.bilibili.scraper import BilibiliScraper, _wbi_sign

MID = os.environ.get("MID", "3546957274417333")
API = "https://api.bilibili.com/x/polymer/web-space/seasons_series_list"


async def main():
    config.load()
    scraper = BilibiliScraper()
    await scraper._fetch_wbi_keys()
    client = scraper.client
    page = 1
    try:
        while True:
            base = {"mid": MID, "page_num": str(page), "page_size": "20"}
            params = dict(base)
            if scraper._wbi_img_key and scraper._wbi_sub_key:
                params = _wbi_sign(params, scraper._wbi_img_key, scraper._wbi_sub_key)
            r = await client.get(API, params=params)
            d = r.json()
            if d.get("code") != 0:
                r = await client.get(API, params=base)
                d = r.json()
            if d.get("code") != 0:
                print(f"错误 code={d.get('code')} msg={d.get('message')}"); break
            data = d.get("data", {}) or {}
            il = data.get("items_lists", {}) or {}
            seasons = il.get("seasons_list", []) or []
            series = il.get("series_list", []) or []
            for s in seasons:
                m = s.get("meta", {})
                print(f"[合集season] id={m.get('season_id')} 共{m.get('total')}个 《{m.get('name')}》")
            for s in series:
                m = s.get("meta", {})
                print(f"[系列series] id={m.get('series_id')} 共{m.get('total')}个 《{m.get('name')}》")
            pg = il.get("page", {}) or data.get("page", {}) or {}
            total = pg.get("total", 0)
            if page * 20 >= total or (not seasons and not series):
                break
            page += 1
            await asyncio.sleep(2)
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
