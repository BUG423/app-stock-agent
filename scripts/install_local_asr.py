"""Install the pinned SenseVoice INT8 and Silero VAD model files.

The model files are runtime data under ``data/models`` and are intentionally
not committed to Git.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import urllib.request
from pathlib import Path

MODEL_REPOSITORY = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
MODEL_FILE_URLS = (
    f"https://hf-mirror.com/{MODEL_REPOSITORY}/resolve/main/model.int8.onnx",
    f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/main/model.int8.onnx",
)
TOKENS_FILE_URLS = (
    f"https://hf-mirror.com/{MODEL_REPOSITORY}/resolve/main/tokens.txt",
    f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/main/tokens.txt",
)
VAD_MODEL_URLS = (
    ("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"),
)
MODEL_SHA256 = "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _opener(use_proxy: bool):
    if use_proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _download(url: str, destination: Path, use_proxy: bool) -> None:
    existing_size = destination.stat().st_size if destination.is_file() else 0
    headers = {"User-Agent": "stockagent-model-installer/1"}
    if existing_size:
        headers["Range"] = f"bytes={existing_size}-"
        print(f"从 {existing_size / 1024 / 1024:.1f} MiB 继续", flush=True)
    request = urllib.request.Request(url, headers=headers)
    with _opener(use_proxy).open(request, timeout=120) as response:
        append = existing_size > 0 and getattr(response, "status", None) == 206
        with destination.open("ab" if append else "wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)


def _download_first(urls: tuple[str, ...], destination: Path, use_proxy: bool) -> None:
    errors: list[str] = []
    for url in urls:
        print(f"下载: {url}", flush=True)
        try:
            _download(url, destination, use_proxy)
            return
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            print(f"下载失败，尝试下一镜像: {exc}", flush=True)
    raise RuntimeError("所有模型下载地址均失败: " + "; ".join(errors))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install(model_dir: Path, force: bool, use_proxy: bool) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "model.int8.onnx"
    tokens_path = model_dir / "tokens.txt"
    vad_path = model_dir / "silero_vad.onnx"

    model_valid = (
        model_path.is_file()
        and model_path.stat().st_size >= 100 * 1024 * 1024
        and _sha256(model_path) == MODEL_SHA256
    )
    if force or not model_valid:
        tmp_model = model_path.with_suffix(".download")
        _download_first(MODEL_FILE_URLS, tmp_model, use_proxy)
        model_hash = _sha256(tmp_model)
        if model_hash != MODEL_SHA256:
            tmp_model.unlink(missing_ok=True)
            raise RuntimeError(f"SenseVoice INT8 SHA-256 不匹配: {model_hash}")
        tmp_model.replace(model_path)

    if force or not tokens_path.is_file() or tokens_path.stat().st_size < 10 * 1024:
        tmp_tokens = tokens_path.with_suffix(".download")
        _download_first(TOKENS_FILE_URLS, tmp_tokens, use_proxy)
        tmp_tokens.replace(tokens_path)

    if force or not vad_path.is_file():
        tmp_vad = vad_path.with_suffix(".tmp")
        _download_first(VAD_MODEL_URLS, tmp_vad, use_proxy)
        tmp_vad.replace(vad_path)

    if model_path.stat().st_size < 100 * 1024 * 1024:
        raise RuntimeError("SenseVoice INT8 模型文件过小，安装不完整")
    model_hash = _sha256(model_path)
    if model_hash != MODEL_SHA256:
        raise RuntimeError(f"SenseVoice INT8 SHA-256 不匹配: {model_hash}")
    if tokens_path.stat().st_size < 10 * 1024:
        raise RuntimeError("SenseVoice tokens 文件过小，安装不完整")
    if vad_path.stat().st_size < 100 * 1024:
        raise RuntimeError("Silero VAD 模型文件过小，安装不完整")

    print(f"本地 ASR 模型已安装到 {model_dir}")
    for path in (model_path, tokens_path, vad_path):
        print(f"  {path.name}: {path.stat().st_size / 1024 / 1024:.1f} MiB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "models" / "sensevoice",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--use-proxy", action="store_true")
    args = parser.parse_args()
    install(args.model_dir.expanduser().resolve(), args.force, args.use_proxy)


if __name__ == "__main__":
    main()
