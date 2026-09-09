from handlers import room_handler as rh


def test_timeline_replay_setting_normalizes_supported_values() -> None:
    assert rh._normalize_timeline_replay_seconds(0) == 0
    assert rh._normalize_timeline_replay_seconds(120) == 120
    assert rh._normalize_timeline_replay_seconds(300) == 300
    assert rh._normalize_timeline_replay_seconds(600) == 600


def test_timeline_replay_setting_falls_back_to_five_minutes() -> None:
    assert rh._normalize_timeline_replay_seconds(None) == 300
    assert rh._normalize_timeline_replay_seconds(240) == 300
    assert rh._normalize_timeline_replay_seconds(300.5) == 300
    assert rh._normalize_timeline_replay_seconds("invalid") == 300
