"""Local Bilibili video transcription with SenseVoice INT8 and Silero VAD."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from stockagent.core.config import config

logger = logging.getLogger(__name__)

MODEL_ID = "sensevoice-small-int8-2024-07-17"
ASR_PIPELINE_VERSION = 3
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ASR_CACHE_DIR = _PROJECT_ROOT / "data" / "local_asr_cache"
LOCAL_ASR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_PROCESS_LOCK_PATH = Path("/tmp/stockagent-local-asr.lock")
_THREAD_LOCK = threading.Lock()
_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


class NoAudioStreamError(RuntimeError):
    """The downloaded video is valid but does not contain an audio stream."""


@dataclass(frozen=True)
class LocalASRSettings:
    enabled: bool
    model_path: Path
    tokens_path: Path
    vad_model_path: Path
    num_threads: int
    failure_retry_seconds: int
    lock_timeout_seconds: int
    max_audio_megabytes: int
    max_text_chars: int
    bypass_proxy: bool
    vad_threshold: float
    min_silence_seconds: float
    min_speech_seconds: float
    max_speech_seconds: float


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _local_asr_config() -> dict[str, Any]:
    if not config._loaded:
        return {}
    value = config.get("scrapers", "bilibili", "local_asr", default={})
    return value if isinstance(value, dict) else {}


def _settings() -> LocalASRSettings:
    value = _local_asr_config()
    model_dir = _resolve_project_path(value.get("model_dir", "data/models/sensevoice"))
    return LocalASRSettings(
        enabled=bool(value.get("enabled", True)),
        model_path=_resolve_project_path(value.get("model_path", model_dir / "model.int8.onnx")),
        tokens_path=_resolve_project_path(value.get("tokens_path", model_dir / "tokens.txt")),
        vad_model_path=_resolve_project_path(
            value.get("vad_model_path", model_dir / "silero_vad.onnx")
        ),
        num_threads=max(1, int(value.get("num_threads", 1))),
        failure_retry_seconds=max(0, int(value.get("failure_retry_seconds", 3600))),
        lock_timeout_seconds=max(1, int(value.get("lock_timeout_seconds", 900))),
        max_audio_megabytes=max(10, int(value.get("max_audio_megabytes", 120))),
        max_text_chars=max(1000, int(value.get("max_text_chars", 16000))),
        bypass_proxy=bool(value.get("bypass_proxy", True)),
        vad_threshold=float(value.get("vad_threshold", 0.25)),
        min_silence_seconds=float(value.get("min_silence_seconds", 0.8)),
        min_speech_seconds=float(value.get("min_speech_seconds", 0.25)),
        max_speech_seconds=float(value.get("max_speech_seconds", 25.0)),
    )


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def local_asr_status() -> dict[str, Any]:
    """Return a cheap readiness check without loading the ONNX model."""
    settings = _settings()
    checks = {
        "sherpa_onnx": _module_available("sherpa_onnx"),
        "numpy": _module_available("numpy"),
        "yt_dlp": _module_available("yt_dlp"),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "model": settings.model_path.is_file(),
        "tokens": settings.tokens_path.is_file(),
        "vad_model": settings.vad_model_path.is_file(),
    }
    missing = [name for name, available in checks.items() if not available]
    return {
        "enabled": settings.enabled,
        "available": settings.enabled and not missing,
        "model_id": MODEL_ID,
        "model_dir": str(settings.model_path.parent),
        "missing": missing,
    }


def _cache_path(bvid: str) -> Path:
    safe_bvid = re.sub(r"[^A-Za-z0-9_-]", "_", bvid)
    return LOCAL_ASR_CACHE_DIR / f"{safe_bvid}.json"


def _read_cache(bvid: str, failure_retry_seconds: int) -> dict[str, Any] | None:
    try:
        value = json.loads(_cache_path(bvid).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if value.get("model_id") != MODEL_ID or value.get("pipeline_version") != ASR_PIPELINE_VERSION:
        return None
    if value.get("success"):
        return {**value, "cache_hit": True}
    created_at = float(value.get("created_at", 0))
    if time.time() - created_at < failure_retry_seconds:
        return {**value, "cache_hit": True}
    return None


def _write_cache(bvid: str, result: dict[str, Any]) -> None:
    path = _cache_path(bvid)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **result,
        "model_id": MODEL_ID,
        "pipeline_version": ASR_PIPELINE_VERSION,
        "created_at": time.time(),
        "cache_hit": False,
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def _subprocess_env(bypass_proxy: bool) -> dict[str, str]:
    env = os.environ.copy()
    if bypass_proxy:
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


def _download_audio(
    bvid: str,
    cookie_string: str,
    directory: Path,
    settings: LocalASRSettings,
) -> Path:
    output_template = str(directory / "audio.%(ext)s")
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
        f"{settings.max_audio_megabytes}M",
        "-f",
        "bestaudio/best",
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
        timeout=240,
        env=_subprocess_env(settings.bypass_proxy),
    )
    candidates = [
        path
        for path in directory.glob("audio.*")
        if path.is_file() and path.suffix not in {".part", ".ytdl"}
    ]
    if completed.returncode != 0 or not candidates:
        detail = (completed.stderr or completed.stdout or "下载结果为空")[-300:].strip()
        raise RuntimeError(f"yt-dlp 下载音频失败: {detail}")
    return max(candidates, key=lambda item: item.stat().st_size)


def _convert_to_wav(
    audio_path: Path,
    directory: Path,
    settings: LocalASRSettings,
) -> Path:
    wav_path = directory / "audio.wav"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=_subprocess_env(settings.bypass_proxy),
    )
    if completed.returncode != 0 or not wav_path.is_file():
        detail = (completed.stderr or completed.stdout or "转码结果为空")[-300:].strip()
        if "Output file does not contain any stream" in detail:
            raise NoAudioStreamError("视频不含音轨")
        raise RuntimeError(f"ffmpeg 音频转码失败: {detail}")
    return wav_path


def _create_recognizer(settings: LocalASRSettings):
    import sherpa_onnx

    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(settings.model_path),
        tokens=str(settings.tokens_path),
        num_threads=settings.num_threads,
        use_itn=True,
        debug=False,
    )


def _create_vad(settings: LocalASRSettings):
    import sherpa_onnx

    vad_config = sherpa_onnx.VadModelConfig()
    vad_config.silero_vad.model = str(settings.vad_model_path)
    vad_config.silero_vad.threshold = settings.vad_threshold
    vad_config.silero_vad.min_silence_duration = settings.min_silence_seconds
    vad_config.silero_vad.min_speech_duration = settings.min_speech_seconds
    vad_config.silero_vad.max_speech_duration = settings.max_speech_seconds
    vad_config.sample_rate = 16000
    vad_config.num_threads = settings.num_threads
    detector = sherpa_onnx.VoiceActivityDetector(vad_config, buffer_size_in_seconds=60)
    return detector, int(vad_config.silero_vad.window_size)


def _decode_wav(wav_path: Path, settings: LocalASRSettings) -> dict[str, Any]:
    import numpy as np

    recognizer = _create_recognizer(settings)
    vad, window_size = _create_vad(settings)
    texts: list[str] = []
    segment_count = 0
    total_samples = 0

    def drain_segments() -> None:
        nonlocal segment_count
        while not vad.empty():
            samples = vad.front.samples
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, samples)
            recognizer.decode_stream(stream)
            text = str(stream.result.text or "").strip()
            vad.pop()
            if text and text not in {".", "。", "The."}:
                texts.append(text)
                segment_count += 1

    pending = np.empty(0, dtype=np.float32)
    with wave.open(str(wav_path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise RuntimeError("本地 ASR 仅接受单声道 16-bit PCM")
        sample_rate = source.getframerate()
        if sample_rate != 16000:
            raise RuntimeError(f"本地 ASR 采样率异常: {sample_rate}")
        while True:
            raw = source.readframes(16000 * 10)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            total_samples += len(samples)
            pending = np.concatenate((pending, samples))
            while len(pending) >= window_size:
                vad.accept_waveform(pending[:window_size])
                pending = pending[window_size:]
                drain_segments()

    if len(pending):
        padded = np.pad(pending, (0, window_size - len(pending)))
        vad.accept_waveform(padded)
    vad.flush()
    drain_segments()
    return {
        "text": _normalize_transcript("\n".join(texts))[: settings.max_text_chars],
        "segment_count": segment_count,
        "duration_seconds": round(total_samples / 16000, 3),
    }


def _normalize_transcript(text: str) -> str:
    """Fix high-confidence finance homophones without guessing stock names."""
    replacements = {
        "只赢": "止盈",
        "止赢": "止盈",
        "只损": "止损",
        "上升回荡": "上升回档",
    }
    normalized = text
    for observed, expected in replacements.items():
        normalized = normalized.replace(observed, expected)
    return normalized


@contextmanager
def _exclusive_asr_lock(timeout_seconds: int) -> Iterator[None]:
    import fcntl

    deadline = time.monotonic() + timeout_seconds
    with _THREAD_LOCK, _PROCESS_LOCK_PATH.open("a+") as lock_file:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("等待本地 ASR 执行锁超时")
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _transcribe_sync(bvid: str, cookie_string: str) -> dict[str, Any]:
    settings = _settings()
    cached = _read_cache(bvid, settings.failure_retry_seconds)
    if cached is not None:
        logger.info("本地 ASR 缓存命中 %s: %s字", bvid, len(cached.get("text", "")))
        return cached

    status = local_asr_status()
    if not settings.enabled:
        return {"success": False, "text": "", "error": "本地 ASR 未启用", **status}
    if not status["available"]:
        missing = ", ".join(status["missing"])
        return {"success": False, "text": "", "error": f"本地 ASR 依赖缺失: {missing}", **status}

    started_at = time.monotonic()
    try:
        with _exclusive_asr_lock(settings.lock_timeout_seconds):
            cached = _read_cache(bvid, settings.failure_retry_seconds)
            if cached is not None:
                return cached
            with tempfile.TemporaryDirectory(prefix=f"stockagent-asr-{bvid}-") as temp_dir:
                directory = Path(temp_dir)
                audio_path = _download_audio(bvid, cookie_string, directory, settings)
                wav_path = _convert_to_wav(audio_path, directory, settings)
                decoded = _decode_wav(wav_path, settings)
        text = decoded["text"].strip()
        result = {
            "success": bool(text),
            "text": text,
            "source": "sensevoice_local",
            "model_id": MODEL_ID,
            "segment_count": decoded["segment_count"],
            "duration_seconds": decoded["duration_seconds"],
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "cache_hit": False,
        }
        if not text:
            result["error"] = "未检测到可识别语音"
            result["error_code"] = "no_speech"
        _write_cache(bvid, result)
        logger.info(
            "本地 ASR 完成 %s: %s字, %s段, %.2fs",
            bvid,
            len(text),
            decoded["segment_count"],
            result["elapsed_seconds"],
        )
        return result
    except Exception as exc:
        logger.warning("本地 ASR 失败 %s: %s", bvid, exc)
        result = {
            "success": False,
            "text": "",
            "source": "sensevoice_local",
            "model_id": MODEL_ID,
            "error": str(exc)[:500],
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "cache_hit": False,
        }
        if isinstance(exc, NoAudioStreamError):
            result["error_code"] = "no_audio_stream"
        _write_cache(bvid, result)
        return result


async def transcribe_video(bvid: str, cookie_string: str = "") -> dict[str, Any]:
    """Download a Bilibili video's audio and transcribe it entirely on this host."""
    return await asyncio.to_thread(_transcribe_sync, bvid, cookie_string)
