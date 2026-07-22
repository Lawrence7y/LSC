from lsc.exporter.clip import compute_export_watchdog_timeout


def test_baseline():
    assert compute_export_watchdog_timeout(None, None, True) == 300


def test_4k_soft():
    assert compute_export_watchdog_timeout(3840, 2160, False) == 1500


def test_1440p_hw():
    assert compute_export_watchdog_timeout(2560, 1440, True) == 450
