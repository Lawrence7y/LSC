"""批次2：dependency_manager 供应链真行为测试（非源码子串门禁）。"""
from __future__ import annotations

import hashlib
import importlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-backend"))


@pytest.fixture()
def dm(monkeypatch):
    monkeypatch.delenv("LSC_FFMPEG_ALLOW_MIRRORS", raising=False)
    monkeypatch.delenv("LSC_FFMPEG_SHA256", raising=False)
    monkeypatch.delenv("LSC_ALLOW_UNVERIFIED_FFMPEG", raising=False)
    monkeypatch.delenv("LSC_PIP_INDEX_URL", raising=False)
    mod = importlib.import_module("dependency_manager")
    importlib.reload(mod)
    return mod


def test_ffmpeg_urls_default_official_only(dm) -> None:
    urls = dm._ffmpeg_download_urls()
    assert urls == [dm._FFMPEG_OFFICIAL_URL]
    assert not any("ghfast" in u or "gh-proxy" in u for u in urls)


def test_ffmpeg_mirrors_require_explicit_opt_in(dm, monkeypatch) -> None:
    monkeypatch.setenv("LSC_FFMPEG_ALLOW_MIRRORS", "1")
    urls = dm._ffmpeg_download_urls()
    assert urls[0] == dm._FFMPEG_OFFICIAL_URL
    assert any("ghfast" in u or "gh-proxy" in u for u in urls[1:])


def test_pip_index_defaults_to_pypi(dm) -> None:
    assert dm._PIP_INDEX_URL == "https://pypi.org/simple"


def test_download_ffmpeg_rejects_unverified_without_hash(dm, monkeypatch, tmp_path) -> None:
    payload = b"z" * (1024 * 1024 + 32)
    monkeypatch.setattr(dm, "check_ffmpeg_ok", lambda: False)
    monkeypatch.setattr(
        dm.urllib.request,
        "urlretrieve",
        lambda url, filename, reporthook=None: Path(filename).write_bytes(payload),
    )
    def _mkdtemp(**kwargs):
        d = tmp_path / "ff"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    monkeypatch.setattr(dm.tempfile, "mkdtemp", _mkdtemp)
    monkeypatch.setattr(dm, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(dm, "_emit_progress", lambda *a, **k: None)
    monkeypatch.setattr(dm, "_emit_done", lambda *a, **k: None)
    errors: list[str] = []
    monkeypatch.setattr(dm, "_emit_error", lambda phase, msg: errors.append(str(msg)))
    assert dm.download_ffmpeg() is False
    assert any("LSC_FFMPEG_SHA256" in e for e in errors)


def test_download_ffmpeg_enforces_sha256_mismatch(dm, monkeypatch, tmp_path) -> None:
    payload = b"x" * (1024 * 1024 + 32)
    monkeypatch.setenv("LSC_FFMPEG_SHA256", "0" * 64)
    monkeypatch.setattr(dm, "check_ffmpeg_ok", lambda: False)
    monkeypatch.setattr(
        dm.urllib.request,
        "urlretrieve",
        lambda url, filename, reporthook=None: Path(filename).write_bytes(payload),
    )
    def _mkdtemp2(**kwargs):
        d = tmp_path / "ffX"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    monkeypatch.setattr(dm.tempfile, "mkdtemp", _mkdtemp2)
    monkeypatch.setattr(dm, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(dm, "_emit_progress", lambda *a, **k: None)
    monkeypatch.setattr(dm, "_emit_done", lambda *a, **k: None)
    errors: list[str] = []
    monkeypatch.setattr(dm, "_emit_error", lambda phase, msg: errors.append(str(msg)))
    assert dm.download_ffmpeg() is False
    assert any("SHA-256" in e or "校验失败" in e for e in errors)


def test_download_ffmpeg_blocks_zip_slip(dm, monkeypatch, tmp_path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "pwn")
        zf.writestr("pad.bin", "A" * (1024 * 1024))
    payload = buf.getvalue()
    monkeypatch.setenv("LSC_FFMPEG_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(dm, "check_ffmpeg_ok", lambda: False)
    monkeypatch.setattr(
        dm.urllib.request,
        "urlretrieve",
        lambda url, filename, reporthook=None: Path(filename).write_bytes(payload),
    )
    def _mkdtemp3(**kwargs):
        d = tmp_path / "ffY"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    monkeypatch.setattr(dm.tempfile, "mkdtemp", _mkdtemp3)
    monkeypatch.setattr(dm, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(dm, "_emit_progress", lambda *a, **k: None)
    monkeypatch.setattr(dm, "_emit_done", lambda *a, **k: None)
    errors: list[str] = []
    monkeypatch.setattr(dm, "_emit_error", lambda phase, msg: errors.append(str(msg)))
    assert dm.download_ffmpeg() is False
    assert any("Zip Slip" in e or "非法文件路径" in e for e in errors)
