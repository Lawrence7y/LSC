"""Regression tests for recording controller option wiring."""
from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _install_fake_capture_module(monkeypatch) -> None:
    fake_recorder = types.ModuleType("lsc.recorder")
    fake_capture = types.ModuleType("lsc.recorder.capture")
    fake_capture._friendly_ffmpeg_message = lambda _code, _stderr: "直播流地址已失效"
    monkeypatch.setitem(sys.modules, "lsc.recorder", fake_recorder)
    monkeypatch.setitem(sys.modules, "lsc.recorder.capture", fake_capture)


class _FakeCapture:
    def __init__(self):
        self.calls = []
        self.status = types.SimpleNamespace(value="recording")
        self.is_recording = True

    def start(self, url, output_path, *, codec="copy", input_args=None, extra_args=None):
        self.calls.append(
            {
                "url": url,
                "output_path": output_path,
                "codec": codec,
                "input_args": input_args,
                "extra_args": extra_args or [],
            }
        )
        return True


def test_recording_controller_select_stream_url_uses_real_quality_presets() -> None:
    from lsc.gui.pages.recording_controller import RecordingController

    info = {
        "streamUrl": "https://example.com/origin.m3u8",
        "selectedQuality": "origin",
        "qualityUrls": {
            "origin": "https://example.com/origin.m3u8",
            "hd": "https://example.com/hd.m3u8",
            "sd": "https://example.com/sd.m3u8",
        },
    }

    assert RecordingController.select_stream_url(info, "原画") == (
        "https://example.com/origin.m3u8",
        "origin",
    )
    assert RecordingController.select_stream_url(info, "高清") == (
        "https://example.com/hd.m3u8",
        "hd",
    )
    assert RecordingController.select_stream_url(info, "流畅") == (
        "https://example.com/sd.m3u8",
        "sd",
    )


def test_recording_controller_cpu_bitrate_mode_builds_target_bitrate_args(tmp_path) -> None:
    from lsc.gui.pages.recording_controller import RecordingController

    ctrl = RecordingController()
    ctrl._capture = _FakeCapture()

    ok, output_path, encoder_used = ctrl.start_recording_with_crf(
        "https://example.com/live.m3u8",
        str(tmp_path),
        "H.264 CPU",
        23,
        param_mode="码率限制",
        bitrate="8000",
        bitrate_unit="kbps",
    )

    assert ok is True
    assert output_path.endswith(".mp4")
    assert encoder_used == "H.264 CPU"
    args = ctrl._capture.calls[-1]["extra_args"]
    assert ["-b:v", "8000k"] == args[4:6]
    assert "-maxrate" in args
    assert "-bufsize" in args
    assert "-crf" not in args


def test_recording_controller_nvenc_crf_mode_uses_vbr_cq_args(tmp_path) -> None:
    from lsc.gui.pages.recording_controller import RecordingController

    ctrl = RecordingController()
    ctrl._capture = _FakeCapture()
    ctrl.check_nvenc_available = lambda: True

    ok, _output_path, encoder_used = ctrl.start_recording_with_crf(
        "https://example.com/live.m3u8",
        str(tmp_path),
        "H.264 NVENC",
        28,
        param_mode="CRF 质量",
    )

    assert ok is True
    assert encoder_used == "H.264 NVENC"
    args = ctrl._capture.calls[-1]["extra_args"]
    assert "-rc" in args
    assert "vbr" in args
    assert "-cq" in args
    assert "28" in args
    assert "-b:v" in args
    assert "0" in args


def test_recording_controller_unlimited_mode_forces_copy(tmp_path) -> None:
    from lsc.gui.pages.recording_controller import RecordingController

    ctrl = RecordingController()
    ctrl._capture = _FakeCapture()

    ok, _output_path, encoder_used = ctrl.start_recording_with_crf(
        "https://example.com/live.m3u8",
        str(tmp_path),
        "H.264 CPU",
        23,
        param_mode="不限制",
    )

    assert ok is True
    assert encoder_used == "Copy"
    assert ctrl._capture.calls[-1]["extra_args"] == []


def test_friendly_ffmpeg_exit_message_uses_stderr_tail(monkeypatch) -> None:
    _install_fake_capture_module(monkeypatch)

    from lsc.gui.pages.recording_controller import friendly_ffmpeg_exit_message

    message = friendly_ffmpeg_exit_message(1, "HTTP error 404 Not Found")

    assert "直播流地址已失效" in message


def test_preflight_recording_returns_error_when_disk_is_low(monkeypatch, tmp_path) -> None:
    from lsc.gui.pages.recording_controller import RecordingController

    ctrl = RecordingController()
    monkeypatch.setattr(
        "shutil.disk_usage",
        lambda _path: (100 * 1024**3, 95 * 1024**3, 5 * 1024**3),
    )

    message = ctrl.preflight_recording(str(tmp_path))

    assert "磁盘空间不足" in message


def test_parse_douyin_url_uses_platform_layer_legacy_shape(monkeypatch) -> None:
    from lsc.gui.pages.recording_controller import RecordingController
    from lsc.platforms.base import StreamInfo

    _qapp()

    def fake_parse_stream(url):
        assert url == "https://live.douyin.com/123456"
        return StreamInfo(
            platform="douyin",
            room_url=url,
            stream_url="https://example.com/live.m3u8",
            title="直播标题",
            streamer="主播A",
            is_live=True,
            quality_urls={"origin": "https://example.com/live.m3u8"},
            selected_quality="origin",
            headers={"Referer": "https://live.douyin.com/"},
        )

    monkeypatch.setattr("lsc.gui.pages.recording_controller.parse_stream", fake_parse_stream)

    ctrl = RecordingController()
    result = ctrl.parse_douyin_url("https://live.douyin.com/123456")

    assert result["isLive"] is True
    assert result["platform"] == "douyin"
    assert result["streamUrl"] == "https://example.com/live.m3u8"
    assert result["streamerName"] == "主播A"
    assert result["_inputArgs"] == [
        "-headers",
        "Referer: https://live.douyin.com/\r\n",
    ]


def test_parse_stream_url_returns_legacy_shape(monkeypatch) -> None:
    from lsc.gui.pages.recording_controller import RecordingController
    from lsc.platforms.base import StreamInfo

    _qapp()

    def fake_parse_stream(url):
        assert url == "https://live.douyin.com/654321"
        return StreamInfo(
            platform="douyin",
            room_url=url,
            stream_url="https://example.com/stream.m3u8",
            title="另一场直播",
            streamer="主播B",
            is_live=True,
            quality_urls={"origin": "https://example.com/stream.m3u8"},
            selected_quality="origin",
            headers={"Referer": "https://live.douyin.com/"},
            raw={"room_id": "654321"},
        )

    monkeypatch.setattr("lsc.gui.pages.recording_controller.parse_stream", fake_parse_stream)

    ctrl = RecordingController()
    result = ctrl.parse_stream_url("https://live.douyin.com/654321")

    assert result["platform"] == "douyin"
    assert result["title"] == "另一场直播"
    assert result["streamerName"] == "主播B"
    assert result["isLive"] is True
    assert result["streamUrl"] == "https://example.com/stream.m3u8"
    assert result["roomUrl"] == "https://live.douyin.com/654321"
    assert result["qualityUrls"] == {"origin": "https://example.com/stream.m3u8"}
    assert result["availableQualities"] == ["origin"]
    assert result["selectedQuality"] == "origin"
    assert result["_headers"] == {"Referer": "https://live.douyin.com/"}
    assert result["_inputArgs"] == [
        "-headers",
        "Referer: https://live.douyin.com/\r\n",
    ]
    assert result["_raw"] == {"room_id": "654321"}
    assert result["error"] == ""
    assert result["errorCode"] == ""
    assert ctrl.input_args == [
        "-headers",
        "Referer: https://live.douyin.com/\r\n",
    ]


def test_start_recording_with_crf_passes_explicit_input_args_to_capture(tmp_path) -> None:
    from lsc.gui.pages.recording_controller import RecordingController

    ctrl = RecordingController()
    ctrl._capture = _FakeCapture()
    input_args = ["-headers", "Referer: https://example.com/\r\n"]

    ok, _output_path, _encoder_used = ctrl.start_recording_with_crf(
        "https://example.com/live.m3u8",
        str(tmp_path),
        "H.264 CPU",
        21,
        input_args=input_args,
    )

    assert ok is True
    assert ctrl._capture.calls[-1]["input_args"] == input_args


def test_start_recording_with_crf_defaults_input_args_to_empty_list(tmp_path) -> None:
    from lsc.gui.pages.recording_controller import RecordingController

    ctrl = RecordingController()
    ctrl._capture = _FakeCapture()

    ok, _output_path, _encoder_used = ctrl.start_recording_with_crf(
        "https://example.com/live.m3u8",
        str(tmp_path),
        "Copy",
        21,
        input_args=None,
    )

    assert ok is True
    assert ctrl._capture.calls[-1]["input_args"] == []


def test_start_url_parse_uses_parse_stream_url(monkeypatch) -> None:
    from lsc.gui.pages import recording_controller as module
    from lsc.gui.pages.recording_controller import RecordingController

    _qapp()

    captured = {}

    class FakeUrlParserWorker:
        def __init__(self, page_url, parse_fn):
            captured["page_url"] = page_url
            captured["parse_fn"] = parse_fn
            self.finished = types.SimpleNamespace(connect=lambda callback: captured.setdefault("callback", callback))

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(module, "UrlParserWorker", FakeUrlParserWorker)

    ctrl = RecordingController()

    def on_parsed(result):
        captured["result"] = result

    ctrl.start_url_parse("https://live.douyin.com/123456", on_parsed)

    assert captured["page_url"] == "https://live.douyin.com/123456"
    assert captured["parse_fn"] == ctrl.parse_stream_url
    assert captured["callback"] is on_parsed
    assert captured["started"] is True
