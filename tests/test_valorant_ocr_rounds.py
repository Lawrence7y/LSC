"""纯 OCR 回合检测器测试：FSM 相位流转 + 循环先验 + 信号处理。"""
from __future__ import annotations

from lsc.analyzer.valorant_ocr_rounds import (
    OcrRoundFSM,
    _apply_phase_cycle_prior,
    _is_combat_timer,
    _is_prep_timer,
)


def _feed_labels(fsm: OcrRoundFSM, seq: list[tuple[str, float, float | None]]) -> list[dict]:
    out: list[dict] = []
    for label, ts, timer in seq:
        out.extend(fsm.feed(label, ts, timer))
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
    """交战超时（175s）强制闭合。"""
    fsm = OcrRoundFSM()
    seq = [
        ("prep", 0.0, 30.0),
        ("combat", 1.0, 90.0),
        ("combat", 180.0, 20.0),
        ("combat", 181.0, 19.0),
        ("combat", 182.0, 18.0),
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    assert rounds[0]["end_by"] == "max_open_close"
    assert rounds[0]["end"] == 180.0


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


def test_missed_prep_closes_on_next_combat():
    """SETTLE 错过准备直接见新交战钟 → 出点取新交战首帧（宁长勿短）。"""
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
        ("combat", 25.0, 90.0),  # 距 result 7s ≥6 且满钟（≥85）→ 新回合
        ("combat", 26.0, 79.0),
    ]
    rounds = _feed_labels(fsm, seq)
    assert len(rounds) == 1
    assert rounds[0]["end"] == 25.0
    assert rounds[0]["end_by"] == "next_combat"


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
        ("combat", 19.0, 63.0),  # 残留钟：距 result 1s → 忽略
        ("combat", 20.0, 62.0),
        ("combat", 21.0, 61.0),
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
    # 未产生假的新回合开局
    assert len(rounds) == 1


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
    """出点契约：next_prep 才 vision_confirmed；伪造出点一律 pending。"""
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
    assert rounds[0]["confirm_status"] == "vision_confirmed"
    assert rounds[0]["end_by"] == "next_prep"
    assert "round_key" not in rounds[0]  # round_key 由消费端统一生成

    # max_open_close → pending
    fsm2 = OcrRoundFSM()
    seq2 = [("prep", 0.0, 30.0), ("combat", 1.0, 90.0), ("combat", 180.0, 20.0)]
    rounds2 = _feed_labels(fsm2, seq2)
    assert rounds2[0]["confirm_status"] == "pending"
    assert rounds2[0]["end_by"] == "max_open_close"

    # next_combat → pending
    fsm3 = OcrRoundFSM()
    seq3 = [("prep", 0.0, 30.0)] + [("combat", float(i), 95.0 - i) for i in range(1, 15)] + [
        ("settle", 15.0, None),
        ("neutral", 16.0, None),
        ("neutral", 17.0, None),
        ("neutral", 18.0, None),
        ("neutral", 19.0, None),
        ("neutral", 20.0, None),
        ("neutral", 21.0, None),
        ("combat", 22.0, 90.0),  # 满钟（≥85）→ 新回合
    ]
    rounds3 = _feed_labels(fsm3, seq3)
    assert rounds3[0]["confirm_status"] == "pending"
    assert rounds3[0]["end_by"] == "next_combat"

    # force_close → pending
    fsm4 = OcrRoundFSM()
    _feed_labels(fsm4, [("prep", 0.0, 30.0)] + [("combat", float(i), 95.0 - i) for i in range(1, 15)])
    closed4 = fsm4.force_close(end_ts=30.0)
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

def test_pending_upgrade_across_windows(monkeypatch, tmp_path):
    """跨窗口 pending 升级：伪造出点回合在后续窗口见到真准备信号 → 升级 vision_confirmed。"""
    import numpy as np

    import lsc.analyzer.valorant_ocr_rounds as mod
    from lsc.analyzer.valorant_ocr_rounds import _round_key

    video = tmp_path / "video.mp4"
    video.write_bytes(b"dummy")

    def _frames_for(start, end):
        return [(float(ts), np.zeros((360, 640, 3), dtype=np.uint8))
                for ts in range(int(start), int(end) + 1)]

    # window1 (0..120)：prep 0-5 → combat 6-100 → settle 101 → neutral → combat 111（next_combat → pending）
    w1_readings = []
    for ts in range(0, 121):
        if ts <= 5:
            w1_readings.append((30.0 - ts, None, None))
        elif ts <= 100:
            w1_readings.append((95.0 - (ts - 6), None, None))
        elif ts == 101 or ts <= 110:
            w1_readings.append((None, None, None))
        else:
            w1_readings.append((90.0 - (ts - 111), None, None))

    it1 = iter(w1_readings)

    def fake_top1(img):
        return next(it1, (None, None, None))

    ci1 = [0]

    def fake_center1(img):
        ci1[0] += 1
        return (False, True) if ci1[0] == 102 else (False, False)

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: _frames_for(0, 120))
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top1)
    monkeypatch.setattr(mod, "_read_center_banner", fake_center1)

    state: dict = {}
    rounds1 = mod.detect_valorant_rounds_ocr(str(video), time_range=(0.0, 120.0),
                                              runtime_state=state, finalize=False)
    assert len(rounds1) == 1
    assert rounds1[0]["confirm_status"] == "pending"
    assert rounds1[0]["end_by"] == "next_combat"

    # window2 (121..150)：回合B 交战钟延续 → 130-135 单次 prep 信号（不足闭合游程）→ 升级事件
    # 注：last_processed_ts=120 会过滤 ≤120 的帧，fake 读数必须与过滤后帧一一对应
    w2_readings = []
    for ts in range(121, 151):
        if ts < 130:
            w2_readings.append((90.0 - (ts - 111), None, None))   # 回合B 交战钟（111 起）
        elif ts <= 135:
            w2_readings.append((30.0 - (ts - 130), None, None))   # 单次 prep 信号（距交战 <30s 不闭合）
        else:
            w2_readings.append((60.0 - (ts - 135), None, None))   # 交战继续

    it2 = iter(w2_readings)

    def fake_top2(img):
        return next(it2, (None, None, None))

    monkeypatch.setattr(mod, "extract_frames_cancellable", lambda *a, **k: _frames_for(121, 150))
    monkeypatch.setattr(mod, "_read_top_anchors", fake_top2)
    monkeypatch.setattr(mod, "_read_center_banner", lambda img: (False, False))

    rounds2 = mod.detect_valorant_rounds_ocr(str(video), time_range=(100.0, 150.0),
                                              runtime_state=state, finalize=False)
    print("ROUNDS2:", rounds2)
    upgraded = [r for r in rounds2 if r.get("upgraded")]
    assert len(upgraded) == 1
    assert upgraded[0]["confirm_status"] == "vision_confirmed"
    assert upgraded[0]["end_by"] == "next_prep"
    assert upgraded[0]["end"] == 131.0  # 准备信号需两帧确认
    assert upgraded[0]["round_key"] == _round_key(6.0)
    # 升级后 pending 队列清空
    assert state.get("pending_out_rounds") == {}


def test_replay_annotation():
    """结算后 ≥5s 的 neutral 段标注为 replay；result_ts 之前的非游戏段不标。"""
    from lsc.analyzer.valorant_ocr_rounds import _annotate_replay

    r = {"start": 10.0, "end": 60.0, "result_ts": 30.0}
    labels = [
        (5.0, "combat", None), (10.0, "combat", None), (15.0, "combat", None),
        (20.0, "neutral", None), (25.0, "neutral", None), (30.0, "settle", None),
        (35.0, "neutral", None), (40.0, "neutral", None), (45.0, "neutral", None),
        (50.0, "neutral", None), (55.0, "neutral", None), (60.0, "prep", None),
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
