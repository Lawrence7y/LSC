# M4 / B1 — AnalyzerPlugin 协议 + Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立与平台适配器同构的分析插件 Protocol + Registry；内置 `generic` stub 与契约测试。本阶段**不**改 `room_handler` 主路径（零切线或仅注册入口）。

**Architecture:** 无状态插件实例 + 会话 `state` dict；Registry `get(game)` 未命中回退 `default()`（generic）。v1 仅 pull 模型（录制文件窗口），无实时帧。

**Tech Stack:** Python Protocol、dataclass、pytest

**Spec:** `docs/spec-deqt-orchestrator-analyzer-plugin.md` §阶段 B1  
**前置:** 无强依赖；可与 M1–M3 并行，但建议 M3 后执行以免 handler 双线改动冲突  
**后继:** M5（B2 插件迁入 + handler 瘦身）

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）。

---

## Spec 校正（写入契约注释）

| Spec 名 | 实际代码（B2 再迁） |
|---------|---------------------|
| `_compute_continuous_scan_range` | `_continuous_valorant_scan_budget`（`room_handler.py:1778`） |
| generic `analyze_file` 场景检测 | `_run_scene_analysis` 在 **room_handler**，不在 pipeline |
| `HighlightAnalyzer` valorant | 音频 `detect_valorant_rounds`；生产 one-shot/continuous 用 **hybrid** |

B1 只定协议与 registry；实现细节在 M5 对齐真实符号。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `lsc/analyzer/base.py` | `AnalyzerCapabilities` / `ScanWindow` / `AnalyzerPlugin` Protocol |
| `lsc/analyzer/registry.py` | `AnalyzerRegistry` + 模块级单例 |
| `lsc/analyzer/generic_plugin.py` | 最小 generic 插件（可 stub `analyze_file` 调 scene 逻辑的占位） |
| `tests/test_analyzer_registry.py` | 注册 / 查找 / 兜底 / 无状态 |
| `tests/test_analyzer_plugin_contract.py` | 契约测试 |

**本阶段不修改：** `room_handler.py` 持续分析主路径（M5 再接）。

---

### Task 1: Protocol + dataclasses

**Files:**
- Create: `lsc/analyzer/base.py`
- Test: 先写 `tests/test_analyzer_registry.py` 的 import 失败驱动

- [ ] **Step 1: 写失败测试骨架**

```python
# tests/test_analyzer_registry.py
from lsc.analyzer.base import AnalyzerCapabilities, AnalyzerPlugin, ScanWindow
from lsc.analyzer.registry import AnalyzerRegistry, get_analyzer, list_analyzers


def test_scan_window_fields():
    w = ScanWindow(start_sec=1.0, end_sec=2.0, timeout_sec=30.0, use_ocr=False)
    assert w.end_sec == 2.0
```

- [ ] **Step 2: 实现 base.py**

```python
# lsc/analyzer/base.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AnalyzerCapabilities:
    realtime_continuous: bool
    posthoc_file: bool
    needs_ocr: bool
    needs_audio: bool
    game_specific: bool


@dataclass(slots=True)
class ScanWindow:
    start_sec: float
    end_sec: float
    timeout_sec: float
    use_ocr: bool


@runtime_checkable
class AnalyzerPlugin(Protocol):
    """无状态分析插件。会话状态放在 state dict，禁止写实例可变字段。"""

    game: str
    display_name: str

    def capabilities(self) -> AnalyzerCapabilities: ...

    def analyze_file(
        self,
        video_path: str,
        *,
        progress_callback: Callable[[str, float, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None: ...

    def plan_scan_window(
        self,
        state: dict[str, Any],
        current_dur: float,
        pressure: dict[str, Any],
    ) -> ScanWindow: ...

    def scan_window(
        self,
        video_path: str,
        window: ScanWindow,
        state: dict[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]: ...
```

- [ ] **Step 3:**

Run: `pytest tests/test_analyzer_registry.py::test_scan_window_fields -v`  
Expected: PASS after base exists（registry 测可先 xfail）

---

### Task 2: Registry

**Files:**
- Create: `lsc/analyzer/registry.py`
- Create: `lsc/analyzer/generic_plugin.py`（最小实现）

- [ ] **Step 1: generic 最小插件**

```python
# lsc/analyzer/generic_plugin.py
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lsc.analyzer.base import AnalyzerCapabilities, ScanWindow


class GenericAnalyzerPlugin:
    game = "generic"
    display_name = "通用场景"

    def capabilities(self) -> AnalyzerCapabilities:
        return AnalyzerCapabilities(
            realtime_continuous=False,
            posthoc_file=True,
            needs_ocr=False,
            needs_audio=False,
            game_specific=False,
        )

    def analyze_file(
        self,
        video_path: str,
        *,
        progress_callback=None,
        cancel_check=None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        # B1: 占位返回 []；B2 迁入 _run_scene_analysis
        if cancel_check and cancel_check():
            return None
        return []

    def plan_scan_window(
        self,
        state: dict[str, Any],
        current_dur: float,
        pressure: dict[str, Any],
    ) -> ScanWindow:
        last = float(state.get("last_analyzed", 0.0) or 0.0)
        lookback = float(pressure.get("lookback_sec", 240.0))
        if last <= 0.0:
            start, end = 0.0, float(current_dur)
        else:
            start = max(0.0, last - lookback)
            end = float(current_dur)
        return ScanWindow(start_sec=start, end_sec=end, timeout_sec=60.0, use_ocr=False)

    def scan_window(
        self,
        video_path: str,
        window: ScanWindow,
        state: dict[str, Any],
        *,
        cancel_check=None,
    ) -> list[dict[str, Any]]:
        # B1 占位；B2 接音频节奏 / scene 增量
        state["last_analyzed"] = window.end_sec
        return []
```

- [ ] **Step 2: Registry**

```python
# lsc/analyzer/registry.py
from __future__ import annotations

import logging
import threading
from typing import Iterable

from lsc.analyzer.base import AnalyzerPlugin
from lsc.analyzer.generic_plugin import GenericAnalyzerPlugin

_log = logging.getLogger(__name__)
_lock = threading.RLock()
_plugins: dict[str, AnalyzerPlugin] = {}
_default_game = "generic"


def register(plugin: AnalyzerPlugin) -> None:
    with _lock:
        _plugins[plugin.game] = plugin
        _log.info("analyzer registered: %s (%s)", plugin.game, plugin.display_name)


def get(game: str | None) -> AnalyzerPlugin:
    with _lock:
        if game and game in _plugins:
            return _plugins[game]
        if game and game not in _plugins:
            _log.warning("analyzer %r not found, fallback to %s", game, _default_game)
        return _plugins[_default_game]


def list_plugins() -> list[AnalyzerPlugin]:
    with _lock:
        return list(_plugins.values())


def default() -> AnalyzerPlugin:
    return get(_default_game)


def _ensure_builtins() -> None:
    with _lock:
        if _default_game not in _plugins:
            register(GenericAnalyzerPlugin())


_ensure_builtins()

# 友好别名
get_analyzer = get
list_analyzers = list_plugins
```

- [ ] **Step 3: 注册/兜底测试**

```python
# tests/test_analyzer_registry.py
from lsc.analyzer.registry import get, list_plugins, register, default
from lsc.analyzer.generic_plugin import GenericAnalyzerPlugin


def test_default_is_generic():
    p = default()
    assert p.game == "generic"


def test_unknown_falls_back_to_generic():
    p = get("does-not-exist")
    assert p.game == "generic"


def test_register_and_get():
    class Dummy:
        game = "dummy_test"
        display_name = "Dummy"
        def capabilities(self):
            from lsc.analyzer.base import AnalyzerCapabilities
            return AnalyzerCapabilities(False, True, False, False, True)
        def analyze_file(self, *a, **k):
            return []
        def plan_scan_window(self, state, current_dur, pressure):
            from lsc.analyzer.base import ScanWindow
            return ScanWindow(0, current_dur, 1, False)
        def scan_window(self, *a, **k):
            return []
    register(Dummy())
    assert get("dummy_test").game == "dummy_test"


def test_stateless_no_cross_talk():
    p = get("generic")
    s1: dict = {"last_analyzed": 10.0}
    s2: dict = {"last_analyzed": 0.0}
    w1 = p.plan_scan_window(s1, 100.0, {})
    w2 = p.plan_scan_window(s2, 100.0, {})
    assert w1.start_sec != w2.start_sec
    # 插件实例无写入
    assert not hasattr(p, "last_analyzed") or getattr(p, "last_analyzed", None) is None
```

Run: `pytest tests/test_analyzer_registry.py -v`  
Expected: PASS

---

### Task 3: 契约测试

**Files:** Create `tests/test_analyzer_plugin_contract.py`

- [ ] **Step 1:**

```python
# tests/test_analyzer_plugin_contract.py
from __future__ import annotations

import pytest

from lsc.analyzer.registry import list_plugins


@pytest.mark.parametrize("plugin", list_plugins(), ids=lambda p: p.game)
def test_capabilities_consistent(plugin):
    caps = plugin.capabilities()
    assert isinstance(caps.realtime_continuous, bool)
    assert isinstance(caps.posthoc_file, bool)
    if plugin.game == "generic":
        assert caps.posthoc_file is True
        assert caps.game_specific is False


@pytest.mark.parametrize("plugin", list_plugins(), ids=lambda p: p.game)
def test_plan_and_scan_window_shape(plugin, tmp_path):
    if not plugin.capabilities().realtime_continuous and plugin.game == "generic":
        # generic B1：plan/scan 仍可调用
        pass
    state: dict = {}
    window = plugin.plan_scan_window(state, current_dur=30.0, pressure={})
    assert window.end_sec >= window.start_sec
    assert window.timeout_sec > 0
    video = tmp_path / "dummy.mp4"
    video.write_bytes(b"")  # 占位；scan 可空实现
    cancelled = {"n": 0}
    def cancel_check():
        cancelled["n"] += 1
        return False
    out = plugin.scan_window(str(video), window, state, cancel_check=cancel_check)
    assert isinstance(out, list)


@pytest.mark.parametrize("plugin", list_plugins(), ids=lambda p: p.game)
def test_cancel_check_on_analyze_file(plugin):
    def cancel_always():
        return True
    result = plugin.analyze_file("nope.mp4", cancel_check=cancel_always)
    assert result is None or result == []
```

Run: `pytest tests/test_analyzer_plugin_contract.py -v`  
Expected: PASS（仅 generic 时）

---

### Task 4: `__init__.py` 导出（可选）

**Files:** Modify `lsc/analyzer/__init__.py` 仅在不破坏现有 `HighlightAnalyzer` 导出的前提下增加：

```python
from lsc.analyzer.registry import get_analyzer, list_analyzers  # optional
```

若现有 `__all__` 严格，可不改，M5 再导出。

- [ ] **Step 1:**

```bash
pytest tests/test_analyzer_registry.py tests/test_analyzer_plugin_contract.py -v
```

Expected: PASS

---

## M4 验收对照

| 验收项 | 证据 |
|--------|------|
| 契约测试绿 | pytest |
| 未命中回退 generic | `test_unknown_falls_back_to_generic` |
| 无状态约束 | `test_stateless_no_cross_talk` |
| room_handler 未改主路径 | `git diff python-backend/handlers/room_handler.py` 空（或仅注释） |

---

## 回滚

删除 `base.py` / `registry.py` / `generic_plugin.py` / 对应 tests。
