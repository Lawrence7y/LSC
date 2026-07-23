from lsc.analyzer.base import AnalyzerCapabilities, ScanWindow
from lsc.analyzer.generic_plugin import GenericAnalyzerPlugin
from lsc.analyzer.registry import default, get, list_plugins, register


def test_scan_window_fields():
    w = ScanWindow(start_sec=1.0, end_sec=2.0, timeout_sec=30.0, use_ocr=False)
    assert w.end_sec == 2.0


def test_default_is_generic():
    p = default()
    assert p.game == "generic"


def test_unknown_falls_back_to_generic():
    p = get("does-not-exist")
    assert p.game == "generic"


def test_register_and_get():
    class Dummy:
        game = "dummy_test"
        display_name = "Dummy"

        def capabilities(self):
            return AnalyzerCapabilities(False, True, False, False, True)

        def analyze_file(self, *a, **k):
            return []

        def plan_scan_window(self, state, current_dur, pressure):
            return ScanWindow(0, current_dur, 1, False)

        def scan_window(self, *a, **k):
            return []

    register(Dummy())
    assert get("dummy_test").game == "dummy_test"


def test_stateless_no_cross_talk():
    p = get("generic")
    s1: dict = {"last_analyzed": 260.0}
    s2: dict = {"last_analyzed": 0.0}
    w1 = p.plan_scan_window(s1, 100.0, {})
    w2 = p.plan_scan_window(s2, 100.0, {})
    assert w1.start_sec != w2.start_sec
    # 插件实例无写入
    assert not hasattr(p, "last_analyzed") or getattr(p, "last_analyzed", None) is None
