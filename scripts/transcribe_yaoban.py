"""用本地 ASR 批量转写妖板选手视频 → 存 transcripts/ + 更新 DB。

跳过已有转写；SenseVoice INT8 负责下载、VAD 分段、转写和缓存。
下载限频防封号。
"""

import asyncio
import logging
import os
import sys

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stockagent.core.config import config
from stockagent.core.db import get_sessionmaker
from stockagent.core.models import Blogger, RawContent

logging.basicConfig(level=logging.WARNING, format="%(message)s")
UID = "3546957274417333"
OUT_DIR = "transcripts"


async def main():
    config.load()
    from stockagent.llm.local_asr import transcribe_video

    os.makedirs(OUT_DIR, exist_ok=True)
    cookie = config.get("scrapers", "bilibili", "cookies", default="")
    maker = await get_sessionmaker()
    async with maker() as s:
        b = (await s.execute(select(Blogger).where(Blogger.platform_user_id == UID))).scalar_one()
        rcs = list(
            (
                await s.execute(
                    select(RawContent)
                    .where(RawContent.blogger_id == b.id)
                    .order_by(RawContent.published_at.desc())
                )
            )
            .scalars()
            .all()
        )

    print(f"待处理 {len(rcs)} 个视频（转写存 {OUT_DIR}/）\n")
    total_chars = new_count = skip_count = 0
    for i, rc in enumerate(rcs):
        fpath = f"{OUT_DIR}/yaoban_{rc.content_id}.txt"
        if os.path.exists(fpath):
            skip_count += 1
            continue
        # DB 已有转写内容 → 直接存文件（避免重复 ASR）
        if len(rc.text) > len(rc.title) + 100:
            open(fpath, "w").write(rc.text)
            skip_count += 1
            print(f"[{i + 1}/{len(rcs)}] {rc.title[:30]} → DB已有，存文件")
            continue
        try:
            result = await transcribe_video(rc.content_id, cookie)
            text = str(result.get("text", "")).strip()
            if not text:
                raise RuntimeError(result.get("error", "未识别到语音"))
        except Exception as e:
            print(f"[{i + 1}/{len(rcs)}] {rc.title[:30]} → 转写失败: {e}")
            continue
        full = f"{rc.title}\n{text}"[:8000]
        open(fpath, "w").write(full)
        async with maker() as s2:
            r = await s2.get(RawContent, rc.id)
            r.text = full
            await s2.commit()
        total_chars += len(text)
        new_count += 1
        duration = float(result.get("duration_seconds", 0))
        flag = "短视频" if duration < 60 else f"{duration / 60:.1f}分钟"
        print(f"[{i + 1}/{len(rcs)}] {rc.title[:30]} → {len(text)}字 ({flag})")
    print(f"\n完成: 新增转写 {new_count} 个 ({total_chars}字), 跳过 {skip_count} 个")


if __name__ == "__main__":
    asyncio.run(main())
