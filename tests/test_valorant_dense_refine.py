from __future__ import annotations

import pytest

from lsc.analyzer.round_detector import build_frame_evidence


def test_ocr_failure_leaves_scores_none_not_zero() -> None:
    ev = build_frame_evidence(
        timestamp=12.0,
        probs={"non_game": 0.01, "buy": 0.02, "combat": 0.9, "result": 0.05, "replay": 0.02},
        predicted_class="combat",
        timer_seconds=None,
        left_score=None,
        right_score=None,
        model_version="stub",
    )
    assert ev.left_score is None
    assert ev.right_score is None
    assert ev.timer_seconds is None


def test_dense_start_is_first_stable_combat_frame() -> None:
    from lsc.analyzer.round_detector import refine_boundary_from_sequence

    seq = [
        (0.0, "buy"), (0.1, "buy"), (0.2, "buy"),
        (0.3, "combat"), (0.4, "combat"), (0.5, "combat"),
    ]
    start = refine_boundary_from_sequence(seq, target="combat", min_stable=3, fps=10)
    assert start == 0.3


def test_end_keeps_1_5s_result_tail_unless_replay() -> None:
    from lsc.analyzer.round_detector import compute_clip_end

    assert compute_clip_end(result_event_ts=10.0, next_states=[]) == 11.5
    assert compute_clip_end(
        result_event_ts=10.0,
        next_states=[(10.8, "replay")],
    ) == 10.8


def test_no_strong_end_does_not_emit_vision_confirmed() -> None:
    from lsc.analyzer.round_detector import grade_round_confirmation

    status = grade_round_confirmation(
        start_strong=True,
        end_strong=False,
        score_confirm=False,
    )
    assert status == "pending"


def test_strong_boundaries_emit_vision_confirmed() -> None:
    from lsc.analyzer.round_detector import grade_round_confirmation

    assert grade_round_confirmation(
        start_strong=True,
        end_strong=True,
        score_confirm=True,
    ) == "vision_confirmed"
    assert grade_round_confirmation(
        start_strong=True,
        end_strong=True,
        score_confirm=False,
    ) == "vision_confirmed"
    assert grade_round_confirmation(
        start_strong=True,
        end_strong=False,
        score_confirm=True,
    ) == "pending"


def test_low_confidence_argmax_is_unknown() -> None:
    import numpy as np

    from lsc.analyzer.round_detector import _predict_class_from_probs

    label = _predict_class_from_probs(
        np.asarray([0.20, 0.21, 0.20, 0.20, 0.19], dtype=np.float32),
        thresholds={"stable_prob": 0.55, "high_prob": 0.8},
    )

    assert label == "unknown"


def test_extract_frames_raises_on_ffmpeg_failure(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    import lsc.analyzer.round_detector as rd
    import lsc.utils.cancellable_ffmpeg as cancellable
    from lsc.analyzer.round_detector import extract_frames_cancellable

    class _FailedFFmpeg:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self) -> None:
            pass

        def wait(self, timeout_sec: float):
            del timeout_sec
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"decode failed")

    monkeypatch.setattr(cancellable, "CancellableFFmpeg", _FailedFFmpeg)
    monkeypatch.setattr(rd, "ffmpeg_hwaccel_args", lambda _mode: [])
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"broken")

    with pytest.raises(RuntimeError, match="hybrid frame extract failed"):
        extract_frames_cancellable(
            str(video),
            start_sec=0.0,
            end_sec=10.0,
            fps=1.0,
            ffmpeg_path="ffmpeg",
        )


def test_extract_frames_cmd_uses_hwaccel_and_downscale(tmp_path, monkeypatch) -> None:
    """New pipe-based implementation: verify hwaccel args and vf filter in command."""
    import subprocess as _sp

    import lsc.analyzer.round_detector as rd
    from lsc.analyzer.round_detector import extract_frames_cancellable

    cmds: list[list[str]] = []

    class _FakeProc:
        def __init__(self, cmd, **_kw):
            cmds.append(list(cmd))
            self.returncode = 0
            self.stdout = _FakePipe(b"")
            self.stderr = _FakePipe(b"")

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

        def communicate(self, timeout=None):
            return b"", b""

    class _FakePipe:
        def __init__(self, data: bytes):
            self._data = data
            self._pos = 0

        def read(self, n=-1):
            if self._pos >= len(self._data):
                return b""
            chunk = self._data[self._pos:self._pos + n]
            self._pos += n
            return chunk

    monkeypatch.setattr(_sp, "Popen", _FakeProc)
    monkeypatch.setattr(
        rd, "ffmpeg_hwaccel_args", lambda _mode: ["-hwaccel", "d3d11va"]
    )
    monkeypatch.setattr(rd, "read_settings_ocr_accel", lambda: "dml")
    # 禁用 GPU scale 路径，专注测试 hwaccel + CPU 滤镜
    monkeypatch.setattr(rd, "build_hwaccel_vf", lambda vf, **kw: ([], vf))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    extract_frames_cancellable(
        str(video),
        start_sec=1.0,
        end_sec=3.0,
        fps=1.0,
        ffmpeg_path="ffmpeg",
    )

    # 应有 hwaccel 尝试 + 纯 CPU 回退 = 2 个命令（第一个成功则只执行 1 个）
    assert len(cmds) >= 1
    cmd = cmds[0]
    assert cmd[1:3] == ["-hwaccel", "d3d11va"]
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=640:-2" in vf
    assert "fps=1.000" in vf


def test_extract_frames_falls_back_without_hwaccel(tmp_path, monkeypatch) -> None:
    """Pipe-based: first attempt with hwaccel fails, second without succeeds."""
    import subprocess as _sp

    import lsc.analyzer.round_detector as rd
    from lsc.analyzer.round_detector import extract_frames_cancellable

    cmds: list[list[str]] = []
    call_count = [0]

    class _FallbackProc:
        def __init__(self, cmd, **_kw):
            cmds.append(list(cmd))
            call_count[0] += 1
            # 第一次（带 hwaccel）失败，第二次成功
            self.returncode = 1 if "-hwaccel" in cmd else 0
            self.stdout = _FakePipe2(b"")
            self.stderr = _FakePipe2(b"hw fail" if self.returncode == 1 else b"")

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            pass

        def communicate(self, timeout=None):
            return b"", b""

    class _FakePipe2:
        def __init__(self, data: bytes):
            self._data = data
            self._pos = 0

        def read(self, n=-1):
            if self._pos >= len(self._data):
                return b""
            chunk = self._data[self._pos:self._pos + n]
            self._pos += n
            return chunk

    monkeypatch.setattr(_sp, "Popen", _FallbackProc)
    monkeypatch.setattr(
        rd, "ffmpeg_hwaccel_args", lambda _mode: ["-hwaccel", "cuda"]
    )
    monkeypatch.setattr(rd, "read_settings_ocr_accel", lambda: "cuda")
    # 禁用 GPU scale 路径，专注测试 hwaccel 回退逻辑
    monkeypatch.setattr(rd, "build_hwaccel_vf", lambda vf, **kw: ([], vf))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    extract_frames_cancellable(
        str(video),
        start_sec=0.0,
        end_sec=2.0,
        fps=1.0,
        ffmpeg_path="ffmpeg",
    )

    assert len(cmds) == 2
    assert "-hwaccel" in cmds[0]
    assert "-hwaccel" not in cmds[1]
    assert "scale=640:-2" in cmds[1][cmds[1].index("-vf") + 1]


def test_digit_anchors_ignore_timer_digits(monkeypatch) -> None:
    import numpy as np

    import lsc.analyzer.ocr_detector as ocr_detector
    from lsc.analyzer.round_detector import read_top_digit_anchors

    def _box(x: float):
        return [[x, 0.0], [x + 10.0, 0.0], [x + 10.0, 10.0], [x, 10.0]]

    def _ocr(_img):
        return ([
            (_box(90.0), "1:23", 0.99),
            (_box(10.0), "5", 0.99),
            (_box(170.0), "4", 0.99),
        ], None)

    monkeypatch.setattr(ocr_detector, "_get_ocr", lambda: _ocr)

    timer, left, right = read_top_digit_anchors(
        np.zeros((200, 200, 3), dtype=np.uint8)
    )

    assert timer == 83.0
    assert (left, right) == (5, 4)


def test_digit_anchors_pass_ndarray_not_tempfile(monkeypatch) -> None:
    import numpy as np

    import lsc.analyzer.ocr_detector as ocr_detector
    from lsc.analyzer.round_detector import read_top_digit_anchors

    seen: dict = {}

    def _ocr(img):
        seen["type"] = type(img).__name__
        seen["shape"] = getattr(img, "shape", None)
        return ([], None)

    monkeypatch.setattr(ocr_detector, "_get_ocr", lambda: _ocr)
    read_top_digit_anchors(np.zeros((100, 200, 3), dtype=np.uint8))

    assert seen["type"] == "ndarray"
    assert seen["shape"] == (12, 200, 3)
