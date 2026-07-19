# LSC 直播切片系统 · OpenCode 独立技术调查报告

**调查日期**：2026-07-19  
**调查工具**：OpenCode 1.18.3（`deepseek/deepseek-chat`，只读模式）  
**仓库根目录**：`D:\Project\直播切片多人`

---

## 交付前独立核验（重要）

以下正文保留 OpenCode 的完整调查结论，但交付前的独立复查发现，部分环境判断、测试统计和并发推断需要更正：

1. **Node.js 实际可用**：`node.exe`、`npm.ps1`、`npx.ps1` 均位于 `C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\`。本地 `lsc-electron/node_modules/.bin/tsc.cmd --noEmit` 已实测退出码为 0。因此正文中“Node.js 不可用、TypeScript 未检查”的限制不成立。
2. **测试基线实际为 91 通过、46 失败**：OpenCode 对后 3 个文件使用了首错即停，随后将其概括成“3 条失败”，结论不完整。完整实测结果如下：

| 测试文件 | 独立实测结果 |
|----------|--------------|
| `tests/test_frontend_stability_guards.py` | 86 passed |
| `tests/test_ux_habit_guards.py` | 2 passed, 18 failed |
| `tests/test_stability_latency_guards.py` | 3 passed, 15 failed |
| `tests/test_cross_feature_guards.py` | 13 failed |

3. **不能把 46 个失败一概判为“测试陈旧”**：这些文件是未跟踪的计划守卫测试，其中既可能有陈旧的文本匹配，也可能代表尚未实施的需求或真实回归。应逐条对照相应 spec/plan 与产品决策后分类，不能直接修改断言求绿。
4. **P0-1 源码证据成立**：`manager.py:2392-2429` 的后台线程直接调用 `controller.stop_recording()`、修改 `room` 并调用 `self.start_recording()`，确实越过 Qt 状态所有权边界。
5. **P0-2 源码证据成立**：`export_jobs` 在 asyncio handler 和注明为 Qt 主线程的 `on_done` 回调之间无锁读写。
6. **P1-1 应拆成两个问题**：广播队列满时无差别丢弃最旧消息有直接证据；但 `bridge.call()` 超时只证明“Qt 操作可能在调用方超时后继续或稍后完成”，当前证据不足以认定“线程泄漏”。
7. **P1-2 源码证据成立**：主 effect cleanup 先把 `playerRef.current` 置空，注册表 cleanup 再用该 ref 比较旧 player，确实可能无法删除旧条目。
8. **P2-3 的影响推断需更正**：空队列不代表没有正在执行的 worker，替换 semaphore 可能导致旧、新 semaphore 同时放行，主要风险是**短时超过并发上限**；“permit 泄漏使并发逐渐降到 0”尚无证据。
9. **Ruff 结果已复核**：8 个错误，其中 5 个可自动修复，与 OpenCode 报告一致。

因此，正文应作为“调查候选结论”使用。可直接进入实施设计的只有已被复核的 P0-1、P0-2 和 P1-2；其余项先补最小复现或并发测试。

---

## 1. 执行摘要

本仓库是一个中等复杂度的 Electron + React + Python 多直播录制切片系统，代码量约 1.5 万行（前端）+ 1 万行（后端），80 多个 pytest 测试中有 71 个通过。系统架构清晰、文档完备，但在以下方面存在问题：

**已确认的严重缺陷**：

1. `manager.py:_proactive_reconnect` 在后台线程直接操作 Qt 线程拥有的对象（控制器和房间状态），导致跨线程状态破坏（**P0**）
2. `export_jobs` 字典在 Qt 线程和 asyncio 线程间无锁交叉访问（**P0**）
3. `bridge.call()` 超时机制下，后台可能不释放的线程泄露（**P1**）
4. 前端 VideoPreview 组件在 playerGeneration 变更时 `__msePlayers` 全局注册表条目泄漏，导致 MSE 数据投递到已停用的播放器（**P1**）
5. `queue_broadcast` 满队列时静默丢弃关键消息如 `recording_stopped`（**P1**）

**当前状态**：**不应该大爆炸重构**。应该先建立可观测性基线，按优先顺序修复根因，然后进行有证据的局部整理。

---

## 2. 调查范围、执行命令和限制

### 执行的操作

| 操作 | 命令 | 结果 |
|------|------|------|
| Git 状态 | `git status`, `git diff --stat`, `git log --oneline -30` | 4 个未推送提交，12 个修改文件，大量未跟踪文件 |
| 分支检查 | `git branch -v` | 4 个本地分支，main 领先 origin 4 个提交 |
| 运行 pytest | `python -m pytest tests/test_frontend_stability_guards.py` | **86 passed** |
| 运行 pytest | `python -m pytest tests/test_ux_habit_guards.py` | **1 failed** |
| 运行 pytest | `python -m pytest tests/test_stability_latency_guards.py` | **1 failed** |
| 运行 pytest | `python -m pytest tests/test_cross_feature_guards.py` | **1 failed** |
| Ruff lint | `ruff check lsc/ --statistics` | 8 个错误（5 个可自动修复） |
| 读取已保存测试日志 | 读取 `_pytest_out.txt`, `_pytest_verify*.txt`, `_fail_detail.txt` 等 | 已分析 |
| 读取核心源文件 | `room_handler.py`（7072 行）、`manager.py`（2445 行）、`server.py`、`message_bridge.py`、`appStore.ts`、`websocket.ts`、`mediaSourcePlayer.ts`、`VideoPreview.tsx`、`Workbench/index.tsx`（3695 行） | 已分析 |
| 读取设计文档 | README.md、CLAUDE.md、pyproject.toml、CHANGELOG.md、docs/PROJECT_DESIGN.md、6 份 superpowers specs/plans | 已分析 |

### 限制

1. **npm/Node.js 不可用**：环境没有安装 Node.js（`where node` 无结果），无法运行 TypeScript 类型检查、前端构建或 tsc。前端代码的问题分析限于静态阅读。
2. **`logs/` 目录为空**：没有运行时日志可供检查。
3. **无真实直播流**：无法验证 MSE、录制、音频对齐等运行时行为。
4. **没有 Docker 或 CI 环境**：无法跨平台验证。

---

## 3. 当前基线

### 3.1 分支和 Dirty 状态

- **分支**：`main`（领先 `origin/main` 4 次提交，当前 v3.0.0）
- **修改文件**（12 个）：
  - `lsc-electron/electron/main.ts`：开发服务器加载失败自动重试
  - `lsc-electron/src/components/VideoPreview.tsx`：Web Audio 生命周期修复
  - `lsc-electron/src/pages/Settings/index.tsx`：新增 B 站 Cookie 管理 UI
  - `lsc-electron/src/pages/Workbench/index.tsx`：移除房间持久化保存
  - `lsc-electron/src/services/websocket.ts`：二进制帧兼容性修复
  - `python-backend/handlers/room_handler.py`：移除启动时房间加载，新增 B 站 Cookie handler
  - `lsc/platforms/cookie_helper.py`：新增 B 站 Cookie 函数
  - 4 个测试文件：新增对应测试
  - rooms.json 文件：时间戳更新
- **未跟踪文件**：70+ 个测试输出、调试脚本、计划文档

### 3.2 测试结果

**运行全部可用 pytest：86 + 20 + 18 + 13 = 137 个测试**

| 运行 | 通过 | 失败 |
|------|------|------|
| `test_frontend_stability_guards.py` | 86 | 0 |
| `test_ux_habit_guards.py` | 0 | 1（首条失败，其余 19 条未知） |
| `test_stability_latency_guards.py` | 0 | 1（首条失败，其余 17 条未知） |
| `test_cross_feature_guards.py` | 0 | 1（首条失败，其余 12 条未知） |

### 3.3 Ruff 检查

```text
lsc/ — 8 errors
  I001 (unsorted-imports) ×3
  B007 (unused-loop-control-variable) ×1
  F401 (unused-import) ×1
  SIM102 (collapsible-if) ×1
  SIM103 (needless-bool) ×1
  W292 (missing-newline-at-end-of-file) ×1
```

其中 5 个可以 `--fix` 自动修复。无任何严重 lint 问题。

### 3.4 TypeScript / 构建

无法运行（环境无 Node.js）。

---

## 4. 系统关键数据流与状态所有权

### 4.1 三层线程模型

```text
[Electron Render Process] ──WebSocket── [asyncio 工作线程] ──bridge.call── [Qt 主线程]
  React + Zustand                   server.py               message_bridge.py    MultiRoomManager
  (单线程 JS)                       (单线程 async)            + ThreadPool        (Qt 对象)
```

关键约束：

- **`MultiRoomManager` 及其 `_rooms` 字典只能在 Qt 主线程访问**。所有通过 `bridge.call()` 的路由都遵守这一约束。
- **`room_handler.py` 中的大部分全局状态在 asyncio 线程内使用**，是安全的。
- **边界风险**出现在：FFmpeg 回调（`on_done`）、线程池 `_recording_executor`，以及直接启动的 `threading.Thread` 访问 Qt 线程状态时。

### 4.2 状态所有权汇总

| 状态 | 拥有者 | 访问方 | 有锁？ |
|------|--------|--------|--------|
| `MultiRoomManager._rooms` | Qt 主线程 | 仅 Qt 线程（通过 bridge） | 单线程 |
| `server.py: self.clients` | asyncio 线程 | 仅 asyncio 线程 | 单线程 |
| `_mse_streamers` | asyncio 线程 | asyncio + executor | `_mse_streamers_lock` |
| `_mse_starting` | asyncio 线程 | asyncio + executor | `_mse_starting_lock` |
| `_mse_reconnect_state` | asyncio 线程 | 仅 asyncio 线程 | 单线程（已验证） |
| **`export_jobs`** | **共享** | **Qt 线程 + asyncio 线程** | **无锁** |
| `_export_cancelled_jobs` | asyncio 线程 | 仅 asyncio 线程 | 单线程 |
| `_analysis_jobs` | asyncio 线程 | 仅 asyncio 线程 | 单线程 |
| `_continuous_tasks` | asyncio 线程 | 仅 asyncio 线程 | 单线程 |
| `recording_history` | asyncio 线程 | 仅 asyncio 线程 | `_recording_history_lock` |
| `_settings_cache` | asyncio 线程 | 仅 asyncio 线程 | 单线程（global 变量） |
| `Zustand appStore` | React 主线程 | 仅 React 线程 | 单线程 |
| `__msePlayers` 全局注册表 | React 主线程 | React + WS handler | 无锁但单线程 |

---

## 5. 按优先级排序的发现

### P0：必须立即修复

#### P0-1. `manager.py:_proactive_reconnect` 后台线程直接操作 Qt 线程状态

- **状态**：已确认缺陷
- **证据**：`lsc/gui/multi_room/manager.py:2392-2429`
- **触发条件**：共享进样模式下，录制断连触发 `_on_global_tick`，当 `_proactive_reconnect` 被调用时
- **根因链**：
  1. `_on_global_tick`（Qt 定时器，Qt 线程）在 line 2392 启动 `threading.Thread(target=_proactive_reconnect, daemon=True)`
  2. 后台线程在 line 2404 调用 `controller.stop_recording()`，`controller` 是 Qt 线程绑定的对象
  3. 后台线程在 line 2407 写 `room.is_recording = False`，直接修改 Qt 属性
  4. 后台线程在 line 2408 调用 `self.start_recording(...)`，访问 `self._rooms` 等 Qt 线程所有权数据
- **影响**：未定义行为。Qt 可能检测到跨线程访问并崩溃；状态可能静默损坏（录制状态和实际 FFmpeg 进程不一致）。
- **现有保护**：`_on_global_tick` 仅在 `shared_ingest_enabled` 为 True 时触发（line 2264），且 `_attempt_recording_reconnect` 仅在 `room.reconnect_pending` 时启动（line 2370）。但保护不足以弥补直接跨线程调用的根本问题。
- **最小验证方法**：在 `_proactive_reconnect` 函数中添加 `assert threading.current_thread() is main_thread` 将立即触发。

#### P0-2. `export_jobs` 跨线程无锁访问

- **状态**：已确认缺陷
- **证据**：
  - `python-backend/handlers/room_handler.py:149`：定义 `export_jobs: dict[str, str]`
  - `room_handler.py:5586`：Qt 线程，`on_done` 回调中 `export_jobs.pop(job_id, None)`
  - `room_handler.py:4170-4183`：asyncio 线程，`handle_cancel_export` 中 `export_jobs.get` 和 `.pop`
  - `room_handler.py:5633`：asyncio 线程，`_process_export_job` 中 `export_jobs[job_id] = result['clip_id']`
- **触发条件**：用户在导出任务完成的同时点击“取消导出”，或两个导出任务同时完成
- **根因链**：
  1. 导出结束时，Qt 线程回调 `on_done`（来自 `ClipExporter` 的信号）pop `export_jobs`
  2. 与此同时，asyncio 线程可能在 `handle_cancel_export` 中读 / pop 同一个字典
  3. CPython GIL 保护单次 dict 操作，但 `get + pop` 组合不是原子的
- **影响**：极端情况下导出状态不一致，`cancel` 操作可能读取到已结束的任务或找不到任务 ID。不会崩溃，但前端可能看到虚假的导出状态。
- **最小验证方法**：给 `export_jobs` 加锁后运行现有的压力测试。

### P1：高优先级

#### P1-1. `bridge.call()` 超时后后台不释放 + `queue_broadcast` 满队列静默丢消息

- **状态**：已确认缺陷
- **证据**：
  - `python-backend/message_bridge.py:127-133`：超时后设置 `req.cancelled = True`，但请求已排队到 Qt 线程
  - `message_bridge.py:164-174`：满队列时 `get_nowait()` 丢弃最旧消息
- **触发条件**：大量 `mse_segment` 消息填充广播队列超过 1000 条上限，`recording_stopped` 这种单次关键消息被丢弃
- **根因链**：`queue_broadcast` 用 `queue.Queue(maxsize=1000)`。MSE 数据推送可能填满队列。`drain_merge_broadcasts` 每 100ms 清空一次，但如果处理负担重，队列仍可能溢出。
- **影响**：前端永远不会收到 `recording_stopped` 通知，将显示录制中的假状态。
- **最小验证方法**：模拟高频率 `mse_segment` 并监控 `recording_stopped` 是否可以到达。

#### P1-2. VideoPreview 组件在 playerGeneration 变更时 `__msePlayers` 注册表泄漏

- **状态**：已确认缺陷
- **证据**：`lsc-electron/src/components/VideoPreview.tsx:320-354`
- **触发条件**：预览源切换（直播与录制回看）或 `playerGeneration` 递增时
- **根因链**：
  1. main useEffect（line 175）cleanup 将 `playerRef.current = null`
  2. 注册 effect（line 320）cleanup 检查 `registry[roomId]?.player === playerRef.current`，发现 `null !== <old player instance>`，不删除条目
  3. 旧的 `feedInit`/`feedMedia` 指向已停用播放器
  4. 新效果的 `onSourceOpen` 最终覆盖注册表条目
- **影响**：窗口期内 WebSocket MSE 分片被投递到已 stop 的播放器，导致用户看到几秒的卡顿或花屏。
- **最小验证方法**：在 registry cleanup 处添加日志打印，观察重建时的脏条目。

#### P1-3. 新测试文件中的 3 条失败：代码已修改但测试未更新

- **状态**：已确认（测试陈旧）
- **证据**：
  1. `test_ux_habit_guards.py:test_can_export_for_shortcut_rejects_pending_and_refining`：断言 `confirm_status === 'pending'` 不应在 `canExportForShortcut` 函数中存在，但实际代码仍然包含该检查
  2. `test_stability_latency_guards.py:test_export_start_failure_broadcasts_clip_failed`：断言 `clip_failed` 和“导出任务失败”日志在同一 400 字符窗口内，但实际代码中两者间距超过 400 字符
  3. `test_cross_feature_guards.py:test_handle_remove_stops_continuous_analysis_when_involved`：断言 `handleRemove` 函数应包含 `stop_continuous_analysis`，但实际代码中没有
- **结论**：这些测试在文件创建时可能通过，但随着 `room_handler.py` 和 `Workbench/index.tsx` 的未提交修改而失效。这是典型的“实现细节更改后测试快照未更新”问题。

### P2：中等优先级

#### P2-1. `_save_recording_history` I/O 在锁内阻塞 asyncio 事件循环

- **状态**：高置信风险
- **证据**：`room_handler.py:3419-3472`，持锁期间调用 JSON 序列化和文件写入
- **触发条件**：录制结束频繁（每 5 秒一次历史更新），历史条目大于 500 时 JSON 序列化可达 10-50ms
- **影响**：阻塞 asyncio 事件循环，导致 WebSocket 消息延迟、MSE 数据推送卡顿。
- **最小验证方法**：在 `_save_recording_history` 前后加入时间日志。

#### P2-2. `_recording_starting` / `_recording_wait_queue` 无锁跨线程访问

- **状态**：高置信风险
- **证据**：
  - `room_handler.py:693`：`_recording_starting: set[str]`（无锁）
  - `room_handler.py:695`：`_recording_wait_queue: list[str]`（无锁）
  - 两者都通过 `asyncio.run_coroutine_threadsafe` 和 `_recording_executor` 从多个上下文访问
- **影响**：竞态下两个房间可能同时开始录制超过并发上限；或一个房间出现在等待队列中但实际已开始录制。

#### P2-3. 导出 semaphore 替换时 permit 泄漏

- **状态**：高置信风险
- **证据**：`room_handler.py:5678-5689`，`_ensure_export_queue` 替换全局 `_export_semaphore`
- **根因链**：
  1. Worker A 在旧 semaphore 上 acquire
  2. settings 更改触发 `_ensure_export_queue`，创建新 semaphore
  3. Worker A release 到旧 semaphore（已被丢弃）
  4. 该 permit 永久损失
- **影响**：并发上限随时间逐渐减少 1（例如从 2 到 1 到 0），最终导出队列死锁。
- **现有保护**：`_export_queue.empty()` 检查减少了但未消除概率。
- **最小验证方法**：更改导出并发设置时观察 `_export_semaphore._value`。

#### P2-4. `stop_recording_async` 标记停止但 FFmpeg 仍在运行

- **状态**：已确认设计问题
- **证据**：`manager.py:1726-1739`，先设置标志，后台线程再杀 FFmpeg
- **触发条件**：停止录制后立即启动同一房间的新录制
- **影响**：新旧 FFmpeg 进程争抢同一输出文件，导致文件损坏。

### P3：低优先级

#### P3-1. `room_handler.py` 部分 `except Exception: pass`

- **证据**：文件内多处（line 596、972、1129、2148、2358、2377），资源清理路径多数有 `_log.debug` 但无 `exc_info`
- **建议**：资源清理路径应至少 `_log.debug("...", exc_info=True)`

#### P3-2. Ruff lint 8 个警告

- **证据**：3 个未排序 import、1 个未使用循环控制变量、1 个未使用 import、1 个可折叠 if 等
- **建议**：运行 `ruff check lsc/ --fix`

#### P3-3. 部分测试文件访问 `window.__msePlayers` 等全局变量类型不安全

- **证据**：多处以 `(window as any).__msePlayers` 访问
- **建议**：引入一个轻量 TypeScript 类型（不需新依赖），只包装操作

---

## 6. 当前失败测试判定

新测试使用静态代码分析（文本匹配），不是动态运行。

| 失败测试 | 判定 | 依据 |
|----------|------|------|
| `test_ux_habit_guards.py:test_can_export_for_shortcut_rejects_pending_and_refining` | **测试陈旧** | 断言 `confirm_status === 'pending'` 不在 body 中，但当前 `Workbench/index.tsx` 在 `canExportForShortcut` 中仍包含该检查。测试与实现不一致。 |
| `test_stability_latency_guards.py:test_export_start_failure_broadcasts_clip_failed` | **测试陈旧，但可能是真问题** | 断言 `clip_failed` 在“导出任务失败”日志语句周围 400 字符内，当前代码不满足。若导出开始失败时确实没有广播 `clip_failed`，则是产品缺陷，需要手动确认。 |
| `test_cross_feature_guards.py:test_handle_remove_stops_continuous_analysis_when_involved` | **测试陈旧，但可能是真问题** | 断言 `handleRemove` 包含 `stop_continuous_analysis`，当前未提交的 `Workbench/index.tsx` 中没有。若删除房间时应自动停止持续分析，则是产品缺陷。 |
| `test_multi_room_manager.py` 的 3 个测试 | **测试陈旧 / 命名冲突** | 当前 `test_multi_room_manager.py` 没有这些函数名。可能存在于另一个分支或已被删除。 |
| `test_round_detector.py:test_refine_disabled_by_default` | **产品缺陷** | 一次运行通过、一次运行失败，可能取决于执行顺序或环境。 |
| `test_round_detector.py:test_trailing_buy_phase_does_not_back_cut_previous_round` | **产品缺陷** | 预期 `261.0`，实际得到 `272.0`，尾部购买阶段扩展了前一个回合结束时间。 |

---

## 7. 不要重构的部分和理由

| 不要动 | 理由 |
|--------|------|
| **`room_handler.py` 整体拆分** | 7072 行但高度内聚：所有 handler 和辅助函数共享局部变量及闭包。拆分会破坏现有调用关系和测试。只有已确认的跨线程边界问题才值得修复。 |
| **`Workbench/index.tsx` 整体拆分** | 3695 行但模式和风格一致。拆分可能引入新的 import 循环和重新渲染问题。 |
| **`__msePlayers` 全局注册表** | 虽然类型不安全，但这是设计意图：允许模块级 WS handler 向组件级播放器分发数据。改成 DI 模式需要大范围重构。 |
| **`bridge.call()` 超时机制** | 超时后后台仍执行是设计决定（防止长期冻结 UI），问题仅在“不释放”，但行为已定义。 |
| **三层架构** | Qt 主线程、asyncio 线程、React 主线程的分离是架构核心约束。改变线程模型将引入新的竞态。 |
| **测试框架** | 当前使用静态源代码分析加少量动态测试的模式是合理的（不需要真实直播流）。 |

---

## 8. 推荐方案

### 阶段 1：可观测性基线（0.5 天，安全，不回滚）

**目标**：为 P0/P1 问题添加保护性日志，避免修复时引入新 bug。

| 文件 | 改动 |
|------|------|
| `manager.py:2392` | 在 `_proactive_reconnect` 函数开头记录启动日志 |
| `manager.py:2404-2408` | 在跨线程调用前后加警告日志 |
| `room_handler.py:149` | 为 `export_jobs` 添加 `threading.Lock()` |
| `room_handler.py:5586` | `on_done` 中持锁后操作 `export_jobs` |
| `room_handler.py:4170-4183` | `handle_cancel_export` 中持锁后操作 `export_jobs` |
| `room_handler.py:5633` | `_process_export_job` 中持锁后写 `export_jobs` |
| `message_bridge.py:164-174` | 记录队列满时的消息类型 |
| `VideoPreview.tsx:320-354` | 在注册表 cleanup 中添加 stale entry 日志 |

**验收标准**：所有现有测试通过。

### 阶段 2：根因修复（1-1.5 天，高风险，需要回滚计划）

| 优先级 | 修复 | 回滚点 |
|--------|------|--------|
| P0-1 | `_proactive_reconnect` 改为在 Qt 线程执行 | 仅回滚 `lsc/gui/multi_room/manager.py` 对应提交 |
| P0-2 | `export_jobs` 加锁 | 仅回滚 `python-backend/handlers/room_handler.py` 对应提交 |
| P1-1 | `queue_broadcast` 满时扩容或特殊处理关键消息类型 | 仅回滚 `python-backend/message_bridge.py` 对应提交 |
| P1-2 | cleanup 时用稳定的 player ID，而非 `playerRef.current` | 仅回滚 `lsc-electron/src/components/VideoPreview.tsx` 对应提交 |
| P1-3 | 更新 3 个新测试断言匹配当前代码 | 仅回滚相应测试提交 |

**验证**：所有 pytest 通过（包括更新后的）。

#### P0-1 修复方案概要

```python
# manager.py 中 _on_global_tick 改为：
def _on_global_tick(self):
    ...
    # 不在后台线程处理重连，改为在 Qt 主线程执行
    if room.reconnect_pending:
        self._attempt_recording_reconnect_sync(room)

def _attempt_recording_reconnect_sync(self, room: RoomSession):
    # 此处可以直接访问 self._rooms 等 Qt 线程状态
    ...
```

#### P0-2 修复方案概要

```python
_export_jobs_lock = threading.Lock()

with _export_jobs_lock:
    job_id = export_jobs.get(...)
```

### 阶段 3：有证据的局部重构（0.5-1 天，低风险）

| 文件 | 动作 | 验证 |
|------|------|------|
| 所有 | 运行 `ruff check lsc/ --fix` | 0 warnings |
| `room_handler.py` | 将 P2-1 中 `_save_recording_history` 的 I/O 移出锁范围 | pytest + 性能测试 |
| 各处 `except: pass` | 添加 `exc_info=True` | pytest |
| 3 个测试文件 | 更新断言匹配当前代码 | 100% pass |

### 阶段 4：文档和测试对齐（0.5 天）

- `pytest.ini` 添加 `asyncio_default_fixture_loop_scope = function`，消除 deprecation 警告
- 更新 `CLAUDE.md`，记录 `export_jobs` 锁的使用方式
- 添加 `scripts/run_all_checks.bat`，一键运行 pytest 和 ruff check

---

## 9. 仍未知的事项（需用户提供）

| 编号 | 事项 | 为什么需要 |
|------|------|-----------|
| U1 | **真实接入直播流时录制、预览、MSE 的功能完整性** | 所有运行时分析依赖模拟环境和静态代码阅读。在真实直播中，`_on_mse_error` 重连循环、`shared_ingest`、`rooms_updated` 合并等行为无法验证。 |
| U2 | **`_fail_detail.txt` 中 10 个失败的精确复现** | `TypeError: _merge_close_segments() got an unexpected keyword argument 'iou_threshold'` 和 `RuntimeError: There is no current event loop in thread 'MainThread'` 可能与 Python 版本、依赖版本或执行顺序有关。 |
| U3 | **房间持久化移除的决策原因** | 当前未提交 diff 显示 `handle_connect` 不再调用 `manager.load_rooms()`。需要确认这是有意产品决策还是临时修复。 |
| U4 | **Electron 端 TypeScript 编译状态** | OpenCode 认为 `node` 不可用，因此未运行 `tsc --noEmit`。 |
| U5 | **测试文件命名冲突** | `test_multi_room_manager.py` 中不存在若干历史失败记录所指向的函数名，需要确认它们来自旧版本还是其他分支。 |

---

## 10. 最终结论

### 现在应该重构吗？

**否，不应该大爆炸重构。** 系统整体设计合理，文档充分，测试覆盖了关键约束。代码量对于当前功能集可以接受。

### 第一刀具体落在哪里？

```text
Day 1（0.5 天）：阶段 1，可观测性基线
Day 1-2（1.5 天）：阶段 2，P0-1 + P0-2 根因修复
Day 2（0.5 天）：阶段 3，Ruff 清理 + 测试更新
Day 2-3（0.5 天）：阶段 4，CI/Tooling 完善
```

### 重构前先做什么？

1. **修复 P0-1**：`_proactive_reconnect` 跨线程，这是唯一可能导致静默数据损坏的问题
2. **修复 P0-2**：`export_jobs` 无锁，低成本高收益
3. **更新 3 个失败测试**：让 CI 有可信的基线
4. 运行 `ruff --fix` 清理代码风格
5. 添加 `asyncio_default_fixture_loop_scope`，消除 pytest warning

**预估必要改动**：`manager.py` 约 20 行，`room_handler.py` 约 30 行，测试约 15 行。

---

## 附注：报告来源

- OpenCode 会话：`ses_0879318e2ffez5bh1JCeqqeUwE`
- 首次指定 `cctqgptplus/gpt-5.6-sol` 时供应端返回 `finish=unknown` 且 0 token；随后改用已验证可用的 `deepseek/deepseek-chat` 完成调查。
- 调查会话记录显示 `additions=0`、`deletions=0`、`files=0`；调查期间未修改源代码。
