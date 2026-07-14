# 无畏契约相位调度持续分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用相位状态机调度无畏契约持续分析：转场窗加密 OCR、买枪/交战中段休眠，在保住双可信边界的前提下降低占用；停录精修补漏。

**Architecture:** 新增可单测的纯逻辑模块 `lsc/analyzer/phase_scheduler.py`（profile 参数 + 状态机 + 扫描预算）。`room_handler` 的 continuous worker/loop 只负责喂信号与广播。前端增加手动 `pov|broadcast` 选择，并用 `round_phase`（勿覆写已有生命周期字段 `phase`）展示调度相位。

**Tech Stack:** Python 3.12, pytest, NumPy（既有）, React/TypeScript/Ant Design, WebSocket

**Spec:** `docs/superpowers/specs/2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md`

---

## 文件结构

| 文件 | 职责 |
|------|------|
| **Create** `lsc/analyzer/phase_scheduler.py` | Profile 常量、相位枚举、状态转移、扫描预算（纯函数，无 Qt/FFmpeg） |
| **Create** `tests/test_phase_scheduler.py` | 调度器单测 |
| **Modify** `python-backend/handlers/room_handler.py` | 接入调度器；改 scan budget / worker OCR 间隔；广播 `round_phase`；启动参数 `valorant_profile`；finalize 不破坏已导出 |
| **Modify** `tests/test_continuous_analysis_guards.py` | 预算与 OCR 门控随相位变化；profile 校验 |
| **Modify** `lsc-electron/src/types/index.ts` | 状态与启动字段类型 |
| **Modify** `lsc-electron/src/pages/Workbench/index.tsx` | Profile 选择与启动下发 |
| **Modify** `lsc-electron/src/components/AnalysisProgress.tsx` | 展示 `round_phase` / profile |
| **Modify** `tests/test_frontend_stability_guards.py` | 若有字符串守卫则同步 |
| **Optional touch** `lsc/analyzer/round_detector.py` | 仅当 worker 需把 `phase_sample_interval` 降到 `<2.0` 时放宽 `max(2.0, …)` 钳制 |

**命名冲突（必须遵守）：**

- 既有 `ContinuousAnalysisStatus.phase` = `idle|running|finalizing|completed|error`（生命周期）
- 本功能新增 `round_phase` = `unknown|buy|pre_combat|combat|post_combat`
- 新增 `round_phase_detail: string`（短中文）
- Spec 里的 `phase` 在实现与协议中一律映射为 `round_phase`

---

### Task 1: Profile 与相位预算纯模块

**Files:**
- Create: `lsc/analyzer/phase_scheduler.py`
- Create: `tests/test_phase_scheduler.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_phase_scheduler.py
from lsc.analyzer.phase_scheduler import (
    PROFILE_POV,
    PROFILE_BROADCAST,
    get_profile,
    next_round_phase,
    scan_budget_for_phase,
    RoundPhase,
)


def test_get_profile_defaults_and_aliases() -> None:
    assert get_profile(None).name == PROFILE_POV
    assert get_profile("broadcast").name == PROFILE_BROADCAST
    assert get_profile("BROADCAST").buy_sleep_sec < get_profile("pov").buy_sleep_sec


def test_buy_sleep_then_pre_combat_on_wake() -> None:
    cfg = get_profile("pov")
    # 刚进 buy，未满 sleep → 仍 buy，且预算几乎不 OCR
    st = next_round_phase(
        RoundPhase.BUY,
        cfg,
        now_mono=100.0,
        phase_entered_at=90.0,  # 仅过 10s < 22
        signals={"energy_rise": True, "left_buy_ocr": False, "chime": False, "has_end": False, "has_start": False},
    )
    assert st.phase == RoundPhase.BUY
    budget = scan_budget_for_phase(st.phase, cfg, last_analyzed=50.0, current_dur=80.0)
    assert budget.need_ocr is False
    assert budget.ocr_interval_sec >= 9.0


def test_broadcast_forbids_energy_only_pre_combat() -> None:
    cfg = get_profile("broadcast")
    st = next_round_phase(
        RoundPhase.BUY,
        cfg,
        now_mono=200.0,
        phase_entered_at=100.0,  # 已过 sleep
        signals={"energy_rise": True, "left_buy_ocr": False, "chime": False, "has_end": False, "has_start": False},
    )
    assert st.phase == RoundPhase.BUY  # 低 rms_trust：不能仅凭能量进 pre_combat


def test_chime_wakes_post_combat() -> None:
    cfg = get_profile("pov")
    st = next_round_phase(
        RoundPhase.COMBAT,
        cfg,
        now_mono=300.0,
        phase_entered_at=200.0,
        signals={"energy_rise": False, "left_buy_ocr": False, "chime": True, "has_end": False, "has_start": True},
    )
    assert st.phase == RoundPhase.POST_COMBAT


def test_post_combat_confirm_returns_to_buy_or_unknown() -> None:
    cfg = get_profile("pov")
    st = next_round_phase(
        RoundPhase.POST_COMBAT,
        cfg,
        now_mono=400.0,
        phase_entered_at=380.0,
        signals={
            "energy_rise": False,
            "left_buy_ocr": False,
            "chime": False,
            "has_end": True,
            "has_start": True,
            "next_buy_seen": True,
        },
    )
    assert st.phase == RoundPhase.BUY
    assert st.just_confirmed is True
```

- [ ] **Step 2: 跑测确认失败**

Run: `pytest tests/test_phase_scheduler.py -q`

Expected: FAIL（模块不存在或符号缺失）

- [ ] **Step 3: 最小实现**

在 `lsc/analyzer/phase_scheduler.py` 实现：

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


PROFILE_POV = "pov"
PROFILE_BROADCAST = "broadcast"


class RoundPhase(str, Enum):
    UNKNOWN = "unknown"
    BUY = "buy"
    PRE_COMBAT = "pre_combat"
    COMBAT = "combat"
    POST_COMBAT = "post_combat"


@dataclass(frozen=True)
class ValorantProfile:
    name: str
    buy_sleep_sec: float
    pre_combat_window_sec: float
    post_combat_window_sec: float
    rms_trust_high: bool
    ocr_sparse_interval_sec: float
    ocr_dense_interval_sec: float
    unknown_reanchor_sec: float
    max_combat_force_post_sec: float
    min_dwell_sec: float = 3.0
    lookback_sec: float = 45.0  # 相位短窗，远小于旧 240s 默认密扫语义


_PROFILES = {
    PROFILE_POV: ValorantProfile(
        name=PROFILE_POV,
        buy_sleep_sec=22.0,
        pre_combat_window_sec=12.0,
        post_combat_window_sec=25.0,
        rms_trust_high=True,
        ocr_sparse_interval_sec=12.0,
        ocr_dense_interval_sec=2.0,
        unknown_reanchor_sec=45.0,
        max_combat_force_post_sec=130.0,
        lookback_sec=40.0,
    ),
    PROFILE_BROADCAST: ValorantProfile(
        name=PROFILE_BROADCAST,
        buy_sleep_sec=12.0,
        pre_combat_window_sec=18.0,
        post_combat_window_sec=35.0,
        rms_trust_high=False,
        ocr_sparse_interval_sec=7.0,
        ocr_dense_interval_sec=1.5,
        unknown_reanchor_sec=30.0,
        max_combat_force_post_sec=130.0,
        lookback_sec=55.0,
    ),
}


def get_profile(name: str | None) -> ValorantProfile:
    key = (name or PROFILE_POV).strip().lower()
    if key in ("hvv", "commentary", "broadcast"):
        key = PROFILE_BROADCAST
    if key in ("game", "client", "pov"):
        key = PROFILE_POV
    return _PROFILES.get(key, _PROFILES[PROFILE_POV])


@dataclass
class PhaseTransition:
    phase: RoundPhase
    just_confirmed: bool = False
    detail: str = ""


@dataclass
class PhaseScanBudget:
    scan_start: float
    scan_end: float
    need_audio: bool
    need_ocr: bool
    ocr_interval_sec: float
    lookback_sec: float


def next_round_phase(
    current: RoundPhase,
    cfg: ValorantProfile,
    *,
    now_mono: float,
    phase_entered_at: float,
    signals: dict[str, Any],
) -> PhaseTransition:
    dwell = max(0.0, now_mono - phase_entered_at)
    energy_rise = bool(signals.get("energy_rise"))
    left_buy = bool(signals.get("left_buy_ocr"))
    chime = bool(signals.get("chime"))
    has_start = bool(signals.get("has_start"))
    has_end = bool(signals.get("has_end"))
    next_buy = bool(signals.get("next_buy_seen"))
    energy_collapse = bool(signals.get("energy_collapse"))

    # 全局超时重锚
    if current != RoundPhase.UNKNOWN and dwell >= cfg.unknown_reanchor_sec and not has_end:
        if current == RoundPhase.COMBAT and dwell >= cfg.max_combat_force_post_sec:
            return PhaseTransition(RoundPhase.POST_COMBAT, detail="combat_timeout")
        if current in (RoundPhase.BUY, RoundPhase.PRE_COMBAT) and dwell >= cfg.unknown_reanchor_sec:
            return PhaseTransition(RoundPhase.UNKNOWN, detail="reanchor")

    if current == RoundPhase.UNKNOWN:
        if left_buy or energy_rise:
            return PhaseTransition(RoundPhase.BUY if not left_buy else RoundPhase.PRE_COMBAT, detail="anchored")
        return PhaseTransition(RoundPhase.UNKNOWN, detail="seeking_anchor")

    if current == RoundPhase.BUY:
        if dwell < cfg.buy_sleep_sec:
            return PhaseTransition(RoundPhase.BUY, detail="buy_sleep")
        if left_buy or (cfg.rms_trust_high and energy_rise):
            return PhaseTransition(RoundPhase.PRE_COMBAT, detail="wake_pre_combat")
        return PhaseTransition(RoundPhase.BUY, detail="buy_wait")

    if current == RoundPhase.PRE_COMBAT:
        if has_start or left_buy:
            return PhaseTransition(RoundPhase.COMBAT, detail="combat_locked")
        if dwell >= cfg.pre_combat_window_sec * 2:
            return PhaseTransition(RoundPhase.UNKNOWN, detail="pre_combat_miss")
        return PhaseTransition(RoundPhase.PRE_COMBAT, detail="locking_start")

    if current == RoundPhase.COMBAT:
        if chime or energy_collapse or dwell >= cfg.max_combat_force_post_sec:
            return PhaseTransition(RoundPhase.POST_COMBAT, detail="enter_post")
        return PhaseTransition(RoundPhase.COMBAT, detail="in_combat")

    if current == RoundPhase.POST_COMBAT:
        if has_start and has_end:
            nxt = RoundPhase.BUY if next_buy else RoundPhase.UNKNOWN
            return PhaseTransition(nxt, just_confirmed=True, detail="confirmed")
        if dwell >= cfg.post_combat_window_sec * 2:
            return PhaseTransition(RoundPhase.UNKNOWN, detail="post_timeout")
        return PhaseTransition(RoundPhase.POST_COMBAT, detail="locking_end")

    return PhaseTransition(RoundPhase.UNKNOWN, detail="fallback")


def scan_budget_for_phase(
    phase: RoundPhase,
    cfg: ValorantProfile,
    *,
    last_analyzed: float,
    current_dur: float,
    pending_start: float | None = None,
) -> PhaseScanBudget:
    """短窗扫描预算。不跳过中间：仍从 last_analyzed 回看 overlap 再向前。"""
    lookback = cfg.lookback_sec
    if phase == RoundPhase.POST_COMBAT:
        lookback = max(lookback, cfg.post_combat_window_sec + 10.0)
    elif phase == RoundPhase.PRE_COMBAT:
        lookback = max(lookback, cfg.pre_combat_window_sec + 10.0)
    elif phase == RoundPhase.UNKNOWN:
        lookback = max(lookback, 90.0)

    if last_analyzed <= 0:
        start, end = 0.0, float(current_dur)
    else:
        start = max(0.0, float(last_analyzed) - lookback)
        end = float(current_dur)
        if pending_start is not None:
            start = min(start, max(0.0, float(pending_start) - 5.0))

    dense = phase in (RoundPhase.PRE_COMBAT, RoundPhase.POST_COMBAT, RoundPhase.UNKNOWN)
    sleep_ocr = phase == RoundPhase.BUY
    need_ocr = dense or (phase == RoundPhase.BUY and False)  # buy sleep: no OCR
    if phase == RoundPhase.COMBAT:
        need_ocr = False
    if sleep_ocr:
        need_ocr = False

    interval = cfg.ocr_dense_interval_sec if dense else cfg.ocr_sparse_interval_sec
    return PhaseScanBudget(
        scan_start=round(start, 3),
        scan_end=round(end, 3),
        need_audio=True,
        need_ocr=need_ocr,
        ocr_interval_sec=interval if need_ocr else 999.0,
        lookback_sec=lookback,
    )


PHASE_DETAIL_ZH = {
    "buy_sleep": "买枪休眠中",
    "buy_wait": "买枪侦测中",
    "wake_pre_combat": "等待屏障解除",
    "locking_start": "锁定回合起点",
    "in_combat": "交战中",
    "enter_post": "等待回合结束",
    "locking_end": "锁定回合终点",
    "confirmed": "回合已确认",
    "seeking_anchor": "寻找回合锚点",
    "reanchor": "相位重锚",
}
```

（实现时可按测试微调转移条件，但必须满足：broadcast 禁止纯能量进 `pre_combat`；钟声进 `post_combat`；双可信才 `just_confirmed`。）

- [ ] **Step 4: 跑测确认通过**

Run: `pytest tests/test_phase_scheduler.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/phase_scheduler.py tests/test_phase_scheduler.py
git commit -m "$(cat <<'EOF'
feat: add Valorant round phase scheduler profiles

EOF
)"
```

---

### Task 2: 持续分析扫描预算接入相位

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_continuous_valorant_scan_budget` 及调用处）
- Modify: `tests/test_continuous_analysis_guards.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_continuous_analysis_guards.py` 追加：

```python
def test_continuous_valorant_budget_honors_phase_short_window() -> None:
    # 与本文件其它测试相同的 room_handler 导入方式（见文件顶部）
    scan_range, use_ocr, _, full = room_handler._continuous_valorant_scan_budget(
        "valorant_round",
        last_analyzed=100.0,
        current_dur=180.0,
        pressure={"level": "normal"},
        tick_count=3,
        round_phase="buy",
        valorant_profile="pov",
    )
    assert full is False
    assert use_ocr is False  # buy sleep / buy phase: no OCR
    assert scan_range[1] - scan_range[0] < 240.0


def test_continuous_valorant_budget_dense_ocr_in_post_combat() -> None:
    _, use_ocr, _, _ = room_handler._continuous_valorant_scan_budget(
        "valorant_round",
        last_analyzed=100.0,
        current_dur=150.0,
        pressure={"level": "normal"},
        tick_count=5,
        round_phase="post_combat",
        valorant_profile="broadcast",
    )
    assert use_ocr is True
```

（若现有测试用 `import room_handler` / 改 `sys.path`，**复制该文件已有 import 风格**，不要自创包名。）

- [ ] **Step 2: 跑测确认失败**

Run: `pytest tests/test_continuous_analysis_guards.py::test_continuous_valorant_budget_honors_phase_short_window -v`

Expected: FAIL（签名尚无 `round_phase` / `valorant_profile`）

- [ ] **Step 3: 扩展 `_continuous_valorant_scan_budget`**

签名增加可选参数（保持旧调用兼容）：

```python
def _continuous_valorant_scan_budget(
    mode: str,
    last_analyzed: float,
    current_dur: float,
    pressure: dict[str, Any] | None = None,
    tick_count: int = 0,
    round_phase: str | None = None,
    valorant_profile: str | None = None,
    pending_start: float | None = None,
) -> tuple[tuple[float, float], bool, int, bool]:
```

当 `mode == "valorant_round"` 且提供 `round_phase` 时：

```python
from lsc.analyzer.phase_scheduler import (
    RoundPhase, get_profile, scan_budget_for_phase,
)
cfg = get_profile(valorant_profile)
try:
    phase = RoundPhase(round_phase or "unknown")
except ValueError:
    phase = RoundPhase.UNKNOWN
budget = scan_budget_for_phase(
    phase, cfg,
    last_analyzed=last_analyzed,
    current_dur=current_dur,
    pending_start=pending_start,
)
use_ocr = budget.need_ocr and _continuous_valorant_refine_with_ocr(mode, pressure)
scan_range = (budget.scan_start, budget.scan_end)
# timeout 仍按 scan_duration 估算
```

未传 `round_phase` 时保留旧 lookback 行为（兼容旧测试），但 valorant_round 新主路径应始终传入。

更新旧测试 `test_continuous_valorant_ocr_enabled_every_tick`：**改为**断言「非 pause 时允许 OCR，但 buy 相位关闭」——不要再要求每个 tick 都 OCR。

- [ ] **Step 4: 跑相关测试**

Run: `pytest tests/test_continuous_analysis_guards.py -q`

Expected: PASS（含改写后的 OCR 测试）

- [ ] **Step 5: Commit**

```bash
git add python-backend/handlers/room_handler.py tests/test_continuous_analysis_guards.py
git commit -m "$(cat <<'EOF'
feat: phase-aware scan budget for Valorant continuous analysis

EOF
)"
```

---

### Task 3: Worker / 主循环接入状态机与信号

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_continuous_valorant_worker`、`_continuous_analysis_loop`、`handle_start_continuous_analysis`）
- Modify: `tests/test_continuous_analysis_guards.py` 或 `tests/test_synced_continuous_analysis.py`

- [ ] **Step 1: 写失败测试（启动参数与状态字段）**

```python
def test_start_continuous_accepts_valorant_profile_in_source() -> None:
    from pathlib import Path
    src = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "valorant_profile" in src
    assert "round_phase" in src
    assert "phase_scheduler" in src
```

```python
def test_worker_passes_phase_sample_interval_from_budget() -> None:
    from pathlib import Path
    src = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "ocr_sample_interval" in src
    assert "round_phase" in src
```

- [ ] **Step 2: 跑测确认当前可能已部分通过；若 `phase_scheduler` 未接入则 FAIL**

Run: `pytest tests/test_continuous_analysis_guards.py::test_start_continuous_accepts_valorant_profile_in_source -v`

- [ ] **Step 3: 实现接入**

1. `handle_start_continuous_analysis` 读取：

```python
valorant_profile = (data.get("valorant_profile") or "pov")
# 规范化
from lsc.analyzer.phase_scheduler import get_profile
profile_name = get_profile(valorant_profile).name
```

传入 `_continuous_analysis_loop(..., valorant_profile=profile_name)`。

2. 任务状态初始化增加：

```python
'round_phase': 'unknown',
'round_phase_detail': '',
'round_phase_entered_at': time.monotonic(),
'valorant_profile': profile_name,
'pending_start': None,
```

3. 每 tick 在 kick worker 前：

- 用上一轮 detector 结果 / 轻量启发式填充 `signals`：
  - `has_start` / `has_end`：来自候选回合的 `ocr_confirmed` 或 `start_by`/`end_by`
  - `chime`：若结果带 chime 裁尾或 task 记录 `last_chime_at` 很近
  - `left_buy_ocr`：OCR 买枪→非买枪（若本轮无 OCR，则为 False）
  - `energy_rise` / `energy_collapse`：可由本轮 RMS 摘要字段填充；若 detector 暂无摘要，**pov** 可在 `buy` 醒后用「扫描返回新 combat 段」近似为 rise；**broadcast** 保持 False 除非 `left_buy_ocr`
- 调用 `next_round_phase` 更新 `round_phase`
- 调用扩展后的 `_continuous_valorant_scan_budget(..., round_phase=..., valorant_profile=...)`
- 将 `ocr_sample_interval`、`refine_with_ocr=use_ocr`、`scan_range` 写入 task_state

4. Worker 里构造 `ValorantRoundConfig` 时：

```python
_ocr_iv = float(task_state.get('ocr_sample_interval', 2.0) or 2.0)
_round_config = ValorantRoundConfig(
    full_round=True,
    phase_sample_interval=max(1.0, _ocr_iv),  # 允许 dense 1.0–1.5；若仍钳 2.0 则改 round_detector
)
```

若 `round_detector` 仍有 `max(2.0, …)` 硬钳，同步改掉该钳制为 `max(1.0, …)`，并加一行注释说明相位加密需要。

5. **确认门不变**：只有 `ocr_confirmed`（或既有等价条件）才入列导出；`just_confirmed` 仅表示调度器认为本回合可闭合，仍须过现有确认过滤。

6. 状态广播增加字段（生命周期 `phase` 不动）：

```python
'round_phase': state.get('round_phase'),
'round_phase_detail': PHASE_DETAIL_ZH.get(detail, detail),
'valorant_profile': state.get('valorant_profile'),
'pending_round': state.get('pending_start') is not None,
```

- [ ] **Step 4: 跑守卫与同步测试**

Run:

```bash
pytest tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add python-backend/handlers/room_handler.py lsc/analyzer/round_detector.py tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py
git commit -m "$(cat <<'EOF'
feat: wire phase scheduler into Valorant continuous analysis loop

EOF
)"
```

---

### Task 4: Finalize 与已导出保护

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（finalize 分支）
- Modify: `tests/test_continuous_analysis_guards.py`

- [ ] **Step 1: 写失败测试（源码/行为守卫）**

```python
def test_finalize_does_not_clobber_exported_clips_policy() -> None:
    """精修不得默认重导或删除已成功导出文件——用策略注释/分支守卫。"""
    from pathlib import Path
    src = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "已导出" in src or "already exported" in src.lower() or "skip_refine_exported" in src
```

若现有 finalize 已只追加漏检、不重导成功项，则在 finalize 合并逻辑处加明确注释与 `continue` 分支，并让测试断言该标记字符串存在：

```python
# skip_refine_exported: 已成功导出的 clip 不因精修边界变化自动重导
```

- [ ] **Step 2: 实现**

Finalize 扫描（停录后宽窗/`refine_with_ocr=True`）结果合并时：

1. 与 `confirmed_round_keys` 去重。
2. 新回合 → 正常入列导出。
3. 已存在且导出状态为成功 → **跳过**更新/重导。
4. 已存在但仍在队列/失败 → 允许用精修边界更新后再导出。

取消持续分析：清空 `pending_start`，不导出开放尾（已有行为则补测试守卫）。

- [ ] **Step 3: 跑测**

Run: `pytest tests/test_continuous_analysis_guards.py -q`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add python-backend/handlers/room_handler.py tests/test_continuous_analysis_guards.py
git commit -m "$(cat <<'EOF'
fix: keep finalize from clobbering successfully exported clips

EOF
)"
```

---

### Task 5: 前端 Profile 选择与状态展示

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`
- Modify: `lsc-electron/src/components/AnalysisProgress.tsx`
- Modify: `tests/test_frontend_stability_guards.py`（若存在相关断言）

- [ ] **Step 1: 扩展类型**

在 `ContinuousAnalysisStatus` 增加：

```typescript
valorant_profile?: 'pov' | 'broadcast'
round_phase?: 'unknown' | 'buy' | 'pre_combat' | 'combat' | 'post_combat'
round_phase_detail?: string
pending_round?: boolean
```

注意：不要改既有 `phase?: 'idle' | 'running' | ...`。

- [ ] **Step 2: Workbench 启动 UI**

在无畏契约回合切割 + 持续分析弹层/区域增加 Radio：

```tsx
const [valorantProfile, setValorantProfile] = useState<'pov' | 'broadcast'>('pov')
// ...
{isValorantRoundCutting && (
  <Radio.Group value={valorantProfile} onChange={(e) => setValorantProfile(e.target.value)}>
    <Radio.Button value="pov">游戏视角</Radio.Button>
    <Radio.Button value="broadcast">赛事解说</Radio.Button>
  </Radio.Group>
)}
```

`start_continuous_analysis` payload 增加：

```ts
valorant_profile: isValorantRoundCutting ? valorantProfile : undefined,
```

可用 `localStorage` 键 `lsc.valorant_profile` 记住上次选择（可选，YAGNI 可跳过）。

- [ ] **Step 3: AnalysisProgress 展示**

在 compact 与完整卡片中，当 `mode === 'valorant_round'` 时显示：

- profile 标签：`游戏视角` / `赛事解说`
- `round_phase_detail` 或按 `round_phase` 映射的中文
- `pending_round` 时 Tag「等待回合结束」

映射示例：

```ts
const ROUND_PHASE_LABEL: Record<string, string> = {
  unknown: '寻找回合',
  buy: '买枪期',
  pre_combat: '等待开战',
  combat: '交战中',
  post_combat: '等待结束',
}
```

- [ ] **Step 4: 前端守卫（若仓库用源码字符串测）**

```python
def test_frontend_sends_valorant_profile() -> None:
    from pathlib import Path
    wb = Path("lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    assert "valorant_profile" in wb
    assert "游戏视角" in wb
```

Run: `pytest tests/test_frontend_stability_guards.py -q`（或新测所在文件）

- [ ] **Step 5: Commit**

```bash
git add lsc-electron/src/types/index.ts lsc-electron/src/pages/Workbench/index.tsx lsc-electron/src/components/AnalysisProgress.tsx tests/test_frontend_stability_guards.py
git commit -m "$(cat <<'EOF'
feat: expose Valorant profile and round phase in continuous analysis UI

EOF
)"
```

---

### Task 6: 回归与验收对照

**Files:** 无新文件；跑测试 + 对照 spec 验收表

- [ ] **Step 1: 跑核心套件**

```bash
pytest tests/test_phase_scheduler.py tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py tests/test_round_detector.py tests/test_frontend_stability_guards.py -q
```

Expected: PASS

- [ ] **Step 2: 对照 spec 验收清单（人工勾选）**

| # | 标准 | 对应实现 |
|---|------|----------|
| 1 | pov 买枪休眠少 OCR | `scan_budget_for_phase(BUY)` → `need_ocr=False` |
| 2 | broadcast 不纯能量确认 | `rms_trust_high=False` + 确认门仍要 OCR |
| 3 | 双可信才入列 | 既有确认过滤未放宽 |
| 4 | 去重 | 既有 `confirmed_round_keys` |
| 5 | 多房间 sync | 既有路径，仅主房跑调度 |
| 6 | finalize 补漏不毁已导出 | Task 4 |
| 7 | 非 valorant 不变 | budget 仅 valorant_round 走相位 |
| 8 | 状态含 round_phase/profile | Task 3+5 |

- [ ] **Step 3: Commit（若有测试/注释修补）**

```bash
git add -u
git commit -m "$(cat <<'EOF'
test: verify phase-scheduler continuous analysis acceptance guards

EOF
)"
```

仅在有实际改动时提交。

---

## Self-Review（计划作者已做）

1. **Spec 覆盖：** 状态机、双 profile、短窗预算、确认门、finalize、前端、错误回退（reanchor/combat timeout）均有 Task；自动 profile 判别明确不在范围。
2. **占位符：** 无 TBD；测试与关键代码块已写出。
3. **类型一致：** 协议字段统一为 `valorant_profile`、`round_phase`、`round_phase_detail`、`pending_round`；避免与生命周期 `phase` 冲突。
4. **Import 注意：** `test_continuous_analysis_guards.py` 必须沿用文件现有 `room_handler` 导入方式，计划中的伪 import 以仓库现状为准。

---

## 执行方式

Plan complete and saved to `docs/superpowers/plans/2026-07-14-valorant-phase-scheduler-continuous-analysis.md`.

两种执行方式：

1. **Subagent-Driven（推荐）** — 每任务派生子代理，任务间评审  
2. **Inline Execution** — 本会话按 executing-plans 批量推进并设检查点  

选哪一种？
