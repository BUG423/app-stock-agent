"""本地 OCR：RapidOCR 识别图片，并为 B 站短视频抽帧识别画面文字。"""

import asyncio
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from stockagent.core.config import config

logger = logging.getLogger(__name__)

STOCK_CODE_PATTERN = re.compile(r"(?<!\d)([034689]\d{5})(?!\d)")
_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
OCR_CACHE_DIR = _PROJECT_ROOT / "data" / "ocr_cache"
OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
OCR_PIPELINE_VERSION = 2

_ocr_engine: Any | None = None
_ocr_engine_lock = threading.Lock()
_ocr_call_lock = threading.Lock()


def _video_ocr_config() -> dict[str, Any]:
    if not config._loaded:
        return {}
    value = config.get("scrapers", "bilibili", "video_ocr", default={})
    return value if isinstance(value, dict) else {}


def _bypass_proxy() -> bool:
    if not config._loaded:
        return True
    return bool(config.get("scrapers", "bilibili", "bypass_proxy", default=True))


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine
    with _ocr_engine_lock:
        if _ocr_engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as exc:
                raise RuntimeError("RapidOCR 未安装，请安装 rapidocr 和 onnxruntime") from exc
            _ocr_engine = RapidOCR()
    return _ocr_engine


def _extract_ocr_lines(result: Any, min_confidence: float = 0.55) -> list[str]:
    """兼容 RapidOCR 3.x 输出，并过滤低置信度空行。"""
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)

    # 兼容旧版 `(rows, elapsed)` 结构，便于已有缓存环境平滑升级。
    if texts is None and isinstance(result, tuple) and result:
        rows = result[0]
        if isinstance(rows, list):
            parsed: list[tuple[str, float]] = []
            for row in rows:
                if isinstance(row, (list, tuple)) and len(row) >= 3:
                    parsed.append((str(row[1]), float(row[2])))
            texts = [item[0] for item in parsed]
            scores = [item[1] for item in parsed]

    if not texts:
        return []
    if not scores:
        scores = [1.0] * len(texts)

    lines: list[str] = []
    for raw_text, raw_score in zip(texts, scores):
        text = re.sub(r"\s+", " ", str(raw_text or "")).strip()
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            score = 0.0
        if text and score >= min_confidence:
            lines.append(text)
    return lines


def _merge_unique_texts(texts: list[str]) -> str:
    """按出现顺序合并多帧文字，去掉完全重复的 OCR 行。"""
    seen: set[str] = set()
    merged: list[str] = []
    for block in texts:
        for raw_line in (block or "").splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            key = line.replace(" ", "").casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(line)
    return "\n".join(merged)


def _result_from_text(text: str, **extra: Any) -> dict[str, Any]:
    stock_codes = list(dict.fromkeys(STOCK_CODE_PATTERN.findall(text)))
    return {
        "text": text,
        "stock_codes": stock_codes,
        "success": bool(text.strip()),
        **extra,
    }


def _recognize_image_sync(image: Any, min_confidence: float) -> dict[str, Any]:
    engine = _get_ocr_engine()
    with _ocr_call_lock:
        result = engine(image)
    lines = _extract_ocr_lines(result, min_confidence=min_confidence)
    return _result_from_text("\n".join(lines), line_count=len(lines))


async def ocr_image_url(image_url: str) -> dict[str, Any]:
    """下载图片并在本机用 RapidOCR 识别，不调用外部视觉 API。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com/",
    }
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers=headers,
            trust_env=not _bypass_proxy(),
        ) as client:
            response = await client.get(image_url)
            response.raise_for_status()
            return await ocr_image_bytes(response.content)
    except Exception as exc:
        logger.error("OCR 下载图片失败: %s", exc)
        return {"text": "", "stock_codes": [], "success": False, "error": str(exc)}


async def ocr_image_bytes(image_bytes: bytes) -> dict[str, Any]:
    """对图片字节做本地 OCR。"""
    try:
        min_confidence = float(_video_ocr_config().get("min_confidence", 0.55))
        return await asyncio.to_thread(_recognize_image_sync, image_bytes, min_confidence)
    except Exception as exc:
        logger.error("OCR 识别失败: %s", exc)
        return {"text": "", "stock_codes": [], "success": False, "error": str(exc)}


def _cache_path(bvid: str) -> Path:
    safe_bvid = re.sub(r"[^A-Za-z0-9_-]", "_", bvid)
    return OCR_CACHE_DIR / f"{safe_bvid}.json"


def _read_video_cache(bvid: str, failure_retry_seconds: int) -> dict[str, Any] | None:
    path = _cache_path(bvid)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if data.get("pipeline_version") != OCR_PIPELINE_VERSION:
        return None
    if data.get("success"):
        data["cache_hit"] = True
        return data
    created_at = float(data.get("created_at", 0))
    if time.time() - created_at < failure_retry_seconds:
        data["cache_hit"] = True
        return data
    return None


def _write_video_cache(bvid: str, result: dict[str, Any]) -> None:
    path = _cache_path(bvid)
    payload = {
        **result,
        "pipeline_version": OCR_PIPELINE_VERSION,
        "created_at": time.time(),
        "cache_hit": False,
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    if _bypass_proxy():
        for key in _PROXY_ENV_KEYS:
            env.pop(key, None)
    return env


def _write_cookie_file(cookie_string: str, directory: Path) -> Path | None:
    if not cookie_string:
        return None
    path = directory / "bilibili.cookies.txt"
    lines = ["# Netscape HTTP Cookie File"]
    for pair in cookie_string.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{key}\t{value}")
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o600)
    return path


def _download_bilibili_video(bvid: str, cookie_string: str, directory: Path) -> Path:
    output_template = str(directory / "video.%(ext)s")
    cookie_file = _write_cookie_file(cookie_string, directory)
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--no-part",
        "--no-continue",
        "--socket-timeout",
        "20",
        "--retries",
        "2",
        "--max-filesize",
        "100M",
        "-f",
        "bestvideo[vcodec^=avc1][height<=1080]/bestvideo[height<=1080]/best[height<=1080]/best",
        "-o",
        output_template,
    ]
    if cookie_file:
        command.extend(["--cookies", str(cookie_file)])
    command.append(f"https://www.bilibili.com/video/{bvid}")

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        env=_subprocess_env(),
    )
    candidates = [
        path
        for path in directory.glob("video.*")
        if path.is_file() and path.suffix not in {".part", ".ytdl"}
    ]
    if completed.returncode != 0 or not candidates:
        detail = (completed.stderr or completed.stdout or "下载结果为空")[-300:].strip()
        raise RuntimeError(f"yt-dlp 下载失败: {detail}")
    return max(candidates, key=lambda item: item.stat().st_size)


def _sample_video_frames(video_path: Path, interval_seconds: float, max_frames: int) -> list[Any]:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_subprocess_env(),
    )
    try:
        duration = max(0.0, float(probe.stdout.strip())) if probe.returncode == 0 else 0.0
    except ValueError:
        duration = 0.0

    sample_count = (
        min(max_frames, max(1, math.ceil(duration / interval_seconds)))
        if duration > 0
        else max_frames
    )
    frame_rate = sample_count / duration if duration > 0 else 1.0 / interval_seconds
    frame_dir = video_path.parent / "ocr-frames"
    frame_dir.mkdir(exist_ok=True)
    output_template = frame_dir / "frame-%03d.jpg"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps={frame_rate:.8f}",
            "-frames:v",
            str(sample_count),
            "-q:v",
            "2",
            str(output_template),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env=_subprocess_env(),
    )
    frame_paths = sorted(frame_dir.glob("frame-*.jpg"))[:max_frames]
    if completed.returncode != 0 or not frame_paths:
        detail = (completed.stderr or completed.stdout or "抽帧结果为空")[-300:].strip()
        raise RuntimeError(f"ffmpeg 视频抽帧失败: {detail}")
    return [path.read_bytes() for path in frame_paths]


def _ocr_bilibili_video_sync(
    bvid: str,
    cookie_string: str,
    interval_seconds: float,
    max_frames: int,
    min_confidence: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"stockagent-ocr-{bvid}-") as temp_dir:
        video_path = _download_bilibili_video(bvid, cookie_string, Path(temp_dir))
        frames = _sample_video_frames(video_path, interval_seconds, max_frames)
        texts = [_recognize_image_sync(frame, min_confidence).get("text", "") for frame in frames]
    text = _merge_unique_texts(texts)
    return _result_from_text(text, frame_count=len(frames), source="video_frames")


async def ocr_bilibili_video(
    bvid: str,
    cookie_string: str = "",
    cover_url: str = "",
) -> dict[str, Any]:
    """OCR B站视频封面和均匀抽取的画面帧，结果按 BV 号持久缓存。"""
    ocr_cfg = _video_ocr_config()
    failure_retry_seconds = int(ocr_cfg.get("failure_retry_seconds", 3600))
    cached = _read_video_cache(bvid, failure_retry_seconds)
    if cached is not None:
        return cached

    interval_seconds = max(0.5, float(ocr_cfg.get("frame_interval_seconds", 3.0)))
    max_frames = max(1, int(ocr_cfg.get("max_frames", 8)))
    min_confidence = float(ocr_cfg.get("min_confidence", 0.55))
    blocks: list[str] = []
    cover_result: dict[str, Any] | None = None
    video_result: dict[str, Any] | None = None
    errors: list[str] = []

    if cover_url:
        cover_result = await ocr_image_url(cover_url)
        if cover_result.get("success"):
            blocks.append(cover_result.get("text", ""))
        elif cover_result.get("error"):
            errors.append(f"封面: {cover_result['error']}")

    try:
        video_result = await asyncio.to_thread(
            _ocr_bilibili_video_sync,
            bvid,
            cookie_string,
            interval_seconds,
            max_frames,
            min_confidence,
        )
        if video_result.get("success"):
            blocks.append(video_result.get("text", ""))
    except Exception as exc:
        logger.warning("视频画面 OCR 失败 %s: %s", bvid, exc)
        errors.append(f"视频: {exc}")

    text = _merge_unique_texts(blocks)
    result = _result_from_text(
        text,
        frame_count=int((video_result or {}).get("frame_count", 0)),
        cover_ocr=bool((cover_result or {}).get("success")),
        source="cover_and_video_frames",
        cache_hit=False,
    )
    if errors and not result["success"]:
        result["error"] = "; ".join(errors)[:500]
    _write_video_cache(bvid, result)
    return result


async def ocr_video_frame(video_url: str) -> dict[str, Any]:
    """兼容旧入口：B站视频 URL 自动转到本地视频 OCR。"""
    match = re.search(r"(BV[A-Za-z0-9]+)", video_url)
    if not match:
        return {
            "text": "",
            "stock_codes": [],
            "success": False,
            "error": "仅支持包含 BV 号的 B站视频 URL",
        }
    return await ocr_bilibili_video(match.group(1))
