from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from lsc.analyzer.round_detector import detect_valorant_rounds_hybrid

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "valorant_vision"

_CLASS_INDEX = {
    "non_game": 0,
    "buy": 1,
    "combat": 2,
    "result": 3,
    "replay": 4,
}


def _make_class_frame(cls: str) -> np.ndarray:
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    img[0, 0, 0] = _CLASS_INDEX[cls]
    return img


def _class_at_time(ts: float) -> str:
    if ts < 2.5:
        return "buy"
    if ts < 39.5:
        return "combat"
    if ts < 42.0:
        return "result"
    return "combat"


def _fake_extract(
    video_path: str,
    *,
    start_sec: float,
    end_sec: float,
    fps: float,
    ffmpeg_path: str,
    cancel_check=None,
    overlap_sec: float = 2.0,
) -> list[tuple[float, np.ndarray]]:
    del video_path, ffmpeg_path, overlap_sec
    if cancel_check and cancel_check():
        from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

        raise FFmpegCancelled("cancelled")
    frames: list[tuple[float, np.ndarray]] = []
    step = 1.0 / max(fps, 0.1)
    t = start_sec
    while t <= end_sec + 1e-6:
        frames.append((t, _make_class_frame(_class_at_time(t))))
        t += step
    return frames


class _EncodedClassifier:
    model_version = "test-v1"

    @property
    def thresholds(self) -> dict[str, float]:
        return {"stable_prob": 0.55, "high_prob": 0.8}

    def load(self) -> None:
        return None

    def predict_batch(self, frames_bgr: list[np.ndarray]) -> np.ndarray:
        rows = []
        for frame in frames_bgr:
            idx = int(frame[0, 0, 0])
            row = np.full(5, 0.02, dtype=np.float32)
            row[idx] = 0.92
            rows.append(row)
        return np.stack(rows)


def _fake_anchors(frame_bgr: np.ndarray) -> tuple[float | None, int | None, int | None]:
    if int(frame_bgr[0, 0, 0]) == _CLASS_INDEX["result"]:
        # 结算帧：有比分、无交战倒计时（交战钟会挡住 result 关局）
        return None, 1, 0
    return None, None, None


@pytest.fixture
def hybrid_deps():
    return {
        "classifier": _EncodedClassifier(),
        "extract_fn": _fake_extract,
        "read_anchors_fn": _fake_anchors,
    }


def test_hybrid_detect_sets_boundary_source_and_round_key(tmp_path, hybrid_deps) -> None:
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")

    rounds = detect_valorant_rounds_hybrid(
        str(video),
        time_range=(0.0, 60.0),
        model_dir=FIXTURE_DIR,
        cancel_check=lambda: False,
        session_id="sess1",
        **hybrid_deps,
    )

    assert rounds
    assert rounds[0]["boundary_source"] == "valorant_hybrid_v1"
    assert rounds[0]["round_key"] == "hybrid-sess1-3"
    assert rounds[0]["phase"] == "combat"
    assert rounds[0]["start_by"] == "model_buy_exit"
    assert rounds[0]["end_by"] in {"model_result", "model_score"}
    assert "boundary_evidence" in rounds[0]
    assert "model_version" in rounds[0]


def test_dense_refine_does_not_change_round_key(tmp_path, hybrid_deps) -> None:
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")

    rounds = detect_valorant_rounds_hybrid(
        str(video),
        time_range=(0.0, 60.0),
        session_id="sess1",
        **hybrid_deps,
    )

    assert len(rounds) == 1
    assert rounds[0]["round_key"] == "hybrid-sess1-3"
    assert rounds[0]["start"] != 3.0 or rounds[0]["end"] != rounds[0]["start"]


def test_discarded_and_open_rounds_not_in_output(tmp_path, hybrid_deps) -> None:
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")

    def combat_only_extract(
        video_path: str,
        *,
        start_sec: float,
        end_sec: float,
        fps: float,
        ffmpeg_path: str,
        cancel_check=None,
        overlap_sec: float = 2.0,
    ) -> list[tuple[float, np.ndarray]]:
        del video_path, ffmpeg_path, overlap_sec
        frames = []
        step = 1.0 / max(fps, 0.1)
        t = start_sec
        while t <= end_sec + 1e-6:
            frames.append((t, _make_class_frame("combat")))
            t += step
        return frames

    rounds = detect_valorant_rounds_hybrid(
        str(video),
        time_range=(0.0, 200.0),
        extract_fn=combat_only_extract,
        classifier=hybrid_deps["classifier"],
        read_anchors_fn=hybrid_deps["read_anchors_fn"],
    )
    assert rounds == []


def test_cancel_check_returns_empty(tmp_path, hybrid_deps) -> None:
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")

    cancelled = {"value": False}

    def cancel_after_first(*_args, **_kwargs):
        if cancelled["value"]:
            from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

            raise FFmpegCancelled("cancelled")
        cancelled["value"] = True
        return _fake_extract(*_args, **_kwargs)

    from lsc.utils.cancellable_ffmpeg import FFmpegCancelled

    with pytest.raises(FFmpegCancelled):
        detect_valorant_rounds_hybrid(
            str(video),
            time_range=(0.0, 60.0),
            cancel_check=lambda: True,
            extract_fn=cancel_after_first,
            classifier=hybrid_deps["classifier"],
            read_anchors_fn=hybrid_deps["read_anchors_fn"],
        )


def test_start_refine_runs_once_per_round(tmp_path, hybrid_deps) -> None:
    video = tmp_path / "x.mp4"
    video.write_bytes(b"fake")
    dense_calls: list[tuple[float, float]] = []

    def counting_extract(
        video_path: str,
        *,
        start_sec: float,
        end_sec: float,
        fps: float,
        ffmpeg_path: str,
        cancel_check=None,
        overlap_sec: float = 2.0,
    ) -> list[tuple[float, np.ndarray]]:
        if abs(fps - 9.0) < 0.01:
            dense_calls.append((start_sec, end_sec))
        return _fake_extract(
            video_path,
            start_sec=start_sec,
            end_sec=end_sec,
            fps=fps,
            ffmpeg_path=ffmpeg_path,
            cancel_check=cancel_check,
            overlap_sec=overlap_sec,
        )

    rounds = detect_valorant_rounds_hybrid(
        str(video),
        time_range=(0.0, 60.0),
        session_id="sess1",
        classifier=hybrid_deps["classifier"],
        extract_fn=counting_extract,
        read_anchors_fn=hybrid_deps["read_anchors_fn"],
    )

    assert len(rounds) == 1
    # opened 精修一次起点 + closed 精修终点；禁止 closed 再跑一遍起点密扫
    assert len(dense_calls) == 2


def test_incremental_runtime_state_keeps_open_round_across_windows(tmp_path) -> None:
    video = tmp_path / "long-round.mp4"
    video.write_bytes(b"fake")

    def long_round_class(ts: float) -> str:
        if ts < 30.0:
            return "buy"
        if ts < 170.0:
            return "combat"
        if ts < 174.0:
            return "result"
        return "buy"

    def long_round_extract(
        video_path: str,
        *,
        start_sec: float,
        end_sec: float,
        fps: float,
        ffmpeg_path: str,
        cancel_check=None,
        overlap_sec: float = 2.0,
    ) -> list[tuple[float, np.ndarray]]:
        del video_path, ffmpeg_path
        frames = []
        t = max(0.0, start_sec - overlap_sec)
        stop = end_sec + overlap_sec
        step = 1.0 / max(fps, 0.1)
        while t <= stop + 1e-6:
            frames.append((t, _make_class_frame(long_round_class(t))))
            t += step
        return frames

    state: dict = {}
    common = {
        "classifier": _EncodedClassifier(),
        "extract_fn": long_round_extract,
        "read_anchors_fn": _fake_anchors,
        "session_id": "session-long",
        "runtime_state": state,
    }

    first = detect_valorant_rounds_hybrid(str(video), time_range=(0.0, 130.0), **common)
    assert state["prev_predicted"] == "combat"
    second = detect_valorant_rounds_hybrid(str(video), time_range=(100.0, 190.0), **common)

    assert first == []
    assert len(second) == 1
    assert second[0]["round_key"].startswith("hybrid-session-long-")


def test_incremental_runtime_state_keeps_lookback_for_round_closure(tmp_path) -> None:
    video = tmp_path / "incremental.mp4"
    video.write_bytes(b"fake")
    coarse_starts: list[float] = []

    def recording_extract(*args, **kwargs):
        if abs(float(kwargs["fps"]) - 1.0) < 0.01:
            coarse_starts.append(float(kwargs["start_sec"]))
        return _fake_extract(*args, **kwargs)

    state: dict = {}
    common = {
        "classifier": _EncodedClassifier(),
        "extract_fn": recording_extract,
        "read_anchors_fn": _fake_anchors,
        "runtime_state": state,
    }

    detect_valorant_rounds_hybrid(str(video), time_range=(0.0, 60.0), **common)
    first_cursor = float(state["last_processed_ts"])
    detect_valorant_rounds_hybrid(str(video), time_range=(0.0, 90.0), **common)

    assert coarse_starts[0] == 0.0
    # 增量窗必须保留调度器请求的回看上下文；下一局买枪画面需要它来闭合
    # 上一局。旧帧仍会在进入 FSM 前去重，因此不会重复推理。
    assert coarse_starts[-1] == 0.0
    assert first_cursor == 60.0
    assert state["last_inference_frames"] < 40
    assert state["inference_frames_total"] > state["last_inference_frames"]
