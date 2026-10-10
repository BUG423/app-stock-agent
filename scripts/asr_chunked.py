"""Transcribe one Bilibili video with local SenseVoice + Silero VAD.

Usage: ``python3 scripts/asr_chunked.py BVxxxxxxxx``
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockagent.core.config import config
from stockagent.llm.local_asr import LOCAL_ASR_CACHE_DIR, transcribe_video


async def run(bvid: str, refresh: bool) -> int:
    config.load()
    if refresh:
        cache_path = LOCAL_ASR_CACHE_DIR / f"{bvid}.json"
        cache_path.unlink(missing_ok=True)
    cookie = config.get("scrapers", "bilibili", "cookies", default="")
    result = await transcribe_video(bvid, cookie)
    metadata = {key: value for key, value in result.items() if key != "text"}
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(result.get("text", ""))
    return 0 if result.get("success") else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bvid")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.bvid, args.refresh)))


if __name__ == "__main__":
    main()
