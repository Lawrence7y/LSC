# 核心稳定性与逻辑问题修复 - Product Requirement Document

## Overview
- **Summary**: 本项目旨在修复直播切片多人系统中经过全面代码审查发现的核心稳定性问题、逻辑缺陷和资源泄漏问题，涵盖 Electron 主进程、Python 后端、WebSocket 通信层和前端 React 组件四个层面，确保程序在网络波动、并发操作、长时间运行等场景下的鲁棒性。
- **Purpose**: 解决影响用户正常使用的关键 bug：断连恢复后过时命令误执行、跨线程状态竞态、UI 阻塞、文件路径权限白名单错误、资源泄漏导致的内存增长和进程僵尸问题。
- **Target Users**: 使用直播切片多人系统进行多房间直播录制和剪辑的内容创作者。

## Goals
- 修复 WebSocket 断连重连后过时命令误执行问题，避免录制/预览状态非预期跳转
- 消除 Python 后端跨线程状态访问的竞态条件，确保房间状态一致性
- 异步化 MSE 预览启动流程，消除点击预览时 2 秒 UI 冻结
- 完善 Python 解释器依赖检测，后端启动失败时给出明确提示而非静默崩溃
- 统一批量录制目录回退逻辑，避免录制文件分散在多个目录
- 修复 Electron openPath 路径白名单，支持用户自定义输出目录
- 清理房间删除时的 MSE 段缓存泄漏
- 修复 refresh worker 取消逻辑和 WebSocket 连接超时
- 为广播队列增加大小限制防止内存溢出

## Non-Goals (Out of Scope)
- 不重写 MP4 box 解析器（保留现有标记搜索方式，仅增加健壮性保护）
- 不重构广播循环为事件驱动（保留轮询方式，仅添加队列大小限制）
- 不优化前端 HMR 热更新状态（开发体验问题，不影响生产）
- 不统一 logging 模块替换所有 print（仅关键路径修改）
- 不重构全局 window 属性污染（P3 级别优化，后续迭代）

## Background & Context
经过对整个代码库的全面审查，发现了 20 个问题点。其中 P0-P1 级别的问题直接影响核心功能稳定性：
1. WebSocket 重连后会发送断连期间缓存的所有消息，包括已经过时的状态变更命令（如用户已取消的"开始录制"）
2. 批量录制工作线程直接修改 RoomSession 状态，没有线程同步保护
3. MSE 启动时在调用线程 sleep(2) 等待 FFmpeg 探测，如果在主线程调用会导致 UI 冻结
4. Python 解释器检测只检查版本号，不验证 PySide6/websockets 等必需依赖
5. openPath 白名单硬编码为 ~/LSC，用户自定义输出目录无法打开
6. 房间删除时前端 MSE 段缓存未清理，造成内存泄漏
7. URL 刷新 worker 存储在 _connect_workers 但键名带前缀，删除房间时无法取消
8. WebSocket 连接缺少超时，极端情况下 Promise 永远 pending

## Functional Requirements
- **FR-1**: WebSocket 消息队列必须区分可排队消息和不可排队消息，状态变更类命令（start/stop/toggle）在断连超时时应丢弃而非排队
- **FR-2**: 所有修改 RoomSession 状态的操作必须在 Qt 主线程执行，跨线程调用通过 Qt Signal/Slot 或 bridge.call() 调度
- **FR-3**: MseStreamer.start() 的 FFmpeg 启动探测必须在后台线程执行，不得阻塞调用线程
- **FR-4**: Electron 启动 Python 后端前必须验证关键依赖（PySide6, websockets）是否可用，缺失时向用户显示明确错误
- **FR-5**: 批量录制时所有房间必须使用统一的输出目录（原始目录或统一回退目录），不得分散
- **FR-6**: openPath 和 showItemInFolder 的路径白名单必须包含用户在设置中配置的自定义输出目录
- **FR-7**: 房间被删除时，前端必须清理该房间对应的 MSE init/segment 缓存
- **FR-8**: URL 刷新 worker 必须存储在独立字典中，删除房间时能正确取消
- **FR-9**: WebSocket 连接必须有超时机制（10秒），超时后触发重连流程
- **FR-10**: QtManagerBridge 的广播队列必须有上限（1000条），超出后丢弃最旧消息

## Non-Functional Requirements
- **NFR-1**: 性能：点击"预览"按钮到界面响应的时间不得超过 100ms（不含实际流连接时间）
- **NFR-2**: 稳定性：连续运行 4 小时、执行 50 次连接/断开/录制/删除操作后，内存增长不超过 50MB，无 FFmpeg 僵尸进程
- **NFR-3**: 错误提示：后端启动失败时，前端必须显示具体缺失的依赖名称而非"未知错误"
- **NFR-4**: 兼容性：所有修复不得破坏现有 WebSocket 协议消息格式
- **NFR-5**: 线程安全：Python 端必须通过 GIL + Signal 机制保证状态一致性，不得出现 torn reads

## Constraints
- **Technical**: 
  - Python 端使用 PySide6 Qt 框架，跨线程必须使用 Signal/Slot 或 QMetaObject.invokeMethod
  - WebSocket 协议使用 JSON 消息，向后兼容
  - Electron 主进程使用 Node.js child_process 管理 Python 子进程
- **Business**: 修复必须在当前架构下完成，不引入新的大型依赖
- **Dependencies**: 现有 websockets 库、PySide6、Zustand、React 18

## Assumptions
- Qt Signal/Slot 的 AutoConnection 机制在跨线程时会自动将槽调用调度到接收者所在线程
- 用户设置的自定义输出目录在 settings 中持久化，启动时可通过 IPC 获取
- 断连超过 5 秒后，排队的命令类消息已失去时效性，可以安全丢弃
- MSE 启动探测即使异步化，FFmpeg 快速失败的错误仍需正确上报

## Acceptance Criteria

### AC-1: WebSocket 断连命令队列过时消息不执行
- **Given**: WebSocket 已连接并正在预览/录制某房间
- **When**: 用户点击"停止录制"后立即断网，等待 10 秒后恢复网络
- **Then**: 重连后不会发送断连期间排队的任何 start/stop 命令，房间状态与用户最后一次操作一致
- **Verification**: `programmatic` + `human-judgment`

### AC-2: 跨线程房间状态修改无竞态
- **Given**: 4 个房间同时进行批量录制
- **When**: 录制过程中快速点击开始/停止/删除房间
- **Then**: 无崩溃、无状态不一致（如房间显示"录制中"但实际无 FFmpeg 进程）
- **Verification**: `programmatic` + `human-judgment`

### AC-3: 预览启动不阻塞 UI
- **Given**: 程序已启动并添加了房间
- **When**: 用户点击"预览"按钮
- **Then**: 界面在 100ms 内响应（按钮立即变为 loading 状态），无卡顿冻结
- **Verification**: `human-judgment`

### AC-4: Python 依赖缺失时显示明确错误
- **Given**: 系统 Python 缺少 PySide6 或 websockets 模块
- **When**: 用户启动程序
- **Then**: 前端显示错误消息"缺少必需依赖: PySide6, websockets，请运行 pip install -r requirements.txt"，而非黑屏或"连接失败"
- **Verification**: `programmatic` + `human-judgment`

### AC-5: 批量录制目录一致性
- **Given**: 默认输出目录不可写（如权限不足）
- **When**: 用户点击"全部录制"
- **Then**: 所有房间的录制文件都写入同一个回退目录（~/.lsc/output），不会部分在原目录部分在回退目录
- **Verification**: `programmatic`

### AC-6: 自定义输出目录可打开
- **Given**: 用户在设置中配置了自定义输出目录（如 D:\Videos\直播）
- **When**: 录制完成后点击"打开文件夹"
- **Then**: 资源管理器正确打开该目录，不弹出"不允许打开此路径"错误
- **Verification**: `human-judgment`

### AC-7: 房间删除后 MSE 缓存清理
- **Given**: 房间已开启预览，MSE 缓存中有 init/segment 数据
- **When**: 用户删除该房间
- **Then**: 前端 _mseInitCache 和 _mseSegmentCache 中对应 room_id 的条目被移除
- **Verification**: `programmatic`

### AC-8: WebSocket 连接超时不挂起
- **Given**: 后端端口被防火墙丢弃（无 RST 响应）
- **When**: 前端尝试连接
- **Then**: 10 秒后触发超时，进入重连流程而非永久 pending
- **Verification**: `programmatic`

### AC-9: 广播队列有上限不溢出
- **Given**: WebSocket 客户端断开但后端继续运行
- **When**: 后端持续产生广播消息（如录制进度、状态更新）
- **Then**: 广播队列大小不超过 1000 条，内存不无限增长
- **Verification**: `programmatic`

### AC-10: 删除房间时 refresh worker 被取消
- **Given**: 正在进行 URL 刷新（后台线程）
- **When**: 用户删除该房间
- **Then**: refresh worker 被 requestInterruption 取消，完成回调不会访问已删除的 room
- **Verification**: `programmatic`

## Open Questions
- [ ] 命令类消息的具体黑名单/白名单策略需要确认：除了 start_recording/stop_recording/enable_preview/disable_preview 之外，还有哪些消息属于"不应排队"类型？
- [ ] Python 依赖检测是在主进程做还是在后端启动脚本中做？需要确认错误信息如何传递到前端
