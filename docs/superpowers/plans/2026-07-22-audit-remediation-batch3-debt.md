# Audit Remediation Batch3 Debt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复专项债：mypy/CI/依赖可运行、settings `output_dir` 白名单、生产 sourcemap 关闭、FFmpeg 命令脱敏、日志级别可诊断、低风险性能收敛。

**Architecture:** 先修配置使工具链变绿，再加安全校验与日志脱敏，最后改 sleep/deepcopy/异步写盘等低风险性能点。

**Tech Stack:** mypy、ruff、GitHub Actions、Electron vite、pytest

**Spec:** `docs/superpowers/specs/2026-07-22-audit-remediation-batch3-debt-design.md`  
**可并行:** Batch2 尾部；不阻塞 Batch1

**执行约束：** 工作目录 `D:\Project\直播切片多人`；**不要 git commit**（除非用户要求）；CI 门槛宁窄勿假绿。

---

## 文件结构

| 文件 | Task |
|------|------|
| `pyproject.toml` | D1a, D1b |
| `.github/workflows/ci.yml` | D1c |
| `tests/test_frontend_stability_guards.py` | D1d（若 Batch2 已绿则回归） |
| `python-backend/handlers/room_handler.py` `save_settings` / 路径工具 | D2a |
| `lsc-electron/vite.config.ts` | D2b |
| `lsc/core/services/mse_streamer.py` | D2c |
| 多处 `except` debug 吞异常（定点） | D3 |
| `lsc/core/services/shared_ingest.py` | D4 sleep |
| `lsc/analyzer/round_detector.py` hybrid deepcopy | D4 |
| `lsc-electron/electron/main.ts` console 写盘 | D4 |

---

### Task 1: D1a — 修复 mypy 配置

**Files:** `pyproject.toml`

- [ ] **Step 1:** 将

```toml
warn_return_value = true
```

改为：

```toml
warn_return_any = true
```

- [ ] **Step 2:** 增加可运行范围，例如：

```toml
[tool.mypy]
files = ["lsc/core", "lsc/config.py"]
exclude = ["tests/", "docs/", ".runtime/", "lsc-electron/", "scripts/"]
```

（`python-backend` 连字符目录：**不要**当作包名扫；若需检查，用显式文件列表另开 follow-up。）

- [ ] **Step 3:**

Run: `mypy`  
Expected: 退出码 0，或仅剩已记录的 ignore；不得再因非法选项启动失败。

---

### Task 2: D1b — `pyproject.toml` 运行时依赖

**Files:** `pyproject.toml`；对照 `requirements.txt`

- [ ] **Step 1:** 在 `[project] dependencies` 加入后端启动最小集，至少：

```toml
dependencies = [
  "PySide6>=6.6",
  "numpy>=1.26",
  "websockets>=12.0",
  "psutil>=5.9",
]
```

其余与 `requirements.txt` 对齐的必需项一并加入；OCR/torch 等可放 `[project.optional-dependencies]`。

- [ ] **Step 2:** 文档一句：开发仍推荐 `pip install -r requirements.txt`。

---

### Task 3: D1c — CI 真实红线

**Files:** `.github/workflows/ci.yml`

- [ ] **Step 1: lint job**

```yaml
- name: Run ruff (lsc + python-backend)
  run: ruff check lsc/ python-backend/

- name: Run mypy (configured scope)
  run: mypy

- name: Ruff scripts informational
  run: ruff check scripts/valorant_vision/ --exit-zero
  continue-on-error: true
```

合入前须清零 `lsc/`+`python-backend/` 的 ruff 问题（或 `# noqa` 有理由的定点）。

- [ ] **Step 2: 新增 electron-typecheck job（PR）**

```yaml
electron-typecheck:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
      with:
        node-version: "20"
        cache: npm
        cache-dependency-path: lsc-electron/package-lock.json
    - run: npm ci
      working-directory: lsc-electron
    - run: npx tsc --noEmit
      working-directory: lsc-electron
```

- [ ] **Step 3: coverage** — 保持上传；若加 `--cov-fail-under`，首版 ≤40 或仅 `lsc/core`。禁止继续用全量 ruff `exit-zero` 伪装主路径绿灯。

---

### Task 4: D1d — frontend guards 回归

- [ ] **Step 1:** `pytest tests/test_frontend_stability_guards.py -v`  
Expected: 0 failed（依赖 Batch2 H3c；若未做 Batch2，本 Task 只修与 Debt 无关的过时断言）。

---

### Task 5: D2a — `output_dir` 白名单

**Files:** `python-backend/handlers/room_handler.py`（`save_settings` / `handle_save_settings`）；`tests/test_output_dir_whitelist.py`

- [ ] **Step 1: 辅助函数**

```python
def _is_allowed_output_dir(path: str) -> bool:
    real = os.path.realpath(os.path.expanduser(path))
    home = os.path.realpath(os.path.expanduser("~"))
    allowed_roots = [
        home,
        os.path.join(home, "LSC"),
    ]
    return any(real == root or real.startswith(root + os.sep) for root in allowed_roots)
```

- [ ] **Step 2:** `handle_save_settings`：非法路径 → `{success: False, error: "导出目录不在允许范围内"}`，不写入。
- [ ] **Step 3: 负例测试** — `C:\Windows\System32`（或 `/etc`）被拒；`~/LSC/recordings` 通过。

---

### Task 6: D2b — 生产构建关闭 sourcemap

**Files:** `lsc-electron/vite.config.ts`

- [ ] **Step 1:** 对 `main` / `preload` 的 build 配置：

```typescript
sourcemap: false,
minify: true, // 或 'esbuild'
```

开发模式配置保持可读。若 `vite-plugin-electron` 分 mode，仅 `mode === 'production'` 关闭 sourcemap。

- [ ] **Step 2:** `npx tsc --noEmit` 仍通过。

---

### Task 7: D2c — FFmpeg 命令日志脱敏

**Files:** `lsc/core/services/mse_streamer.py:281` 及同类 INFO

- [ ] **Step 1: 脱敏函数**

```python
def _redact_ffmpeg_cmd(cmd: list[str]) -> list[str]:
    out: list[str] = []
    hide_next = False
    for c in cmd:
        if hide_next:
            out.append("<redacted>")
            hide_next = False
            continue
        if c in ("-headers", "-headers:"):
            out.append(str(c))
            hide_next = True
            continue
        s = str(c)
        if "cookie" in s.lower() or "authorization" in s.lower():
            out.append("<redacted>")
        else:
            out.append(s)
    return out
```

- [ ] **Step 2:** INFO 使用 `_redact_ffmpeg_cmd(cmd)`；完整命令仅 DEBUG。
- [ ] **Step 3:** 单测：含 Cookie 的 cmd 被 redacted。

---

### Task 8: D3 — 业务路径日志升级

**Files:** 定点替换（优先 `clip.py`、`mse_streamer.py`、`room_handler` 非清理路径）

- [ ] **Step 1:** 将业务 `except Exception` + `_log.debug("操作异常（已忽略）")` 改为 `_log.warning("...: %s", exc)`（清理/`stop()` 路径保留 debug）。
- [ ] **Step 2:** 不做全仓无差别替换；本 Task 至少改导出与 MSE 两条主路径各 ≥3 处。

---

### Task 9: D4 — 性能低风险三项

**9a. shared_ingest 监控 sleep**

- [ ] 将 `time.sleep(0.05)`（约 984/998/1012 行）改为 `time.sleep(0.25)`。

**9b. hybrid deepcopy**

- [ ] `round_detector.py` ~2503：避免无条件 `copy.deepcopy(existing_fsm)`；若 FSM 提供 `clone()`/`copy_shallow` 则用；否则只在需要隔离突变时深拷贝，热路径复用或拷必要字段。加注释说明不变式。配既有 hybrid 测试不回归。

**9c. Electron console 写盘**

- [ ] 将主进程对 renderer console 的 `appendFileSync` 改为：内存队列 + `fs.appendFile` 异步，或 ≥50ms 合并刷盘；错误吞掉并限频。

---

### Task 10: Batch3 回归

```text
mypy
ruff check lsc/ python-backend/
pytest tests/test_output_dir_whitelist.py tests/test_mse_cmd_redact.py -v
cd lsc-electron && npx tsc --noEmit
```

Expected: 全通过。

---

## Spec 覆盖

| 项 | Task |
|----|------|
| D1a–d | 1–4 |
| D2a–c | 5–7 |
| D3 | 8 |
| D4 三项 | 9 |
| 非目标 | 未列入（不拆文件、不清理 release/） |
| 回归 | 10 |

## 明确不做（本计划）

- 音频 `readframes` 流式大重构  
- 广播改为纯推模型  
- 删除 git 内 `release/win-unpacked`  
- Valorant 模型重训  
