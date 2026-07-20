from __future__ import annotations

from lsc.analyzer.valorant_round_fsm import (
    FrameEvidence,
    RoundFSM,
    RoundFSMConfig,
)


def _ev(ts: float, cls: str, *, p: float = 0.9, left=None, right=None, timer=None) -> FrameEvidence:
    probs = {c: 0.02 for c in ("non_game", "buy", "combat", "result", "replay")}
    probs[cls] = p
    return FrameEvidence(
        timestamp=ts,
        class_probabilities=probs,
        predicted_class=cls,
        timer_seconds=timer,
        left_score=left,
        right_score=right,
        model_version="stub",
    )


def test_full_buy_combat_result_closes_one_round() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2, max_open_sec=150.0))
    out = []
    # buy
    for t in (1.0, 2.0):
        out.extend(fsm.feed(_ev(t, "buy")))
    # combat
    for t in (3.0, 4.0):
        out.extend(fsm.feed(_ev(t, "combat")))
    # result + score +1
    for t in (40.0, 41.0):
        out.extend(fsm.feed(_ev(t, "result", left=1, right=0)))
    closed = [e for e in out if e.kind == "closed"]
    assert len(closed) == 1
    assert closed[0].start == 3.0
    assert closed[0].confirm_status in {"vision_confirmed", "pending"}


def test_single_frame_jitter_does_not_transition() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2))
    events = []
    events.extend(fsm.feed(_ev(1.0, "buy")))
    events.extend(fsm.feed(_ev(2.0, "combat")))  # single combat — no open
    assert not any(e.kind == "opened" for e in events)


def test_non_game_and_replay_score_increment_does_not_close() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2))
    events = []
    for t, c in [(1.0, "buy"), (2.0, "buy"), (3.0, "combat"), (4.0, "combat")]:
        events.extend(fsm.feed(_ev(t, c)))
    events.extend(fsm.feed(_ev(5.0, "non_game", left=1, right=0)))
    events.extend(fsm.feed(_ev(6.0, "replay", left=1, right=0)))
    events.extend(fsm.feed(_ev(7.0, "replay", left=2, right=0)))
    assert not any(e.kind == "closed" for e in events)


def test_replay_and_non_game_never_open_or_close() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2))
    events = []
    for t, c in [(1.0, "buy"), (2.0, "buy"), (3.0, "combat"), (4.0, "combat")]:
        events.extend(fsm.feed(_ev(t, c)))
    events.extend(fsm.feed(_ev(5.0, "replay")))
    events.extend(fsm.feed(_ev(6.0, "replay")))
    events.extend(fsm.feed(_ev(7.0, "non_game")))
    assert not any(e.kind == "closed" for e in events)


def test_open_over_150s_discarded_not_forced() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2, max_open_sec=150.0))
    events = []
    for t in (1.0, 2.0):
        events.extend(fsm.feed(_ev(t, "buy")))
    for t in (3.0, 4.0):
        events.extend(fsm.feed(_ev(t, "combat")))
    events.extend(fsm.feed(_ev(160.0, "combat")))
    assert any(e.kind == "discarded" for e in events)
    assert not any(e.kind == "closed" for e in events)


def test_mid_combat_start_waits_for_buy() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2))
    events = []
    for t in (1.0, 2.0, 3.0):
        events.extend(fsm.feed(_ev(t, "combat")))
    assert not any(e.kind == "opened" for e in events)


def test_refine_does_not_change_round_key() -> None:
    fsm = RoundFSM(RoundFSMConfig(coarse_stable_frames=2))
    events = []
    for t in (1.0, 2.0):
        events.extend(fsm.feed(_ev(t, "buy")))
    for t in (3.0, 4.0):
        events.extend(fsm.feed(_ev(t, "combat")))
    opened = next(e for e in events if e.kind == "opened")
    key = opened.round_key
    fsm.apply_refine(round_key=key, start=3.1, end=None)
    assert opened.round_key == key
