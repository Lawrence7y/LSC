from __future__ import annotations

import pytest

from lsc.analyzer.registry import list_plugins


@pytest.mark.parametrize("plugin", list_plugins(), ids=lambda p: p.game)
def test_capabilities_consistent(plugin):
    caps = plugin.capabilities()
    assert isinstance(caps.realtime_continuous, bool)
    assert isinstance(caps.posthoc_file, bool)
    if plugin.game == "generic":
        assert caps.posthoc_file is True
        assert caps.game_specific is False


@pytest.mark.parametrize("plugin", list_plugins(), ids=lambda p: p.game)
def test_plan_and_scan_window_shape(plugin, tmp_path):
    if not plugin.capabilities().realtime_continuous and plugin.game == "generic":
        # generic B1：plan/scan 仍可调用
        pass
    state: dict = {}
    window = plugin.plan_scan_window(state, current_dur=30.0, pressure={})
    assert window.end_sec >= window.start_sec
    assert window.timeout_sec > 0
    video = tmp_path / "dummy.mp4"
    video.write_bytes(b"")  # 占位；scan 可空实现
    cancelled = {"n": 0}

    def cancel_check():
        cancelled["n"] += 1
        return False

    out = plugin.scan_window(str(video), window, state, cancel_check=cancel_check)
    assert isinstance(out, list)


@pytest.mark.parametrize("plugin", list_plugins(), ids=lambda p: p.game)
def test_cancel_check_on_analyze_file(plugin):
    def cancel_always():
        return True

    result = plugin.analyze_file("nope.mp4", cancel_check=cancel_always)
    assert result is None or result == []
