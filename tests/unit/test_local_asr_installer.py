"""Local ASR model installer mirror fallback tests."""

from scripts import install_local_asr


def test_download_first_falls_back_to_next_mirror(tmp_path, monkeypatch):
    destination = tmp_path / "model.bin"
    calls: list[str] = []

    def fake_download(url, path, _use_proxy):
        calls.append(url)
        if url == "https://primary.test/model":
            path.write_bytes(b"partial")
            raise OSError("primary unavailable")
        path.write_bytes(b"complete")

    monkeypatch.setattr(install_local_asr, "_download", fake_download)

    install_local_asr._download_first(
        ("https://primary.test/model", "https://backup.test/model"),
        destination,
        use_proxy=False,
    )

    assert calls == ["https://primary.test/model", "https://backup.test/model"]
    assert destination.read_bytes() == b"complete"
