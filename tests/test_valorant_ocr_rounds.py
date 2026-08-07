"""纯 OCR 回合检测器测试：FSM 相位流转 + 循环先验 + 信号处理。"""
from __future__ import annotations

from lsc.analyzer.valorant_ocr_rounds import (
    OcrRoundFSM,
    _apply_phase_cycle_prior,
    _is_combat_timer,
    _is_prep_timer,
)


def _feed_labels(fsm: OcrRoundFSM, seq: list) -> list[dict]:
    out: list[dict] = []
    for item in seq:
        label, ts, timer = item[0], item[1], item[2]
        timer_raw = bool(item[3]) if len(item) > 3 else False
        out.extend(fsm.feed(label, ts, timer, timer_raw=timer_raw))
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
    assert not _is_combat_timer(45.0)
    assert _is_prep_timer(45.0)
    assert _is_prep_timer(1.0)
    assert not _is_prep_timer(0.0)
    assert not _is_prep_timer(None)


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
    """跨窗口 FSM 持久化：回合在窗口 A 打开（无出点不产出），窗口 B 见真准备信号后闭合产出。"""
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

    # window2 (121..150)：SETTLE 延续 → prep 130-131（两帧确认）→ 闭合产出
    # 注：last_processed_ts=120 会过滤 ≤120 的帧，fake 读数必须与过滤后帧一一对应
    w2_readings = []
    for ts in range(121, 151):
        if ts < 130:
            w2_readings.append((None, None, None))
        else:
            w2_readings.append((30.0 - (ts - 130), None, None))

    it2 = iter(w2_readings)

    def fake_top2(img):
        return next(it2, (None, None, None))

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: _frames_for(121, 150))
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top2)
    monkeypatch.setattr(mod, "_read_center_banner", lambda img: (False, False))

    rounds2 = mod.detect_valorant_rounds_ocr(str(video), time_range=(100.0, 150.0),
                                              runtime_state=state, finalize=False)
    assert len(rounds2) == 1
    assert rounds2[0]["start"] == 7.0   # 入点跨窗口持久化（锚点两帧确认 @7）
    assert rounds2[0]["end"] == 131.0   # 窗口 B 的真准备信号（两帧确认）
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
    """prep 密扫 min_start_ts：游程首帧不得早于结算保护线（排除结算画面倒计时）。"""
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    frames = [(90.0 + i * 0.1, np.zeros((360, 640, 3), dtype=np.uint8)) for i in range(61)]
    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: frames)

    # 前 30 帧（90-93s）结算倒计时（prep 读数），之后（93+）真准备
    readings = [(30.0 - i * 0.1, None, None) for i in range(30)] + [(28.0, None, None)] * 31
    it = iter(readings)
    monkeypatch.setattr(mod, "_read_top_anchors", lambda img: next(it, (None, None, None)))
    monkeypatch.setattr(mod, "_read_center_banner", lambda img: (False, False))

    # 无 min_start_ts：选中结算倒计时首帧 90.0
    ts = mod._refine_boundary_ts("v.mp4", "ffmpeg", 95.0, "prep")
    assert ts == 90.0

    # min_start_ts=94.0：结算倒计时（90-93s）被排除，游程从 94.0 起（94-95.9 持续 prep）
    it2 = iter(readings)
    monkeypatch.setattr(mod, "_read_top_anchors", lambda img: next(it2, (None, None, None)))
    ts2 = mod._refine_boundary_ts("v.mp4", "ffmpeg", 95.0, "prep", min_start_ts=94.0)
    assert ts2 == 94.0

    # min_start_ts=96.0：94-95.9 也排除 → None（保留粗扫值）
    it3 = iter(readings)
    monkeypatch.setattr(mod, "_read_top_anchors", lambda img: next(it3, (None, None, None)))
    ts3 = mod._refine_boundary_ts("v.mp4", "ffmpeg", 95.0, "prep", min_start_ts=96.0)
    assert ts3 is None

def test_settle_residual_countdown_not_exported_as_round(monkeypatch, tmp_path):
    """结算后残余交战钟 52→≤45 不得标成 prep/combat，避免导出买枪空窗（观感入出点反了）。

    正确：整段交战+结算保持到 HUD 空档后的真准备，再 next_prep 闭合。
    """
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod

    video = tmp_path / "video.mp4"
    video.write_bytes(b"dummy")

    # combat 0-12 → settle 13 → 残余钟 52→7 (14-59) → 空档 → prep 70+
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
        return (False, True) if ci[0] == 14 else (False, False)

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
    assert rounds[0]["start"] == 1.0  # 锚点两帧确认
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

    # 回合1：prep 0-5 → combat 6-30 → settle 31 → neutral → prep 60 → 闭合 60
    # 回合2：combat 61 起（新回合交战钟），next_combat 闭合回合1？
    # 简化：直接构造相邻重叠场景——回合1 end=60.5（密扫后），回合2 start=60.4
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
        return (False, True) if ci[0] == 32 else (False, False)

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
    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: [
        (float(ts), np.zeros((360, 640, 3), dtype=np.uint8)) for ts in range(0, 70)
    ])
    monkeypatch.setattr(mod, "_read_top_anchors", lambda img: next(it, (None, None, None)))
    monkeypatch.setattr(mod, "_read_center_banner", lambda img: (False, False))

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
