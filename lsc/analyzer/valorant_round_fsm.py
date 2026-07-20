from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

RESULT_TAIL_SEC = 1.5
NON_GAME_ABORT_SEC = 5.0
BOUNDARY_SOURCE = "valorant_hybrid_v1"


class _State(Enum):
    WAIT_BUY = "wait_buy"
    WAIT_COMBAT = "wait_combat"
    ROUND_OPEN = "round_open"


@dataclass(frozen=True)
class FrameEvidence:
    timestamp: float
    class_probabilities: dict[str, float]
    predicted_class: str
    timer_seconds: float | None
    left_score: int | None
    right_score: int | None
    model_version: str


@dataclass
class RoundEvent:
    kind: str  # opened | closed | discarded | resync
    round_key: str
    start: float | None = None
    end: float | None = None
    confirm_status: str | None = None
    start_by: str | None = None
    end_by: str | None = None
    boundary_source: str | None = None
    boundary_evidence: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class RoundFSMConfig:
    coarse_stable_frames: int = 2
    max_open_sec: float = 150.0
    result_tail_sec: float = RESULT_TAIL_SEC
    non_game_abort_sec: float = NON_GAME_ABORT_SEC


class RoundFSM:
    """WAIT_BUY -> WAIT_COMBAT -> ROUND_OPEN -> WAIT_BUY (pure time evidence)."""

    def __init__(self, config: RoundFSMConfig | None = None) -> None:
        self._config = config or RoundFSMConfig()
        self._state = _State.WAIT_BUY
        self._stable_class: str | None = None
        self._stable_count = 0
        self._stable_run_first_ts: float | None = None
        self._open_start: float | None = None
        self._round_key: str | None = None
        self._baseline_left: int | None = None
        self._baseline_right: int | None = None
        self._score_increment_seen = False
        self._ocr_conflict = False
        self._non_game_start: float | None = None
        self._refined: dict[str, dict[str, float | None]] = {}

    def feed(self, ev: FrameEvidence) -> list[RoundEvent]:
        events: list[RoundEvent] = []

        if self._state == _State.ROUND_OPEN:
            if self._open_start is not None and ev.timestamp - self._open_start > self._config.max_open_sec:
                events.append(self._discard())
            elif ev.predicted_class == "non_game":
                if self._non_game_start is None:
                    self._non_game_start = ev.timestamp
                elif ev.timestamp - self._non_game_start > self._config.non_game_abort_sec:
                    events.append(self._discard())
            else:
                self._non_game_start = None

        if self._state == _State.ROUND_OPEN:
            score_close = self._try_close_on_score(ev)
            if score_close is not None:
                events.append(score_close)
                return events

        cls = ev.predicted_class
        if cls in ("non_game", "replay"):
            return events

        if self._state == _State.ROUND_OPEN and cls == "result":
            self._note_score(ev)
            if self._advance_stable(cls, ev.timestamp):
                close = self._close_round(
                    first_result_ts=self._stable_run_first_ts or ev.timestamp,
                    end_by="model_result",
                )
                if close is not None:
                    events.append(close)
                return events
            return events

        if self._advance_stable(cls, ev.timestamp):
            events.extend(self._on_stable_transition(cls))

        return events

    def apply_refine(
        self,
        round_key: str,
        *,
        start: float | None = None,
        end: float | None = None,
    ) -> None:
        entry = self._refined.setdefault(round_key, {"start": None, "end": None})
        if start is not None:
            entry["start"] = start
        if end is not None:
            entry["end"] = end

    def _advance_stable(self, cls: str, ts: float) -> bool:
        if cls == self._stable_class:
            self._stable_count += 1
        else:
            self._stable_class = cls
            self._stable_count = 1
            self._stable_run_first_ts = ts
        return self._stable_count >= self._config.coarse_stable_frames

    def _on_stable_transition(self, cls: str) -> list[RoundEvent]:
        first_ts = self._stable_run_first_ts or 0.0
        self._reset_stable()

        if self._state == _State.WAIT_BUY and cls == "buy":
            self._state = _State.WAIT_COMBAT
            return []

        if self._state == _State.WAIT_COMBAT and cls == "combat":
            self._state = _State.ROUND_OPEN
            self._open_start = first_ts
            self._round_key = f"hybrid-{int(first_ts)}"
            self._baseline_left = None
            self._baseline_right = None
            self._score_increment_seen = False
            self._ocr_conflict = False
            self._non_game_start = None
            return [
                RoundEvent(
                    kind="opened",
                    round_key=self._round_key,
                    start=first_ts,
                    start_by="model_buy_exit",
                    boundary_source=BOUNDARY_SOURCE,
                )
            ]

        return []

    def _note_score(self, ev: FrameEvidence) -> None:
        increment, conflict = self._check_score_increment(ev)
        if conflict:
            self._ocr_conflict = True
        if increment:
            self._score_increment_seen = True

    def _try_close_on_score(self, ev: FrameEvidence) -> RoundEvent | None:
        if ev.predicted_class == "result":
            self._note_score(ev)
        increment, conflict = self._check_score_increment(ev)
        if conflict:
            self._ocr_conflict = True
            return None
        if not increment:
            return None
        if self._ocr_conflict:
            return None
        return self._close_round(first_result_ts=ev.timestamp, end_by="model_score")

    def _close_round(self, *, first_result_ts: float, end_by: str) -> RoundEvent | None:
        if self._round_key is None or self._open_start is None:
            return None

        end = first_result_ts + self._config.result_tail_sec
        if end_by == "model_result" and not self._score_increment_seen and self._ocr_conflict:
            return None
        if end_by == "model_score" and self._ocr_conflict:
            return None

        if self._score_increment_seen and end_by in {"model_result", "model_score"}:
            confirm_status = "vision_confirmed"
        elif end_by == "model_result":
            confirm_status = "pending"
        else:
            confirm_status = "pending"

        refined = self._refined.get(self._round_key, {})
        start = refined.get("start", self._open_start)
        if refined.get("end") is not None:
            end = refined["end"]  # type: ignore[assignment]

        event = RoundEvent(
            kind="closed",
            round_key=self._round_key,
            start=start,
            end=end,
            confirm_status=confirm_status,
            start_by="model_buy_exit",
            end_by=end_by,
            boundary_source=BOUNDARY_SOURCE,
        )
        self._reset_round()
        return event

    def _check_score_increment(self, ev: FrameEvidence) -> tuple[bool, bool]:
        left = ev.left_score
        right = ev.right_score
        if left is None and right is None:
            return False, False

        if self._baseline_left is None and self._baseline_right is None:
            self._baseline_left = left if left is not None else 0
            self._baseline_right = right if right is not None else 0
            if left == 1 and right == 0:
                return True, False
            if right == 1 and left == 0:
                return True, False
            return False, False

        bl = self._baseline_left if self._baseline_left is not None else 0
        br = self._baseline_right if self._baseline_right is not None else 0
        cl = left if left is not None else bl
        cr = right if right is not None else br

        dl = cl - bl
        dr = cr - br

        if dl < 0 or dr < 0:
            return False, True
        if dl > 1 or dr > 1:
            return False, True
        if dl == 1 and dr == 0:
            return True, False
        if dr == 1 and dl == 0:
            return True, False
        if dl == 1 and dr == 1:
            return False, True
        return False, False

    def _discard(self) -> RoundEvent:
        key = self._round_key or "hybrid-discard"
        event = RoundEvent(kind="discarded", round_key=key)
        self._reset_round()
        return event

    def _reset_stable(self) -> None:
        self._stable_class = None
        self._stable_count = 0
        self._stable_run_first_ts = None

    def _reset_round(self) -> None:
        self._state = _State.WAIT_BUY
        self._open_start = None
        self._round_key = None
        self._baseline_left = None
        self._baseline_right = None
        self._score_increment_seen = False
        self._ocr_conflict = False
        self._non_game_start = None
        self._reset_stable()
