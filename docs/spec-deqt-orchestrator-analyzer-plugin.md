# Spec: 去 Qt 化（纯 Python 编排核心）+ 游戏分析插件化

## 概述

两项架构演进，分四个阶段落地，每个阶段独立可交付、可回滚：

- **去 Qt 化**：将 `MultiRoomManager`（QObject）迁移为纯 Python `RoomOrchestrator`，保留 actor 串行模型，仅把传输层从 Qt 信号换成 `queue.Queue` + 专用线程。完成后 `python-backend` 不再依赖 PySide6，`QtManagerBridge` 整套机制（10s 超时 / `cancelled` 标志 / `_MAX_PENDING_REQUESTS` 防护）整体删除，已知问题 #2（bridge.call 超时后命令乱序）随架构消失。
- **分析插件化**：照搬平台适配器已验证的 Protocol + Registry 模式，把硬连线的 Valorant 分析（`round_detector` / `ocr_detector` / `phase_scheduler` 等）与通用场景检测封装为可插拔插件，`room_handler.py`（7617 行）中的游戏专属逻辑下沉到插件。

**明确不做**：不删除 `lsc/gui`（PySide6 原生 GUI 通过薄壳继续可用）；不改变任何 WebSocket 消息形状（`rooms_updated` / `recording_started` 等 payload 字节级不变）；不触碰前端 13 项冻结交互；不在本 spec 中做 shared ingest 实时帧推送分析（列为未来选项，见 §6.4）。

---

## 现状事实（已核实）

### Qt 耦合面（比预期小，集中在 3 个文件）

| 文件 | Qt 依赖 | 规模 |
|------|---------|------|
| `lsc/gui/multi_room/manager.py` | `QObject` + 7 个 Signal + 3 个 QThread worker + 1 个 1s 分层 QTimer + 1 个选区循环 QTimer + QThreadPool（仅文件大小查询） | 2526 行 |
| `lsc/gui/pages/recording_controller.py` | QThread / QTimer / Signal | PySide6 GUI 侧 |
| `lsc/gui/common_workers.py` | QThread / Signal | PySide6 GUI 侧 |

核心服务层（`recording_service` / `export_service` / `shared_ingest` / `mse_streamer` / `platforms`）**已是纯 Python**，零 Qt 符号。

### MultiRoomManager 公共 API（迁移必须完整保留）

`add_room` / `get_room` / `list_rooms` / `room_count` / `remove_room` / `connect_room` / `disconnect_room` / `start_preview` / `stop_preview` / `seek_preview` / `seek_selected_previews` / `refresh_stream_url` / `refresh_stream_url_async` / `start_recording` / `stop_recording` / `stop_recording_async` / 心跳分层 tick / 选区循环试听。

### Signal 清单（事件总线必须一一对应）

`room_connect_finished(room_id, success, error)` / `batch_record_progress(room_id, success)` / `batch_record_finished(started, total)` / `recording_stopped(room_id, reason, message)` / `global_tick` / `medium_tick` / `low_tick`。

### QThread worker 清单（→ ThreadPoolExecutor）

- `_ConnectWorker`：流地址解析（`parse_stream` + `select_quality`），支持 `requestInterruption` 取消
- `_MetadataProbeWorker`：ffprobe 分辨率/帧率探测
- `_BatchRecordWorker`：批量录制启动（内层 ThreadPoolExecutor ≤4 并发）
- `SizeUpdateRunnable`（QThreadPool）：录制文件大小后台查询

### 桥接层调用现状

`room_handler.py` 中 15 处 `bridge.call(...)` + 直接 `mgr.*` 调用混用；调用方已统一在 `_bridge_executor` / `_recording_executor` 线程池中执行，迁移后改为对 orchestrator 的直接调用即可，handler 侧改动是机械替换。

### 装配点（去 Qt 化需改线的入口）

- `python-backend/main.py:143` — 创建 QApplication + MultiRoomManager，`_install_qt_message_handler` 已有 ImportError 容错
- `python-backend/server.py:369` — import MultiRoomManager
- `python-backend/start.py:22` — import MultiRoomManager

### 分析硬连线现状

- `room_handler.py` 直接 import：`phase_scheduler`、`ocr_detector`、`pipeline`、`round_detector`、`ocr_accel`、`valorant_frame_classifier`；持续分析循环（扫描窗口计算、压力门控、增量追赶、取消、进度广播）全部内嵌在 handler 中
- **帧源事实**：持续分析是对**录制文件的增量窗口 FFmpeg 扫描**（`_compute_continuous_scan_range`，last_analyzed + lookback 追赶，相位调度器短窗预算），不是 shared ingest 实时帧。插件协议 v1 按此设计（pull 模型：文件路径 + 窗口）
- `lsc/analyzer/pipeline.py` 的 `HighlightAnalyzer` 已是离线文件分析门面（game="valorant" 字符串分支）

---

## 阶段 A1：纯 Python 编排核心 `lsc/core/orchestrator.py`

### 目标

新增三个模块，`MultiRoomManager` 的全部非 Qt 逻辑原样迁入，行为（含时序、频率、阈值）保持不变。

### 新模块

**`lsc/core/events.py` — 事件总线**

```python
class EventBus:
    def subscribe(self, event: str, callback: Callable) -> None: ...
    def unsubscribe(self, event: str, callback: Callable) -> None: ...
    def emit(self, event: str, *args) -> None: ...  # 仅在编排线程调用
```

- 事件名与现有 Signal 一一对应：`room_connect_finished` / `batch_record_progress` / `batch_record_finished` / `recording_stopped` / `global_tick` / `medium_tick` / `low_tick`
- **派发纪律（关键设计决策）**：所有 `emit` 只在编排器线程执行，订阅者回调禁止阻塞/重入 orchestrator 写操作——以此显式化并替代 Qt 的"接收方线程队列派发"语义，杜绝朴素回调替换信号带来的重入与锁序风险
- 订阅者列表用 `RLock` 保护；回调异常捕获并记日志，不中断派发链

**`lsc/core/orchestrator.py` — RoomOrchestrator**

- 命令队列 `queue.Queue` + 专用守护线程（actor 模型）：所有公开方法体在编排线程串行执行，与现状（Qt 主线程串行）语义一致
- 对外原语与 bridge 对齐，纯 Python 实现：
  - `call(fn, *args, timeout=10.0)` — `threading.Event` 等待结果；保留 `cancelled` 标志与 `_MAX_PENDING_REQUESTS = 8` 堆积防护；调用方已在编排线程时直接执行
  - `submit(fn, *args)` — fire-and-forget
- **心跳**：编排主循环用 deadline 等待替代 QTimer——`queue.get(timeout=到下一次 1s tick 的剩余时间)`，tick 计数器驱动 high(1s)/medium(5s)/low(10s) 分层，频率与现状完全一致；`global_tick/medium_tick/low_tick` 通过 EventBus 发出
- **选区循环试听 timer** → 编排线程内的调度堆（deadline 回调），不新增线程
- **worker 迁移**：
  - `_ConnectWorker` / `_MetadataProbeWorker` / `_BatchRecordWorker` → `ThreadPoolExecutor` future + 完成回调以命令形式回投编排队列；`requestInterruption` 语义用 worker 对象上的 `threading.Event` 取消标志复刻（解析循环内检查点保持原位）
  - `SizeUpdateRunnable` → 同一 executor 提交
- 内部 `_rooms` 的 `RLock` 保护保持不变；构造签名（`controller_factory` / `preview_factory`）保持不变

### 验证

- 新测试 `tests/test_orchestrator.py`：不依赖 Qt（不设 `QT_QPA_PLATFORM`）实例化 orchestrator，覆盖 add/connect/record/stop/心跳分层/tick 频率/取消语义
- 事件序列对等测试：同一场景脚本分别驱动旧 manager 与新 orchestrator，断言事件名 + 参数序列完全一致

---

## 阶段 A2：`manager.py` 退化为薄 Qt 壳

### 目标

`lsc/gui`（PySide6 原生 GUI）不删除、继续可用；Qt 依赖收敛到一个文件。

### 改动

- `MultiRoomManager(QObject)` 保留原类名与原 Signal 清单，内部持有 `RoomOrchestrator` 并**委托全部公共方法**
- Qt Signal 只做一件事：订阅 EventBus 对应事件并原样转发（signal emit），供 `lsc/gui` 页面继续 connect
- `manager.py` 中全部业务逻辑删除（已迁入 orchestrator），文件从 2526 行收敛到约 200 行的壳
- `_is_stream_offline_error` 等被 `room_handler` 引用的纯函数移入 `lsc/core/orchestrator.py` 或 `lsc/utils/`，`manager.py` 保留 re-export 兼容旧 import

### 验证

- `tests/test_multi_room_manager.py` / `tests/test_stability_guards.py` / `tests/test_recording_reconnect_tick.py` 等存量测试**不改断言**全部通过（薄壳保证行为兼容）
- PySide6 GUI 手动冒烟：添加房间 → 连接 → 录制 → 停止

---

## 阶段 A3：`python-backend` 拆除桥接层

### 目标

`python-backend` 进程完全不 import PySide6；`QtManagerBridge` 删除。

### 改动

**`python-backend/main.py`**
- 删除 QApplication 创建与 Qt 事件循环；编排线程由 `RoomOrchestrator` 自带
- 删除 `_install_qt_message_handler`（已无 Qt 消息可接）
- 启动流程简化为：创建 orchestrator → 创建 broadcast hub → 启动 WebSocket 工作线程

**`python-backend/message_bridge.py` → 重构为 `python-backend/broadcast_hub.py`**
- 保留广播队列全部机制（`_DROPPABLE_TYPES` / `_TERMINAL_TYPES` / `_enqueue_preserving_terminal` / `_expand_broadcast_queue` / `notify_broadcast` 事件驱动唤醒）——这部分本就与 Qt 无关
- 删除 `QtManagerBridge`、`bridge.call`、`bridge.submit`、`_execute` Signal、`_pending_count` 防护（orchestrator 内部已有等价防护）
- 事件订阅从 `manager.signal.connect(...)` 改为 `orchestrator.bus.subscribe(...)`，回调体（`_on_connect_finished` 等的 payload 构造）原样保留，保证消息形状不变

**`python-backend/handlers/room_handler.py`**
- 15 处 `bridge.call(fn, ...)` → 直接 `orchestrator.call(fn, ...)`（签名对齐，机械替换）
- `from lsc.gui.multi_room.manager import MultiRoomManager` → `from lsc.core.orchestrator import RoomOrchestrator`
- `_is_stream_offline_error` 改从 core 位置 import

**`python-backend/server.py` / `start.py`**：import 同步更新。

### 验证

- `tests/test_message_bridge.py` / `test_message_bridge_terminal.py` / `test_drain_merge_broadcasts.py` 全绿（广播行为不变）
- `grep -r "PySide6" python-backend/` 结果为空
- 后端启动冒烟：Electron 拉起后端，房间连接/录制/预览/MSE 全链路手动验证
- 记录安装包移除 PySide6 后的体积变化（记录数据，不强制目标值）

---

## 阶段 B1：分析插件协议与 Registry

### 目标

建立与平台适配器同构的插件机制；v1 协议严格匹配现状的 pull 模型（录制文件窗口扫描），不引入实时帧推送。

### 新模块

**`lsc/analyzer/base.py` — 插件协议**

```python
@dataclass(frozen=True, slots=True)
class AnalyzerCapabilities:
    realtime_continuous: bool      # 支持持续分析（录制中增量扫描）
    posthoc_file: bool             # 支持离线文件分析
    needs_ocr: bool                # 需要 OCR 加速资源
    needs_audio: bool              # 需要音频流
    game_specific: bool            # 游戏专属 or 通用

@dataclass(slots=True)
class ScanWindow:
    start_sec: float
    end_sec: float
    timeout_sec: float
    use_ocr: bool

class AnalyzerPlugin(Protocol):
    game: str                # 唯一标识，如 "valorant" / "generic"
    display_name: str

    def capabilities(self) -> AnalyzerCapabilities: ...

    # —— 离线文件分析（对应现 HighlightAnalyzer.analyze）——
    def analyze_file(
        self,
        video_path: str,
        *,
        progress_callback: Callable[[str, float, str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None: ...

    # —— 持续分析（对应现持续分析循环中的扫描段）——
    def plan_scan_window(
        self,
        state: dict[str, Any],       # 插件自有状态（last_analyzed、round_phase、pending_start、prediction 等）
        current_dur: float,
        pressure: dict[str, Any],    # 资源压力门控参数
    ) -> ScanWindow: ...

    def scan_window(
        self,
        video_path: str,
        window: ScanWindow,
        state: dict[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]: ...     # 新发现的回合/高光段，原地更新 state
```

- **无状态约束**（照搬平台适配器）：插件实例无状态，所有会话状态放在 `state` dict 中流转，单例可并发复用
- `plan_scan_window` 把现 `_compute_continuous_scan_range` 的相位调度逻辑收进插件；generic 插件实现 lookback 追赶策略

**`lsc/analyzer/registry.py` — AnalyzerRegistry**

- `register(plugin)` / `get(game)` / `list()` / `default()`，模块级单例
- `get(game)` 未命中时回退 `default()`（generic），与 `GenericPageAdapter` 兜底同构
- 内置注册：`valorant`（阶段 B2 迁入）、`generic`（包装现 FFmpeg scene 检测）

### 验证

- `tests/test_analyzer_registry.py`：注册/查找/兜底回退/无状态约束（连续调用不串状态）
- `tests/test_analyzer_plugin_contract.py`：对两个内置插件跑契约测试（capabilities 一致性、scan_window 返回结构、cancel_check 生效）

---

## 阶段 B2：插件实现迁入 + `room_handler` 瘦身

### 目标

游戏专属逻辑从 handler 下沉到插件；handler 只保留编排（线程、压力门控、进度/结果广播、取消）。

### 改动

**`lsc/analyzer/valorant_plugin.py`（新）**
- 包装 `round_detector` / `ocr_detector` / `phase_scheduler` / `ocr_accel` / `valorant_frame_classifier` 的现有能力，实现 `AnalyzerPlugin`
- `plan_scan_window` = 现 `_compute_continuous_scan_range` 的 `valorant_round` 分支（相位调度主路径 + lookback 兼容路径）
- `scan_window` = 现持续分析中的窗口扫描段（含 OCR refine、pressure 门控里的 `use_ocr` 决策）
- `analyze_file` = 现 `HighlightAnalyzer` 的 valorant 分支
- **本阶段不物理移动** `round_detector.py` 等存量文件，只做包装；物理归拢为 `lsc/analyzer/valorant/` 包留作后续独立任务（涉及大量 import 与测试更新）

**`lsc/analyzer/generic_plugin.py`（新）**
- `analyze_file` = 现 `_run_scene_analysis` 的 FFmpeg scene filter 逻辑（阈值、15s 切分、2s/5s padding、<3s 过滤、去重）
- `capabilities()` = 仅 `posthoc_file=True`

**`python-backend/handlers/room_handler.py` 瘦身**
- 持续分析循环保留的部分：线程生命周期、`_continuous_effective_interval` 压力门控与暂停、进度/状态/结果广播（`continuous_analysis_status` / `analysis_progress` / `continuous_highlights` payload 不变）、取消与锁守卫
- 下沉到插件的部分：扫描窗口计算（`_compute_continuous_scan_range`）、窗口扫描执行、OCR refine 决策、游戏分支判断
- 入口改动：`start_analysis` / `start_continuous_analysis` 按 `game`/`mode` 参数经 `AnalyzerRegistry.get()` 解析插件，未注册游戏回退 generic 并记日志
- 直接 import 的 `phase_scheduler` / `ocr_detector` / `round_detector` 等全部移除，改为经插件协议调用

### 验证

- 存量测试全绿：`test_continuous_analysis_guards.py` / `test_synced_continuous_analysis.py` / `test_phase_scheduler.py` / `test_round_detector.py` / `test_valorant_dense_refine.py` / `test_round_boundary_optimization.py` 等（这些测试可继续直接测底层模块，不受插件层影响）
- 新增插件级对等测试：同一录像文件，旧路径（迁移前留档脚本）与新路径（插件）产出的回合区间逐项一致（允许 ±0.5s 浮点容差）
- 广播 payload 快照对比：`continuous_analysis_status` / `continuous_highlights` 字段集不变

---

## 阶段 B3（可选，独立排期）：物理归拢与清理

- `round_detector.py`（2716 行）拆分为 `lsc/analyzer/valorant/` 包（detect / refine / fsm / clock / ocr 子模块），仅文件组织调整，不改逻辑
- `room_handler.py`（7617 行）按域拆分：room 生命周期 / 预览 / 录制 / 导出 / 分析 五个 handler 模块
- 仓库根目录 `_tmp_*` / `_dump_*` / `nul` 等调试残留清理，`.gitignore` 补充规则

---

## 关键设计决策与风险

### 事件派发纪律（最高风险点）

Qt 信号跨线程是**接收方线程队列派发**；朴素 Python 回调是**发射方线程同步执行**。直接替换会引发重入与锁序 bug。决策：EventBus 的 `emit` 只允许在编排线程调用（`assert` 守护 + 文档约束），订阅者要做重活的必须自行转发到其他线程。对等测试覆盖事件顺序。

### actor 模型保留，而非"直接调用 + 锁"

不改成"handler 线程直接持锁调 manager"——那会把串行保证变成锁竞争，行为时序变得不可推理。保留命令队列的代价（一次线程切换）在本场景可忽略（房间操作均为低频人工操作）。

### 心跳频率原样保留

1s/5s/10s 分层频率被多个 guard 测试与前端刷新节奏隐式依赖，本 spec 不做任何"顺手优化"。deadline 等待实现的 tick 漂移 < 50ms，优于 QTimer 实测水平。

### shared ingest 实时帧分析（未来选项，不在本 spec）

插件协议 v1 是 pull 模型（文件窗口扫描），匹配现状。未来若要降低检测延迟，可在 `SharedRoomIngest` 增加 analysis subscriber（fMP4 段 → 轻量 ffmpeg rawvideo 解码 → 推送帧给插件的 `on_frame` 可选钩子）。协议预留扩展空间但不现在实现。

### 回滚策略

每阶段独立 PR、独立可回滚：A1 纯新增（零风险）；A2 壳切换（改 1 个文件）；A3 桥接拆除（git revert 即恢复）；B1 纯新增；B2 有对等测试兜底。任意阶段出问题不阻塞其他阶段交付。

---

## 里程碑与验收清单

| 里程碑 | 内容 | 验收 |
|--------|------|------|
| M1 | A1 orchestrator + EventBus | `test_orchestrator.py` 绿；事件对等测试绿 |
| M2 | A2 manager 薄壳化 | 存量 manager 相关测试零改动全绿；PySide6 GUI 冒烟通过 |
| M3 | A3 后端去 Qt | `python-backend` 无 PySide6 import；广播测试全绿；Electron 全链路冒烟通过 |
| M4 | B1 插件协议 + Registry | 契约测试绿 |
| M5 | B2 插件迁入 + handler 瘦身 | 分析存量测试全绿；插件对等测试绿；payload 快照一致 |

每阶段完成后更新 `docs/PROJECT_DESIGN.md` 对应章节（第三部分跨线程通信、第十八部分已知问题 #2 标记为已消除）。