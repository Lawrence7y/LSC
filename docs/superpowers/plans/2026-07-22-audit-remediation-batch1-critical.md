# Audit Remediation Batch1 Critical Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复审查 Critical×5：`analysis_time` NameError、WS Origin+token、Electron 单实例与后端生命周期、导出 watchdog 超时参数化、OCR probe 缓存写失败不致命。

**Architecture:** 纯函数抽取（origin/token/timeout）便于单测；Electron 生成 `LSC_WS_TOKEN` 注入后端 env，渲染进程用 `?token=` 连接（浏览器 WS 不能设自定义 Header）；watchdog 闭包捕获探测到的宽高与是否硬件编码。

**Tech Stack:** Python 3.10+、websockets、Electron/TypeScript、pytest

**Spec:** `docs/superpowers/specs/2026-07-22-audit-remediation-batch1-critical-design.md`  
**Overview:** `docs/superpowers/specs/2026-07-22-audit-remediation-overview-design.md`

**执行约束：**
- 工作目录：`D:\Project\直播切片多人`
- 每 Task 结束后跑该 Task 指定的 pytest；不要扩大到无关重构
- **不要 git commit**（除非用户明确要求）

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `python-backend/ws_auth.py` | **新建**：`is_origin_allowed`、`is_ws_token_required`、`validate_ws_token`、`extract_token_from_path` |
| `python-backend/server.py` | 握手调用 origin+token 校验 |
| `python-backend/handlers/room_handler.py` | C1：三处 `save_analysis_results` 传入真实耗时 |
| `lsc/exporter/clip.py` | C4：`compute_export_watchdog_timeout` + watchdog 使用局部变量 |
| `lsc/analyzer/ocr_accel.py` | C5：`save_probe_cache` 吞 `OSError` |
| `lsc-electron/electron/main.ts` | C2/C3：生成 token、单实例锁、before-quit 等待 |
| `lsc-electron/electron/preload.ts` | 暴露 `getBackendWsToken` |
| `lsc-electron/src/types/index.ts` | 类型补全 |
| `lsc-electron/src/services/websocketUrl.ts` | URL 附加 `?token=` |
| `tests/test_ws_auth.py` | **新建**：origin/token 单测 |
| `tests/test_server.py` | 改用真实 `is_origin_allowed`；补绕过负例 |
| `tests/test_export_watchdog_timeout.py` | **新建** |
| `tests/test_ocr_accel_cache.py` | **新建**或扩展既有 |
| `tests/test_analysis_time_save_guards.py` | **新建**：源码/行为守卫 |

---

### Task 1: C1 — `analysis_time` 计时与三处落盘

**Files:**
- Modify: `python-backend/handlers/room_handler.py`（`_do_analysis` ~5244、`_do_analysis_and_export` ~5338、持续分析落盘 ~7149）
- Create: `tests/test_analysis_time_save_guards.py`

- [ ] **Step 1: 写失败守卫测试**

```python
# tests/test_analysis_time_save_guards.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLER = (ROOT / "python-backend" / "handlers" / "room_handler.py").read_text(encoding="utf-8")


def test_analysis_export_defines_analysis_time_before_save():
    """_do_analysis_and_export must assign analysis_time before save_analysis_results."""
    assert "analysis_time_sec=analysis_time" in HANDLER
    # 必须在同一函数作用域内有 monotonic 差值赋值（禁止未定义名）
    assert "analysis_time = time.monotonic() - t0" in HANDLER or \
           "analysis_time = time.monotonic()-t0" in HANDLER
    assert "t0 = time.monotonic()" in HANDLER


def test_start_analysis_passes_analysis_time_sec():
    """handle_start_analysis 落盘不得省略 analysis_time_sec。"""
    # 粗粒度：save_analysis_results 调用均应带 analysis_time_sec= 关键字
    import re
    calls = list(re.finditer(r"save_analysis_results\((.*?)\)", HANDLER, re.S))
    assert len(calls) >= 3
    for m in calls:
        args = m.group(1)
        assert "analysis_time_sec" in args, f"missing analysis_time_sec in: {args[:120]}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_analysis_time_save_guards.py -v`  
Expected: FAIL（当前仅 export 路径有未定义名；另两处无关键字）

- [ ] **Step 3: 实现**

在 `_do_analysis` 开头（拿到 `video_path` 后、分析前）：

```python
t0 = time.monotonic()
```

在 `save_analysis_results(video_path, room_id, mode, highlights)` 改为：

```python
analysis_time = time.monotonic() - t0
save_analysis_results(
    video_path, room_id, mode, highlights,
    analysis_time_sec=analysis_time,
)
```

在 `_do_analysis_and_export` 同样在分析前设 `t0`，落盘前：

```python
analysis_time = time.monotonic() - t0
save_analysis_results(
    video_path, main_room_id, mode, highlights,
    analysis_time_sec=analysis_time,
    weights=weights if weights else None,
)
```

持续分析最终落盘（~7149）：在该协程/循环进入分析生命周期时已有 `_scan_start_mono` 或补：

```python
# 在 continuous task 启动处（若尚无）：
state["_session_t0"] = time.monotonic()
```

落盘：

```python
_t0 = float(stop_state.get("_session_t0") or stop_state.get("_scan_start_mono") or time.monotonic())
save_analysis_results(
    video_path, room_id, mode, all_highlights,
    analysis_time_sec=max(0.0, time.monotonic() - _t0),
)
```

（若 `stop_state` 名在该作用域不同，用 `_continuous_tasks.get(room_id, {})`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_analysis_time_save_guards.py -v`  
Expected: PASS

---

### Task 2: C2 — 抽取 `ws_auth` 并修 Origin/token

**Files:**
- Create: `python-backend/ws_auth.py`
- Create: `tests/test_ws_auth.py`
- Modify: `python-backend/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: 写 `tests/test_ws_auth.py`（先红）**

```python
import os
import pytest
from ws_auth import (
    is_origin_allowed,
    extract_token_from_path,
    validate_ws_token,
    is_ws_token_required,
)


def test_origin_localhost_ok():
    assert is_origin_allowed("http://localhost:5173") is True
    assert is_origin_allowed("http://127.0.0.1:9876") is True
    assert is_origin_allowed("null") is True


def test_origin_prefix_bypass_rejected():
    assert is_origin_allowed("http://localhost.attacker.com") is False
    assert is_origin_allowed("http://127.0.0.1.evil.com") is False
    assert is_origin_allowed("https://example.com") is False
    assert is_origin_allowed("") is False


def test_extract_token_from_path():
    assert extract_token_from_path("/?token=abc") == "abc"
    assert extract_token_from_path("/?token=abc&x=1") == "abc"
    assert extract_token_from_path("/") is None


def test_validate_token(monkeypatch):
    monkeypatch.setenv("LSC_WS_TOKEN", "secret-token-value")
    monkeypatch.setenv("LSC_WS_TOKEN_REQUIRED", "1")
    assert validate_ws_token("secret-token-value") is True
    assert validate_ws_token("wrong") is False
    assert validate_ws_token("") is False


def test_token_not_required(monkeypatch):
    monkeypatch.setenv("LSC_WS_TOKEN_REQUIRED", "0")
    assert is_ws_token_required() is False
    assert validate_ws_token("") is True
```

- [ ] **Step 2: 实现 `python-backend/ws_auth.py`**

```python
"""WebSocket Origin + token helpers for LSC backend."""
from __future__ import annotations

import hmac
import os
from urllib.parse import parse_qs, urlparse


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    if origin == "null":
        return True
    try:
        parsed = urlparse(origin)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return host in _LOCAL_HOSTS


def is_ws_token_required() -> bool:
    return os.environ.get("LSC_WS_TOKEN_REQUIRED", "1").strip() not in ("0", "false", "False", "no")


def expected_ws_token() -> str:
    return os.environ.get("LSC_WS_TOKEN", "") or ""


def extract_token_from_path(path: str | None) -> str | None:
    if not path:
        return None
    # path may be "/?token=..." or full URI
    raw = path if "://" not in path else urlparse(path).path + (
        "?" + urlparse(path).query if urlparse(path).query else ""
    )
    if "?" not in raw:
        # also allow path-only query via urlparse
        q = urlparse(path if "://" in path else f"ws://x{path}").query
    else:
        q = raw.split("?", 1)[1]
    vals = parse_qs(q).get("token") or []
    return vals[0] if vals else None


def validate_ws_token(provided: str | None) -> bool:
    if not is_ws_token_required():
        return True
    expected = expected_ws_token()
    if not expected:
        # required but server has no token configured → reject all
        return False
    return hmac.compare_digest(provided or "", expected)
```

- [ ] **Step 3: 改 `server.py` `handle_client`**

在 Origin 校验处替换为：

```python
from ws_auth import is_origin_allowed, extract_token_from_path, validate_ws_token

# ... 取得 origin 后：
if not is_origin_allowed(origin):
    _log.warning("Rejected WebSocket connection from origin: %s", origin)
    ...
    return

# path/query token
path = ""
if hasattr(websocket, "path"):
    path = websocket.path or ""
elif hasattr(websocket, "request") and getattr(websocket.request, "path", None):
    path = websocket.request.path or ""
token = extract_token_from_path(path)
if not validate_ws_token(token):
    _log.warning("Rejected WebSocket connection: invalid or missing token")
    ...
    return
```

（按已安装 `websockets` 版本探测 `path` 属性；若 path 不含 query，读 `websocket.request.path` / `websocket.request.headers` 文档对齐。）

- [ ] **Step 4: 更新 `tests/test_server.py` 的 `_check_origin` 委托 `is_origin_allowed`，并新增：**

```python
def test_localhost_attacker_rejected(self):
    assert self._check_origin("http://localhost.attacker.com") is False
```

- [ ] **Step 5: 跑测**

Run: `pytest tests/test_ws_auth.py tests/test_server.py -v`  
Expected: PASS（`python-backend` 需在 `PYTHONPATH`：`cd python-backend` 或 conftest 已包含）

若 import 失败，在测试中：

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python-backend"))
```

---

### Task 3: C2 — Electron 注入 token + 前端 URL

**Files:**
- Modify: `lsc-electron/electron/main.ts`
- Modify: `lsc-electron/electron/preload.ts`
- Modify: `lsc-electron/src/types/index.ts`
- Modify: `lsc-electron/src/services/websocketUrl.ts`

- [ ] **Step 1: `main.ts` 生成并注入 token**

在模块级：

```typescript
import { randomBytes } from 'crypto'

const backendWsToken: string = randomBytes(32).toString('base64url')
```

在 `safeEnv` 中增加：

```typescript
LSC_WS_TOKEN: backendWsToken,
LSC_WS_TOKEN_REQUIRED: '1',
```

IPC：

```typescript
ipcMain.handle('get-backend-ws-token', () => backendWsToken)
```

- [ ] **Step 2: `preload.ts`**

```typescript
getBackendWsToken: () => ipcRenderer.invoke('get-backend-ws-token'),
```

- [ ] **Step 3: `types/index.ts` 补 `getBackendWsToken?: () => Promise<string | null>`**

- [ ] **Step 4: `websocketUrl.ts`**

在解析到最终 `wsUrl` 后：

```typescript
export async function resolveWebSocketUrl(): Promise<string> {
  // ...existing resolve...
  let url = /* existing result, e.g. backend or DEFAULT_WS_URL */

  const token = await window.electronAPI?.getBackendWsToken?.()
  if (token) {
    const u = new URL(url)
    u.searchParams.set('token', token)
    url = u.toString()
  }
  return url
}
```

（保持与现有函数名一致；若函数不叫 `resolveWebSocketUrl`，改现有导出。）

- [ ] **Step 5: 类型检查**

Run: `cd lsc-electron && npx tsc --noEmit`  
Expected: 无因本改动引入的错误

---

### Task 4: C3 — 单实例锁 + before-quit 等待后端退出

**Files:**
- Modify: `lsc-electron/electron/main.ts`

- [ ] **Step 1: 单实例锁（在 `app.whenReady` 之前）**

```typescript
const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })
}
```

注意：现有文件结构若已有顶层逻辑，把锁放在创建窗口/`spawnBackend` 之前，避免双开各起一个后端。

- [ ] **Step 2: before-quit 等待**

将 fire-and-forget 的 `killBackend` 改为可等待（Windows 上 `taskkill` 后轮询端口或 `proc` 退出；POSIX 保留 SIGTERM 轮询）。示例：

```typescript
let isQuitting = false

app.on('before-quit', (event) => {
  if (isQuitting) return
  if (!backendProcess) return
  event.preventDefault()
  isQuitting = true
  const done = () => app.quit()
  killBackendAndWait(5000).finally(done)
})
```

实现 `killBackendAndWait(ms: number): Promise<void>`：调用现有 taskkill/SIGTERM，在超时内等 `exit` 事件。

- [ ] **Step 3: Job Object（优选，Windows）**

若引入成本低：spawn 后把 `backendProcess.pid` 加入 Job Object（可用 `windows-kill` 或原生 FFI）。**最低交付**可跳过 Job Object，但须在 PR 说明「崩溃残留仍为已知限制」。本计划要求至少完成单实例 + before-quit 等待。

- [ ] **Step 4: 手工清单（写入 PR 描述）**
  1. 双开应用只留一窗  
  2. 正常退出后 `netstat` 无 9876 占用  

---

### Task 5: C4 — 导出 watchdog 超时参数化

**Files:**
- Modify: `lsc/exporter/clip.py`
- Create: `tests/test_export_watchdog_timeout.py`

- [ ] **Step 1: 写测试**

```python
from lsc.exporter.clip import compute_export_watchdog_timeout


def test_baseline():
    assert compute_export_watchdog_timeout(None, None, True) == 300


def test_4k_soft():
    # 4K *2.5, soft *2 → 300*2.5*2 = 1500
    assert compute_export_watchdog_timeout(3840, 2160, False) == 1500


def test_1440p_hw():
    assert compute_export_watchdog_timeout(2560, 1440, True) == 450  # 300*1.5
```

- [ ] **Step 2: 实现函数（模块级，供 watchdog 与测试）**

```python
def compute_export_watchdog_timeout(
    width: int | None,
    height: int | None,
    hardware_encoder: bool,
    base: int = 300,
) -> int:
    timeout = base
    if width and height:
        pixels = int(width) * int(height)
        if pixels > 3840 * 2160:
            timeout = int(timeout * 2.5)
        elif pixels > 1920 * 1080:
            timeout = int(timeout * 1.5)
    if not hardware_encoder:
        timeout = int(timeout * 2.0)
    return timeout
```

- [ ] **Step 3: 在 `export_clip` Popen 分支，启动 watchdog 前**

利用已有 `_probe_source_video`：

```python
src_res, _ = self._probe_source_video(video_path)
src_w, src_h = (src_res if src_res else (None, None))
hw_enc = bool(getattr(effective_profile, "is_hardware", True))
watchdog_timeout = compute_export_watchdog_timeout(src_w, src_h, hw_enc)

def _watchdog() -> None:
    try:
        proc.wait(timeout=watchdog_timeout)
    except subprocess.TimeoutExpired:
        ...
```

删除对 `self.width` / `self.height` / `self.hardware_encoder` 的依赖。

- [ ] **Step 4: 跑测**

Run: `pytest tests/test_export_watchdog_timeout.py -v`  
Expected: PASS

---

### Task 6: C5 — OCR probe 缓存写失败不致命

**Files:**
- Modify: `lsc/analyzer/ocr_accel.py`
- Create: `tests/test_ocr_probe_cache_readonly.py`

- [ ] **Step 1: 测试**

```python
from unittest.mock import patch
from pathlib import Path
import lsc.analyzer.ocr_accel as oa


def test_save_probe_cache_oserror_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(oa, "_probe_cache_path", lambda: tmp_path / "probe.json")

    def boom(*a, **k):
        raise OSError("read-only")

    with patch.object(Path, "write_text", boom):
        oa.save_probe_cache({"cpu": 1.0}, selected="cpu", ort_version="1")
    # 不抛即通过
```

- [ ] **Step 2: 改 `save_probe_cache`**

```python
def save_probe_cache(...):
    try:
        path = _probe_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        ...
        tmp.write_text(...)
        tmp.replace(path)
    except OSError as exc:
        _log.warning("OCR probe cache 写入失败（忽略）: %s", exc)
```

- [ ] **Step 3: 确认 `create_ocr` / `run_probe_if_needed` 在 cache 失败后仍继续**（已调用 `save_probe_cache` 不抛则自然满足）。

- [ ] **Step 4: 跑测**

Run: `pytest tests/test_ocr_probe_cache_readonly.py -v`  
Expected: PASS

---

### Task 7: Batch1 回归

- [ ] **Step 1:**  
Run: `pytest tests/test_analysis_time_save_guards.py tests/test_ws_auth.py tests/test_server.py tests/test_export_watchdog_timeout.py tests/test_ocr_probe_cache_readonly.py -v`  
Expected: 全 PASS

- [ ] **Step 2:** 手工：Electron 启动 → WS 已连 → 非法浏览器页无 token 无法驱动 API

---

## Spec 覆盖自检

| Spec 项 | Task |
|---------|------|
| C1 analysis_time | Task 1 |
| C2 Origin+token | Task 2–3 |
| C3 单实例+生命周期 | Task 4 |
| C4 watchdog | Task 5 |
| C5 OCR cache | Task 6 |
| 回归 | Task 7 |

**设计修正（已写入 spec）：** token 使用 URL query `?token=`，不用 `X-LSC-Token` Header。
