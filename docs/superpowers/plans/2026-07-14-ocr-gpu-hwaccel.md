# OCR GPU / 硬解加速 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为持续分析 OCR 增加 `ocr_accel`（auto/dml/cuda/cpu）加速与 FFmpeg 硬解抽帧，设置页可切换，默认 auto 探针选最快后端并安全回退 CPU。

**Architecture:** 新建纯逻辑模块 `lsc/analyzer/ocr_accel.py` 负责枚举校验、provider 探测、微基准缓存、RapidOCR 构造参数与 FFmpeg `-hwaccel` 参数；`ocr_detector` / `round_detector` 消费该模块；`save_settings` 持久化并在变更时 `invalidate_ocr()`；前端 Settings 露出四档 Select。

**Tech Stack:** Python 3.10+、rapidocr-onnxruntime、onnxruntime（可选 directml/gpu）、FFmpeg、React/TS Settings、pytest

**Spec:** [docs/superpowers/specs/2026-07-14-ocr-gpu-hwaccel-design.md](../specs/2026-07-14-ocr-gpu-hwaccel-design.md)

---

## File map

| 文件 | 职责 |
|------|------|
| `lsc/analyzer/ocr_accel.py` | 新建：归一化、探测、探针缓存、RapidOCR kwargs、hwaccel 参数 |
| `lsc/analyzer/ocr_detector.py` | `_get_ocr` / `invalidate_ocr`；抽帧注入 hwaccel+回退 |
| `lsc/analyzer/round_detector.py` | 回合 OCR 抽帧同样注入 hwaccel+回退 |
| `python-backend/handlers/room_handler.py` | 默认 settings、`save_settings` 校验 `ocr_accel`、变更时 invalidate |
| `lsc-electron/src/types/index.ts` | `RecordSettings.ocr_accel` |
| `lsc-electron/src/store/appStore.ts` | `defaultSettings.ocr_accel = 'auto'` |
| `lsc-electron/src/pages/Settings/index.tsx` | 「OCR 加速」Select + 说明 |
| `tests/test_ocr_accel.py` | 单元测试（探针/归一化/命令构建/回退） |
| `tests/test_frontend_stability_guards.py` | 设置页文案/字段守卫（若已有类似模式则追加） |
| `requirements-ai.txt` | 可选依赖说明注释（不强制改包名） |
| `CLAUDE.md` §3.2 设置表 | 一行文档同步（可选，与实现同提交） |

---

### Task 1: `ocr_accel` 纯逻辑 — 归一化与 provider 探测

**Files:**
- Create: `lsc/analyzer/ocr_accel.py`
- Test: `tests/test_ocr_accel.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ocr_accel.py
from lsc.analyzer.ocr_accel import (
    VALID_OCR_ACCEL,
    normalize_ocr_accel,
    list_accel_candidates,
    resolve_ocr_accel,
)


def test_normalize_ocr_accel_defaults_and_aliases() -> None:
    assert normalize_ocr_accel(None) == "auto"
    assert normalize_ocr_accel("") == "auto"
    assert normalize_ocr_accel("CPU") == "cpu"
    assert normalize_ocr_accel("DirectML") == "dml"
    assert normalize_ocr_accel("bogus") == "auto"
    assert set(VALID_OCR_ACCEL) == {"auto", "dml", "cuda", "cpu"}


def test_list_accel_candidates_cpu_always(monkeypatch) -> None:
    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel._onnx_providers",
        lambda: ["CPUExecutionProvider"],
    )
    monkeypatch.setattr("lsc.analyzer.ocr_accel._is_windows_dml_capable", lambda: False)
    assert list_accel_candidates() == ["cpu"]


def test_list_accel_candidates_includes_dml_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel._onnx_providers",
        lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr("lsc.analyzer.ocr_accel._is_windows_dml_capable", lambda: True)
    assert list_accel_candidates() == ["dml", "cpu"]


def test_resolve_forced_dml_falls_back_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("lsc.analyzer.ocr_accel.list_accel_candidates", lambda: ["cpu"])
    assert resolve_ocr_accel("dml", probe_timings=None) == "cpu"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ocr_accel.py::test_normalize_ocr_accel_defaults_and_aliases tests/test_ocr_accel.py::test_list_accel_candidates_cpu_always tests/test_ocr_accel.py::test_list_accel_candidates_includes_dml_on_windows tests/test_ocr_accel.py::test_resolve_forced_dml_falls_back_when_unavailable -v`

Expected: FAIL（模块或符号不存在）

- [ ] **Step 3: Minimal implementation**

```python
# lsc/analyzer/ocr_accel.py
"""OCR 加速后端选择：auto / dml / cuda / cpu。"""
from __future__ import annotations

import logging
import platform
import re
from typing import Any

_log = logging.getLogger(__name__)

VALID_OCR_ACCEL = frozenset({"auto", "dml", "cuda", "cpu"})
_ALIAS = {
    "automatic": "auto",
    "directml": "dml",
    "dml": "dml",
    "cuda": "cuda",
    "gpu": "cuda",
    "cpu": "cpu",
    "auto": "auto",
}


def normalize_ocr_accel(value: Any) -> str:
    if value is None:
        return "auto"
    key = str(value).strip().lower()
    if not key:
        return "auto"
    mapped = _ALIAS.get(key, key)
    if mapped not in VALID_OCR_ACCEL:
        _log.warning("非法 ocr_accel=%r，回退 auto", value)
        return "auto"
    return mapped


def _onnx_providers() -> list[str]:
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception as exc:
        _log.debug("onnxruntime providers 不可用: %s", exc)
        return []


def _is_windows_dml_capable() -> bool:
    if platform.system() != "Windows":
        return False
    # Win10 1903+ ≈ build 18362
    m = re.search(r"(\d+)$", platform.version())
    if not m:
        return True
    try:
        return int(m.group(1)) >= 18362
    except ValueError:
        return True


def list_accel_candidates() -> list[str]:
    providers = set(_onnx_providers())
    out: list[str] = []
    if "DmlExecutionProvider" in providers and _is_windows_dml_capable():
        out.append("dml")
    if "CUDAExecutionProvider" in providers:
        out.append("cuda")
    out.append("cpu")
    return out


def resolve_ocr_accel(
    mode: Any,
    *,
    probe_timings: dict[str, float] | None = None,
) -> str:
    """将用户设置解析为实际可用后端。

    probe_timings: 可选 {\"dml\": ms, \"cuda\": ms, \"cpu\": ms}，auto 时选最小正值。
    """
    normalized = normalize_ocr_accel(mode)
    candidates = list_accel_candidates()
    if normalized == "auto":
        if probe_timings:
            usable = {
                k: v
                for k, v in probe_timings.items()
                if k in candidates and isinstance(v, (int, float)) and v > 0
            }
            if usable:
                return min(usable, key=usable.get)
        # 无探针时偏好顺序：dml → cuda → cpu
        for pref in ("dml", "cuda", "cpu"):
            if pref in candidates:
                return pref
        return "cpu"
    if normalized in candidates:
        return normalized
    _log.warning("ocr_accel=%s 不可用（candidates=%s），回退 cpu", normalized, candidates)
    return "cpu"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ocr_accel.py::test_normalize_ocr_accel_defaults_and_aliases tests/test_ocr_accel.py::test_list_accel_candidates_cpu_always tests/test_ocr_accel.py::test_list_accel_candidates_includes_dml_on_windows tests/test_ocr_accel.py::test_resolve_forced_dml_falls_back_when_unavailable -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lsc/analyzer/ocr_accel.py tests/test_ocr_accel.py
git commit -m "$(cat <<'EOF'
feat(analyzer): add ocr_accel normalize and provider resolve

EOF
)"
```

（仅当用户明确要求提交时执行本步；否则跳过 commit，继续 Task 2。）

---

### Task 2: RapidOCR kwargs、探针缓存、`create_ocr` / 失效

**Files:**
- Modify: `lsc/analyzer/ocr_accel.py`
- Modify: `lsc/analyzer/ocr_detector.py`
- Test: `tests/test_ocr_accel.py`

- [ ] **Step 1: Write the failing tests**

```python
# 追加到 tests/test_ocr_accel.py
from lsc.analyzer import ocr_accel as oa


def test_rapidocr_kwargs_for_backends() -> None:
    assert oa.rapidocr_kwargs_for("cpu") == {}
    dml = oa.rapidocr_kwargs_for("dml")
    assert dml.get("det_use_dml") is True
    assert dml.get("rec_use_dml") is True
    cuda = oa.rapidocr_kwargs_for("cuda")
    assert cuda.get("det_use_cuda") is True or cuda.get("use_cuda") is True


def test_probe_cache_roundtrip(tmp_path, monkeypatch) -> None:
    cache_file = tmp_path / "ocr_accel_probe.json"
    monkeypatch.setattr(oa, "_probe_cache_path", lambda: cache_file)
    oa.save_probe_cache({"dml": 10.0, "cpu": 40.0}, selected="dml", ort_version="1.0")
    loaded = oa.load_probe_cache()
    assert loaded is not None
    assert loaded["selected"] == "dml"
    assert loaded["timings"]["cpu"] == 40.0


def test_create_ocr_uses_resolved_backend(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(oa, "resolve_ocr_accel", lambda mode, probe_timings=None: "cpu")
    monkeypatch.setattr(oa, "rapidocr_kwargs_for", lambda effective: {})
    monkeypatch.setitem(__import__("sys").modules, "rapidocr_onnxruntime", type("M", (), {"RapidOCR": FakeOCR})())
    # 通过 ocr_detector 测试更直观 — 见 Step 3 集成
```

（若 `create_ocr` 放在 `ocr_accel.py`，上面 Fake 导入可改为直接 `oa.create_ocr("auto")`。）

推荐 API（实现时固定）：

```python
def rapidocr_kwargs_for(effective: str) -> dict[str, Any]: ...
def load_probe_cache() -> dict[str, Any] | None: ...
def save_probe_cache(timings: dict[str, float], *, selected: str, ort_version: str) -> None: ...
def run_probe_if_needed(mode: str) -> str:
    """auto 时读缓存或跑微基准后 resolve；非 auto 直接 resolve。"""
def create_ocr(mode: str | None = None) -> Any: ...
```

- [ ] **Step 2: Run new tests — expect FAIL**

Run: `pytest tests/test_ocr_accel.py::test_rapidocr_kwargs_for_backends tests/test_ocr_accel.py::test_probe_cache_roundtrip -v`

- [ ] **Step 3: Implement kwargs + cache + create_ocr**

要点：

```python
# ocr_accel.py 追加
import json
import time
from pathlib import Path

def _probe_cache_path() -> Path:
    # 与 persistence 一致：项目根 data/
    root = Path(__file__).resolve().parents[2]  # lsc/analyzer -> repo root
    return root / "data" / "ocr_accel_probe.json"

def rapidocr_kwargs_for(effective: str) -> dict[str, Any]:
    eff = normalize_ocr_accel(effective)
    if eff == "dml":
        return {"det_use_dml": True, "cls_use_dml": True, "rec_use_dml": True}
    if eff == "cuda":
        return {"det_use_cuda": True, "cls_use_cuda": True, "rec_use_cuda": True}
    return {}

def load_probe_cache() -> dict[str, Any] | None:
    path = _probe_cache_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.debug("读 OCR 探针缓存失败: %s", exc)
        return None
    # TTL 7 天
    saved_at = float(data.get("saved_at", 0))
    if time.time() - saved_at > 7 * 86400:
        return None
    return data

def save_probe_cache(timings: dict[str, float], *, selected: str, ort_version: str) -> None:
    path = _probe_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timings": timings,
        "selected": selected,
        "ort_version": ort_version,
        "saved_at": time.time(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _ort_version() -> str:
    try:
        import onnxruntime as ort
        return str(getattr(ort, "__version__", "unknown"))
    except Exception:
        return "unknown"

def run_probe_if_needed(mode: str | None) -> str:
    normalized = normalize_ocr_accel(mode)
    if normalized != "auto":
        return resolve_ocr_accel(normalized, probe_timings=None)
    cache = load_probe_cache()
    if cache and cache.get("ort_version") == _ort_version():
        timings = cache.get("timings") or {}
        selected = resolve_ocr_accel("auto", probe_timings=timings)
        _log.info("OCR accel from cache: %s timings=%s", selected, timings)
        return selected
    # 无真实图时：不跑重基准，按偏好顺序（完整探针可在 create_ocr 首次成功后异步补；
    # 本轮最小：无缓存则 resolve 无 timings）
    selected = resolve_ocr_accel("auto", probe_timings=None)
    _log.info("OCR accel selected (no probe timings): %s", selected)
    return selected

def create_ocr(mode: str | None = None) -> Any:
    from rapidocr_onnxruntime import RapidOCR
    effective = run_probe_if_needed(mode)
    kwargs = rapidocr_kwargs_for(effective)
    try:
        inst = RapidOCR(**kwargs)
    except Exception as exc:
        _log.warning("RapidOCR(%s) 失败，回退 CPU: %s", effective, exc)
        effective = "cpu"
        inst = RapidOCR()
    _log.info("OCR accel active: %s kwargs=%s", effective, kwargs)
    return inst
```

可选增强（同 Task 内若时间允许）：用 1x1 或内置小 PNG 对每个 candidate 计时写入 cache；**最低要求**是缓存读写 + kwargs + 构造回退。

`ocr_detector.py`：

```python
_ocr_instance: Any = None
_ocr_mode_used: str | None = None

def invalidate_ocr() -> None:
    global _ocr_instance, _ocr_mode_used
    _ocr_instance = None
    _ocr_mode_used = None

def _current_ocr_accel_setting() -> str:
    try:
        from python-backend path — 避免循环依赖
    ...
```

为避免 `ocr_detector` ↔ `room_handler` 循环导入，用：

```python
def _current_ocr_accel_setting() -> str:
    try:
        from lsc.analyzer.ocr_accel import normalize_ocr_accel
        # 轻量读 settings.json：复用 room_handler.load_settings 会循环
        # 改为 ocr_accel.read_settings_ocr_accel() 直接读 SETTINGS_FILE
        return normalize_ocr_accel(oa.read_settings_ocr_accel())
    except Exception:
        return "auto"
```

在 `ocr_accel.py` 增加：

```python
def read_settings_ocr_accel() -> str:
    # SETTINGS_FILE = 项目根 settings.json（与 room_handler 同路径）
    ...
```

路径：`Path(__file__).resolve().parents[2] / "settings.json"`。

```python
def _get_ocr() -> Any:
    global _ocr_instance
    if _ocr_instance is None:
        from lsc.analyzer.ocr_accel import create_ocr, read_settings_ocr_accel
        _ocr_instance = create_ocr(read_settings_ocr_accel())
    return _ocr_instance
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_ocr_accel.py -v`

- [ ] **Step 5: Commit（仅用户要求时）**

```bash
git add lsc/analyzer/ocr_accel.py lsc/analyzer/ocr_detector.py tests/test_ocr_accel.py
git commit -m "feat(analyzer): create RapidOCR with ocr_accel backends"
```

---

### Task 3: FFmpeg hwaccel 参数构建 + 抽帧回退

**Files:**
- Modify: `lsc/analyzer/ocr_accel.py`
- Modify: `lsc/analyzer/ocr_detector.py`（所有 `ffmpeg ... -i` OCR 抽帧）
- Modify: `lsc/analyzer/round_detector.py`（phase OCR 抽帧约 1117–1125 行）
- Test: `tests/test_ocr_accel.py`

- [ ] **Step 1: Write failing tests**

```python
def test_ffmpeg_hwaccel_args() -> None:
    from lsc.analyzer.ocr_accel import ffmpeg_hwaccel_args
    assert ffmpeg_hwaccel_args("cpu") == []
    assert ffmpeg_hwaccel_args("dml") == ["-hwaccel", "d3d11va"]
    assert ffmpeg_hwaccel_args("cuda") == ["-hwaccel", "cuda"]
    # auto 在 Windows 上倾向 d3d11va
    import platform
    if platform.system() == "Windows":
        assert ffmpeg_hwaccel_args("auto") == ["-hwaccel", "d3d11va"]
    else:
        assert ffmpeg_hwaccel_args("auto") == []


def test_run_ffmpeg_ocr_extract_retries_without_hwaccel(monkeypatch) -> None:
    from lsc.analyzer.ocr_accel import run_ffmpeg_with_hwaccel_fallback
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        class R:
            returncode = 1 if "-hwaccel" in cmd else 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    base = ["ffmpeg", "-y", "-i", "in.mp4", "out_%05d.jpg"]
    r = run_ffmpeg_with_hwaccel_fallback(base, hwaccel_args=["-hwaccel", "d3d11va"])
    assert r.returncode == 0
    assert len(calls) == 2
    assert "-hwaccel" in calls[0]
    assert "-hwaccel" not in calls[1]
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_ocr_accel.py::test_ffmpeg_hwaccel_args tests/test_ocr_accel.py::test_run_ffmpeg_ocr_extract_retries_without_hwaccel -v`

- [ ] **Step 3: Implement helper + wire extractors**

```python
def ffmpeg_hwaccel_args(effective_or_mode: str) -> list[str]:
    mode = normalize_ocr_accel(effective_or_mode)
    if mode == "cpu":
        return []
    if mode == "cuda":
        return ["-hwaccel", "cuda"]
    if mode in ("dml", "auto"):
        if platform.system() == "Windows":
            return ["-hwaccel", "d3d11va"]
        return []
    return []


def run_ffmpeg_with_hwaccel_fallback(
    cmd_without_hwaccel: list[str],
    *,
    hwaccel_args: list[str],
    timeout: int = 360,
) -> Any:
    import subprocess
    # 将 hwaccel 插到 ffmpeg 可执行名之后
    def _insert(cmd: list[str], hw: list[str]) -> list[str]:
        if not hw:
            return list(cmd)
        return [cmd[0], *hw, *cmd[1:]]

    first = _insert(cmd_without_hwaccel, hwaccel_args)
    result = subprocess.run(
        first, capture_output=True, text=True, encoding="utf-8",
        errors="ignore", timeout=timeout,
    )
    if result.returncode == 0 or not hwaccel_args:
        return result
    _log.warning("FFmpeg hwaccel 失败 (code=%s)，回退软解", result.returncode)
    return subprocess.run(
        cmd_without_hwaccel, capture_output=True, text=True, encoding="utf-8",
        errors="ignore", timeout=timeout,
    )
```

在 `ocr_detector.py` / `round_detector.py`：把原来的 `subprocess.run(cmd, ...)` 换成：

```python
from lsc.analyzer.ocr_accel import (
    ffmpeg_hwaccel_args,
    read_settings_ocr_accel,
    run_ffmpeg_with_hwaccel_fallback,
    run_probe_if_needed,
)
effective = run_probe_if_needed(read_settings_ocr_accel())
hw = ffmpeg_hwaccel_args(effective if effective != "auto" else read_settings_ocr_accel())
# 注意：cpu 模式必须空 hw；auto/dml→d3d11va
result = run_ffmpeg_with_hwaccel_fallback(cmd, hwaccel_args=hw, timeout=...)
```

`cmd` 保持**不含** `-hwaccel` 的基命令。

- [ ] **Step 4: Run full ocr_accel tests**

Run: `pytest tests/test_ocr_accel.py -v`

Expected: PASS

- [ ] **Step 5: Commit（仅用户要求时）**

```bash
git add lsc/analyzer/ocr_accel.py lsc/analyzer/ocr_detector.py lsc/analyzer/round_detector.py tests/test_ocr_accel.py
git commit -m "feat(analyzer): OCR frame extract with hwaccel fallback"
```

---

### Task 4: 后端 settings 持久化 + 保存时 invalidate

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`load_settings` 默认值、`save_settings`）
- Test: `tests/test_ocr_accel.py` 或 `tests/test_persistence.py`（若存在 settings 测试则追加）

- [ ] **Step 1: Write failing test**

```python
def test_save_settings_normalizes_ocr_accel_and_invalidates(monkeypatch, tmp_path) -> None:
    import python-backend.handlers.room_handler as rh
    # monkeypatch SETTINGS_FILE 到 tmp_path / settings.json
    called = {"n": 0}
    monkeypatch.setattr(
        "lsc.analyzer.ocr_detector.invalidate_ocr",
        lambda: called.__setitem__("n", called["n"] + 1),
    )
    rh.save_settings({**rh.load_settings(), "ocr_accel": "bogus"})
    data = rh.load_settings()
    assert data.get("ocr_accel") == "auto"
    assert called["n"] == 1
```

（若 `save_settings` 测试挂钩困难，可测独立函数 `_normalize_settings_ocr_accel(settings: dict) -> dict`。）

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Wire room_handler**

在默认 settings dict（约 1669–1684 行）加入 `'ocr_accel': 'auto'`。

在 `save_settings`：

```python
from lsc.analyzer.ocr_accel import normalize_ocr_accel

def save_settings(settings: dict):
    if "ocr_accel" in settings:
        settings = {**settings, "ocr_accel": normalize_ocr_accel(settings.get("ocr_accel"))}
    ...
    # 写盘成功后：
    try:
        from lsc.analyzer.ocr_detector import invalidate_ocr
        invalidate_ocr()
    except Exception as exc:
        _log.debug("invalidate_ocr failed: %s", exc)
```

`get_settings` / `handle_get_settings` 若补默认字段，一并补 `ocr_accel`（与 `shared_ingest_enabled` 同类）。

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_ocr_accel.py -v`（及本 Task 新增用例）

- [ ] **Step 5: Commit（仅用户要求时）**

```bash
git add python-backend/handlers/room_handler.py tests/test_ocr_accel.py
git commit -m "feat(backend): persist ocr_accel and invalidate OCR singleton"
```

---

### Task 5: 前端类型、默认值、设置页 UI

**Files:**
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/store/appStore.ts`
- Modify: `lsc-electron/src/pages/Settings/index.tsx`
- Test: `tests/test_frontend_stability_guards.py`（追加字符串守卫）

- [ ] **Step 1: Write failing frontend guard test**

```python
def test_settings_exposes_ocr_accel_control() -> None:
    from pathlib import Path
    settings = Path("lsc-electron/src/pages/Settings/index.tsx").read_text(encoding="utf-8")
    types = Path("lsc-electron/src/types/index.ts").read_text(encoding="utf-8")
    assert "ocr_accel" in types
    assert "OCR 加速" in settings
    assert 'value="auto"' in settings or "value={'auto'}" in settings or 'value="auto"' in settings
    assert "DirectML" in settings
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_frontend_stability_guards.py::test_settings_exposes_ocr_accel_control -v`

- [ ] **Step 3: Implement UI**

`types/index.ts` 的 `RecordSettings`：

```typescript
  /** OCR 加速：auto | dml | cuda | cpu */
  ocr_accel?: 'auto' | 'dml' | 'cuda' | 'cpu'
```

`appStore.ts` `defaultSettings`：

```typescript
  ocr_accel: 'auto',
```

`Settings/index.tsx`：在「共享进样」行附近插入：

```tsx
<SettingsRow label="OCR 加速">
  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end', maxWidth: 360 }}>
    <select
      value={settings.ocr_accel || 'auto'}
      onChange={(e) => {
        const v = e.target.value as 'auto' | 'dml' | 'cuda' | 'cpu'
        handleRecordChange('ocr_accel', v)
        send('save_settings', { ...settings, ocr_accel: v, appSettings })
        message.success('OCR 加速已保存（下次识别生效）', 2)
      }}
      className="settings-select"
    >
      <option value="auto">自动（推荐）</option>
      <option value="dml">DirectML（Windows GPU）</option>
      <option value="cuda">CUDA（NVIDIA）</option>
      <option value="cpu">仅 CPU</option>
    </select>
    <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5, textAlign: 'right' }}>
      持续分析 OCR 推理加速；自动会探测并选最快后端，弱核显可能回退 CPU。
    </div>
  </div>
</SettingsRow>
```

- [ ] **Step 4: Run guard + tsc**

Run: `pytest tests/test_frontend_stability_guards.py::test_settings_exposes_ocr_accel_control -v`

Run: `cd lsc-electron && npx tsc --noEmit`

Expected: PASS

- [ ] **Step 5: Commit（仅用户要求时）**

```bash
git add lsc-electron/src/types/index.ts lsc-electron/src/store/appStore.ts lsc-electron/src/pages/Settings/index.tsx tests/test_frontend_stability_guards.py
git commit -m "feat(ui): expose OCR accel setting"
```

---

### Task 6: 文档与可选依赖说明 + 回归

**Files:**
- Modify: `requirements-ai.txt`（注释）
- Modify: `CLAUDE.md` 设置表一行（`ocr_accel`）
- Modify: `docs/superpowers/specs/2026-07-14-ocr-gpu-hwaccel-design.md` 状态 → 实施中/已计划

- [x] **Step 1: Annotate requirements-ai.txt**

在 `rapidocr-onnxruntime` 旁加注释：

```text
# OCR：默认 CPU。Windows GPU 可选替换安装：
#   pip uninstall onnxruntime -y && pip install onnxruntime-directml
# NVIDIA 可选：onnxruntime-gpu（与 directml 互斥，勿同时装）
rapidocr-onnxruntime>=1.3,<2
```

- [x] **Step 2: CLAUDE.md 设置表追加一行**

| `ocr_accel` | `"auto"` | `"auto"` / `"dml"` / `"cuda"` / `"cpu"`。持续分析 OCR 加速；auto 探针选最快并缓存。 |

- [x] **Step 3: Full regression**

Run: `pytest tests/test_ocr_accel.py tests/test_frontend_stability_guards.py tests/test_continuous_analysis_guards.py tests/test_round_detector.py -q`

Expected: PASS（或仅与本改动无关的既有失败需注明）

- [ ] **Step 4: Manual checklist（执行者勾选）**

```text
- [ ] 设置页可见「OCR 加速」四档，保存后 settings.json 有 ocr_accel
- [ ] 选「仅 CPU」后日志含 OCR accel active: cpu
- [ ] 有 DirectML 时选自动/DirectML，日志含 dml（或探针回退说明）
- [ ] 持续分析仍能检出回合 / 不崩溃
```

- [ ] **Step 5: Commit（仅用户要求时）**

```bash
git add requirements-ai.txt CLAUDE.md docs/superpowers/specs/2026-07-14-ocr-gpu-hwaccel-design.md
git commit -m "docs: document ocr_accel optional GPU backends"
```

---

## Spec coverage checklist

| Spec 项 | Task |
|---------|------|
| `ocr_accel` 四档 + 默认 auto | 1, 4, 5 |
| DirectML / CUDA / CPU + 强制不可用回退 | 1, 2 |
| 探针缓存 TTL / ORT 版本 | 2 |
| RapidOCR 单例 + invalidate | 2, 4 |
| FFmpeg hwaccel + 软解回退 | 3 |
| 设置页露出开关 | 5 |
| 不改确认门 / 相位机 | （无任务改动该路径） |
| 可选依赖文档 | 6 |
| 测试计划 | 1–5 |

## Placeholder / consistency self-review

- API 名统一：`normalize_ocr_accel`、`resolve_ocr_accel`、`rapidocr_kwargs_for`、`ffmpeg_hwaccel_args`、`run_ffmpeg_with_hwaccel_fallback`、`invalidate_ocr`、`create_ocr`。
- 设置键统一：`ocr_accel`（非 `ocr_acceleration`）。
- Commit 步注明「仅用户要求时」以符合仓库提交规则。
- 完整微基准（真图计时）在 Task 2 标为可选增强；最低路径用偏好顺序 + 缓存结构，避免无 GPU 环境测试卡死。
