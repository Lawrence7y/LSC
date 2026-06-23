"""Tests for mpv preview widget diagnostics."""
from __future__ import annotations

import builtins
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_mpv_widget_reports_missing_libmpv_dll(monkeypatch) -> None:
    """缺少 libmpv DLL 时，错误信息要能直接指导用户修复。"""
    _qapp()
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mpv":
            raise OSError("Cannot find mpv-2.dll")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr("shutil.which", lambda name: None)

    from lsc.gui.components.mpv_widget import MpvWidget

    widget = MpvWidget()
    try:
        assert widget.is_available() is False
        assert "mpv-2.dll" in widget.init_error()
        assert "PATH" in widget.init_error()
    finally:
        widget.cleanup()


def test_mpv_widget_uses_native_window_attributes_for_embedded_video(monkeypatch) -> None:
    """libmpv 内嵌窗口应避免被 Qt 普通背景重绘覆盖。"""
    _qapp()
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mpv":
            raise OSError("Cannot find mpv-2.dll")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr("shutil.which", lambda name: None)

    from lsc.gui.components.mpv_widget import MpvWidget

    widget = MpvWidget()
    try:
        assert widget.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        assert widget.testAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        assert widget.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert widget.autoFillBackground() is False
    finally:
        widget.cleanup()


def test_mpv_widget_adds_project_runtime_libmpv_path(monkeypatch, tmp_path) -> None:
    """项目本地 .runtime/libmpv 应自动加入 DLL 搜索路径。"""
    _qapp()

    from lsc.gui.components import mpv_widget as mpv_module
    from lsc.gui.components.mpv_widget import MpvWidget

    runtime_lib = tmp_path / ".runtime" / "libmpv"
    runtime_lib.mkdir(parents=True)
    monkeypatch.setattr(mpv_module, "_project_root", lambda: tmp_path)

    added = []
    monkeypatch.setattr(os, "add_dll_directory", lambda path: added.append(path), raising=False)

    old_path = os.environ.get("PATH", "")
    try:
        MpvWidget._prepare_libmpv_search_paths()
        assert str(runtime_lib) in os.environ["PATH"].split(os.pathsep)
        assert str(runtime_lib) in added
    finally:
        os.environ["PATH"] = old_path


def test_mpv_widget_falls_back_to_ffplay_when_libmpv_missing(monkeypatch) -> None:
    """libmpv 不可用但 ffplay 可用时，仍应允许直播预览。"""
    _qapp()
    real_import = builtins.__import__
    launched: list[list[str]] = []

    def fake_import(name, *args, **kwargs):
        if name == "mpv":
            raise OSError("Cannot find mpv-2.dll")
        return real_import(name, *args, **kwargs)

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def fake_popen(cmd, **kwargs):
        launched.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr("shutil.which", lambda name: "C:/ffmpeg/bin/ffplay.exe" if name == "ffplay" else None)
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "lsc.utils.process_launcher.prepare_launch",
        lambda path: ({}, 0, None),
    )

    from lsc.gui.components.mpv_widget import MpvWidget

    widget = MpvWidget()
    try:
        assert widget.is_available() is True
        assert "ffplay" in widget.init_error()
        widget.set_stream_headers({"Referer": "https://example.com/"})
        widget.play("https://example.com/live.m3u8")
        assert launched
        assert launched[0][0].endswith("ffplay.exe")
        assert "-headers" in launched[0]
        assert "Referer: https://example.com/" in " ".join(launched[0])
    finally:
        widget.cleanup()


def test_mpv_widget_rebind_recreates_backend_and_restores_current_stream(monkeypatch) -> None:
    """嵌入房间卡片后要强制重建 mpv，并继续播放当前直播源。"""
    _qapp()

    import sys
    import types

    instances = []

    class FakeMPV:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.played = []
            self.pause = True
            self.mute = False
            self.terminated = False
            self.options = {}
            # Simulate real mpv: kwargs with underscores become options with hyphens
            for k, v in kwargs.items():
                self.options[k.replace("_", "-")] = v
            instances.append(self)

        def __setitem__(self, key, value):
            self.options[key] = value

        def __getitem__(self, key):
            return self.options[key]

        def play(self, path):
            self.played.append(path)

        def terminate(self):
            self.terminated = True

        def stop(self):
            pass

    fake_mpv = types.ModuleType("mpv")
    fake_mpv.MPV = FakeMPV
    monkeypatch.setitem(sys.modules, "mpv", fake_mpv)

    from lsc.gui.components.mpv_widget import MpvWidget

    widget = MpvWidget()
    try:
        widget.set_stream_headers({"Referer": "https://live.bilibili.com/"})
        widget.set_muted(True)
        widget.play("https://example.com/live.flv")

        widget.rebind_video_output()

        # 4 instances: 1 initial + 1 from _play (headers rebuild) + 1 from rebind + 1 from rebind's _play (headers rebuild)
        assert len(instances) == 4
        assert instances[0].terminated is True
        assert instances[2].terminated is True
        assert instances[-1].played == ["https://example.com/live.flv"]
        assert instances[-1].mute is True
        assert instances[-1].options["http-header-fields"] == ["Referer: https://live.bilibili.com/"]
    finally:
        widget.cleanup()
