from lsc.analyzer.round_detector import refine_end_with_next_buy_anchor


def test_picks_last_result_before_buy_not_early_flash() -> None:
    seq = [
        (10.0, "combat"),
        (11.0, "result"),
        (11.1, "result"),
        (11.2, "result"),  # early flash
        (12.0, "combat"),
        (20.0, "result"),
        (20.1, "result"),
        (20.2, "result"),
        (21.0, "buy"),
    ]
    end_ts, ok = refine_end_with_next_buy_anchor(
        seq,
        coarse_end=11.0,
        round_start=0.0,
        lookahead_sec=45.0,
        lookback_sec=8.0,
        min_stable=3,
    )
    assert ok is True
    assert 20.0 <= end_ts <= 22.5


def test_rejects_result_without_following_transition() -> None:
    seq = [
        (10.0, "combat"),
        (11.0, "result"),
        (11.1, "result"),
        (11.2, "result"),
        (12.0, "combat"),
    ]
    end_ts, ok = refine_end_with_next_buy_anchor(
        seq,
        coarse_end=11.0,
        round_start=0.0,
        lookahead_sec=45.0,
        lookback_sec=8.0,
        min_stable=3,
    )
    assert ok is False
    assert end_ts == 11.0


def test_prefers_buy_anchor_over_later_non_game() -> None:
    seq = [
        (10.0, "combat"),
        (15.0, "result"),
        (15.1, "result"),
        (15.2, "result"),
        (16.0, "buy"),
        (30.0, "non_game"),
    ]
    end_ts, ok = refine_end_with_next_buy_anchor(
        seq,
        coarse_end=14.0,
        round_start=0.0,
        lookahead_sec=45.0,
        lookback_sec=8.0,
        min_stable=3,
    )
    assert ok is True
    assert 15.0 <= end_ts <= 17.5


def test_accepts_replay_as_transition() -> None:
    seq = [
        (10.0, "combat"),
        (12.0, "result"),
        (12.1, "result"),
        (12.2, "result"),
        (13.0, "replay"),
    ]
    end_ts, ok = refine_end_with_next_buy_anchor(
        seq,
        coarse_end=12.0,
        round_start=0.0,
        lookahead_sec=45.0,
        lookback_sec=8.0,
        min_stable=3,
    )
    assert ok is True
    assert 12.0 <= end_ts <= 14.0
