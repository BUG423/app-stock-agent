"""Extract screen text from one Bilibili video with local RapidOCR.

Usage: ``python3 scripts/ocr_slides.py BVxxxxxxxx [SEASON_ID]``
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stockagent.core.config import config
from stockagent.llm.ocr import ocr_bilibili_video


async def main() -> None:
    config.load()
    bvid = sys.argv[1]
    season = sys.argv[2] if len(sys.argv) > 2 else "8292379"
    cookie = config.get("scrapers", "bilibili", "cookies", default="")
    result = await ocr_bilibili_video(bvid, cookie_string=cookie)
    body = str(result.get("text", "")).strip()
    if not body:
        raise SystemExit(f"RapidOCR 未识别到文字: {result.get('error', '无有效画面文字')}")

    directory = f"transcripts/season_{season}"
    os.makedirs(directory, exist_ok=True)
    path = f"{directory}/{bvid}.txt"
    title = ""
    if os.path.exists(path):
        title = open(path, encoding="utf-8").read().split("\n", 1)[0]
    output = f"{title}\n【画面文字OCR】\n{body}" if title else body
    with open(path, "w", encoding="utf-8") as target:
        target.write(output)
    print(f"RapidOCR 得 {len(body)} 字 → {path}")


if __name__ == "__main__":
    asyncio.run(main())
