# M5 / B2 — 分析插件迁入 + room_handler 瘦身 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Valorant / generic 能力封装为插件；`room_handler` 持续分析与 one-shot 经 Registry 调用；handler 只保留编排（线程、压力门控、广播、取消）。底层 `round_detector` 等**不物理搬家**。

**Architecture:** 包装层插件；`plan_scan_window` / `scan_window` / `analyze_file` 对齐现网 hybrid + scene 路径。广播 payload 字段集不变。

**Tech Stack:** AnalyzerPlugin（M4）、现有 analyzer 模块、pytest

**Spec:** `docs/spec-deqt-orchestrator-analyzer-plugin.md` §阶段 B2  
**前置:** M4  
**后继:** 可选 B3（物理归拢，另开 plan）

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。  
**兼容：** `tests/test_continuous_analysis_guards.py` 依赖 `_continuous_valorant_scan_budget` —— 迁出后必须在 `room_handler` **re-export** 或改测试 import 到插件辅助函数（优先 re-export 薄包装，减少测试 churn）。

---

## 关键命名对照

| Spec / 计划用语 | 实际符号 |
|-----------------|----------|
| plan 扫描窗口 | `_continuous_valorant_scan_budget` → 收入 `ValorantAnalyzerPlugin.plan_scan_window`（及返回值适配 `ScanWindow`） |
| scan 执行 | `_continuous_valorant_worker` 内 `detect_valorant_rounds_hybrid` |
| one-shot valorant | `_analyze_scene_or_rounds` → hybrid（**不是** `HighlightAnalyzer` 音频路径） |
| generic / scene | `_run_scene_analysis`（room_handler 1999+） |
| OCR refine | `_continuous_valorant_refine_with_ocr` 当前恒 `False` —— 插件内保持同行为 |

---

## 文件结构

| 文件 | 动作 |
|------|------|
| `lsc/analyzer/valorant_plugin.py` | **新建** 包装 hybrid + phase budget |
| `lsc/analyzer/generic_plugin.py` | **充实** `analyze_file` ← `_run_scene_analysis` 逻辑 |
| `lsc/analyzer/registry.py` | 注册 valorant |
| `python-backend/handlers/room_handler.py` | 瘦身：循环保留，扫描/分析经插件 |
| `tests/test_analyzer_plugin_parity.py` | 新旧路径对等 |
| `tests/test_continuous_analysis_guards.py` | 保持绿（re-export） |

---

### Task 1: 抽出 scene 分析到可复用函数

**Files:**
- Prefer create: `lsc/analyzer/scene_analysis.py`（把 `_run_scene_analysis` 主体迁入）
- 或：先复制到 `generic_plugin.py` 再让 room_handler 调插件

推荐：**迁到 `lsc/analyzer/scene_analysis.py`**，handler 与 generic 共用，避免循环 import。

- [ ] **Step 1: 移动 `_run_scene_analysis`（1999–2212）到**

```python
# lsc/analyzer/scene_analysis.py
def run_scene_analysis(video_path: str, threshold: float = ..., ...) -> list[dict]:
    ...  # 原逻辑：FFmpeg scene filter、15s 切分、padding、过滤、去重
```

`room_handler._run_scene_analysis` 改为：

```python
def _run_scene_analysis(*args, **kwargs):
    from lsc.analyzer.scene_analysis import run_scene_analysis
    return run_scene_analysis(*args, **kwargs)
```

- [ ] **Step 2:**

```bash
pytest tests/test_continuous_analysis_guards.py -v -k "scene" --collect-only
# 跑任何覆盖 scene 的现有测试；若无专用测，跑 one-shot 相关：
pytest tests/test_synced_continuous_analysis.py -v --tb=short
```

Expected: 不因移动而红。

---

### Task 2: `ValorantAnalyzerPlugin`

**Files:** Create `lsc/analyzer/valorant_plugin.py`

- [ ] **Step 1: 实现 capabilities + analyze_file**

```python
# lsc/analyzer/valorant_plugin.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lsc.analyzer.base import AnalyzerCapabilities, ScanWindow


class ValorantAnalyzerPlugin:
    game = "valorant"
    display_name = "Valorant"

    def capabilities(self) -> AnalyzerCapabilities:
        return AnalyzerCapabilities(
            realtime_continuous=True,
            posthoc_file=True,
            needs_ocr=True,
            needs_audio=True,
            game_specific=True,
        )

    def analyze_file(
        self,
        video_path: str,
        *,
        progress_callback=None,
        cancel_check=None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """对齐 _analyze_scene_or_rounds 的 valorant 分支（hybrid）。"""
        options = options or {}
        from lsc.analyzer.round_detector import (
            ModelContractError,
            detect_valorant_rounds_hybrid,
        )
        try:
            return detect_valorant_rounds_hybrid(
                video_path,
                ffmpeg_path=options.get("ffmpeg_path"),
                model_dir=options.get("model_dir"),
                cancel_check=cancel_check,
                progress_callback=progress_callback,
                session_id=options.get("session_id"),
            )
        except ModelContractError:
            raise
        except Exception:
            # 与 handler 一致：失败可回落 scene（由调用方决定）
            return None
```

- [ ] **Step 2: `plan_scan_window` 包装 `_continuous_valorant_scan_budget`**

把 `room_handler._continuous_valorant_scan_budget` **移到** `valorant_plugin.py`（或 `lsc/analyzer/valorant_scan_budget.py`），返回值适配：

```python
def plan_scan_window(self, state, current_dur, pressure) -> ScanWindow:
    scan_range, use_ocr, timeout, full_rescan = compute_valorant_scan_budget(
        mode=state.get("mode", "valorant_round"),
        last_analyzed=float(state.get("last_analyzed", 0.0) or 0.0),
        current_dur=current_dur,
        pressure=pressure,
        tick_count=int(state.get("tick_count", 0) or 0),
        round_phase=state.get("round_phase"),
        valorant_profile=state.get("valorant_profile"),
        pending_start=state.get("pending_start"),
        prediction=state.get("prediction"),
    )
    state["full_rescan"] = full_rescan  # 编排侧若需要
    start, end = scan_range
    return ScanWindow(
        start_sec=float(start),
        end_sec=float(end),
        timeout_sec=float(timeout),
        use_ocr=bool(use_ocr),
    )
```

`room_handler` 保留：

```python
def _continuous_valorant_scan_budget(*args, **kwargs):
    """兼容 tests/test_continuous_analysis_guards.py。"""
    from lsc.analyzer.valorant_plugin import compute_valorant_scan_budget
    return compute_valorant_scan_budget(*args, **kwargs)
```

**禁止**改 guards 测试的断言；只保入口。

- [ ] **Step 3: `scan_window`**

```python
def scan_window(self, video_path, window, state, *, cancel_check=None):
    from lsc.analyzer.round_detector import detect_valorant_rounds_hybrid
    mode = state.get("mode", "valorant_round")
    game = state.get("game", "valorant")
    if game == "valorant" and mode == "valorant_round":
        rounds = detect_valorant_rounds_hybrid(
            video_path,
            time_range=(window.start_sec, window.end_sec),
            ffmpeg_path=state.get("ffmpeg_path"),
            model_dir=state.get("model_dir"),
            cancel_check=cancel_check,
            progress_callback=state.get("progress_callback"),
            session_id=state.get("session_id"),
            classifier=state.get("classifier"),
            runtime_state=state.get("runtime_state"),
            # 其余 kwargs 与 _continuous_valorant_worker._do_scan 对齐
        )
        state["last_analyzed"] = window.end_sec
        return rounds or []
    # 非 valorant_round：委托音频节奏检测（从 worker else 分支搬）
    from ... import detect_rounds_by_audio_rhythm  # 实际符号以 worker 为准
    ...
```

对照 `room_handler.py:6181–6206` 逐参数抄写，禁止漏 `runtime_state` / classifier 复用。

- [ ] **Step 4: 注册**

在 `registry.py` `_ensure_builtins`：

```python
from lsc.analyzer.valorant_plugin import ValorantAnalyzerPlugin
register(ValorantAnalyzerPlugin())
register(GenericAnalyzerPlugin())  # 已有则跳过
```

---

### Task 3: 充实 `GenericAnalyzerPlugin.analyze_file`

**Files:** `lsc/analyzer/generic_plugin.py`

- [ ] **Step 1:**

```python
def analyze_file(...):
    if cancel_check and cancel_check():
        return None
    from lsc.analyzer.scene_analysis import run_scene_analysis
    opts = options or {}
    return run_scene_analysis(
        video_path,
        threshold=float(opts.get("threshold", 0.3)),
        # 其余与 _run_scene_analysis 签名对齐
    )
```

`capabilities` 保持 B1；若持续 scene 模式也走 `plan/scan`，可把 `realtime_continuous=True` 仅在确实接线后改 —— 默认保持 False，continuous scene 仍可用 handler 旧音频节奏分支直至明确迁完。

---

### Task 4: handler 接线（瘦身）

**Files:** `python-backend/handlers/room_handler.py`

- [ ] **Step 1: one-shot `_analyze_scene_or_rounds`**

```python
def _analyze_scene_or_rounds(video_path, game="valorant", threshold=...):
    from lsc.analyzer.registry import get as get_analyzer
    plugin = get_analyzer(game)
    result = plugin.analyze_file(
        video_path,
        options={"threshold": threshold, "ffmpeg_path": ..., "model_dir": ...},
        cancel_check=...,
        progress_callback=...,
    )
    if not result and game == "valorant":
        # 保持旧回落：hybrid 失败 → scene
        return get_analyzer("generic").analyze_file(...)
    return result or []
```

删除函数内直接 `from lsc.analyzer.round_detector import detect_valorant_rounds_hybrid`（改由插件）。

- [ ] **Step 2: continuous worker**

在 `_continuous_valorant_worker` / `_do_scan`：

```python
plugin = get_analyzer(game)
# state 组装：mode, last_analyzed, round_phase, profile, prediction, model_dir, ...
window = plugin.plan_scan_window(state, current_dur, pressure)
# finalize override / degrade 仍在 handler（编排）：可改 window.use_ocr / timeout
highlights = plugin.scan_window(video_path, window, state, cancel_check=scan_abort)
```

**保留在 handler：**

- `_continuous_analysis_loop` 生命周期
- `_continuous_effective_interval` 压力门控 / `pause_analysis`
- `_build_continuous_status_payload` / `continuous_analysis_status` / `continuous_highlights` 广播
- 同步多房间映射 `_map_highlight_to_room`
- 取消与 `_analysis_semaphore`
- phase FSM tick（`next_round_phase` / `predict_round_clock`）—— 可作为 state 更新留在 handler，或下一步再下沉；**本 Task 至少把 scan budget + hybrid 扫描下沉**

若 phase FSM 暂留 handler：在 kick 前写 `state["round_phase"]=...` 再 `plan_scan_window`。

- [ ] **Step 3: 移除 handler 内对 analyzer 的散落 import**

```bash
rg -n "phase_scheduler|ocr_detector|round_detector|valorant_frame_classifier|ocr_accel" python-backend/handlers/room_handler.py
```

允许保留：`ocr_accel.normalize_ocr_accel`（settings）、`invalidate_ocr`（设置变更）—— 属资源生命周期，非游戏检测分支。  
游戏检测分支应只经插件。

- [ ] **Step 4:**

```bash
pytest tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py -v
pytest tests/test_phase_scheduler.py tests/test_round_detector.py tests/test_valorant_dense_refine.py tests/test_round_boundary_optimization.py -v
pytest tests/test_analyzer_registry.py tests/test_analyzer_plugin_contract.py -v
```

Expected: PASS

---

### Task 5: 插件对等测试 + payload 快照

**Files:** Create `tests/test_analyzer_plugin_parity.py`

- [ ] **Step 1: 回合区间对等（有样例录像时）**

若仓库有 fixture 短视频 / mock：

```python
def test_valorant_analyze_file_matches_hybrid_direct(monkeypatch, tmp_path):
    # monkeypatch detect_valorant_rounds_hybrid 返回固定段
    fixed = [{"start": 1.0, "end": 10.0, "title": "R1"}]
    monkeypatch.setattr(
        "lsc.analyzer.round_detector.detect_valorant_rounds_hybrid",
        lambda *a, **k: fixed,
    )
    from lsc.analyzer.registry import get
    out = get("valorant").analyze_file(str(tmp_path / "x.mp4"))
    assert out == fixed
```

真实文件对等（可选，标 `@pytest.mark.slow`）：旧路径脚本 vs 插件，区间 ±0.5s。

- [ ] **Step 2: payload 字段快照**

对 `_build_continuous_status_payload` 返回的 key 集做断言（与迁移前金样对比）：

```python
REQUIRED_STATUS_KEYS = {
    "running", "room_id", "mode", "game", "last_analyzed", ...
}  # 从现网一次运行或源码列出完整集合

def test_status_payload_keys_stable():
    # 构造最小假状态调用 _build_continuous_status_payload
    ...
    assert REQUIRED_STATUS_KEYS <= set(payload)
```

`continuous_highlights` 字段集同样冻结。

- [ ] **Step 3:**

```bash
pytest tests/test_analyzer_plugin_parity.py -v
```

Expected: PASS

---

### Task 6: 文档

- [ ] 更新 `docs/PROJECT_DESIGN.md` 分析章节：说明 AnalyzerRegistry + 插件；持续分析仍为录制文件 pull 扫描。
- [ ] Spec B3 物理归拢**不要**在本 plan 做。

---

## M5 验收对照

| 验收项 | 证据 |
|--------|------|
| 分析存量测试全绿 | 上方 pytest 列表 |
| 插件对等 / 契约绿 | parity + contract |
| payload 字段集不变 | snapshot 测试 |
| handler 无游戏分支直接 import detector | `rg` 清点 |
| 底层文件未物理搬家 | `lsc/analyzer/round_detector.py` 仍在原路径 |

---

## 回滚

Revert handler 接线；删除 `valorant_plugin.py`；generic 恢复 stub；registry 只留 generic。`scene_analysis.py` 可保留（无害）。
