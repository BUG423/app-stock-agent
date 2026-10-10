"""RapidOCR 本地图片/视频文字提取回归测试。"""

from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from stockagent.analysis.pipeline import _guard_ocr_stock_mentions
from stockagent.core.config import config
from stockagent.llm import ocr
from stockagent.scrapers.bilibili import scraper as bilibili_scraper
from stockagent.scrapers.bilibili.scraper import BilibiliScraper


def test_extract_rapidocr_lines_filters_low_confidence():
    result = SimpleNamespace(
        txts=("中际旭创 300308", "噪声", "  回踩五日线  "),
        scores=(0.99, 0.2, 0.88),
    )

    lines = ocr._extract_ocr_lines(result, min_confidence=0.55)

    assert lines == ["中际旭创 300308", "回踩五日线"]


def test_merge_video_frames_deduplicates_repeated_lines():
    text = ocr._merge_unique_texts(
        [
            "持仓：中际旭创 300308\n今日盈利 2%",
            "持仓：中际旭创 300308\n止损：跌破五日线",
        ]
    )

    assert text.splitlines() == [
        "持仓：中际旭创 300308",
        "今日盈利 2%",
        "止损：跌破五日线",
    ]
    assert ocr._result_from_text(text)["stock_codes"] == ["300308"]


def test_sample_video_frames_uses_ffmpeg_for_av1_compatible_decode(tmp_path, monkeypatch):
    video_path = tmp_path / "silent-av1.mp4"
    video_path.write_bytes(b"video")

    def fake_run(command, **_kwargs):
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout="12.0\n", stderr="")
        frame_dir = tmp_path / "ocr-frames"
        (frame_dir / "frame-001.jpg").write_bytes(b"frame-one")
        (frame_dir / "frame-002.jpg").write_bytes(b"frame-two")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)

    frames = ocr._sample_video_frames(video_path, interval_seconds=3.0, max_frames=8)

    assert frames == [b"frame-one", b"frame-two"]


def test_video_ocr_success_cache_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_CACHE_DIR", tmp_path)
    ocr._write_video_cache(
        "BV-test",
        {"success": True, "text": "浪潮信息 000977", "stock_codes": ["000977"]},
    )

    cached = ocr._read_video_cache("BV-test", failure_retry_seconds=3600)

    assert cached is not None
    assert cached["cache_hit"] is True
    assert cached["stock_codes"] == ["000977"]


def test_video_ocr_cache_from_old_pipeline_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_CACHE_DIR", tmp_path)
    (tmp_path / "BV-old.json").write_text(
        '{"success": true, "text": "旧版零帧缓存", "created_at": 1}',
        encoding="utf-8",
    )

    assert ocr._read_video_cache("BV-old", failure_retry_seconds=3600) is None


def test_ocr_guard_drops_obscured_name_and_keeps_visible_evidence():
    content = SimpleNamespace(
        content_type="video",
        text="【视频画面OCR】\n世茂\n万科A\n中际旭创 300308",
        metadata={"has_ocr": True, "ocr_stock_codes": ["300308"]},
    )
    mentions = [
        {"name": "世茂能源", "code": "605028"},
        {"name": "世茂", "code": "600823"},
        {"name": "万科A", "code": "000002"},
        {"name": "中际旭创", "code": "300308"},
    ]

    guarded = _guard_ocr_stock_mentions(content, mentions)

    assert guarded == [
        {"name": "万科A", "code": "unknown"},
        {"name": "中际旭创", "code": "300308"},
    ]


def test_silent_video_ocr_guard_rejects_partial_name_completion():
    content = SimpleNamespace(
        content_type="video",
        text="【视频画面OCR】\n神州数\n现价 27.460",
        metadata={
            "has_ocr": True,
            "ocr_stock_codes": [],
            "ocr_fallback_reason": "no_audio_stream",
        },
    )

    guarded = _guard_ocr_stock_mentions(
        content,
        [{"name": "神州数", "code": "000034"}],
    )

    assert guarded == []


def test_ocr_guard_accepts_only_matching_high_confidence_external_verification():
    content = SimpleNamespace(
        content_type="video",
        text="【视频画面OCR】\n世茂\n源\n10700\n24.940",
        metadata={
            "has_ocr": True,
            "ocr_stock_codes": [],
            "verified_stock": {
                "code": "605028",
                "name": "世茂能源",
                "confidence": "high",
                "evidence": ["名称和价格交叉确认"],
            },
        },
    )

    guarded = _guard_ocr_stock_mentions(
        content,
        [
            {"name": "世茂能源", "code": "605028"},
            {"name": "*ST世茂", "code": "600823"},
        ],
    )

    assert guarded == [
        {
            "name": "世茂能源",
            "code": "605028",
            "resolution_confidence": "high",
            "resolution_evidence": ["名称和价格交叉确认"],
        }
    ]


@pytest.mark.asyncio
async def test_target_short_video_combines_local_ocr_and_local_asr(monkeypatch):
    if not config._loaded:
        config.load()
    scraper = BilibiliScraper.__new__(BilibiliScraper)
    scraper._cookies_str = ""
    scraper._transcribe_videos = True
    scraper._video_ocr_enabled = True
    scraper._video_ocr_user_ids = {"3707010912618582"}
    monkeypatch.setattr(scraper, "_is_recent_for_media_enrichment", lambda _published: True)

    calls = {"ocr": 0, "asr": 0}

    async def fake_ocr(**kwargs):
        calls["ocr"] += 1
        assert kwargs["bvid"] == "BV1short"
        return {
            "success": True,
            "text": "今日持仓：中际旭创 300308\n跌破五日线止损",
            "stock_codes": ["300308"],
            "frame_count": 4,
            "cache_hit": False,
        }

    async def fake_asr(*_args, **_kwargs):
        calls["asr"] += 1
        return {
            "success": True,
            "text": "今天继续持有。",
            "source": "sensevoice_local",
            "model_id": "sensevoice-small-int8-2024-07-17",
            "segment_count": 1,
            "cache_hit": False,
            "duration_seconds": 12.0,
            "elapsed_seconds": 1.2,
        }

    monkeypatch.setattr(bilibili_scraper, "ocr_bilibili_video", fake_ocr)
    monkeypatch.setattr(bilibili_scraper, "transcribe_video", fake_asr)

    text, metadata = await scraper._enrich_video_text(
        user_id="3707010912618582",
        bvid="BV1short",
        title="10个起步，实盘记录第十四天",
        description="",
        cover_url="https://example.test/cover.jpg",
        duration_seconds=12,
        published_at=None,
    )

    assert calls == {"ocr": 1, "asr": 1}
    assert "【视频画面OCR｜只认完整可见文字，遮挡内容不得猜测】" in text
    assert "300308" in text
    assert "【视频语音内容】" in text
    assert "今天继续持有" in text
    assert metadata == {
        "has_asr": True,
        "has_ocr": True,
        "ocr_stock_codes": ["300308"],
        "ocr_frame_count": 4,
        "ocr_cache_hit": False,
        "asr_source": "sensevoice_local",
        "asr_model": "sensevoice-small-int8-2024-07-17",
        "asr_segment_count": 1,
        "asr_cache_hit": False,
        "asr_duration_seconds": 12.0,
        "asr_elapsed_seconds": 1.2,
    }


@pytest.mark.asyncio
async def test_target_long_video_combines_local_ocr_and_local_asr(monkeypatch):
    if not config._loaded:
        config.load()
    scraper = BilibiliScraper.__new__(BilibiliScraper)
    scraper._cookies_str = ""
    scraper._transcribe_videos = True
    scraper._video_ocr_enabled = True
    scraper._video_ocr_user_ids = {"3707010912618582"}
    monkeypatch.setattr(scraper, "_is_recent_for_media_enrichment", lambda _published: True)

    async def fake_ocr(**_kwargs):
        return {
            "success": True,
            "text": "世茂能源 605028",
            "stock_codes": ["605028"],
            "frame_count": 4,
            "cache_hit": False,
        }

    async def fake_asr(*_args, **_kwargs):
        return {
            "success": True,
            "text": "今天继续持有，跌破二十四元离场。",
            "source": "sensevoice_local",
            "model_id": "sensevoice-small-int8-2024-07-17",
            "segment_count": 2,
            "cache_hit": False,
            "duration_seconds": 65.0,
            "elapsed_seconds": 8.2,
        }

    monkeypatch.setattr(bilibili_scraper, "ocr_bilibili_video", fake_ocr)
    monkeypatch.setattr(bilibili_scraper, "transcribe_video", fake_asr)

    text, metadata = await scraper._enrich_video_text(
        user_id="3707010912618582",
        bvid="BV1long",
        title="实盘记录",
        description="",
        cover_url="https://example.test/cover.jpg",
        duration_seconds=65,
        published_at=None,
    )

    assert "【视频画面OCR｜只认完整可见文字，遮挡内容不得猜测】" in text
    assert "世茂能源 605028" in text
    assert "【视频语音内容】" in text
    assert "跌破二十四元离场" in text
    assert metadata["has_ocr"] is True
    assert metadata["has_asr"] is True
    assert metadata["asr_source"] == "sensevoice_local"
    assert metadata["asr_segment_count"] == 2


@pytest.mark.asyncio
async def test_old_target_video_skips_media_download(monkeypatch):
    if not config._loaded:
        config.load()
    scraper = BilibiliScraper.__new__(BilibiliScraper)
    scraper._cookies_str = ""
    scraper._transcribe_videos = True
    scraper._video_ocr_enabled = True
    scraper._video_ocr_user_ids = {"3707010912618582"}
    monkeypatch.setattr(scraper, "_is_recent_for_media_enrichment", lambda _published: False)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("旧视频不应下载或调用识别服务")

    monkeypatch.setattr(bilibili_scraper, "ocr_bilibili_video", forbidden)
    monkeypatch.setattr(bilibili_scraper, "transcribe_video", forbidden)

    text, metadata = await scraper._enrich_video_text(
        user_id="3707010912618582",
        bvid="BV1old",
        title="旧实盘",
        description="旧内容",
        cover_url="",
        duration_seconds=12,
        published_at=None,
    )

    assert text == "旧实盘\n旧内容"
    assert metadata == {"has_asr": False, "has_ocr": False}


@pytest.mark.asyncio
async def test_silent_video_uses_ocr_fallback_for_other_bloggers(monkeypatch):
    if not config._loaded:
        config.load()
    scraper = BilibiliScraper.__new__(BilibiliScraper)
    scraper._cookies_str = ""
    scraper._transcribe_videos = True
    scraper._video_ocr_enabled = True
    scraper._video_ocr_user_ids = {"3707010912618582"}
    monkeypatch.setattr(scraper, "_is_recent_for_media_enrichment", lambda _published: True)

    async def fake_asr(*_args, **_kwargs):
        return {
            "success": False,
            "text": "",
            "error": "视频不含音轨",
            "error_code": "no_audio_stream",
        }

    async def fake_ocr(**_kwargs):
        return {
            "success": True,
            "text": "景旺电子 603228\n回踩五日线不破",
            "stock_codes": ["603228"],
            "frame_count": 4,
            "cache_hit": False,
        }

    monkeypatch.setattr(bilibili_scraper, "transcribe_video", fake_asr)
    monkeypatch.setattr(bilibili_scraper, "ocr_bilibili_video", fake_ocr)

    text, metadata = await scraper._enrich_video_text(
        user_id="3546957274417333",
        bvid="BV1silent",
        title="低吸两只新票",
        description="",
        cover_url="",
        duration_seconds=12,
        published_at=None,
    )

    assert "景旺电子 603228" in text
    assert metadata["has_asr"] is False
    assert metadata["has_ocr"] is True
    assert metadata["asr_error_code"] == "no_audio_stream"
    assert metadata["ocr_fallback_reason"] == "no_audio_stream"
