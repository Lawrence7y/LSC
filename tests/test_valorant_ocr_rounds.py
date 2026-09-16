"""纯 OCR 回合检测器测试：FSM 相位流转 + 循环先验 + 信号处理。"""
from __future__ import annotations

from lsc.analyzer.valorant_ocr_rounds import (
    OcrRoundFSM,
    _apply_phase_cycle_prior,
    _is_combat_timer,
)


def test_broadcast_fast_top_roi_skips_per_frame_wide_fallback(monkeypatch):
    """在线赛事粗扫的普通帧只跑紧 ROI；宽 ROI 仅由周期哨兵调用。"""
    import numpy as np

    import lsc.analyzer.ocr_detector as ocr_detector
    import lsc.analyzer.valorant_ocr_rounds as mod

    calls: list[tuple[int, int]] = []

    def fake_ocr(image):
        calls.append(tuple(image.shape[:2]))
        return [], 0.0

    monkeypatch.setattr(ocr_detector, "_get_ocr", lambda: fake_ocr)
    frame = np.zeros((360, 640, 3), dtype=np.uint8)

    mod._read_top_anchors(frame, "broadcast_fast")
    assert len(calls) == 1
    assert calls[0][0] == int(360 * mod._TOP_BAND_RATIO)

    calls.clear()
    mod._read_top_anchors(frame, "broadcast")
    assert len(calls) == len(mod._BROADCAST_TOP_BAND_RATIOS)


def _feed_labels(
    fsm: OcrRoundFSM,
    seq: list,
    *,
    broadcast_mode: bool = False,
) -> list[dict]:
    out: list[dict] = []
    for item in seq:
        label, ts, timer = item[0], item[1], item[2]
        timer_raw = bool(item[3]) if len(item) > 3 else False
        out.extend(
            fsm.feed(
                label,
                ts,
                timer,
                timer_raw=timer_raw,
                broadcast_mode=broadcast_mode,
            )
        )
    return out


def test_normal_cycle_clips_from_combat_to_next_prep():
    """POV 循环：准备→交战→结算→下一准备。入点=交战首帧，出点=下回合准备首帧。"""
    fsm = OcrRoundFSM()
    seq = [
        ("neutral", 0.0, None),
        ("prep", 1.0, 30.0),       # 第一回合准备
        ("prep", 2.0, 29.0),
        ("combat", 3.0, 70.0),     # 入点候选
        ("combat", 4.0, 69.0),
        ("combat", 5.0, 40.0),     # 交战尾段（锚点存活）
        ("combat", 6.0, 20.0),
        ("combat", 7.0, 10.0),
        ("combat", 8.0, 5.0),
        ("combat", 9.0, 2.0),
        ("combat", 10.0, 1.0),
        ("combat", 11.0, 0.0),
        ("combat", 12.0, 0.0),
        ("settle", 13.0, None),    # 结算
        ("neutral", 14.0, None),   # 回放/非游戏
        ("neutral", 15.0, None),
        ("neutral", 16.0, None),
        ("neutral", 17.0, None),
        ("neutral", 18.0, None),
        ("neutral", 19.0, None),
        ("prep", 20.0, 30.0),      # 下回合准备首帧 = 出点
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    r = rounds[0]
    assert r["start"] == 3.0
    assert r["end"] == 20.0
    assert r["end_by"] == "next_prep"
    assert r["start_by"] == "ocr_combat"
    assert r["boundary_source"] == "valorant_ocr_v1"
    assert r["phase"] == "combat"


def test_broadcast_cycle_with_replay():
    """赛事循环：准备→交战→结算→回放→下一准备，回放也包进切片。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 95.0),
        ("combat", 2.0, 94.0),
        ("combat", 3.0, 93.0),
        ("combat", 4.0, 92.0),
        ("combat", 5.0, 91.0),
        ("combat", 6.0, 90.0),
        ("combat", 7.0, 89.0),
        ("combat", 8.0, 88.0),
        ("combat", 9.0, 87.0),
        ("combat", 10.0, 86.0),
        ("combat", 11.0, 85.0),
        ("combat", 12.0, 84.0),
        ("settle", 13.0, None),
        ("neutral", 14.0, None),   # 回放
        ("neutral", 15.0, None),
        ("neutral", 16.0, None),
        ("neutral", 17.0, None),
        ("neutral", 18.0, None),
        ("prep", 19.0, 28.0),
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    assert (rounds[0]["start"], rounds[0]["end"]) == (1.0, 19.0)


def test_broadcast_fresh_clock_does_not_split_replay_without_prep():
    """官方回放中的高计时器不能绕过准备阶段开启临时新回合。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0, True),
        ("combat", 2.0, 89.0, True),
        ("combat", 3.0, 88.0, True),
        ("combat", 4.0, 87.0, True),
        ("combat", 5.0, 86.0, True),
        ("combat", 6.0, 85.0, True),
        ("combat", 7.0, 84.0, True),
        ("combat", 8.0, 83.0, True),
        ("combat", 9.0, 82.0, True),
        ("combat", 10.0, 81.0, True),
        ("combat", 11.0, 80.0, True),
        ("combat", 12.0, 79.0, True),
        ("settle", 13.0, None),
        ("neutral", 14.0, None),
        ("neutral", 15.0, None),
        ("neutral", 16.0, None),
        ("neutral", 17.0, None),
        ("neutral", 18.0, None),
        ("combat", 19.0, 95.0, True),  # Replay HUD 的伪新计时器
        ("combat", 20.0, 94.0, True),
        ("combat", 21.0, 93.0, True),
        ("neutral", 22.0, None),
        ("neutral", 23.0, None),
        ("neutral", 24.0, None),
        ("prep", 25.0, 30.0),
        ("prep", 26.0, 29.0),
    ]

    rounds = _feed_labels(fsm, seq, broadcast_mode=True)

    assert len(rounds) == 1
    assert rounds[0]["start"] == 1.0
    assert rounds[0]["end"] == 25.0
    assert rounds[0]["end_by"] == "next_prep"


def test_broadcast_late_fresh_clock_closes_missing_prep_as_pending():
    """真实下一回合缺少 prep 时，不能把多个回合吞成一个巨候选。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0, True),
        ("combat", 1.0, 95.0, True),
        ("combat", 2.0, 94.0, True),
        ("combat", 3.0, 93.0, True),
        ("combat", 4.0, 92.0, True),
        ("settle", 5.0, None, False),
        # 结算后超过 45s，新的真实满钟读数应触发降级 next_combat。
        ("neutral", 20.0, None, False),
        ("combat", 51.0, 95.0, True),
        ("combat", 52.0, 94.0, True),
    ]

    rounds = _feed_labels(fsm, seq, broadcast_mode=True)

    assert len(rounds) == 1
    assert rounds[0]["start"] == 1.0
    assert rounds[0]["end"] == 51.0
    assert rounds[0]["end_by"] == "next_combat"
    assert rounds[0]["confirm_status"] == "pending"


def test_settle_prep_too_close_to_result_ignored():
    """结算画面 5s 倒计时不得当准备阶段：距 result <6s 的 prep 忽略。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 89.0),
        ("combat", 3.0, 88.0),
        ("combat", 4.0, 87.0),
        ("combat", 5.0, 86.0),
        ("combat", 6.0, 85.0),
        ("combat", 7.0, 84.0),
        ("combat", 8.0, 83.0),
        ("combat", 9.0, 82.0),
        ("combat", 10.0, 81.0),
        ("combat", 11.0, 80.0),
        ("combat", 12.0, 79.0),
        ("combat", 13.0, 78.0),
        ("combat", 14.0, 77.0),
        ("combat", 15.0, 76.0),
        ("combat", 16.0, 75.0),
        ("combat", 17.0, 74.0),
        ("settle", 18.0, None),
        ("prep", 19.0, 3.0),   # 结算倒计时：距 result 1s → 忽略
        ("prep", 20.0, 2.0),
        ("prep", 21.0, 1.0),
        ("prep", 22.0, 30.0),  # 真准备：距 result 4s → 仍忽略
        ("prep", 23.0, 29.0),
        ("prep", 24.0, 28.0),  # 距 result 6s → 闭合
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    assert rounds[0]["end"] == 24.0
    assert rounds[0]["end_by"] == "next_prep"


def test_midstream_join_requires_countdown():
    """中段切入：连续 3 帧交战钟且递减才开局；不递减不开局。"""
    fsm = OcrRoundFSM()
    seq = [
        ("combat", 0.0, 90.0),
        ("combat", 1.0, 89.0),
        ("combat", 2.0, 88.0),
        ("combat", 3.0, 87.0),
        ("combat", 4.0, 86.0),
        ("combat", 5.0, 85.0),
        ("combat", 6.0, 84.0),
        ("combat", 7.0, 83.0),
        ("combat", 8.0, 82.0),
        ("combat", 9.0, 81.0),
        ("combat", 10.0, 80.0),
        ("combat", 11.0, 79.0),
        ("combat", 12.0, 78.0),
        ("settle", 13.0, None),
        ("neutral", 14.0, None),
        ("neutral", 15.0, None),
        ("neutral", 16.0, None),
        ("neutral", 17.0, None),
        ("neutral", 18.0, None),
        ("neutral", 19.0, None),
        ("prep", 20.0, 30.0),
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    assert rounds[0]["start"] == 0.0
    assert rounds[0]["end"] == 20.0

    # 冻结倒计时（回放残留）：连续读数不递减 → 不开局
    fsm2 = OcrRoundFSM()
    seq2 = [
        ("combat", 0.0, 90.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 90.0),
        ("neutral", 3.0, None),
    ]
    assert _feed_labels(fsm2, seq2) == []


def test_combat_prep_without_result_requires_run_and_min_duration():
    """无结算信号直接见 prep：须 prep 连续 ≥4 帧且距交战 ≥30s（防交战尾段误判）。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 89.0),
        ("combat", 3.0, 88.0),
        ("combat", 4.0, 87.0),
        ("combat", 5.0, 86.0),
        ("combat", 6.0, 85.0),
        ("combat", 7.0, 84.0),
        ("combat", 8.0, 83.0),
        ("combat", 9.0, 82.0),
        ("combat", 10.0, 81.0),
        ("combat", 11.0, 80.0),
        ("combat", 12.0, 79.0),
        ("combat", 13.0, 78.0),
        ("combat", 14.0, 77.0),
        ("combat", 15.0, 76.0),
        ("combat", 16.0, 75.0),
        ("combat", 17.0, 74.0),
        ("combat", 18.0, 73.0),
        ("combat", 19.0, 72.0),
        ("combat", 20.0, 71.0),
        ("combat", 21.0, 70.0),
        ("combat", 22.0, 69.0),
        ("combat", 23.0, 68.0),
        ("combat", 24.0, 67.0),
        ("combat", 25.0, 66.0),
        ("combat", 26.0, 65.0),
        ("combat", 27.0, 64.0),
        ("combat", 28.0, 63.0),
        ("combat", 29.0, 62.0),
        ("combat", 30.0, 61.0),
        ("combat", 31.0, 60.0),
        ("combat", 32.0, 59.0),
        ("combat", 33.0, 58.0),
        ("combat", 34.0, 57.0),
        ("combat", 35.0, 56.0),
        ("prep", 36.0, 30.0),   # 距交战 35s ≥30，prep 游程开始
        ("prep", 37.0, 29.0),
        ("prep", 38.0, 28.0),
        ("prep", 39.0, 27.0),   # 连续 4 帧 → 闭合，出点=首帧 36
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    assert rounds[0]["end"] == 36.0
    assert rounds[0]["end_by"] == "next_prep"

    # 交战尾段单帧 prep（距交战 <30s）→ 不闭合
    fsm2 = OcrRoundFSM()
    seq2 = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 89.0),
        ("combat", 3.0, 88.0),
        ("combat", 4.0, 87.0),
        ("combat", 5.0, 86.0),
        ("combat", 6.0, 85.0),
        ("combat", 7.0, 84.0),
        ("combat", 8.0, 83.0),
        ("combat", 9.0, 82.0),
        ("combat", 10.0, 81.0),
        ("combat", 11.0, 80.0),
        ("combat", 12.0, 79.0),
        ("combat", 13.0, 78.0),
        ("combat", 14.0, 77.0),
        ("combat", 15.0, 76.0),
        ("combat", 16.0, 75.0),
        ("combat", 17.0, 74.0),
        ("combat", 18.0, 73.0),
        ("combat", 19.0, 72.0),
        ("prep", 20.0, 30.0),  # 距交战 19s <30 → 忽略，不闭合
        ("combat", 21.0, 71.0),
    ]
    assert _feed_labels(fsm2, seq2) == []


def test_settle_timeout_closes_open_tail():
    """结算后长时间等不到下回合准备 → 超时闭合，宁长勿短。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 89.0),
        ("combat", 3.0, 88.0),
        ("combat", 4.0, 87.0),
        ("combat", 5.0, 86.0),
        ("combat", 6.0, 85.0),
        ("combat", 7.0, 84.0),
        ("combat", 8.0, 83.0),
        ("combat", 9.0, 82.0),
        ("combat", 10.0, 81.0),
        ("combat", 11.0, 80.0),
        ("combat", 12.0, 79.0),
        ("combat", 13.0, 78.0),
        ("combat", 14.0, 77.0),
        ("combat", 15.0, 76.0),
        ("settle", 16.0, None),
    ]
    rounds = _feed_labels(fsm, seq)
    assert rounds == []  # 未闭合
    closed = fsm.force_close(end_ts=116.0)
    assert len(closed) == 1
    assert closed[0]["end_by"] == "open_tail"
    assert closed[0]["end"] == 116.0


def test_max_open_force_close():
    """严格契约：交战超时不再强制闭合，回合保持打开直到真出点或收尾。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 180.0, 20.0),
        ("combat", 181.0, 19.0),
        ("combat", 182.0, 18.0),
    ]
    rounds = _feed_labels(fsm, seq)
    assert rounds == []  # 无真出点 → 不产出
    # 收尾例外：finalize 时 open_tail+pending 产出
    closed = fsm.force_close(end_ts=182.0)
    assert len(closed) == 1
    assert closed[0]["end_by"] == "open_tail"
    assert closed[0]["confirm_status"] == "pending"


def test_short_round_discarded():
    """过短回合（<10s）丢弃。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 89.0),
        ("combat", 3.0, 88.0),
        ("combat", 4.0, 87.0),
        ("combat", 5.0, 86.0),
        ("combat", 6.0, 85.0),
        ("combat", 7.0, 84.0),
        ("combat", 8.0, 83.0),
        ("combat", 9.0, 82.0),
        ("combat", 10.0, 81.0),
        ("combat", 11.0, 80.0),
        ("combat", 12.0, 79.0),
        ("combat", 13.0, 78.0),
        ("combat", 14.0, 77.0),
        ("combat", 15.0, 76.0),
        ("combat", 16.0, 75.0),
        ("combat", 17.0, 74.0),
        ("combat", 18.0, 73.0),
        ("combat", 19.0, 72.0),
        ("settle", 20.0, None),
        ("neutral", 21.0, None),
        ("neutral", 22.0, None),
        ("neutral", 23.0, None),
        ("neutral", 24.0, None),
        ("neutral", 25.0, None),
        ("prep", 26.0, 28.0),
    ]
    # 交战 1.0→26.0（25s）→ 闭合 26.0：end-start=25 >= 10 → 保留
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1

    fsm2 = OcrRoundFSM()
    seq2 = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 89.0),
        ("combat", 3.0, 88.0),
        ("settle", 4.0, None),
        ("neutral", 5.0, None),
        ("neutral", 6.0, None),
        ("neutral", 7.0, None),
        ("neutral", 8.0, None),
        ("neutral", 9.0, None),
        ("neutral", 10.0, None),
        ("prep", 10.0, 28.0),  # end-start=9 < 10 → 丢弃
    ]
    assert _feed_labels(fsm2, seq2) == []


def test_settle_ignores_residual_extrapolated_clock():
    """结算后外推残余钟（非 raw）即使距结算 ≥45s 也不得 next_combat 开新局。

    现场：结算后 52→7 的残余倒计时被当成交战，在 ts≈58 误开新回合，
    切片变成买枪/空窗（用户观感「入出点搞反」）。
    """
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
    ] + [("combat", float(i), 95.0 - i) for i in range(2, 15)] + [
        ("settle", 15.0, None),
        ("neutral", 16.0, None),
        ("neutral", 20.0, None),
        # 距结算 45s+，但 timer_raw=False 的残余外推钟
        ("combat", 70.0, 7.0, False),
        ("combat", 71.0, 6.0, False),
        ("combat", 72.0, 5.0, False),
    ]
    assert _feed_labels(fsm, seq) == []
    # 仍停在 SETTLE，可用真满钟开新回合
    closed = _feed_labels(fsm, [("combat", 80.0, 95.0, True)])
    assert len(closed) == 1
    assert closed[0]["end_by"] == "next_combat"
    assert closed[0]["end"] == 80.0


def test_missed_prep_closes_on_next_combat():
    """SETTLE 错过准备见新交战钟：旧回合以降级 next_combat 闭合（pending），并开新回合。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 89.0),
        ("combat", 3.0, 88.0),
        ("combat", 4.0, 87.0),
        ("combat", 5.0, 86.0),
        ("combat", 6.0, 85.0),
        ("combat", 7.0, 84.0),
        ("combat", 8.0, 83.0),
        ("combat", 9.0, 82.0),
        ("combat", 10.0, 81.0),
        ("combat", 11.0, 80.0),
        ("combat", 12.0, 79.0),
        ("combat", 13.0, 78.0),
        ("combat", 14.0, 77.0),
        ("combat", 15.0, 76.0),
        ("combat", 16.0, 75.0),
        ("combat", 17.0, 74.0),
        ("settle", 18.0, None),
        ("neutral", 19.0, None),
        ("neutral", 20.0, None),
        ("neutral", 21.0, None),
        ("neutral", 22.0, None),
        ("neutral", 23.0, None),
        ("neutral", 24.0, None),
        ("combat", 25.0, 90.0, True),  # 满钟 → 旧回合 next_combat 闭合 + 新回合开局
        ("combat", 26.0, 79.0),
        ("neutral", 27.0, None),
        ("neutral", 28.0, None),
        ("neutral", 29.0, None),
        ("neutral", 30.0, None),
        ("neutral", 31.0, None),
        ("neutral", 32.0, None),
        ("neutral", 33.0, None),
        ("neutral", 34.0, None),
        ("neutral", 35.0, None),
        ("neutral", 36.0, None),
        ("neutral", 37.0, None),
        ("neutral", 38.0, None),
        ("neutral", 39.0, None),
        ("neutral", 40.0, None),
        ("neutral", 41.0, None),
        ("neutral", 42.0, None),
        ("neutral", 43.0, None),
        ("neutral", 44.0, None),
        ("neutral", 45.0, None),
        ("neutral", 46.0, None),
        ("neutral", 47.0, None),
        ("neutral", 48.0, None),
        ("neutral", 49.0, None),
        ("neutral", 50.0, None),
        ("neutral", 51.0, None),
        ("neutral", 52.0, None),
        ("neutral", 53.0, None),
        ("neutral", 54.0, None),
        ("prep", 55.0, 30.0),    # 新回合无 result：距交战 30s，游程开始
        ("prep", 56.0, 29.0),
        ("prep", 57.0, 28.0),
        ("prep", 58.0, 27.0),    # 连续 4 帧 → 闭合，出点=55
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 2
    assert rounds[0]["start"] == 1.0
    assert rounds[0]["end"] == 25.0
    assert rounds[0]["end_by"] == "next_combat"
    assert rounds[0]["confirm_status"] == "pending"
    assert rounds[1]["start"] == 25.0
    assert rounds[1]["end"] == 55.0
    assert rounds[1]["end_by"] == "next_prep"
    assert rounds[1]["confirm_status"] == "vision_confirmed"


def test_chained_settle_miss_prep_keeps_each_round():
    """连续多次 SETTLE→新交战钟：每次都应降级闭合，不得连环放弃导致长空窗漏检。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("settle", 20.0, None),
        ("neutral", 26.0, None),
        ("combat", 30.0, 95.0, True),  # 闭合 1–30 pending
        ("settle", 50.0, None),
        ("neutral", 56.0, None),
        ("combat", 60.0, 95.0, True),  # 闭合 30–60 pending
        ("settle", 80.0, None),
        ("neutral", 86.0, None),
        ("prep", 90.0, 30.0),  # 真出点闭合 60–90
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 3
    assert rounds[0]["end_by"] == "next_combat" and rounds[0]["start"] == 1.0 and rounds[0]["end"] == 30.0
    assert rounds[1]["end_by"] == "next_combat" and rounds[1]["start"] == 30.0 and rounds[1]["end"] == 60.0
    assert rounds[2]["end_by"] == "next_prep" and rounds[2]["start"] == 60.0 and rounds[2]["end"] == 90.0


def test_settle_residual_combat_clock_ignored():
    """结算画面残余交战钟（距 result <6s）不得当新回合开局。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 89.0),
        ("combat", 3.0, 88.0),
        ("combat", 4.0, 87.0),
        ("combat", 5.0, 86.0),
        ("combat", 6.0, 85.0),
        ("combat", 7.0, 84.0),
        ("combat", 8.0, 83.0),
        ("combat", 9.0, 82.0),
        ("combat", 10.0, 81.0),
        ("combat", 11.0, 80.0),
        ("combat", 12.0, 79.0),
        ("combat", 13.0, 78.0),
        ("combat", 14.0, 77.0),
        ("combat", 15.0, 76.0),
        ("combat", 16.0, 75.0),
        ("combat", 17.0, 74.0),
        ("settle", 18.0, None),
        ("combat", 19.0, 63.0, True),  # 残留钟：距 result 1s → 忽略
        ("combat", 20.0, 62.0, True),
        ("combat", 21.0, 61.0, True),
        ("neutral", 22.0, None),
        ("neutral", 23.0, None),
        ("neutral", 24.0, None),
        ("neutral", 25.0, None),
        ("prep", 26.0, 30.0),    # 真准备 → 闭合
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    assert rounds[0]["end"] == 26.0
    assert rounds[0]["end_by"] == "next_prep"
    assert rounds[0]["start"] == 1.0  # 未产生假的新回合开局


def test_settle_extrapolated_clock_never_opens_new_round():
    """高值残余外推钟（结算后 last_timer 外推 ≥85）不得开新回合（timer_raw=False）。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 2.0, 89.0),
        ("combat", 3.0, 88.0),
        ("combat", 4.0, 87.0),
        ("combat", 5.0, 86.0),
        ("combat", 6.0, 85.0),
        ("combat", 7.0, 84.0),
        ("combat", 8.0, 83.0),
        ("combat", 9.0, 82.0),
        ("combat", 10.0, 81.0),
        ("combat", 11.0, 80.0),
        ("combat", 12.0, 79.0),
        ("combat", 13.0, 78.0),
        ("combat", 14.0, 77.0),
        ("combat", 15.0, 76.0),
        ("combat", 16.0, 75.0),
        ("combat", 17.0, 74.0),
        ("settle", 18.0, None),
        ("combat", 19.0, 90.0, False),  # 外推 90（非 raw）→ 不得开新回合
        ("combat", 20.0, 89.0, False),
        ("combat", 21.0, 88.0, False),
        ("combat", 22.0, 87.0, False),
        ("combat", 23.0, 86.0, False),
        ("neutral", 24.0, None),
        ("combat", 25.0, 85.0, False),  # 外推残余钟（非 raw）→ 忽略，不开新回合
        ("combat", 26.0, 84.0, False),
        ("combat", 27.0, 83.0, False),
        ("combat", 28.0, 82.0, False),
        ("combat", 29.0, 81.0, False),
        ("combat", 30.0, 80.0, False),
        ("combat", 31.0, 79.0, False),
        ("prep", 32.0, 30.0),
        ("prep", 33.0, 29.0),    # 两帧确认 → 真准备 → 闭合
    ]
    rounds = _feed_labels(fsm, seq)
    # 外推残余钟不触发新回合 → 回合 1 起，32 处真 prep 闭合
    assert len(rounds) == 1
    assert rounds[0]["start"] == 1.0
    assert rounds[0]["end"] == 32.0
    assert rounds[0]["end_by"] == "next_prep"


def test_phase_cycle_prior_patches_gaps_and_removes_noise():
    # 不补缝：交战段之间的 neutral 保持原样（非游戏阶段透明）
    assert _apply_phase_cycle_prior(["combat", "neutral", "combat"]) == [
        "neutral", "neutral", "neutral",
    ]
    assert _apply_phase_cycle_prior(["combat", "neutral", "neutral", "combat"]) == [
        "neutral", "neutral", "neutral", "neutral",
    ]
    # 删短噪：≤2 帧孤立 combat → 前一帧标签
    assert _apply_phase_cycle_prior(["neutral", "combat", "neutral", "prep"]) == [
        "neutral", "neutral", "neutral", "prep",
    ]
    assert _apply_phase_cycle_prior(["settle", "combat", "combat", "prep"]) == [
        "settle", "settle", "settle", "prep",
    ]
    # 长交战段保持
    assert _apply_phase_cycle_prior(["combat"] * 5) == ["combat"] * 5


def test_timer_helpers():
    assert _is_combat_timer(46.0)
    assert _is_combat_timer(100.0)
    assert not _is_combat_timer(106.0)  # 超物理上限（误读）
    # ≤45s 不是"交战钟"：回合计时器最后 45 秒同样是 ≤45s。
    assert not _is_combat_timer(45.0)
    assert not _is_combat_timer(1.0)
    assert not _is_combat_timer(0.0)
    assert not _is_combat_timer(None)


def test_no_prep_phase_is_inferred_from_low_timer(monkeypatch, tmp_path):
    """删除「≤45s 计时器 = 买枪/准备」推断：锚点失效 + 尾段低计时器不得提前闭合。

    复现 2026-09-12 赛事流：真实出点前 ~45s 处回合计时器读到 ≤45s，旧实现把该帧
    标成 prep → 4 帧游程后以 next_prep 收尾（351.312 → 403.125，真实 449.875）。
    新契约：低计时器只算 neutral，必须等结算（比分跳变）或新回合满钟才闭合。
    """
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    video = tmp_path / "video.mp4"
    video.write_bytes(b"dummy")

    # 0-29  交战钟 100→71（建立锚点/回合）
    # 30-59 交战钟 70→41（继续交战）
    # 60-74 交战钟 40→1（旧实现把 45 穿越后的连续 ≤45 读数当 prep → 60 处闭合）
    # 75    结算横幅 → SETTLE
    # 76-95 HUD 空档（无读数）
    # 96+   新回合满钟 100 → 旧回合以 next_combat 闭合
    readings = []
    for ts in range(0, 120):
        if ts <= 29:
            readings.append((100.0 - ts, None, None))
        elif ts <= 59:
            readings.append((70.0 - (ts - 30), None, None))
        elif ts <= 74:
            readings.append((40.0 - (ts - 60), None, None))
        elif ts < 96:
            readings.append((None, None, None))
        else:
            readings.append((100.0 - (ts - 96), None, None))

    it = iter(readings)

    def fake_top(img):
        return next(it, (None, None, None))

    ci = [0]

    def fake_center(img):
        ci[0] += 1
        # 第 76 帧（ts=75）命中结算横幅
        return (False, True) if ci[0] == 76 else (False, False)

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: [
        (float(ts), np.zeros((360, 640, 3), dtype=np.uint8)) for ts in range(0, 120)
    ])
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top)
    monkeypatch.setattr(mod, "_read_center_banner", fake_center)

    rounds = mod.detect_valorant_rounds_ocr(
        str(video), time_range=(0.0, 120.0), runtime_state={}, finalize=False,
        refine_boundaries=False,
    )
    assert len(rounds) == 1
    assert rounds[0]["start"] == 0.0
    # 关键：出点必须在新回合满钟（96）附近，而不是尾段低计时器游程起点（旧实现 ≈60）
    assert rounds[0]["end"] >= 95.0, rounds[0]
    assert rounds[0]["end_by"] == "next_combat"


def test_fsm_clone_is_independent():
    seq_base = [("prep", 0.0, 30.0), ("combat", 1.0, 90.0)] + [
        ("combat", float(i), 95.0 - i) for i in range(2, 15)
    ]
    fsm = OcrRoundFSM()
    _feed_labels(fsm, seq_base)
    cloned = fsm.clone()
    closed = _feed_labels(cloned, [("settle", 15.0, None), ("prep", 21.0, 30.0)])
    assert len(closed) == 1
    assert closed[0]["start"] == 1.0
    assert closed[0]["end"] == 21.0
    # 原 fsm 未推进，仍可独立闭合
    closed2 = _feed_labels(fsm, [("settle", 15.0, None), ("prep", 21.0, 30.0)])
    assert len(closed2) == 1
    assert closed2[0]["end"] == 21.0

def test_close_contract_pending_vs_confirmed():
    """出点契约：next_prep → vision_confirmed；纯 COMBAT/SETTLE 无闭合不产出；收尾 open_tail。"""
    # next_prep → vision_confirmed
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
    ] + [("combat", float(i), 95.0 - i) for i in range(2, 15)] + [
        ("settle", 15.0, None),
        ("neutral", 16.0, None),
        ("neutral", 17.0, None),
        ("neutral", 18.0, None),
        ("neutral", 19.0, None),
        ("neutral", 20.0, None),
        ("prep", 21.0, 30.0),
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    assert rounds[0]["confirm_status"] == "vision_confirmed"
    assert rounds[0]["end_by"] == "next_prep"
    assert "round_key" not in rounds[0]  # round_key 由消费端统一生成

    # 无真出点（长时间 COMBAT）→ 不产出
    fsm2 = OcrRoundFSM()
    seq2 = [("prep", 0.0, 30.0), ("combat", 1.0, 90.0), ("combat", 180.0, 20.0)]
    assert _feed_labels(fsm2, seq2) == []

    # 无真出点（SETTLE 等不到 prep）→ 不产出
    fsm3 = OcrRoundFSM()
    seq3 = [("prep", 0.0, 30.0)] + [("combat", float(i), 95.0 - i) for i in range(1, 15)] + [
        ("settle", 15.0, None),
        ("neutral", 16.0, None),
        ("neutral", 17.0, None),
        ("neutral", 18.0, None),
        ("neutral", 19.0, None),
        ("neutral", 20.0, None),
        ("neutral", 21.0, None),
    ]
    assert _feed_labels(fsm3, seq3) == []

    # 收尾例外：force_close → open_tail + pending
    fsm4 = OcrRoundFSM()
    _feed_labels(fsm4, [("prep", 0.0, 30.0)] + [("combat", float(i), 95.0 - i) for i in range(1, 15)])
    closed4 = fsm4.force_close(end_ts=30.0)
    assert len(closed4) == 1
    assert closed4[0]["confirm_status"] == "pending"
    assert closed4[0]["end_by"] == "open_tail"


def test_prep_run_interrupted_resets():
    """无 result 的 prep 游程：非 prep 帧打断必须清零，不得跨间隔累计。"""
    # 31s/50s/70s/90s 不连续 prep 信号 → 不产生假出点
    fsm = OcrRoundFSM()
    seq = [("prep", 0.0, 30.0), ("combat", 1.0, 90.0)] + [
        ("combat", float(i), 95.0 - i) for i in range(2, 31)
    ] + [
        ("prep", 31.0, 30.0),    # 距交战 30s，游程开始
        ("combat", 32.0, 60.0),  # 打断 → 清零
        ("combat", 33.0, 59.0),
        ("combat", 34.0, 58.0),
        ("prep", 50.0, 30.0),    # 新游程（被打断后重新计数）
        ("neutral", 51.0, None), # 打断 → 清零
        ("prep", 70.0, 30.0),
        ("combat", 71.0, 50.0),  # 打断 → 清零
        ("prep", 90.0, 30.0),
        ("prep", 91.0, 29.0),
        ("prep", 92.0, 28.0),
        ("prep", 93.0, 27.0),    # 连续 4 帧 → 闭合，出点=90（真实首帧）
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    assert rounds[0]["end"] == 90.0
    assert rounds[0]["end_by"] == "next_prep"
    assert rounds[0]["confirm_status"] == "vision_confirmed"


def test_round_key_is_ten_second_bucket():
    """round_key 10s 桶：边界漂移 <5s 键稳定（与消费端 _valorant_round_key 一致）。"""
    from lsc.analyzer.valorant_ocr_rounds import _round_key

    assert _round_key(100.0) == _round_key(104.9)
    assert _round_key(100.0) != _round_key(106.0)
    assert _round_key(4.0) == _round_key(4.5)

def test_open_round_closes_across_windows(monkeypatch, tmp_path):
    """跨窗口 FSM 持久化：回合在窗口 A 打开（无出点不产出），窗口 B 见真准备横幅后闭合产出。

    2026-09-12 契约变更：出点只认中央准备横幅，不再认 ≤45s 计时器读数
    （窗口 B 仍喂 ≤45s 买枪倒计时，用于证明它**不再**触发出点）。
    """
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    video = tmp_path / "video.mp4"
    video.write_bytes(b"dummy")

    def _frames_for(start, end):
        return [(float(ts), np.zeros((360, 640, 3), dtype=np.uint8))
                for ts in range(int(start), int(end) + 1)]

    # window1 (0..120)：prep 0-5 → combat 6-100（锚点两帧确认 @7）→ settle 101 → SETTLE 等 prep
    w1_readings = []
    for ts in range(0, 121):
        if ts <= 5:
            w1_readings.append((30.0 - ts, None, None))
        elif ts <= 100:
            w1_readings.append((95.0 - (ts - 6), None, None))
        else:
            w1_readings.append((None, None, None))

    it1 = iter(w1_readings)
    ci1 = [0]

    def fake_top1(img):
        return next(it1, (None, None, None))

    def fake_center1(img):
        ci1[0] += 1
        return (False, True) if ci1[0] == 102 else (False, False)

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: _frames_for(0, 120))
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top1)
    monkeypatch.setattr(mod, "_read_center_banner", fake_center1)

    state: dict = {}
    rounds1 = mod.detect_valorant_rounds_ocr(str(video), time_range=(0.0, 120.0),
                                              runtime_state=state, finalize=False)
    assert rounds1 == []  # 严格契约：无真出点不产出

    # window2 (121..150)：SETTLE 延续 → 130 起中央准备横幅 → 闭合产出
    # 注：last_processed_ts=120 会过滤 ≤120 的帧，fake 读数必须与过滤后帧一一对应
    w2_readings = []
    for ts in range(121, 151):
        if ts < 130:
            w2_readings.append((None, None, None))
        else:
            w2_readings.append((30.0 - (ts - 130), None, None))

    it2 = iter(w2_readings)
    ci2 = [0]

    def fake_top2(img):
        return next(it2, (None, None, None))

    def fake_center2(img):
        ci2[0] += 1
        # 仅粗扫第 10 帧（ts=130）命中准备横幅；后续（含密扫）保持无横幅，
        # 避免密扫把 end 再往前 refinement 到别的帧上。
        return (True, False) if ci2[0] == 10 else (False, False)

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: _frames_for(121, 150))
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top2)
    monkeypatch.setattr(mod, "_read_center_banner", fake_center2)

    rounds2 = mod.detect_valorant_rounds_ocr(str(video), time_range=(100.0, 150.0),
                                              runtime_state=state, finalize=False)
    assert len(rounds2) == 1
    assert rounds2[0]["start"] == 6.0   # 入点跨窗口持久化（首帧回溯至交战首帧 @6）
    assert rounds2[0]["end"] == 130.0   # 窗口 B 的真准备横幅首帧
    assert rounds2[0]["confirm_status"] == "vision_confirmed"
    assert rounds2[0]["end_by"] == "next_prep"


def test_replay_annotation():
    """结算后 ≥5s 的 neutral 段标注为 replay；result_ts 之前的非游戏段不标。"""
    from lsc.analyzer.valorant_ocr_rounds import _annotate_replay

    r = {"start": 10.0, "end": 60.0, "result_ts": 30.0}
    labels = [
        (5.0, "combat", None, True), (10.0, "combat", None, True), (15.0, "combat", None, True),
        (20.0, "neutral", None, False), (25.0, "neutral", None, False), (30.0, "settle", None, False),
        (35.0, "neutral", None, False), (40.0, "neutral", None, False), (45.0, "neutral", None, False),
        (50.0, "neutral", None, False), (55.0, "neutral", None, False), (60.0, "prep", None, True),
    ]
    _annotate_replay(r, labels)
    assert r["replay_segments"] == [[35.0, 55.0]]

    # 无 result_ts → 不标注
    r2 = {"start": 10.0, "end": 60.0}
    _annotate_replay(r2, labels)
    assert "replay_segments" not in r2


def test_refine_boundary_ts_finds_first_frame(monkeypatch):
    """密扫：±3s @10fps 找目标标签连续游程的首帧真实 PTS。"""
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    frames = [(100.0 + i * 0.1, np.zeros((360, 640, 3), dtype=np.uint8)) for i in range(61)]
    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: frames)

    # combat：前 5 帧无读数，之后 96 持续 → 首帧 = 100.5
    it = iter([(None, None, None)] * 5 + [(96.0, None, None)] * 56)
    monkeypatch.setattr(mod, "_read_top_anchors", lambda img: next(it, (None, None, None)))
    monkeypatch.setattr(mod, "_read_center_banner", lambda img: (False, False))
    ts = mod._refine_boundary_ts("v.mp4", "ffmpeg", 100.0, "combat")
    assert ts == 100.5

    # prep：无 combat 信号 → None（保留粗扫值）
    it2 = iter([(None, None, None)] * 61)
    monkeypatch.setattr(mod, "_read_top_anchors", lambda img: next(it2, (None, None, None)))
    ts2 = mod._refine_boundary_ts("v.mp4", "ffmpeg", 100.0, "combat")
    assert ts2 is None


def test_refine_boundary_ts_respects_min_start(monkeypatch):
    """prep 密扫 min_start_ts：游程首帧不得早于结算保护线（排除结算画面倒计时）。

    2026-09-12 契约变更：prep 密扫**只认中央准备横幅**。≤45s 计时器读数不再算命中
    ——回合计时器最后 45 秒同样是 ≤45s，旧实现据此把出点钉在假边界上。
    """
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    frames = [(90.0 + i * 0.1, np.zeros((360, 640, 3), dtype=np.uint8)) for i in range(61)]
    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: frames)

    # 只有 ≤45s 计时器读数（无任何横幅）→ 不得命中（旧实现会返回 90.0）
    low_readings = [(30.0 - i * 0.1, None, None) for i in range(61)]
    it_low = iter(low_readings)
    monkeypatch.setattr(mod, "_read_top_anchors", lambda img: next(it_low, (None, None, None)))
    monkeypatch.setattr(mod, "_read_center_banner", lambda img: (False, False))
    assert mod._refine_boundary_ts("v.mp4", "ffmpeg", 95.0, "prep") is None

    # 准备横幅：前 30 帧（90-93s）与后 21 帧（94-96s）两段游程
    def _banner_reader():
        counter = [0]

        def read(img):
            counter[0] += 1
            return (counter[0] <= 30 or counter[0] > 40, False)

        return read

    reader = _banner_reader()
    monkeypatch.setattr(mod, "_read_center_banner", reader)
    # 无 min_start_ts：选中第一段游程首帧 90.0
    assert mod._refine_boundary_ts("v.mp4", "ffmpeg", 95.0, "prep") == 90.0

    # min_start_ts=94.0：第一段（90-93s）被排除 → 第二段游程首帧 94.0
    reader2 = _banner_reader()
    monkeypatch.setattr(mod, "_read_center_banner", reader2)
    assert mod._refine_boundary_ts("v.mp4", "ffmpeg", 95.0, "prep", min_start_ts=94.0) == 94.0

    # min_start_ts=96.0：两段都排除 → None（保留粗扫值）
    reader3 = _banner_reader()
    monkeypatch.setattr(mod, "_read_center_banner", reader3)
    assert mod._refine_boundary_ts("v.mp4", "ffmpeg", 95.0, "prep", min_start_ts=96.0) is None

def test_settle_residual_countdown_not_exported_as_round(monkeypatch, tmp_path):
    """结算后残余交战钟 52→≤45 不得标成 prep/combat，避免导出买枪空窗（观感入出点反了）。

    正确：整段交战+结算保持到 HUD 空档后的**真准备横幅**，再 next_prep 闭合。
    2026-09-12 起残段低计时器（≤45）连 prep 也不算了，出点只由横幅驱动。
    """
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    video = tmp_path / "video.mp4"
    video.write_bytes(b"dummy")

    # combat 0-12 → settle 13 → 残余钟 52→7 (14-59) → 空档 → 准备横幅 70+
    readings = []
    for ts in range(0, 90):
        if ts <= 12:
            readings.append((95.0 - ts, None, None))
        elif ts == 13:
            readings.append((None, None, None))
        elif ts <= 59:
            readings.append((52.0 - (ts - 14), None, None))
        elif ts < 70:
            readings.append((None, None, None))
        else:
            readings.append((30.0 - (ts - 70), None, None))

    it = iter(readings)
    ci = [0]

    def fake_top(img):
        return next(it, (None, None, None))

    def fake_center(img):
        ci[0] += 1
        if ci[0] == 14:
            return (False, True)      # ts=13 结算横幅
        return (True, False) if ci[0] == 71 else (False, False)  # ts=70 准备横幅

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: [
        (float(ts), np.zeros((360, 640, 3), dtype=np.uint8)) for ts in range(0, 90)
    ])
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top)
    monkeypatch.setattr(mod, "_read_center_banner", fake_center)

    rounds = mod.detect_valorant_rounds_ocr(
        str(video),
        time_range=(0.0, 90.0),
        runtime_state={},
        finalize=False,
        refine_boundaries=False,
    )
    assert len(rounds) == 1
    assert rounds[0]["start"] == 0.0  # 首帧回溯至交战首帧
    assert rounds[0]["end"] >= 70.0
    assert rounds[0]["end_by"] == "next_prep"
    # 不得在残余钟段（≤45 误当 prep）提前闭合成短切片
    assert rounds[0]["end"] - rounds[0]["start"] > 50.0


def test_adjacent_rounds_do_not_overlap(monkeypatch, tmp_path):
    """相邻回合边界修整：前一回合出点不得越过下一回合入点（密扫微调导致重叠时）。"""
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    video = tmp_path / "video.mp4"
    video.write_bytes(b"dummy")

    # 回合1：combat 6-30 → settle 31 → neutral → 准备横幅 60 → 闭合 60
    # 回合2：combat 63 起（新回合交战钟）
    readings = []
    for ts in range(0, 80):
        if ts <= 5:
            readings.append((30.0 - ts, None, None))
        elif ts <= 30:
            readings.append((95.0 - (ts - 6), None, None))
        elif ts == 31 or ts <= 59:
            readings.append((None, None, None))
        elif ts <= 62:
            readings.append((30.0 - (ts - 60), None, None))
        else:
            readings.append((90.0 - (ts - 63), None, None))

    it = iter(readings)
    ci = [0]

    def fake_top(img):
        return next(it, (None, None, None))

    def fake_center(img):
        ci[0] += 1
        if ci[0] == 32:
            return (False, True)   # 结算横幅
        # 准备横幅：给一段连续窗口（粗扫帧与 ts 非严格 1:1，用区间触发更稳）
        return (True, False) if 32 <= ci[0] <= 70 else (False, False)

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: [
        (float(ts), np.zeros((360, 640, 3), dtype=np.uint8)) for ts in range(0, 80)
    ])
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top)
    monkeypatch.setattr(mod, "_read_center_banner", fake_center)

    rounds = mod.detect_valorant_rounds_ocr(str(video), time_range=(0.0, 80.0),
                                             runtime_state={}, finalize=True)
    assert len(rounds) == 2
    assert rounds[0]["end"] <= rounds[1]["start"]
    assert rounds[0]["confirm_status"] == "vision_confirmed"
    assert rounds[0]["end_by"] == "next_prep"


def test_detect_skips_boundary_refine_when_disabled(monkeypatch, tmp_path):
    """refine_boundaries=False：粗扫直接返回，不得调用密扫（持续分析增量路径）。"""
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    video = tmp_path / "video.mp4"
    video.write_bytes(b"dummy")

    # prep 0-5 → combat 6-40 → settle 41 → prep 55 闭合
    readings = []
    # combat 6-40 → settle 41 → 准备横幅 55 闭合
    readings = []
    for ts in range(0, 70):
        if ts <= 5:
            readings.append((30.0 - ts, None, None))
        elif ts <= 40:
            readings.append((95.0 - (ts - 6), None, None))
        elif ts < 55:
            readings.append((None, None, None))
        else:
            readings.append((30.0 - (ts - 55), None, None))
    it = iter(readings)
    ci2 = [0]

    def fake_center2(img):
        ci2[0] += 1
        if ci2[0] == 42:
            return (False, True)  # ts=41 结算横幅
        return (True, False) if ci2[0] == 56 else (False, False)  # ts=55 准备横幅

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: [
        (float(ts), np.zeros((360, 640, 3), dtype=np.uint8)) for ts in range(0, 70)
    ])
    monkeypatch.setattr(mod, "_read_top_anchors", lambda img: next(it, (None, None, None)))
    monkeypatch.setattr(mod, "_read_center_banner", fake_center2)

    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("refine must not run when refine_boundaries=False")

    monkeypatch.setattr(mod, "_refine_boundary_ts", _boom)

    rounds = mod.detect_valorant_rounds_ocr(
        str(video),
        time_range=(0.0, 70.0),
        runtime_state={},
        finalize=False,
        refine_boundaries=False,
    )
    assert called["n"] == 0
    assert rounds
    assert all(r.get("boundary_refined") is False for r in rounds)


def test_refine_valorant_round_boundaries_updates_and_marks(monkeypatch):
    """独立密扫 helper：更新 start/end 并标记 boundary_refined=True。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    rounds = [{
        "start": 100.0,
        "end": 160.0,
        "confirm_status": "vision_confirmed",
        "end_by": "next_prep",
        "boundary_refined": False,
        "result_ts": 150.0,
    }]
    calls: list[tuple] = []

    def fake_refine(_vp, _ff, center, target, *, min_start_ts=None, cancel_check=None):
        calls.append((center, target, min_start_ts))
        if target == "combat":
            return 98.5
        return 158.2

    monkeypatch.setattr(mod, "_refine_boundary_ts", fake_refine)
    out = mod.refine_valorant_round_boundaries(
        rounds, "v.mp4", "ffmpeg", cancel_check=None,
    )
    assert len(calls) == 2
    assert out[0]["start"] == 98.5
    assert out[0]["end"] == 158.2
    assert out[0]["boundary_refined"] is True


def test_refine_broadcast_does_not_mark_refined_without_bidirectional_evidence(monkeypatch):
    """赛事精修只补起点时，不得无条件 boundary_refined=True（双向证据契约）。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    rounds = [{
        "start": 100.0,
        "end": 160.0,
        "source_profile": "broadcast",
        "confirm_status": "pending",
        "end_by": "open_tail",
        "boundary_refined": False,
    }]

    def fake_refine(_vp, _ff, center, target, *, cancel_check=None):
        assert target == "combat"
        return 98.5

    monkeypatch.setattr(mod, "_refine_boundary_ts", fake_refine)
    out = mod.refine_valorant_round_boundaries(
        rounds, "v.mp4", "ffmpeg", cancel_check=None, source_profile="broadcast",
    )
    assert out[0]["start_refined"] == 98.5
    assert out[0]["start_delta"] == 1.5
    assert out[0]["start_confidence"] == 0.95
    # 缺少 end_delta/end_confidence：广播赛事不能声称边界已完整精修。
    assert out[0]["boundary_refined"] is False


def test_refine_broadcast_complete_when_end_evidence_already_present(monkeypatch):
    """赛事精修补上起点证据后，若审计已给出终点证据，则可标记 refined。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    rounds = [{
        "start": 100.0,
        "end": 160.0,
        "source_profile": "broadcast",
        "confirm_status": "vision_confirmed",
        "end_by": "broadcast_exclusion",
        "boundary_refined": False,
        "end_delta": 3.0,
        "end_confidence": 0.92,
        "end_refined": 157.0,
    }]

    def fake_refine(_vp, _ff, center, target, *, cancel_check=None):
        assert target == "combat"
        return 98.5

    monkeypatch.setattr(mod, "_refine_boundary_ts", fake_refine)
    out = mod.refine_valorant_round_boundaries(
        rounds, "v.mp4", "ffmpeg", cancel_check=None, source_profile="broadcast",
    )
    assert out[0]["start_delta"] == 1.5
    assert out[0]["boundary_refined"] is True


def test_post_settle_recovers_on_fresh_clock_without_gap(monkeypatch, tmp_path):
    """结算后即使没有 HUD 空档，新回合满钟（1:40）出现也应正常闭合旧回合（不吞回合）。

    2026-09-12 契约变更：原实现在此依赖「距结算 ≥6s 且读到 ≤45s 准备倒计时」解除
    post_settle_hold；该依据已删除（回合尾段同样 ≤45s）。现在由满钟（≥85s）承担
    同一个职责，且出点为降级 `next_combat`（不再冒充 next_prep）。
    """
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    video = tmp_path / "video.mp4"
    video.write_bytes(b"dummy")

    # combat 0-10 → settle 11 → 结算残余倒计时 5→1 (12-15) → 新回合满钟 100+ (16-19)
    readings = []
    for ts in range(0, 20):
        if ts <= 10:
            readings.append((90.0 - ts, None, None))
        elif ts == 11:
            readings.append((None, None, None))
        elif ts <= 15:
            readings.append((5.0 - (ts - 12), None, None))
        else:
            readings.append((100.0 - (ts - 16), None, None))

    it = iter(readings)
    ci = [0]

    def fake_top(img):
        return next(it, (None, None, None))

    def fake_center(img):
        ci[0] += 1
        # 仅结算横幅；全程无准备横幅（证明不依赖 ≤45s 倒计时）
        return (False, True) if ci[0] == 12 else (False, False)

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: [
        (float(ts), np.zeros((360, 640, 3), dtype=np.uint8)) for ts in range(0, 20)
    ])
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top)
    monkeypatch.setattr(mod, "_read_center_banner", fake_center)

    rounds = mod.detect_valorant_rounds_ocr(
        str(video),
        time_range=(0.0, 20.0),
        runtime_state={},
        finalize=False,
        refine_boundaries=False,
    )
    assert len(rounds) == 1
    assert rounds[0]["start"] == 0.0
    assert rounds[0]["end_by"] == "next_combat"
    assert rounds[0]["end"] >= 16.0


def test_refine_boundary_ts_combat_skips_center_banner_and_early_stops(monkeypatch):
    """combat 密扫必须跳过 center banner OCR，并在连续 2 帧命中时提前退出。"""
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    # 构造 30 帧 (5fps 下 6 秒)
    frames = [(100.0 + i * 0.2, np.zeros((360, 640, 3), dtype=np.uint8)) for i in range(30)]
    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: frames)

    center_called = [0]
    top_called = [0]

    def fake_center(img):
        center_called[0] += 1
        return False, False

    def fake_top(img):
        top_called[0] += 1
        # 前 2 帧无读数，第 3、4 帧出现交战钟 (95.0)，后续依然有帧
        if top_called[0] <= 2:
            return None, None, None
        return 95.0, None, None

    monkeypatch.setattr(mod, "_read_center_banner", fake_center)
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top)

    ts = mod._refine_boundary_ts("v.mp4", "ffmpeg", 103.0, "combat")
    assert ts == 100.4  # 第 3 帧的时间戳 100.0 + 2*0.2 = 100.4
    assert center_called[0] == 0  # combat 密扫完全跳过 center banner OCR
    assert top_called[0] == 4  # 提前退出：仅运行了 4 帧 OCR，而非全部 30 帧



# ── A6（2026-09-10）：关键词表剔除高光叠加字样 + 回放水印否决 ──────────────


def test_end_banner_keywords_exclude_highlight_overlays() -> None:
    """clutch/ace/triple 是解说高光回放的叠加字样，不得再当作回合结束横幅。

    原表中它们与 victory/defeat 同列，会让回放被判为"回合结束"（见根因报告）。
    """
    import lsc.analyzer.valorant_ocr_rounds as mod

    for word in ("clutch", "ace", "triple"):
        assert word not in mod._END_BANNER_KEYWORDS, f"{word} 应已从结束横幅表移除"
    # 真结算词必须保留
    for word in ("victory", "defeat", "eliminated", "获胜", "戰敗"):
        assert word in mod._END_BANNER_KEYWORDS
    # 回放正面识别表存在且含中英关键词
    assert "回放" in mod._REPLAY_BANNER_KEYWORDS
    assert "replay" in mod._REPLAY_BANNER_KEYWORDS


def _stub_center_ocr(monkeypatch, text: str) -> None:
    """让中央横幅 OCR 对任意 ROI 都返回同一串文本。"""
    import lsc.analyzer.ocr_detector as ocr_detector

    def _ocr(_image):
        return ([[(0, 0, 10, 10), text, 0.99]], 0.0)

    monkeypatch.setattr(ocr_detector, "_get_ocr", lambda: _ocr)


def test_replay_banner_vetoes_prep_and_end(monkeypatch) -> None:
    """命中回放/慢动作水印时必须否决 prep/end，不得把回放当边界。"""
    import numpy as np
    import lsc.analyzer.valorant_ocr_rounds as mod

    frame = np.zeros((360, 640, 3), dtype=np.uint8)

    # 基线：纯结算横幅仍判 end
    _stub_center_ocr(monkeypatch, "VICTORY")
    assert mod._read_center_banner(frame, "broadcast") == (False, True)

    # 基线：纯准备横幅仍判 prep
    _stub_center_ocr(monkeypatch, "购买阶段")
    assert mod._read_center_banner(frame, "broadcast") == (True, False)

    # 回放叠加结算字样（英文）→ 否决
    _stub_center_ocr(monkeypatch, "REPLAY VICTORY")
    assert mod._read_center_banner(frame, "broadcast") == (False, False)

    # 回放叠加准备字样（中文）→ 否决
    _stub_center_ocr(monkeypatch, "精彩回放 购买阶段")
    assert mod._read_center_banner(frame, "broadcast") == (False, False)

    # 慢动作水印 → 否决
    _stub_center_ocr(monkeypatch, "SLOW MOTION")
    assert mod._read_center_banner(frame, "broadcast") == (False, False)


# ── A5（2026-09-10）：消费 replay_segments，终点不得伸进赛后回放块 ──────────


def test_apply_replay_end_exclusion_trims_to_first_segment() -> None:
    """终点落在回放段之后 → 收到首个回放段起点，并留下审计字段。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 100.0, "end": 200.0, "result_ts": 190.0,
         "confirm_status": "vision_confirmed", "replay_segments": [[195.0, 205.0]]}
    trimmed = mod.apply_replay_end_exclusion(r)
    assert trimmed == 5.0
    assert r["end"] == 195.0
    assert r["end_before_replay_exclusion"] == 200.0
    assert r["replay_end_excluded_sec"] == 5.0
    # 入点不得被这一改动影响
    assert r["start"] == 100.0


def test_apply_replay_end_exclusion_is_noop_without_segments() -> None:
    """未识别到回放段时为空操作（不得写入任何字段）。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    for r in ({"start": 1.0, "end": 2.0}, {"start": 1.0, "end": 2.0, "replay_segments": []}):
        before = dict(r)
        assert mod.apply_replay_end_exclusion(r) is None
        assert r == before


def test_apply_replay_end_exclusion_respects_result_ts_floor() -> None:
    """回放段起点早于结算瞬间 → 不裁剪（切片必须至少含回合结果）。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 10.0, "end": 100.0, "result_ts": 90.0,
         "replay_segments": [[50.0, 95.0]]}
    assert mod.apply_replay_end_exclusion(r) is None
    assert r["end"] == 100.0


def test_apply_replay_end_exclusion_noop_when_end_already_inside() -> None:
    """终点已不晚于首个回放段起点 → 无需裁剪。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 10.0, "end": 90.0, "result_ts": 80.0,
         "replay_segments": [[95.0, 99.0]]}
    assert mod.apply_replay_end_exclusion(r) is None
    assert r["end"] == 90.0


def test_apply_replay_end_exclusion_survives_malformed_segments() -> None:
    """畸形 segment 不得抛异常（脏数据保护）。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    for bad in ([None], [[1.0]], ["x", 2], [["a", "b"]], {"a": 1}, "nope"):
        r = {"start": 1.0, "end": 100.0, "result_ts": 90.0, "replay_segments": bad}
        assert mod.apply_replay_end_exclusion(r) is None
        assert r["end"] == 100.0


def test_apply_replay_end_exclusion_picks_earliest_of_multiple_segments() -> None:
    """多个回放段时取最早那个起点。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 100.0, "end": 300.0, "result_ts": 295.0, "confirm_status": "vision_confirmed",
         "replay_segments": [[297.0, 305.0], [296.0, 300.0]]}
    trimmed = mod.apply_replay_end_exclusion(r)
    assert trimmed == 4.0
    assert r["end"] == 296.0


def test_apply_replay_end_exclusion_blocks_genuinely_lost_footage() -> None:
    """现场回归：声明回放窗与真实内容不符时**必须不裁**（否则切掉真实交战）。

    2026-09-11 实测：某回合被声明回放段 ``[[674.094,679.094],[681.094,688.094]]``，
    照裁会砍掉 **14.539s**；但该区间逐秒 44 帧零 REPLAY 标记、模型全判 combat
    （p_replay ≤0.007）→ 砍掉的是真实交战画面。该回合的审计状态正是
    ``confirm_status=pending`` + ``broadcast_audit=pending_no_exclusion``，
    由"确认证据"这道门挡住（幅度上限已放宽到 30s，不再由它兜这一例）。
    """
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 657.094, "end": 688.633, "result_ts": 668.094,
         "confirm_status": "pending", "broadcast_audit": "pending_no_exclusion",
         "replay_segments": [[674.094, 679.094], [681.094, 688.094]]}
    assert mod.apply_replay_end_exclusion(r) is None
    assert r["end"] == 688.633, "不得改动终点"
    assert "end_before_replay_exclusion" not in r and "replay_end_excluded_sec" not in r
    assert r["replay_end_exclusion_skipped"] == "boundary_not_confirmed"
    assert r["replay_end_exclusion_candidate_sec"] == 14.539
    assert r["replay_end_exclusion_candidate_from"] == 674.094


def test_apply_replay_end_exclusion_allows_long_but_confirmed_trim() -> None:
    """已确认的回合允许按长窗裁剪：实测该裁的窗口是 6s/11s/16s，5s 上限属用错判据。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 700.0, "end": 776.063, "result_ts": 757.0, "confirm_status": "vision_confirmed",
         "replay_segments": [[760.063, 766.063], [768.063, 775.063]]}
    assert mod.apply_replay_end_exclusion(r) == 16.0
    assert r["end"] == 760.063


def test_apply_replay_end_exclusion_caps_absurd_window() -> None:
    """幅度上限只兜"明显荒谬"的声明窗（>30s），并置人工复核。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    # 回放窗起点必须晚于 result_ts（否则被既有的"不得裁进回合内容"下界先挡掉）
    r = {"start": 100.0, "end": 500.0, "result_ts": 400.0, "confirm_status": "vision_confirmed",
         "replay_segments": [[440.0, 460.0]]}
    assert mod.apply_replay_end_exclusion(r) is None
    assert r["end"] == 500.0
    assert r["replay_end_exclusion_skipped"] == "trim_exceeds_cap"
    assert r["replay_end_exclusion_candidate_sec"] == 60.0
    assert r["boundary_review_required"] is True


def test_apply_replay_end_exclusion_skips_without_confirming_evidence() -> None:
    """终点本身尚未确认（pending）时不得用第二个未确认信号去裁它。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 100.0, "end": 200.0, "result_ts": 190.0,
         "confirm_status": "pending", "replay_segments": [[195.0, 205.0]]}
    assert mod.apply_replay_end_exclusion(r) is None
    assert r["end"] == 200.0
    assert r["replay_end_exclusion_skipped"] == "boundary_not_confirmed"
    assert r["replay_end_exclusion_candidate_sec"] == 5.0
    # 幅度没超限 → 不额外要求复核（避免噪音）
    assert "boundary_review_required" not in r


def test_apply_replay_end_exclusion_accepts_broadcast_audit_passed() -> None:
    """`broadcast_audit=passed` 也算确认证据（与 vision_confirmed 等价）。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 100.0, "end": 200.0, "result_ts": 190.0, "broadcast_audit": "passed",
         "replay_segments": [[196.0, 205.0]]}
    assert mod.apply_replay_end_exclusion(r) == 4.0
    assert r["end"] == 196.0


def test_apply_replay_end_exclusion_keeps_finalized_audit_end() -> None:
    """审计已 passed 的 next_prep 出点（end_refined 已定稿）不得被启发式回放段裁早。

    2026-09-15 扩展：旧判据只认 end_by==broadcast_exclusion，于是「审计 passed +
    密扫已定稿」的 next_prep 回合仍会被 replay_segments（「计时器不可读」的间接
    推断，实测有 ~17s 时间戳偏差与成片误标）裁掉最多 30s 真实内容。
    """
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 100.0, "end": 196.25, "end_refined": 196.25,
         "end_by": "next_prep", "broadcast_audit": "passed",
         "confirm_status": "vision_confirmed", "result_ts": 180.0,
         "replay_segments": [[185.922, 196.0]]}
    assert mod.apply_replay_end_exclusion(r) is None
    assert r["end"] == 196.25, "已定稿出点不得被启发式窗口覆盖"
    assert r["replay_end_exclusion_skipped"] == "visual_end_authoritative"
    assert r["replay_end_exclusion_candidate_sec"] == 10.328
    assert r["boundary_review_required"] is True
    assert "end_before_replay_exclusion" not in r


def test_apply_replay_end_exclusion_still_trims_unfinalized_next_prep_end() -> None:
    """边界不变式：**未定稿**的 next_prep 出点（无 end_refined / 审计未 passed）
    仍按原语义裁剪 —— 那正是 A5 要兜的「模型漏掉的实战镜头回放」。"""
    import lsc.analyzer.valorant_ocr_rounds as mod

    r = {"start": 100.0, "end": 200.0, "end_by": "next_prep",
         "confirm_status": "vision_confirmed", "result_ts": 190.0,
         "replay_segments": [[195.0, 205.0]]}
    assert mod.apply_replay_end_exclusion(r) == 5.0
    assert r["end"] == 195.0

def test_buy_phase_onset_requires_upward_reset() -> None:
    """买枪阶段判据 = ≤45s **且**相对上一原始读数上跳 ≥20s（区分交战尾段的连续下降）。

    现场依据（2026-09-12 抽帧）：买枪/装备界面顶中显示 `ROUND 5 0:03`（30s 倒计时）
    紧跟在上一回合结束（读数趋 0）之后；而真实交战尾段 395-403s 是 46→45→44 连续下降。
    """
    from lsc.analyzer.valorant_ocr_rounds import _is_buy_phase_onset

    # 买枪阶段开始：0 → 30 / 3 → 28
    assert _is_buy_phase_onset(0.0, 30.0)
    assert _is_buy_phase_onset(3.0, 28.0)
    assert _is_buy_phase_onset(0.0, 45.0)          # 边界：45s 仍在买枪区间
    # 交战尾段连续下降：46 → 45 → 44（旧实现会在此判 prep）
    assert not _is_buy_phase_onset(46.0, 45.0)
    assert not _is_buy_phase_onset(45.0, 44.0)
    assert not _is_buy_phase_onset(40.0, 30.0)     # 下降更陡也不行
    # 无前序读数 / 越界 / 读数为 0
    assert not _is_buy_phase_onset(None, 30.0)
    assert not _is_buy_phase_onset(3.0, None)
    assert not _is_buy_phase_onset(3.0, 0.0)
    assert not _is_buy_phase_onset(46.0, 50.0)     # >45 属交战钟，不是买枪


def test_round_closes_at_buy_onset_not_at_live_tail(monkeypatch, tmp_path):
    """核心回归：出点落在「买枪阶段首帧」，不再落在「交战尾段穿越 45s」处。

    复刻 2026-09-12 现场机制：回合中段顶部计时器 OCR 失效（stale >35s ⇒ 锚点解除），
    随后读数已是尾段的 ≤45s 且**连续下降**；旧实现把这 45 秒判成 prep → 4 帧游程后
    以 next_prep 收尾（现场 403.125，真实 449.875）。新契约只在「≤45s 且上跳 ≥20s」
    （新买枪阶段首帧）时判 prep，因此出点必须落在买枪阶段，而不是尾段。

    - 0-39  交战钟 100→61（建立并刷新锚点）
    - 40-74 计时器 OCR 失效（无读数 ⇒ 锚点 stale 解除）
    - 75-99 尾段读数 45→21（连续下降，无上跳）
    - 100   回合结束（读数趋 0）
    - 101+  买枪倒计时 30→…（上跳 ≥20 ⇒ 真出点）
    """
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    video = tmp_path / "video.mp4"
    video.write_bytes(b"dummy")

    readings = []
    for ts in range(0, 130):
        if ts <= 39:
            readings.append((100.0 - ts, None, None))
        elif ts <= 74:
            readings.append((None, None, None))          # OCR 失效（锚点随后 stale 解除）
        elif ts <= 99:
            readings.append((45.0 - (ts - 75), None, None))   # 尾段：连续下降的 ≤45s
        elif ts == 100:
            readings.append((0.5, None, None))           # 回合结束
        else:
            readings.append((30.0 - (ts - 101), None, None))  # 买枪倒计时（上跳）
    it = iter(readings)

    def fake_top(img):
        return next(it, (None, None, None))

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: [
        (float(ts), np.zeros((360, 640, 3), dtype=np.uint8)) for ts in range(0, 130)
    ])
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top)
    monkeypatch.setattr(mod, "_read_center_banner", lambda img: (False, False))

    rounds = mod.detect_valorant_rounds_ocr(
        str(video), time_range=(0.0, 130.0), runtime_state={}, finalize=False,
        refine_boundaries=False,
    )
    closed = [r for r in rounds if r.get("end_by") == "next_prep"]
    # 必须恰好一条：出点落在买枪阶段（≈101+），而不是尾段 45s 穿越处（≈75-79）
    assert len(closed) == 1, rounds
    assert closed[0]["start"] == 0.0
    assert closed[0]["end"] >= 100.0, closed[0]
