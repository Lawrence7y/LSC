"""后台赛事审计「批次吞吐」守卫（2026-09-15，P1-6）。

现场口径（2026-09 多次真实会话）：切片列表长期停在「待审计/待确认」，
`audit_terminal_total` 长时间为 0 —— 因为后台审计的吞吐被三项都钉在最小值：

  吞吐 = (每个扫描周期几个微步骤) x (每步覆盖多少媒体) x (每周期可定稿几条)
       = 1 x 18s x 1   ← 旧口径

而一条弱出点候选的窗口 ≈ 60s 回看 + 90s 后视 = 150s（9 个微步骤），赛事档单窗
≈ 90s 媒体 ≈ 100s+ 墙钟 ⇒ 单条候选要 10-19 分钟才定稿，回合约 2 分钟出一个，
容量只有需求的 1/5-1/8。

本文件钉住放宽后的四条性质：
  1. 步长随滞后自适应（低滞后 30s / 滞后 18s），读不出滞后时保守；
  2. 批次配额随滞后分层（1 / 2 / 3），且必须有「有候选且已就绪」才放量；
  3. 单批次是否再放一步 = 步数硬上限 + 实测步耗时 EMA 双保险（慢机自动退回 1 步）；
  4. **不放宽任何判据与粗扫保护阀**：单步墙钟上限、抢占阈值、取消交付路径原样保留。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _room_handler():
    import handlers.room_handler as room_handler

    return room_handler


def _code_lines(text: str) -> str:
    """只保留代码行（去掉整行注释），供结构化源守卫使用。"""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "python-backend/handlers/room_handler.py"
    ).read_text(encoding="utf-8")


# --------------------------------------------------------------- 步长自适应


@pytest.mark.parametrize(
    ("backlog", "expected"),
    [
        (0.0, 30.0),
        (5.0, 30.0),
        (15.0, 30.0),
        (15.5, 18.0),
        (60.0, 18.0),
        (600.0, 18.0),
        (-3.0, 30.0),
    ],
)
def test_audit_step_media_seconds_follow_backlog(backlog, expected) -> None:
    rh = _room_handler()

    assert rh._audit_step_media_sec(backlog) == expected


def test_audit_step_media_falls_back_conservatively_on_garbage() -> None:
    rh = _room_handler()

    # 读不出滞后 => 用保守步长，绝不在粗扫吃紧时放大单步开销
    assert rh._audit_step_media_sec(None) == rh._BCAST_REFINE_STEP_MEDIA_SEC
    assert rh._audit_step_media_sec("nan-ish") == rh._BCAST_REFINE_STEP_MEDIA_SEC


# --------------------------------------------------------------- 批次配额分层


def test_audit_batch_quota_is_banded_by_backlog() -> None:
    rh = _room_handler()

    # 滞后 > 抢占阈值：只给 1（粗扫在追，别多定稿）
    assert rh._audit_batch_quota(61.0, pending_count=9, ready_count=9) == 1
    # 30 < 滞后 <= 60：2
    assert rh._audit_batch_quota(45.0, pending_count=9, ready_count=9) == 2
    # <= 30：放宽到上限，但不得超过就绪条数
    assert rh._audit_batch_quota(0.0, pending_count=9, ready_count=9) == rh._BCAST_REFINE_MAX_QUOTA
    assert rh._audit_batch_quota(0.0, pending_count=9, ready_count=2) == 2
    assert rh._audit_batch_quota(0.0, pending_count=9, ready_count=1) == 1


def test_audit_batch_quota_requires_ready_candidates() -> None:
    rh = _room_handler()

    # 没有就绪候选（后视窗口还没写满）时放量没有意义，且会把批次预算浪费在空转上
    assert rh._audit_batch_quota(0.0, pending_count=3, ready_count=0) == 1
    assert rh._audit_batch_quota(0.0, pending_count=0, ready_count=3) == 1
    assert rh._audit_batch_quota("bad", pending_count=3, ready_count=3) == 1
    assert rh._audit_batch_quota(0.0) == 1


# --------------------------------------------------------------- 步耗时 EMA


def test_audit_step_estimate_seeds_then_tracks_ema() -> None:
    rh = _room_handler()

    state: dict = {}
    assert rh._audit_step_estimate(state) == rh._BCAST_REFINE_STEP_EST_SEED_SEC
    assert rh._audit_step_estimate(None) == rh._BCAST_REFINE_STEP_EST_SEED_SEC

    first = rh._audit_step_estimate_update(state, 10.0)
    assert first == 10.0, "首个实测值直接作为 EMA（不掺种子）"
    assert state["audit_step_elapsed_ema"] == 10.0

    second = rh._audit_step_estimate_update(state, 20.0)
    # alpha=0.3：10*0.7 + 20*0.3 = 13.0
    assert second == pytest.approx(13.0)
    assert rh._audit_step_estimate(state) == pytest.approx(13.0)
    # 脏输入不得污染 EMA
    assert rh._audit_step_estimate_update(state, "bad") == 0.0
    assert rh._audit_step_estimate(state) == pytest.approx(13.0)


# --------------------------------------------------------------- 批次预算闸


def test_audit_batch_budget_gates_steps_and_wall_clock() -> None:
    rh = _room_handler()

    # 第一步总允许（批次必须至少能推进一次，否则永远不前进）
    assert rh._audit_batch_has_budget(elapsed_sec=0.0, steps_done=0, step_est_sec=99.0) is True
    # 步数硬上限
    assert (
        rh._audit_batch_has_budget(
            elapsed_sec=1.0,
            steps_done=rh._BCAST_REFINE_MAX_STEPS,
            step_est_sec=1.0,
        )
        is False
    )
    # 余量够 => 再放一步；余量不够 => 停（留 1.25 倍余量，避免跨过批次硬边界）
    assert rh._audit_batch_has_budget(elapsed_sec=10.0, steps_done=1, step_est_sec=10.0) is True
    assert rh._audit_batch_has_budget(elapsed_sec=35.0, steps_done=1, step_est_sec=10.0) is False
    # 慢机（实测步 21s）：一步之后就该收工 —— 即自动退回旧行为
    assert rh._audit_batch_has_budget(elapsed_sec=21.0, steps_done=1, step_est_sec=21.0) is False
    # 显式覆盖参数（测试/未来调参用）
    assert (
        rh._audit_batch_has_budget(
            elapsed_sec=1.0, steps_done=1, step_est_sec=1.0,
            batch_max_sec=1.0, max_steps=5,
        )
        is False
    )
    assert (
        rh._audit_batch_has_budget(
            elapsed_sec=0.0, steps_done=2, step_est_sec=1.0,
            batch_max_sec=100.0, max_steps=2,
        )
        is False
    )


def test_audit_batch_budget_is_bounded_by_constants() -> None:
    rh = _room_handler()

    # 批次墙钟必须小于粗扫抢占阈值，且大于单步上限（否则批次等于单步）
    assert rh._BCAST_REFINE_BATCH_MAX_SEC > rh._BCAST_REFINE_STEP_MAX_SEC
    assert rh._BCAST_REFINE_BATCH_MAX_SEC < rh._BCAST_REFINE_KEEP_MAX_SEC
    assert rh._BCAST_REFINE_MAX_STEPS >= 2
    assert rh._BCAST_REFINE_STEP_MEDIA_RELAXED_SEC > rh._BCAST_REFINE_STEP_MEDIA_SEC
    assert (
        rh._BCAST_REFINE_MULTI_QUOTA_BACKLOG_SEC
        < rh._REFINE_PREEMPT_BACKLOG_SEC
    )


# --------------------------------------------------------------- 源守卫


def test_refine_worker_wires_adaptive_throughput() -> None:
    """源守卫：批量循环必须真的用上步长自适应 / 配额分层 / 预算闸。"""
    src = _source()

    assert "_audit_media_step = _audit_step_media_sec(_batch_backlog)" in src
    assert "max_media_step_sec=_audit_media_step," in src
    assert "max_audit_quota = _audit_batch_quota(" in src
    assert "step_est_sec=_audit_step_estimate(task_state)," in src
    assert "_audit_step_estimate_update(" in src
    assert "_batch_steps += 1" in src
    # 非终态不再立刻 break（这正是「一条候选一个周期只走一步」的旧口径）
    assert "若本候选仍有非终态子候选等待后视窗口" not in src
    assert "_batch_finalized += 1" in src
    # 结构化断言（只看代码行，注释里出现 "break" 不算）：非终态分支必须落到
    # 本批次内的 continue，而不是像旧实现那样立刻 break 交回槽位。
    code = _code_lines(src)
    tail = code.split("_cand['_last_audit_dur'] = _dur", 1)[1][:600]
    assert "continue" in tail, "非终态必须在本批次内继续推进同一条候选"
    assert "break" not in tail.split("if not _pending:", 1)[0]


def test_refine_worker_keeps_coarse_scan_protection() -> None:
    """源守卫：放宽批次吞吐不得动粗扫保护阀与取消交付契约。"""
    src = _source()

    # 粗扫抢占（backlog>60s / 审计久占）与 fairness 保护原样保留
    assert "_REFINE_PREEMPT_BACKLOG_SEC = 60.0" in src
    assert "task_state['refine_abort'] = bool(" in src
    assert "not task_state.get('refine_fairness_active')" in src
    # 单步墙钟上限与既有硬预算契约不得被改动
    assert "_BCAST_REFINE_STEP_MAX_SEC = 20.0" in src
    assert "_BCAST_REFINE_STEP_MEDIA_SEC = 18.0" in src
    # 取消路径仍须交付已定稿的拒绝结论
    assert "except FFmpegCancelled:" in src
    assert "_deliver_audit_outcomes_on_cancel(" in src
    # 批次兜底死线 = 批次预算 + 一步余量（不能再用单步上限当整批死线）
    assert "_BCAST_REFINE_BATCH_MAX_SEC" in src
    assert "+ _BCAST_REFINE_STEP_MAX_SEC" in src


def test_refine_worker_reserves_ready_candidates_first() -> None:
    """源守卫：批次内仍先消费「已就绪」候选（后视窗口已写满），再考虑 probe。"""
    src = _source()

    assert "target_list = ready_indices if ready_indices else probe_indices" in src
    assert "if c.get('_audit_continue_ready'):" in src
    # 收尾期强制定稿兜底不得被批次改动破坏
    assert "elif _finalize_now and _dur <= c_end:" in src
