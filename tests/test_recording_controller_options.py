"""Regression tests for recording option wiring."""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_douyin_parser_extracts_quality_url_map() -> None:
    from scripts.douyin_record import extract_ssr_data

    payload = {
        "data": {
            "title": "test live",
            "owner": {"nickname": "主播"},
            "origin": {"main": {"hls": "https://example.com/origin.m3u8"}},
            "hd": {"main": {"hls": "https://example.com/hd.m3u8"}},
            "sd": {"main": {"hls": "https://example.com/sd.m3u8"}},
        }
    }
    encoded = json.dumps(payload, ensure_ascii=False).replace("\\", "\\\\").replace('"', '\\"')
    html = f'self.__pace_f.push([1,"{encoded}"])'

    info = extract_ssr_data(html)

    assert info["qualityUrls"]["origin"] == "https://example.com/origin.m3u8"
    assert info["qualityUrls"]["hd"] == "https://example.com/hd.m3u8"
    assert info["qualityUrls"]["sd"] == "https://example.com/sd.m3u8"


def test_record_page_uses_quality_preset_specific_stream_url() -> None:
    _qapp()

    from lsc.gui.pages.record import RecordPage

    class FakeSignal:
        def __init__(self):
            self.messages = []

        def emit(self, *args):
            self.messages.append(args)

    class FakeController:
        def __init__(self):
            self.stream_url = ""

        @staticmethod
        def select_stream_url(info, quality_preset):
            from lsc.gui.pages.recording_controller import RecordingController

            return RecordingController.select_stream_url(info, quality_preset)

    class FakeConfig:
        quality_selection = "流畅"

        def __init__(self):
            self.connected = []

        def set_connected(self, value):
            self.connected.append(value)

    page = RecordPage.__new__(RecordPage)
    page._ctrl = FakeController()
    page._config = FakeConfig()
    page.status_changed = FakeSignal()

    info = {
        "isLive": True,
        "streamUrl": "https://example.com/origin.m3u8",
        "qualityUrls": {
            "origin": "https://example.com/origin.m3u8",
            "hd": "https://example.com/hd.m3u8",
            "sd": "https://example.com/sd.m3u8",
        },
        "streamerName": "主播",
        "title": "测试直播",
    }

    RecordPage._on_url_parsed(page, info)

    assert page._ctrl.stream_url == "https://example.com/sd.m3u8"
    assert page._config.connected == [True]


def test_recording_controller_cpu_bitrate_mode_builds_target_bitrate_args(tmp_path) -> None:
    from lsc.gui.pages.recording_controller import RecordingController
    from lsc.recorder.capture import CaptureStatus

    class FakeCapture:
        def __init__(self):
            self.calls = []
            self.status = CaptureStatus.RECORDING

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

    ctrl = RecordingController()
    ctrl._capture = FakeCapture()

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
    from lsc.recorder.capture import CaptureStatus

    class FakeCapture:
        def __init__(self):
            self.calls = []
            self.status = CaptureStatus.RECORDING

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

    ctrl = RecordingController()
    ctrl._capture = FakeCapture()
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


def test_friendly_ffmpeg_exit_message_uses_stderr_tail() -> None:
    from lsc.gui.pages.recording_controller import friendly_ffmpeg_exit_message

    message = friendly_ffmpeg_exit_message(1, "HTTP error 404 Not Found")
    assert "直播流地址已失效" in message
