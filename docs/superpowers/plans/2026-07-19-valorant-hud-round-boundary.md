# 无畏契约 HUD 回合边界 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `valorant_round` 持续分析中，用语言无关的 HUD 结构状态转换作为唯一入列边界权威，按「买枪结束→下一买枪开始」切完整回合，并阻断纯音频候选进入切片列表。

**Architecture:** 新增纯逻辑模块 `hud_boundary.py`（帧分类 + 边界状态机），只认 `buy/non-buy` 稳定转换写 `pending_start` / 闭合事件。`phase_scheduler` 仅消费 HUD 稳定态做扫描疏密与状态文案，音频/钟声不得写边界或触发 `clip_queued`。`room_handler` 改为前向游标入列门禁：仅 `boundary_source=valorant_hud_transition` 且时长 ∈ [5, 180] 的闭合回合走现有 `list_only` 链路。

**Tech Stack:** Python 3.12, OpenCV, FFmpeg 抽帧, pytest, 现有 WebSocket `clip_queued` / Zustand

**Spec:** `docs/superpowers/specs/2026-07-19-valorant-hud-round-boundary-design.md`

---

## 审阅锁定（实现不得回退）

| 决策 | 选择 |
|------|------|
| 边界权威 | 仅 HUD 双边界；音频/OCR/chime 不得写 `pending_start`、终点或入列 |
| 终点 | 仅下一回合买枪第一帧；结算/回放留在上一切片 |
| 入列谓词 | `boundary_source == "valorant_hud_transition"` ∧ `start_by=buy_exit_visual` ∧ `end_by=next_buy_visual` ∧ `5 ≤ duration ≤ 180` |
| 35s 门槛 | HUD 路径使用 5–180；旧 `_VALORANT_MIN_EXPORT_DURATION_SEC=35` 不再用于 HUD 入列 |
| trim | HUD 回合入列前**禁止** `_trim_valorant_combat_bounds` 改边界 |
| 相位调度 | 保留 `phase_scheduler` 做 scan budget；边界写入交给 `hud_boundary`（单一权威，非并行第二套入列机） |
| `ocr_confirmed` 升格 | 持续分析 `valorant_round` **停止** OCR 升格入列；列表一律 `confirm_status=pending` |
| 范围 | 仅 continuous `valorant_round` 入列路径；同步分析/音频诊断可保留 sidecar，但不得 `clip_queued` |
| `round_key` | 首次闭合生成 `hud-round-{int(start)}` 后冻结；后续只 upsert 边界 |
| Supersedes | 持续分析边界/入列以本 spec 为准，覆盖 07-12 终点歧义、07-15 OCR 确认门、07-16 rule pack 中「结算也可作终点」 |

---

## 文件结构

| 文件 | 职责 |
|------|------|
| **Create** `lsc/analyzer/hud_boundary.py` | `HudObservation`、帧结构分类、边界状态机、闭合事件 DTO（纯逻辑，无 Qt） |
| **Create** `tests/test_hud_boundary.py` | 分类器 + 状态机单测（合成图 / 观察序列） |
| **Create** `tests/fixtures/valorant_hud/` | 小尺寸 ROI PNG（中/英/俄买枪、交战、结算、加载、选人） |
| **Modify** `lsc/analyzer/round_detector.py` | 新增前向 HUD 观察抽帧 API（双 ROI）；主路径可不跑文本 OCR |
| **Modify** `lsc/analyzer/phase_scheduler.py` | 增加 `hud_buy` / `hud_non_buy` 信号映射到 scan budget；文档注明边界不由此模块写入 |
| **Modify** `python-backend/handlers/room_handler.py` | 前向游标、入列门禁、key 冻结、禁 trim、禁音频 pending 入列、禁 ocr 升格 |
| **Modify** `tests/test_continuous_analysis_guards.py` | 门禁 / merge / trim / 入列契约 |
| **Modify** `tests/test_phase_scheduler.py` | HUD 信号驱动预算；音频不能 `just_confirmed` |
| **Modify** `lsc-electron/src/types/index.ts` | `boundary_source?` / `boundary_confidence?` 可选字段 |
| **Modify** `tests/test_frontend_stability_guards.py` | 类型字符串守卫（若有） |

---

### Task 1: HUD 观察与买枪判定纯函数

**Files:**
- Create: `lsc/analyzer/hud_boundary.py`
- Create: `tests/test_hud_boundary.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_hud_boundary.py
from __future__ import annotations

import numpy as np

from lsc.analyzer.hud_boundary import (
    HudObservation,
    classify_hud_frame,
    is_buy_state,
)


def _blank(h: int = 72, w: int = 128) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_black_frame_is_invalid() -> None:
    obs = classify_hud_frame(top_roi=_blank(), center_roi=_blank())
    assert obs.frame_valid is False
    assert is_buy_state(obs) is False


def test_buy_requires_hud_and_banner_without_timer_contradiction() -> None:
    obs = HudObservation(
        frame_valid=True,
        hud_present=True,
        buy_banner_present=True,
        timer_contradicts_buy=False,
        confidence=0.9,
    )
    assert is_buy_state(obs) is True


def test_timer_contradiction_blocks_buy() -> None:
    obs = HudObservation(
        frame_valid=True,
        hud_present=True,
        buy_banner_present=True,
        timer_contradicts_buy=True,
        confidence=0.9,
    )
    assert is_buy_state(obs) is False


def test_unread_timer_does_not_veto_clear_banner() -> None:
    obs = HudObservation(
        frame_valid=True,
        hud_present=True,
        buy_banner_present=True,
        timer_contradicts_buy=False,
        confidence=0.8,
    )
    assert is_buy_state(obs) is True
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_hud_boundary.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

```python
# lsc/analyzer/hud_boundary.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

# 内部常量（不进 settings）
_MIN_BOUNDARY_CONFIDENCE = 0.35
_HUD_MISSING_TOLERANCE_SEC = 5.0
_MIN_CLOSED_DURATION_SEC = 5.0
_MAX_CLOSED_DURATION_SEC = 180.0
_STABLE_HITS = 2


@dataclass(frozen=True)
class HudObservation:
    frame_valid: bool
    hud_present: bool
    buy_banner_present: bool
    timer_contradicts_buy: bool
    confidence: float


def is_buy_state(obs: HudObservation) -> bool:
    return (
        obs.frame_valid
        and obs.hud_present
        and obs.buy_banner_present
        and not obs.timer_contradicts_buy
    )


def classify_hud_frame(
    top_roi: np.ndarray,
    center_roi: np.ndarray,
    *,
    timer_digits: str | None = None,
) -> HudObservation:
    """结构分类。timer_digits 仅在高置信读出时用于否决；读不到不得否决横幅。"""
    if top_roi is None or center_roi is None or top_roi.size == 0 or center_roi.size == 0:
        return HudObservation(False, False, False, False, 0.0)

    if float(np.mean(top_roi)) < 8.0 and float(np.mean(center_roi)) < 8.0:
        return HudObservation(False, False, False, False, 0.0)

    # Task 2 用合成夹具校准；此处先给可测的亮度/边缘启发式占位，
    # Task 2 会替换为真实结构特征（双行横幅、顶栏布局）。
    hud_present = float(np.std(top_roi)) > 12.0
    buy_banner_present = float(np.std(center_roi)) > 18.0
    frame_valid = hud_present or buy_banner_present

    timer_contradicts = False
    if timer_digits:
        # 买枪倒计时通常 ≤45；明确的战斗倒计时（如 1:xx 且非买枪窗）才否决
        # 具体规则在 Task 2 用夹具收紧；此处：纯数字且 > 45 视为矛盾
        digits = "".join(ch for ch in timer_digits if ch.isdigit())
        if digits.isdigit() and int(digits) > 45:
            timer_contradicts = True

    conf = 0.5
    if hud_present and buy_banner_present:
        conf = 0.85
    elif hud_present:
        conf = 0.6
    return HudObservation(
        frame_valid=frame_valid,
        hud_present=hud_present,
        buy_banner_present=buy_banner_present,
        timer_contradicts_buy=timer_contradicts,
        confidence=conf,
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_hud_boundary.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/hud_boundary.py tests/test_hud_boundary.py
git commit -m "$(cat <<'EOF'
feat(analyzer): add HUD observation DTO and buy-state predicate

EOF
)"
```

---

### Task 2: 买枪 vs 结算结构分类 + ROI 夹具

**Files:**
- Modify: `lsc/analyzer/hud_boundary.py`
- Create: `tests/fixtures/valorant_hud/`（PNG）
- Modify: `tests/test_hud_boundary.py`

- [ ] **Step 1: 生成最小夹具并写失败测试**

用脚本或手工从用户样本裁剪 **仅 ROI**（禁止提交完整录像）。至少包含：

| 文件名 | 期望 |
|--------|------|
| `buy_zh.png` / `buy_en.png` / `buy_ru.png` | `is_buy_state=True` |
| `combat_hud.png` | `frame_valid=True`, `is_buy=False` |
| `result_banner.png`（无可靠 timer 数字） | `is_buy_state=False` |
| `agent_select.png` / `map_load.png` / `desktop.png` | `frame_valid=False` |

若暂无真实裁剪，可用 OpenCV 画合成图：顶栏三块矩形=HUD；中央两行高对比条=买枪；中央超大单块=结算。

```python
# tests/test_hud_boundary.py（追加）
from pathlib import Path
import cv2

FIXTURE = Path(__file__).parent / "fixtures" / "valorant_hud"


def _split_rois(bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, w = bgr.shape[:2]
    top = bgr[0 : max(1, int(h * 0.18)), :]
    center = bgr[int(h * 0.20) : int(h * 0.55), int(w * 0.25) : int(w * 0.75)]
    return top, center


@pytest.mark.parametrize(
    "name, expect_buy, expect_valid",
    [
        ("buy_ru.png", True, True),
        ("combat_hud.png", False, True),
        ("result_banner.png", False, True),
        ("agent_select.png", False, False),
    ],
)
def test_fixture_classification(name: str, expect_buy: bool, expect_valid: bool) -> None:
    path = FIXTURE / name
    assert path.is_file(), f"missing fixture {path}"
    img = cv2.imread(str(path))
    assert img is not None
    top, center = _split_rois(img)
    obs = classify_hud_frame(top, center)
    assert obs.frame_valid is expect_valid
    assert is_buy_state(obs) is expect_buy
```

- [ ] **Step 2: 运行确认失败或夹具缺失**

Run: `pytest tests/test_hud_boundary.py::test_fixture_classification -v`
Expected: FAIL（缺夹具或分类不准）

- [ ] **Step 3: 强化 `classify_hud_frame` 结构特征**

在 `hud_boundary.py` 实现可测启发式（阈值用夹具校准后写成模块常量）：

1. **`hud_present`**：顶栏水平三分区域均有中高亮度连通块（比分/计时/对方状态）。
2. **`buy_banner_present`**：中央 ROI 存在**两行**横向高对比条带，宽高比接近横幅，垂直间距稳定；**不是**单块占满中央的结算大字。
3. **结算否决（无 timer 也必须生效）**：中央主连通域高度占比 > `_RESULT_BLOB_HEIGHT_RATIO`（建议从 0.45 起调）或单行超大字块 → `buy_banner_present=False`（即使对比度高）。
4. **`timer_contradicts_buy`**：仅当传入高置信 `timer_digits` 且明确不在买枪倒计时范围时为 True；缺省 False。

```python
# 伪代码落点（实现时用真实 OpenCV 连通域）
_RESULT_BLOB_HEIGHT_RATIO = 0.45
_BUY_BAND_MIN_ROWS = 2
```

- [ ] **Step 4: 测试通过**

Run: `pytest tests/test_hud_boundary.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/hud_boundary.py tests/test_hud_boundary.py tests/fixtures/valorant_hud
git commit -m "$(cat <<'EOF'
feat(analyzer): calibrate HUD buy vs result structure classifier

EOF
)"
```

---

### Task 3: 边界状态机（唯一写边界权威）

**Files:**
- Modify: `lsc/analyzer/hud_boundary.py`
- Modify: `tests/test_hud_boundary.py`

- [ ] **Step 1: 写失败测试**

```python
from lsc.analyzer.hud_boundary import HudBoundaryMachine, HudObservation


def _obs(buy: bool, *, valid: bool = True, conf: float = 0.9) -> HudObservation:
    return HudObservation(
        frame_valid=valid,
        hud_present=valid,
        buy_banner_present=buy,
        timer_contradicts_buy=False,
        confidence=conf,
    )


def test_stable_buy_combat_buy_closes_one_round() -> None:
    m = HudBoundaryMachine()
    events = []
    # buy, buy, combat, combat, buy, buy
    seq = [
        (100.0, True), (101.0, True),
        (102.0, False), (103.0, False),
        (180.0, True), (181.0, True),
    ]
    for ts, buy in seq:
        ev = m.feed(ts, _obs(buy))
        if ev is not None:
            events.append(ev)
    assert len(events) == 1
    assert events[0].start == 102.0  # 第一帧 non-buy
    assert events[0].end == 180.0    # 第一帧 buy
    assert events[0].start_by == "buy_exit_visual"
    assert events[0].end_by == "next_buy_visual"
    assert events[0].boundary_source == "valorant_hud_transition"
    assert events[0].round_key == "hud-round-000102"


def test_mid_combat_start_waits_for_full_cycle() -> None:
    m = HudBoundaryMachine()
    assert m.feed(10.0, _obs(False)) is None
    assert m.feed(11.0, _obs(False)) is None
    assert m.feed(12.0, _obs(True)) is None  # 仅看到 buy，尚未 non-buy
    assert m.feed(13.0, _obs(True)) is None


def test_single_frame_glitch_does_not_transition() -> None:
    m = HudBoundaryMachine()
    m.feed(1.0, _obs(True))
    m.feed(2.0, _obs(True))  # stable buy
    assert m.feed(3.0, _obs(False)) is None  # 单帧 glitch
    assert m.phase_name == "wait_combat"  # 仍等待开战确认


def test_hud_missing_over_5s_invalidates_open_round() -> None:
    m = HudBoundaryMachine()
    for ts, buy in [(0.0, True), (1.0, True), (2.0, False), (3.0, False)]:
        m.feed(ts, _obs(buy))
    assert m.pending_start == 2.0
    for t in (4.0, 5.0, 6.0, 7.0, 8.0, 9.0):
        m.feed(t, _obs(False, valid=False))
    assert m.pending_start is None
    assert m.phase_name == "wait_buy"


def test_audio_only_feed_api_does_not_exist_for_close() -> None:
    # 契约：机器只接受 HudObservation；无 audio 重载
    assert not hasattr(HudBoundaryMachine, "feed_audio")


def test_duration_gate_drops_too_long() -> None:
    m = HudBoundaryMachine()
    m.feed(0.0, _obs(True)); m.feed(1.0, _obs(True))
    m.feed(2.0, _obs(False)); m.feed(3.0, _obs(False))
    # 下一买枪在 200s → duration 198 > 180
    ev = None
    for ts in (200.0, 201.0):
        ev = m.feed(ts, _obs(True))
    assert ev is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_hud_boundary.py -k "stable_buy or mid_combat or glitch or hud_missing or duration_gate or audio_only" -v`
Expected: FAIL

- [ ] **Step 3: 实现 `HudBoundaryMachine`**

```python
@dataclass(frozen=True)
class ClosedHudRound:
    start: float
    end: float
    phase: str
    start_by: str
    end_by: str
    boundary_source: str
    boundary_confidence: float
    round_key: str


class HudBoundaryMachine:
    """等待买枪 → 等待开战 → 回合已打开 → 闭合。

    转换时间取连续命中的第一帧；确认需 _STABLE_HITS 帧。
    """

    def __init__(self) -> None:
        self.phase_name = "wait_buy"  # wait_buy | wait_combat | round_open
        self.pending_start: float | None = None
        self._pending_conf: float = 0.0
        self._run_label: str | None = None  # "buy" | "non_buy" | "invalid"
        self._run_first_ts: float | None = None
        self._run_hits: int = 0
        self._hud_missing_since: float | None = None
        self._frozen_keys: set[str] = set()

    def feed(self, ts: float, obs: HudObservation) -> ClosedHudRound | None:
        # 1) HUD 缺失容忍
        # 2) 将 obs 映射为 label buy/non_buy/invalid
        # 3) 稳定命中后按状态图转移
        # 4) 闭合时检查时长与 confidence，生成 round_key 并冻结
        ...
```

闭合事件字段必须与 spec JSON 一致。`boundary_confidence = min(start_conf, end_conf)`；若 `< _MIN_BOUNDARY_CONFIDENCE` 则丢弃不入列。

- [ ] **Step 4: 测试通过**

Run: `pytest tests/test_hud_boundary.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/hud_boundary.py tests/test_hud_boundary.py
git commit -m "$(cat <<'EOF'
feat(analyzer): add HUD boundary state machine as sole writer

EOF
)"
```

---

### Task 4: round_detector 前向 HUD 观察 API

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Modify: `tests/test_round_detector.py`

- [ ] **Step 1: 写失败测试（可 mock FFmpeg）**

```python
def test_observe_hud_states_dedupes_overlap_timestamps(self, monkeypatch, tmp_path):
    from lsc.analyzer import round_detector as rd

    # monkeypatch 抽帧返回带重复 ts 的假帧路径列表
    ...
    states = rd.observe_valorant_hud_states(
        str(video),
        ffmpeg_path="ffmpeg",
        time_range=(10.0, 20.0),
        sample_interval=1.0,
    )
    ts_list = [s["ts"] for s in states]
    assert ts_list == sorted(ts_list)
    assert len(ts_list) == len(set(round(t, 3) for t in ts_list))
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_round_detector.py::TestXxx::test_observe_hud_states_dedupes_overlap_timestamps -v`
Expected: FAIL

- [ ] **Step 3: 实现 `observe_valorant_hud_states`**

在 `round_detector.py` 新增（**不要**把逻辑塞进 `_detect_round_phase_markers` 的 OCR 循环）：

```python
def observe_valorant_hud_states(
    video_path: str,
    ffmpeg_path: str,
    *,
    time_range: tuple[float, float],
    sample_interval: float = 1.0,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """前向抽帧：顶栏 + 中央双 ROI → classify_hud_frame。

    返回 [{ts, observation dict / is_buy, confidence, frame_valid}, ...]
    主路径默认不调用文本 OCR。
    """
```

实现要点：

- 复用现有 `run_ffmpeg_with_hwaccel_fallback` / `showinfo` pts 解析 / `tempfile` 清理模式。
- 每帧裁两块 ROI（相对坐标写成模块常量）。
- OpenCV 不可用时返回 `[]` 并由调用方报告「HUD 边界检测不可用」。
- **禁止**在本函数内调用 RapidOCR（OCR 仅可作后续可选诊断扩展）。

- [ ] **Step 4: 测试通过**

Run: `pytest tests/test_round_detector.py -k observe_hud -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/round_detector.py tests/test_round_detector.py
git commit -m "$(cat <<'EOF'
feat(analyzer): add forward HUD observation API without OCR

EOF
)"
```

---

### Task 5: 入列门禁、round_key、trim 旁路、merge 契约

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Modify: `tests/test_continuous_analysis_guards.py`

- [ ] **Step 1: 写失败测试**

```python
def test_hud_round_is_listable_within_5_180() -> None:
    hud = {
        "start": 100.0,
        "end": 112.0,  # 12s < 35
        "phase": "combat",
        "start_by": "buy_exit_visual",
        "end_by": "next_buy_visual",
        "boundary_source": "valorant_hud_transition",
        "boundary_confidence": 0.9,
        "round_key": "hud-round-000100",
    }
    assert room_handler._is_listable_hud_round(hud)
    assert not room_handler._is_listable_hud_round({**hud, "end": 100.0 + 4.0})
    assert not room_handler._is_listable_hud_round({**hud, "end": 100.0 + 181.0})


def test_audio_candidate_is_not_listable() -> None:
    assert not room_handler._is_listable_hud_round({
        "start": 100.0, "end": 160.0, "phase": "combat",
        "start_by": "audio", "end_by": "chime",
    })


def test_trim_skips_hud_visual_bounds() -> None:
    raw = {
        "start": 100.0, "end": 180.0, "phase": "combat",
        "start_by": "buy_exit_visual", "end_by": "next_buy_visual",
        "boundary_source": "valorant_hud_transition",
        "ocr_end": 150.0,
    }
    trimmed = room_handler._trim_valorant_combat_bounds(raw)
    assert trimmed["start"] == 100.0
    assert trimmed["end"] == 180.0


def test_valorant_round_key_prefers_hud_key_and_freezes() -> None:
    assert room_handler._valorant_round_key({
        "start": 321.4,
        "round_key": "hud-round-000321",
    }) == "hud-round-000321"


def test_merge_same_round_key_only_updates_bounds() -> None:
    existing = [{
        "start": 100.0, "end": 160.0,
        "round_key": "hud-round-000100",
        "boundary_source": "valorant_hud_transition",
        "start_by": "buy_exit_visual", "end_by": "next_buy_visual",
    }]
    window = [{
        "start": 100.2, "end": 162.0,
        "round_key": "hud-round-000100",
        "boundary_source": "valorant_hud_transition",
        "start_by": "buy_exit_visual", "end_by": "next_buy_visual",
    }]
    merged = room_handler._merge_round_windows(existing, window)
    assert len(merged) == 1
    assert merged[0]["end"] == 162.0


def test_merge_does_not_collapse_two_hud_rounds_by_overlap() -> None:
    existing = [{
        "start": 100.0, "end": 160.0, "round_key": "hud-round-000100",
        "boundary_source": "valorant_hud_transition",
        "start_by": "buy_exit_visual", "end_by": "next_buy_visual",
    }]
    window = [{
        "start": 150.0, "end": 210.0, "round_key": "hud-round-000150",
        "boundary_source": "valorant_hud_transition",
        "start_by": "buy_exit_visual", "end_by": "next_buy_visual",
    }]
    merged = room_handler._merge_round_windows(existing, window)
    assert len(merged) == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_continuous_analysis_guards.py -k "listable_hud or audio_candidate or trim_skips_hud or hud_key or merge_same_round or merge_does_not_collapse" -v`
Expected: FAIL

- [ ] **Step 3: 实现 handler 辅助函数**

在 `room_handler.py`：

```python
_HUD_MIN_LIST_DURATION_SEC = 5.0
_HUD_MAX_LIST_DURATION_SEC = 180.0
_HUD_BOUNDARY_SOURCE = "valorant_hud_transition"


def _is_listable_hud_round(round_data: dict[str, Any]) -> bool:
    try:
        start = float(round_data.get("start", 0.0))
        end = float(round_data.get("end", 0.0))
    except (TypeError, ValueError):
        return False
    dur = end - start
    return (
        round_data.get("boundary_source") == _HUD_BOUNDARY_SOURCE
        and round_data.get("start_by") == "buy_exit_visual"
        and round_data.get("end_by") == "next_buy_visual"
        and round_data.get("phase") != "pending"
        and _HUD_MIN_LIST_DURATION_SEC <= dur <= _HUD_MAX_LIST_DURATION_SEC
        and float(round_data.get("boundary_confidence") or 0.0) >= 0.35
    )


def _valorant_round_key(round_data: dict[str, Any]) -> str:
    existing = str(round_data.get("round_key") or "").strip()
    if existing:
        return existing
    try:
        start = float(round_data.get("start", 0.0))
    except (TypeError, ValueError):
        start = 0.0
    if round_data.get("boundary_source") == _HUD_BOUNDARY_SOURCE:
        return f"hud-round-{int(start):06d}"
    return f"round-{int(round(start / 10.0)):06d}"
```

修改 `_trim_valorant_combat_bounds`：若 `boundary_source == valorant_hud_transition`，直接 `return dict(round_data)`（浅拷贝），不做 pad/junk。

修改 `_merge_round_windows`：

- 若新旧任一侧带 `_HUD_BOUNDARY_SOURCE`：只按 `round_key` 对齐更新，**禁止**靠时间重叠合并不同 key。
- 旧音频重叠逻辑可保留给非 HUD 诊断数据，但不得把音频 round 升成可入列。

修改 `_auto_export_highlights` 时长门槛：

```python
is_hud = _is_listable_hud_round(hl) or hl.get("boundary_source") == _HUD_BOUNDARY_SOURCE
min_dur = _HUD_MIN_LIST_DURATION_SEC if is_hud else _VALORANT_MIN_EXPORT_DURATION_SEC
if source_end - source_start < min_dur:
    continue
# export_end - export_start 同理
```

并在 list_only 路径要求：仅当 `_is_listable_hud_round(hl)`（valorant continuous）才广播；见 Task 6。

- [ ] **Step 4: 更新旧测试期望**

`test_only_complete_ocr_rounds_are_auto_exportable` 保留作**历史 OCR 可导出门**语义，但新增注释：continuous 入列改走 `_is_listable_hud_round`。可追加：

```python
def test_ocr_exportable_no_longer_lists_without_hud_source() -> None:
    ocr = {
        "start": 102.0, "end": 154.0, "phase": "combat",
        "start_by": "ocr_buy_exit", "end_by": "next_buy",
    }
    assert room_handler._is_auto_exportable_valorant_round(ocr)
    assert not room_handler._is_listable_hud_round(ocr)
```

- [ ] **Step 5: 测试通过并 Commit**

```bash
git add python-backend/handlers/room_handler.py tests/test_continuous_analysis_guards.py
git commit -m "$(cat <<'EOF'
feat(backend): gate valorant list-only on HUD dual bounds

EOF
)"
```

---

### Task 6: 持续分析循环接入前向游标与门禁

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_continuous_analysis_loop` / worker）
- Modify: `tests/test_continuous_analysis_guards.py`

- [ ] **Step 1: 写失败的源码契约测试**

```python
def test_continuous_valorant_lists_only_hud_rounds() -> None:
    src = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    # 入列过滤必须调用 _is_listable_hud_round
    assert "_is_listable_hud_round" in src
    # 不得再把全部 pending_hl 无门禁送入 list_only（允许存在变量名，但赋值须过滤）
    assert "pending_only_hl" in src
    assert "ocr_confirmed_hl = []" in src or "ocr_confirmed_hl =" in src
    # 明确停止 OCR 升格：valorant 分支不应再以 ocr_confirmed 入列
    loop = src[src.index("async def _continuous_analysis_loop") :]
    # 粗检：list_only 调用前应有 listable 过滤
    assert "_is_listable_hud_round" in loop


def test_continuous_loop_keeps_forward_cursor_fields() -> None:
    src = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "last_processed_frame_ts" in src
    assert "observe_valorant_hud_states" in src or "HudBoundaryMachine" in src
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_continuous_analysis_guards.py::test_continuous_valorant_lists_only_hud_rounds tests/test_continuous_analysis_guards.py::test_continuous_loop_keeps_forward_cursor_fields -v`
Expected: FAIL

- [ ] **Step 3: 改造 continuous valorant 路径**

在 `_continuous_tasks[room_id]` 增加：

- `hud_machine`: `HudBoundaryMachine` 实例（或可序列化字段 + 重建）
- `last_processed_frame_ts: float`

每个 tick（在现有 `_analysis_semaphore` 内）：

1. `scan_from = max(0, last_processed_frame_ts - 3.0)`（3s 重叠）
2. `scan_to = worker_dur`
3. `states = observe_valorant_hud_states(..., time_range=(scan_from, scan_to))`
4. 按 ts 去重后 `machine.feed`；收集 `ClosedHudRound` → `dict`
5. `last_processed_frame_ts = max(processed ts)`；**FFmpeg 超时则不推进游标**
6. `full_rounds = _merge_round_windows(all_highlights, new_closed)`（仅 HUD）
7. 入列：

```python
pending_only_hl = [
    h for h in all_highlights
    if _is_listable_hud_round(h)
    and any(
        f"{rid}:{_valorant_round_key(h)}" not in _exported_clip_ids
        for rid in target_room_ids
    )
]
ocr_confirmed_hl = []  # 停止 OCR 升格
# 禁止对 HUD 回合调用 _trim_valorant_combat_bounds
await _auto_export_highlights(..., pending_only_hl, list_only=True, confirm_status="pending")
```

8. `clip_queued` 载荷追加 `boundary_source`、`boundary_confidence`（在 `_auto_export_highlights` 广播 dict 中从 `hl` 透传）。
9. OpenCV 不可用：更新 `analysis_stage` / 状态文案为「HUD 边界检测不可用」；**不**回退音频入列。
10. 音频/chime 仍可更新 `round_phase_detail` 或缩短 sleep，但**不得** `machine.feed` 之外写 start/end。
11. 取消任务：丢弃 `pending_start`；停录 finalize：从游标继续处理剩余帧，开放尾不闭合。
12. 落后追赶：按时间分段，禁止跳到文件尾。

保留 `detect_valorant_rounds` 音频输出仅写入 sidecar/诊断时，确保不进入 `pending_only_hl`。

- [ ] **Step 4: 测试通过**

Run: `pytest tests/test_continuous_analysis_guards.py -v`
Expected: PASS（必要时更新过时的 lookback/OCR 密度守卫，使其匹配「主路径无 OCR」）

- [ ] **Step 5: Commit**

```bash
git add python-backend/handlers/room_handler.py tests/test_continuous_analysis_guards.py
git commit -m "$(cat <<'EOF'
feat(backend): drive continuous valorant rounds from HUD cursor

EOF
)"
```

---

### Task 7: phase_scheduler 降级为扫描调度

**Files:**
- Modify: `lsc/analyzer/phase_scheduler.py`
- Modify: `tests/test_phase_scheduler.py`

- [ ] **Step 1: 写失败测试**

```python
def test_hud_buy_maps_to_buy_phase_budget() -> None:
    cfg = get_profile("valorant")
    st = next_round_phase(
        RoundPhase.UNKNOWN,
        cfg,
        now_mono=10.0,
        phase_entered_at=0.0,
        signals={"hud_buy": True, "hud_non_buy": False},
    )
    assert st.phase in {RoundPhase.BUY, RoundPhase.UNKNOWN}


def test_chime_alone_does_not_set_just_confirmed() -> None:
    cfg = get_profile("valorant")
    st = next_round_phase(
        RoundPhase.COMBAT,
        cfg,
        now_mono=100.0,
        phase_entered_at=50.0,
        signals={
            "chime": True,
            "has_start": True,
            "has_end": True,  # 旧语义
            "hud_boundary_closed": False,
        },
    )
    # 新契约：无 hud_boundary_closed 不得 just_confirmed
    assert st.just_confirmed is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_phase_scheduler.py -k "hud_buy_maps or chime_alone" -v`
Expected: FAIL

- [ ] **Step 3: 最小改动**

- `signals` 增加 `hud_buy` / `hud_non_buy` / `hud_boundary_closed`。
- `just_confirmed=True` **仅当** `hud_boundary_closed`。
- `chime` / `energy_*` 仍可进入 `POST_COMBAT` 以加密扫描，但确认入列不由此产生。
- 模块 docstring 写明：边界写入在 `hud_boundary.HudBoundaryMachine`。

room_handler 推导信号处：用 machine 稳定态填充 `hud_buy`/`hud_non_buy`，闭合事件当帧设 `hud_boundary_closed=True`；**删除**用 `start_by in (ocr_buy_exit,…)` 填 `has_start` 来间接触发确认的路径（或保留 has_* 仅诊断）。

- [ ] **Step 4: 测试通过并 Commit**

```bash
git add lsc/analyzer/phase_scheduler.py tests/test_phase_scheduler.py python-backend/handlers/room_handler.py
git commit -m "$(cat <<'EOF'
refactor(analyzer): confine phase scheduler to scan budget only

EOF
)"
```

---

### Task 8: 前端可选字段

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`（`clip_queued` handler 透传字段）
- Modify: `tests/test_frontend_stability_guards.py`（若守卫检查类型字面量）

- [ ] **Step 1: 扩展类型**

```typescript
// ClipSegment 与 clip_queued 载荷
boundary_source?: string
boundary_confidence?: number
```

- [ ] **Step 2: `clip_queued` 更新时保留字段**

在 Workbench `on('clip_queued')` 的 add/update 对象中写入：

```typescript
boundary_source: data.boundary_source,
boundary_confidence: data.boundary_confidence,
```

不新增 UI，不新增事件。

- [ ] **Step 3: 类型检查**

Run: `cd lsc-electron && npx tsc --noEmit`
Expected: 无新错误

- [ ] **Step 4: Commit**

```bash
git add lsc-electron/src/types/index.ts lsc-electron/src/pages/Workbench/index.tsx tests/test_frontend_stability_guards.py
git commit -m "$(cat <<'EOF'
feat(frontend): accept HUD boundary metadata on clip_queued

EOF
)"
```

---

### Task 9: 回归守卫与验收清单

**Files:**
- Modify: `tests/test_continuous_analysis_guards.py`
- Modify: `docs/superpowers/specs/2026-07-19-valorant-hud-round-boundary-design.md`（状态改为已确认可实施）

- [ ] **Step 1: 补齐契约测试**

```python
def test_auto_export_highlights_skips_non_hud_when_valorant_gate_enabled() -> None:
    """纯函数级：模拟 hl 列表过滤结果。"""
    audio = {"start": 1.0, "end": 60.0, "start_by": "audio", "end_by": "chime", "phase": "combat"}
    hud = {
        "start": 100.0, "end": 160.0, "phase": "combat",
        "start_by": "buy_exit_visual", "end_by": "next_buy_visual",
        "boundary_source": "valorant_hud_transition",
        "boundary_confidence": 0.9,
        "round_key": "hud-round-000100",
    }
    gated = [h for h in (audio, hud) if room_handler._is_listable_hud_round(h)]
    assert gated == [hud]


def test_clip_queued_payload_documents_boundary_fields() -> None:
    src = Path("python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    assert "boundary_source" in src
    assert "boundary_confidence" in src
```

- [ ] **Step 2: 跑相关套件**

```bash
pytest tests/test_hud_boundary.py tests/test_phase_scheduler.py tests/test_continuous_analysis_guards.py tests/test_round_detector.py tests/test_clip_list_upsert.py -v
```

Expected: PASS

- [ ] **Step 3: 本地样本验收（人工，不进 CI）**

对 `recording_20260719_144951_d3f865.mp4`：

1. 启动 continuous `valorant_round`，确认 `clip_queued` 条数接近用户手切 ~20，且无 audio-only。
2. 抽查 3 个切片：起点在买枪横幅消失后 ≤1.5s；终点在下一买枪首帧 ≤1.5s。
3. 结算/回放在片内；无跨回合；无选人/赛后。
4. 确认 pending，手动确认后才导出。

- [ ] **Step 4: 更新 spec 状态行**

```markdown
> **状态：** 已确认，实施计划见 `docs/superpowers/plans/2026-07-19-valorant-hud-round-boundary.md`
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_continuous_analysis_guards.py docs/superpowers/specs/2026-07-19-valorant-hud-round-boundary-design.md
git commit -m "$(cat <<'EOF'
test: lock HUD list-only gates and mark spec ready

EOF
)"
```

---

## Spec 覆盖自检

| Spec 要求 | Task |
|-----------|------|
| 语言无关 HUD 边界 | 1–2 |
| 起点/终点/稳定观察契约 | 3 |
| 前向游标与性能 | 4, 6 |
| 音频不得入列 | 5–6 |
| list_only + pending | 6, 8 |
| round_key / merge / 冻结 | 3, 5 |
| 错误处理（OpenCV/遮挡/开放尾） | 3, 6 |
| 测试策略分层 | 1–3, 5, 7, 9 |
| 前端可选字段 | 8 |
| 审阅：35s/trim/ocr 升格/phase 权威 | 5–7 |

## 占位符扫描

计划中无 TBD/TODO；阈值常量给出初值，由 Task 2 夹具校准后写入模块常量。

---

**Plan complete and saved to `docs/superpowers/plans/2026-07-19-valorant-hud-round-boundary.md`.**

两种执行方式：

1. **Subagent-Driven（推荐）** — 每任务派生子代理，任务间复审，迭代快  
2. **Inline Execution** — 本会话按 executing-plans 批量推进并设检查点  

选哪一种？
