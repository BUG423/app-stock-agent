"""SenseVoice local ASR cache and orchestration tests."""

from contextlib import nullcontext
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from stockagent.llm import local_asr


def _settings(tmp_path: Path) -> local_asr.LocalASRSettings:
    return local_asr.LocalASRSettings(
        enabled=True,
        model_path=tmp_path / "model.int8.onnx",
        tokens_path=tmp_path / "tokens.txt",
        vad_model_path=tmp_path / "silero_vad.onnx",
        num_threads=1,
        failure_retry_seconds=3600,
        lock_timeout_seconds=1,
        max_audio_megabytes=120,
        max_text_chars=16000,
        bypass_proxy=True,
        vad_threshold=0.25,
        min_silence_seconds=0.4,
        min_speech_seconds=0.25,
        max_speech_seconds=25.0,
    )


def test_local_asr_success_cache_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(local_asr, "LOCAL_ASR_CACHE_DIR", tmp_path)
    local_asr._write_cache(
        "BV-test",
        {
            "success": True,
            "text": "世茂能源今天继续持有。",
            "source": "sensevoice_local",
        },
    )

    cached = local_asr._read_cache("BV-test", failure_retry_seconds=3600)

    assert cached is not None
    assert cached["cache_hit"] is True
    assert cached["text"] == "世茂能源今天继续持有。"


def test_normalize_transcript_fixes_only_known_trading_homophones():
    text = "上升回荡战法，跌破支撑只损，第二天不连板就只赢。谨望电子继续观察。"

    assert local_asr._normalize_transcript(text) == (
        "上升回档战法，跌破支撑止损，第二天不连板就止盈。谨望电子继续观察。"
    )


def test_local_asr_failure_cache_only_blocks_same_video(tmp_path, monkeypatch):
    monkeypatch.setattr(local_asr, "LOCAL_ASR_CACHE_DIR", tmp_path)
    monkeypatch.setattr(local_asr.time, "time", lambda: 1000.0)
    local_asr._write_cache("BV-one", {"success": False, "text": "", "error": "下载失败"})

    assert local_asr._read_cache("BV-one", failure_retry_seconds=3600) is not None
    assert local_asr._read_cache("BV-other", failure_retry_seconds=3600) is None
    assert local_asr._read_cache("BV-one", failure_retry_seconds=0) is None


def test_local_asr_cache_from_old_pipeline_version_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(local_asr, "LOCAL_ASR_CACHE_DIR", tmp_path)
    path = local_asr._cache_path("BV-stale")
    path.write_text(
        '{"success": true, "text": "旧转写", '
        f'"model_id": "{local_asr.MODEL_ID}", "pipeline_version": 1}}',
        encoding="utf-8",
    )

    assert local_asr._read_cache("BV-stale", failure_retry_seconds=3600) is None


def test_local_asr_status_reports_runtime_and_model_gaps(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(local_asr, "_settings", lambda: settings)
    monkeypatch.setattr(local_asr, "_module_available", lambda name: name != "sherpa_onnx")
    monkeypatch.setattr(local_asr.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    status = local_asr.local_asr_status()

    assert status["available"] is False
    assert status["missing"] == ["sherpa_onnx", "model", "tokens", "vad_model"]


def test_transcribe_sync_runs_local_decoder_and_persists_cache(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(local_asr, "LOCAL_ASR_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(local_asr, "_settings", lambda: settings)
    monkeypatch.setattr(
        local_asr,
        "local_asr_status",
        lambda: {
            "enabled": True,
            "available": True,
            "model_id": local_asr.MODEL_ID,
            "model_dir": str(tmp_path),
            "missing": [],
        },
    )
    monkeypatch.setattr(local_asr, "_exclusive_asr_lock", lambda _timeout: nullcontext())

    audio_path = tmp_path / "audio.m4a"
    wav_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    wav_path.write_bytes(b"wav")
    monkeypatch.setattr(local_asr, "_download_audio", lambda *_args: audio_path)
    monkeypatch.setattr(local_asr, "_convert_to_wav", lambda *_args: wav_path)
    monkeypatch.setattr(
        local_asr,
        "_decode_wav",
        lambda *_args: {
            "text": "跌破五日线就不买。",
            "segment_count": 2,
            "duration_seconds": 68.5,
        },
    )

    result = local_asr._transcribe_sync("BV-local", "SESSDATA=test")
    cached = local_asr._read_cache("BV-local", failure_retry_seconds=3600)

    assert result["success"] is True
    assert result["source"] == "sensevoice_local"
    assert result["segment_count"] == 2
    assert cached is not None
    assert cached["text"] == "跌破五日线就不买。"


def test_convert_to_wav_classifies_video_without_audio(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    video_path = tmp_path / "silent.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        local_asr.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(
            args=[],
            returncode=1,
            stderr="Output file does not contain any stream",
        ),
    )

    with pytest.raises(local_asr.NoAudioStreamError, match="视频不含音轨"):
        local_asr._convert_to_wav(video_path, tmp_path, settings)


def test_transcribe_sync_marks_no_audio_for_ocr_fallback(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(local_asr, "LOCAL_ASR_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(local_asr, "_settings", lambda: settings)
    monkeypatch.setattr(
        local_asr,
        "local_asr_status",
        lambda: {"enabled": True, "available": True, "missing": []},
    )
    monkeypatch.setattr(local_asr, "_exclusive_asr_lock", lambda _timeout: nullcontext())
    monkeypatch.setattr(local_asr, "_download_audio", lambda *_args: tmp_path / "video.mp4")

    def no_audio(*_args):
        raise local_asr.NoAudioStreamError("视频不含音轨")

    monkeypatch.setattr(local_asr, "_convert_to_wav", no_audio)

    result = local_asr._transcribe_sync("BV-silent", "")

    assert result["success"] is False
    assert result["error"] == "视频不含音轨"
    assert result["error_code"] == "no_audio_stream"
