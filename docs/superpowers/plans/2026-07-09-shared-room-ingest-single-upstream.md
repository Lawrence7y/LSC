# 方案一：单上游录制与预览共用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个直播间在开启“方案一”后，只保留一条上游拉流链路，并同时服务录制、预览、直播跳转、一键对齐、切片导出和持续分析；方案默认可关闭，失败时自动回退旧链路。

**Architecture:** 新增每房间一个 `SharedRoomIngest`，由它统一管理 FFmpeg 上游进程、录制 sink、预览 sink、fMP4 初始化段缓存、预览订阅者和生命周期清理。现有 WebSocket 事件、前端 MSE 播放器、`content_offset` 语义、切片映射和持续分析入口保持不变；所有新行为都放在 `shared_ingest_enabled` 开关后，旧的 `StreamCapture` 和 `MseStreamer` 仍作为回退路径保留。

**Tech Stack:** Python 后端、FFmpeg、fMP4/MSE、Electron/React、WebSocket、pytest、TypeScript `tsc`。

---

## 方案一边界

方案一不是把所有房间合成一条全局流，而是“每个直播间一条上游”。三个直播间就最多三条上游，每条上游内部同时输出录制文件和预览分片。

同一个房间内：

- 开启录制时，不再额外启动独立预览拉流。
- 开启预览时，如果录制已在 shared ingest 中运行，预览直接订阅 shared ingest 的 fMP4 分片。
- 预览先开、再开始录制时，必须迁移到 shared ingest，最终稳定状态只能有一条上游。
- 停止预览不能停止录制。
- 停止录制时，如果预览仍在打开，预览可以短暂重连，但录制文件必须停止写入并可校验。

不改变的外部契约：

- 前端仍只消费 `mse_init`、`mse_segment`、`mse_error`、`mse_reconnecting`、`mse_stopped`。
- 前端“直播”按钮仍调用 `MediaSourcePlayer.goLive()`，跳到当前 MSE buffer live edge。
- 一键对齐仍写入 `content_offset`，含义保持为：更快的直播间相对最慢直播间是正偏移。
- 切片导出仍按 `导出时间 = 标记墙钟时间 - 录制媒体起点 - content_offset` 映射。
- 持续分析仍分析主直播间录制文件，再按 `content_offset` 映射到其他房间。
- 旧链路可随时通过配置关闭 shared ingest 恢复。

## 落地保护线

方案一必须按“先保护现有功能，再减少上游数量”的顺序落地。任何一步不能满足下面保护线，都不能继续扩大开关范围：

1. **默认不改变现状**：`shared_ingest_enabled=false` 时，录制仍走 `StreamCapture`，预览仍走 `MseStreamer`，一键对齐、切片导出、持续分析和底部“直播”按钮的行为必须和当前版本一致。
2. **开关后可回退**：shared ingest 只作为可选路径；创建失败、FFmpeg 启动失败、预览 attach 失败、preview-only 切 dual-output 失败，都必须自动 fallback 到 legacy，并在日志中写明房间、阶段和原因。
3. **协议不破坏**：前后端 WebSocket 事件名、二进制 payload 形态、`content_offset` 符号、切片导出参数和持续分析入口不改。需要新增的诊断字段只能是可选字段。
4. **录制优先级最高**：预览队列满、前端断开、MSE 播放失败、直播按钮跳转失败，都不能阻塞录制 sink，也不能停止正在录制的 shared upstream。
5. **预览体验不中断或可恢复**：预览切换 shared/legacy、preview-only/dual-output 时，必须发送 `mse_reconnecting` 或 replay `mse_init`，避免前端黑屏后无法恢复。
6. **对齐算法不借机改语义**：三房间及以上 pairwise 对齐只增强证据覆盖和置信度计算，不改变 `content_offset` 的存储语义。最快房间相对最慢房间仍写正偏移。
7. **切片和分析不依赖预览流**：切片导出、持续分析、AI 场景检测必须继续以录制文件为数据源；预览 fMP4 分片只服务 MSE 播放和音频捕获诊断。
8. **可观察后再发布**：日志和资源监控必须能同时看到 shared upstream 数、legacy MSE 数、录制 sink 数、预览订阅数、丢帧/丢分片数和 fallback 原因。

## 项目全量影响清单

| 层级 | 文件/模块 | 是否需要改动 | 影响说明 | 保证不影响现有功能的处理 |
| --- | --- | --- | --- | --- |
| Electron 启动 | `lsc-electron/electron/main.ts`、`preload.ts` | 原则上不改，除非打包路径验证失败 | 后端启动、日志路径、Python/FFmpeg 路径会影响 shared FFmpeg 是否可用 | 不新增新的二进制依赖；继续复用现有后端启动方式和 FFmpeg 查找逻辑；打包烟测作为最终门禁 |
| 前端 WebSocket hook | `lsc-electron/src/hooks/useWebSocket.ts`、`services/websocket.ts` | 只允许兼容新增可选字段 | shared 和 legacy 都通过同一消息桥推送 MSE 事件 | 不改 event name；新增字段使用可选类型；旧消息仍可解析 |
| 前端播放器核心 | `lsc-electron/src/services/mediaSourcePlayer.ts` | 需要加诊断和空 buffer 处理 | `goLive()` 无反应主要可能发生在 player 缺失、buffer 为空、init 未 replay | 保持 `goLive()` 对外 API 不变；空 buffer 时等待下一段；有 buffer 时跳 live edge |
| 前端预览组件 | `lsc-electron/src/components/VideoPreview.tsx` | 需要确认挂载、卸载和 init replay | shared preview 切换时可能出现短暂重连 | 挂载后主动 `request_mse_init`；卸载只关闭预览订阅，不触发录制停止 |
| 工作台页面 | `lsc-electron/src/pages/Workbench/index.tsx` | 需要加直播按钮诊断和对齐诊断展示 | 选择房间、player registry、对齐请求都集中在这里 | 直播按钮仍调用现有 player；诊断只写日志或 UI 状态，不改变指令协议 |
| 房间卡片 | `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` | 只允许展示状态增强 | shared/legacy 状态可能需要显示 | 新字段可选；没有 shared 信息时显示旧状态 |
| 录制设置 | `lsc-electron/src/pages/Workbench/components/RecordSettings.tsx` | 原则上不改 | 录制参数、画质、输出目录会影响 shared 命令 | shared 使用现有录制参数，不新增用户必须配置项 |
| 切片列表 | `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 原则上不改 | 切片导出依赖时间映射 | 保持请求参数不变；后端内部使用 `recording_media_start_mono` 修正 |
| 时间线 | `lsc-electron/src/components/Timeline/index.tsx` | 原则上不改 | 标记点的墙钟时间仍是导出基准 | 不改标记数据结构；只在后端映射时使用新的录制媒体起点 |
| 导出队列 | `lsc-electron/src/components/ExportQueue/index.tsx` | 原则上不改 | 导出结果展示不能受 shared 影响 | 导出服务返回结构不变 |
| 系统监控 | `lsc-electron/src/components/Layout/SystemMonitor.tsx` | 可选增强 | 需要看到 shared/legacy 资源状态 | 新增 counters 可选展示；旧统计不存在时不报错 |
| 日志面板 | `lsc-electron/src/components/LogViewer.tsx` | 可选增强 | 需要定位 fallback、对齐捕获不足、直播按钮无反应 | 日志文本增强，不改变筛选协议；禁止输出原始 PCM/base64 |
| 前端状态 | `lsc-electron/src/store/appStore.ts`、`types/index.ts` | 只加可选字段 | 房间状态可能增加 `ingest_mode`、诊断字段 | 所有新增字段可选，旧持久化数据无需迁移 |
| 后端房间事件 | `python-backend/handlers/room_handler.py` | 需要核心改动 | 预览、录制、直播按钮 init replay、对齐、切片、持续分析都从这里进入 | 所有 shared 逻辑受开关保护；失败 fallback legacy；事件协议不变 |
| 后端消息桥 | `python-backend/message_bridge.py`、`server.py` | 原则上不改或只加保护测试 | MSE 分片推送频率可能增加 | 保持 base64 payload 和广播接口；队列限流放在 shared preview subscriber |
| 后端持久化 | `python-backend/persistence.py` | 原则上不改 | `content_offset`、房间状态、切片数据不能迁移失败 | 不改已有字段语义；新增字段缺省可空 |
| 后端入口 | `python-backend/main.py`、`start.py` | 原则上不改 | shutdown 必须清理 shared 进程 | 通过 room handler shutdown hook 清理；入口只验证调用链 |
| 录制核心 | `lsc/core/services/recording_service.py` | 需要核心改动 | 录制先开时 shared dual-output 接入 | shared capture adapter 保持 legacy capture 形状；失败回退 `StreamCapture` |
| shared ingest | `lsc/core/services/shared_ingest.py` | 新核心模块 | 管理单上游、录制 sink、预览 sink、preview-only、重连和清理 | 录制和预览隔离；预览失败不影响录制；stop 逻辑区分 sink/subscriber/upstream |
| ingest registry | `lsc/core/services/ingest_registry.py` | 新核心模块 | 防止同一房间重复上游 | 以 room_id 为主键；线程安全；room cleanup 统一 stop |
| legacy MSE | `lsc/core/services/mse_streamer.py` | 只做兼容抽取 | shared 失败后仍靠 legacy MSE 保底 | 保留完整 legacy 能力；复用 fMP4 parser 但不改变对外事件 |
| fMP4 parser | `lsc/core/services/fmp4_segments.py` | 新/增强模块 | shared 和 legacy 分片一致性 | parser 单测覆盖 partial box、init、media、replay |
| 资源监控 | `lsc/core/services/resource_monitor.py` | 需要增加 counters | 需要确认一房间一上游是否真的生效 | 增加 shared/legacy 计数，不移除旧 CPU/内存指标 |
| 导出服务 | `lsc/core/services/export_service.py`、`lsc/exporter/clip.py` | 需要时间轴保护 | 预览先开再录制时，用户标记和录制文件起点可能不一致 | 使用 `recording_media_start_mono` 优先，缺失时 fallback 旧字段 |
| frame capture | `lsc/core/services/frame_capture.py` | 原则上不改 | AI/截图可能读录制文件或当前帧 | 不让它消费 preview segment；只保持现有输入路径 |
| GUI 多房间管理 | `lsc/gui/multi_room/manager.py`、`session.py` | 需要接入 shared adapter | 桌面录制路径和后端路径必须一致 | manager 使用同一 registry/adapter 语义；session 字段保持兼容 |
| 录制控制器 | `lsc/gui/pages/recording_controller.py` | 只做兼容验证 | UI 录制按钮不能因 shared 字段缺失失效 | 保持现有 start/stop 调用和状态字段 |
| 平台适配 | `lsc/platforms/*.py` | 只做命令参数复用验证 | 不同平台 headers/cookie/referer 不同 | shared FFmpeg 复用现有 stream URL 与 headers 转换；画质刷新后重建 |
| legacy recorder | `lsc/recorder/capture.py` | 原则上不改 | fallback 依赖它 | 不删除、不重命名、不改变公共方法 |
| 对齐算法 | `lsc/editor/audio_aligner.py`、`previewAudioAligner.ts` | 需要三房间 pairwise 和诊断 | 当前三房间只对齐两个、置信度低 | 两两计算可靠边，连通图推导所有房间；捕获不足输出原因 |
| 分析流水线 | `lsc/analyzer/*` | 只做守护验证 | 持续分析不能被 preview-only 或 shared 切换打断 | 仍读主房间录制文件；高光映射仍靠 `content_offset` |
| 配置文件 | `lsc/config.py`、`python-backend/settings.json` | 需要新增开关 | 默认开启会破坏现有用户 | 默认 false；配置缺失时按旧链路运行 |
| 房间配置数据 | `lsc/gui/multi_room/config/rooms.json` | 不主动改 | 用户现有房间数据不能被迁移破坏 | 不写入 shared 专属必填字段；新增运行态字段不持久化或可选 |
| 测试套件 | `tests/*` | 需要增加和扩展 | 防止只测 happy path | 分层测试：配置、parser、shared ingest、room lifecycle、frontend guard、对齐、导出、持续分析、资源监控 |
| 文档 | `README.md`、`docs/superpowers/plans/*` | 需要更新计划，README 可后置 | 用户需要知道开关和回退方式 | 计划先完整；README 只在方案稳定后补使用说明 |

## 当前代码状态

代码库中已经可以看到以下方案一相关对象，后续执行仍以测试和真实流验收为准：

- `D:\Project\直播切片多人\lsc\core\services\shared_ingest.py`：已有 `SharedRoomIngest`、`SharedPreviewHandle`、双输出 FFmpeg 命令、preview-only 命令、预览队列、录制 sink 停止逻辑。
- `D:\Project\直播切片多人\lsc\core\services\ingest_registry.py`：已有 shared ingest registry 和 preview stream registry。
- `D:\Project\直播切片多人\lsc\core\services\fmp4_segments.py`：已有可复用 fMP4 分段解析入口。
- `D:\Project\直播切片多人\lsc\core\services\recording_service.py`：已有 shared ingest 录制适配器和 legacy fallback。
- `D:\Project\直播切片多人\lsc\gui\multi_room\manager.py`：已有 Electron 实际录制路径中的 shared ingest 接入。
- `D:\Project\直播切片多人\python-backend\handlers\room_handler.py`：已有 MSE preview registry、shared preview attach、shutdown cleanup 和部分诊断接入。

执行时必须守住的关键门禁：

- 预览先开、再开始录制时，必须保证旧 legacy MSE preview 被迁移或替换为 shared preview，避免同房间两条上游长期并存。
- shared flag 开启时，未录制房间的预览必须有明确策略：优先 shared preview-only，失败再 fallback legacy，并用自动化测试固定该行为。
- 底部“直播”按钮必须同时覆盖 selected room 为空、player 缺失、buffer 为空、init 未 replay 和正常 live edge 跳转，诊断只增强日志，不改变前后端协议。
- 三个真实直播间的一键对齐、切片导出和持续分析是最终门禁；没有完成 Task 17 前，不允许把 shared ingest 设为默认开启。

## 全项目影响点与解决方案

| 影响点 | 涉及文件 | 风险 | 解决方案 | 验收方式 |
| --- | --- | --- | --- | --- |
| 配置开关 | `lsc/config.py`、`python-backend/settings.json`、`lsc-electron/src/pages/Settings/index.tsx` | 默认开启会影响现有用户；设置入口与后端配置不一致 | `shared_ingest_enabled` 默认 `false`；设置页只作为可选开关；后端启动时打印当前模式 | `tests/test_config.py`，手动切换开关验证 |
| FFmpeg 上游生命周期 | `lsc/core/services/shared_ingest.py`、`lsc/utils/process_launcher.py` | Windows 下进程残留、stdout/stderr 阻塞、网络参数误用到本地文件 | 统一用 `prepare_launch()`；stdout/stderr 非阻塞；仅 HTTP/HTTPS 加 reconnect/timeout 参数；停止时 terminate 后 kill 兜底 | `tests/test_shared_ingest.py`，本地 FFmpeg 烟测，检查无残留进程 |
| fMP4 分段解析 | `lsc/core/services/fmp4_segments.py`、`lsc/core/services/mse_streamer.py` | shared 和 legacy 解析不一致导致前端 MSE 失败 | 抽出 `Fmp4SegmentParser`，legacy 和 shared 共用同一解析规则；保留 init segment 缓存 | `tests/test_mse_segment_parser.py` |
| 录制启动 | `lsc/core/services/recording_service.py`、`lsc/gui/multi_room/manager.py`、`lsc/gui/pages/recording_controller.py` | shared 启动失败会导致不能录制；session 字段缺失影响 UI | shared 仅在开关开启时尝试；失败立即 fallback `StreamCapture`；保持 `RecordingSession.output_path/status/start_time/file_size_mb` | `tests/test_shared_ingest_integration_guards.py`、`tests/test_multi_room_manager.py` |
| 录制停止 | `shared_ingest.py`、`recording_service.py`、`manager.py` | 停止录制误杀预览；或预览继续时录制文件仍被写入 | `stop_recording_sink()` 只停止录制 sink；如果仍有 preview subscriber，切换到 preview-only；如果无预览则 stop room | lifecycle 单测 + 真实流“停录制保留预览” |
| 预览启动 | `python-backend/handlers/room_handler.py`、`lsc/core/services/ingest_registry.py` | 录制中打开预览又启动 legacy MSE，造成两条上游 | `enable_preview` 优先查询 active shared ingest；存在则 `attach_shared()`，不存在才 legacy；shared attach 失败再 legacy | `tests/test_room_handler_lifecycle.py` |
| 预览先开再录制 | `room_handler.py`、`manager.py`、`shared_ingest.py` | 当前最容易出现两条上游：legacy preview 已跑，录制又启动 shared dual-output | 增加录制成功后的 preview reattach：先创建 shared preview handle 并确认 `mse_init`，再停止旧 legacy handle；或在 shared flag 开启时预览一开始就走 preview-only shared | 新增 `test_start_recording_reattaches_existing_preview_to_shared` |
| 预览停止 | `VideoPreview.tsx`、`room_handler.py`、`ingest_registry.py` | 前端卸载预览时后端停止 shared upstream，导致录制中断 | `enable_preview false` 只 detach preview handle；`_stop_idle_shared_ingest()` 仅在无录制且无 subscriber 时停止 upstream | lifecycle 单测 + 手动关闭预览录制仍增长 |
| MSE init replay | `VideoPreview.tsx`、`mediaSourcePlayer.ts`、`room_handler.py`、`shared_ingest.py` | `mse_init` 早于前端 player 注册导致黑屏或直播按钮无效 | shared preview 缓存 `last_init_segment`；`request_mse_init` 对 legacy/shared 都调用 `replay_init()`；前端缓存 init 后挂载立即 feed | `tests/test_frontend_stability_guards.py`，手动重开预览 |
| 底部“直播”按钮 | `Workbench/index.tsx`、`mediaSourcePlayer.ts`、`VideoPreview.tsx` | player registry 缺失、selected rooms 为空、buffer 为空都会表现为按钮无反应 | 保持 `goLive()` 不改协议；增加前端诊断：选中房间数、player 是否存在、buffered 范围；后端确保 shared preview 有 init 和 media segment | `npx tsc --noEmit`，真实预览点击“直播” |
| 一键对齐音频捕获 | `previewAudioAligner.ts`、`VideoPreview.tsx`、`room_handler.py`、`lsc/editor/audio_aligner.py` | shared 切换导致音频轨短暂中断；低音量或静音造成“音频捕获不足” | 预览切换时发 `mse_reconnecting`，前端捕获前检查 readyState、audioTracks、RMS、buffered；后端日志记录 currentTime/buffer/ingest_mode；不传原始 PCM 到日志 | `tests/test_audio_aligner.py`，三房间真实对齐 |
| 三个及以上直播间对齐 | `audio_aligner.py`、`room_handler.py`、`Workbench/index.tsx` | 只对齐其中两个，最快房间仍领先 | 保持 pairwise 图算法：所有房间两两互相关，选择可靠边构建连通图，再统一换算到最慢房间；shared ingest 只改善音频来源稳定性，不改变 offset 符号 | `tests/test_audio_aligner.py` 中三房间/桥接边回归 + 真实三房间 |
| `content_offset` 存储 | `lsc/core/models.py`、`lsc/gui/multi_room/session.py`、`persistence.py`、`Workbench/index.tsx` | 迁移 shared 后误改符号会导致切片和持续分析整体错位 | 不改字段名、不改符号；前端仍通过 `set_content_offset` 写回；后端持久化保持原字段 | `tests/test_synced_continuous_analysis.py` |
| 切片导出 | `python-backend/handlers/room_handler.py`、`lsc/core/services/export_service.py`、`lsc/exporter/clip.py`、`ClipList.tsx` | 预览先开再录制时，录制文件 0 秒不等于用户标记起点 | 新增/使用 `recording_media_start_mono`，以首个录制媒体字节时间为导出基准；旧数据缺失时 fallback `recording_start_mono` | `tests/test_room_handler_lifecycle.py`、手动 mark in/out 导出 |
| 同步高光导出 | `room_handler.py`、`Workbench/index.tsx` | 主房间高光映射到其他房间时 offset delta 错 | 保持 `_map_highlight_to_room()`：`main.content_offset - target.content_offset`；不引入 shared ingest 时间基 | `tests/test_synced_continuous_analysis.py` |
| 持续分析 | `room_handler.py`、`lsc/analyzer/*`、`resource_monitor.py` | shared 写出的 growing MP4 不可读，或 FFmpeg 文件未 flush 导致分析卡住 | 录制输出使用可增长可读的 fragmented MP4；持续分析仍只读录制文件，不读预览分片；资源压力高时跳过本轮 | `tests/test_continuous_analysis_guards.py`，真实边录边分析 |
| AI/场景检测 | `lsc/analyzer/pipeline.py`、`round_detector.py`、`audio_analyzer.py` | 文件时间轴变化导致回合检测时间不准 | 分析时间轴统一使用录制文件时间；房间间映射只通过 `content_offset` | `tests/test_analyzer.py`、真实回合样例 |
| 资源监控 | `lsc/core/services/resource_monitor.py`、`LogViewer.tsx`、`SystemMonitor.tsx` | FFmpeg 数量减少但 CPU 可能因预览转码上升；监控误判 | 增加 `shared_ingests`、`recording_sinks`、`preview_subscribers`、`legacy_mse_streamers` 统计；日志打印 shared/legacy 模式 | `tests/test_resource_monitor.py` |
| 平台 headers/cookies | `lsc/platforms/*`、`lsc/platforms/base.py` | B站/抖音/虎牙等平台缺 Referer/Cookie 导致 shared FFmpeg 拉流失败 | shared 命令复用 `headers_to_ffmpeg_input_args()`；registry 创建时传入 room headers；画质刷新后重建 ingest | `tests/test_platform_adapters.py`，真实平台抽测 |
| 画质切换/刷新流地址 | `room_handler.py`、`manager.py`、`platforms/registry.py` | 旧 shared ingest 继续拉旧 URL | 切换画质、刷新流、断开房间时调用 `stop_room(room_id, reason)`；再按新 stream_url 建新 ingest | lifecycle 单测 |
| 房间移除/后端退出 | `room_handler.py`、`manager.py`、`server.py`、`main.py` | shared 进程残留或后台线程未停 | `shutdown_room_handlers()` 停 continuous task、preview handles、shared ingests、executors；房间移除同样走 stop registry | `tests/test_room_handler_lifecycle.py` |
| WebSocket 消息桥 | `python-backend/message_bridge.py`、`server.py`、`lsc-electron/src/services/websocket.ts` | shared 分片推送频率或大小变化导致 UI 卡顿 | 不改消息结构；继续 base64 二进制 payload；预览队列有上限，满了丢旧分片而不是阻塞录制 | `tests/test_message_bridge.py`，前端长时间预览 |
| 前端状态模型 | `lsc-electron/src/types/index.ts`、`appStore.ts` | 新字段破坏旧房间状态或持久化 | 新字段全部可选：`recording_media_start_mono`、`ingest_mode`、诊断字段；旧客户端忽略 | `npx tsc --noEmit` |
| 打包与启动 | `lsc-electron/electron/main.ts`、`preload.ts`、`build-installer.ps1` | 打包后 FFmpeg 路径、Python 路径、日志路径不同 | 不新增外部二进制依赖；继续使用现有 `ffmpeg_path` 和 `shutil.which("ffmpeg")` fallback；打包后烟测启动和录制 | 手动打包烟测 |
| 回退能力 | 所有 shared 接入点 | shared 部分失败后影响主流程 | 三层 fallback：配置关闭、启动失败 fallback legacy、预览 attach 失败 fallback legacy；日志写明 fallback 原因 | 自动化 fallback 单测 + 手动关闭开关 |

## 执行任务

### Task 1: 固化配置开关和回退门

**Files:**
- Modify: `D:\Project\直播切片多人\lsc\config.py`
- Modify: `D:\Project\直播切片多人\python-backend\settings.json`
- Test: `D:\Project\直播切片多人\tests\test_config.py`

- [ ] **Step 1: 写红测**
  - 断言 `shared_ingest_enabled` 默认是 `False`。
  - 断言 `shared_ingest_preview_queue_bytes` 有合理下限。
  - 断言配置文件可以覆盖 shared ingest 开关。

- [ ] **Step 2: 确认红测失败**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_config.py -q`
  - Expected: 缺字段或默认值不符合时失败。

- [ ] **Step 3: 实现配置字段**
  - 增加 `shared_ingest_enabled: bool = False`。
  - 增加 `shared_ingest_preview_queue_bytes: int = 2 * 1024 * 1024`。
  - 增加 `shared_ingest_preview_drop_policy: str = "drop_oldest"`。
  - 所有接入点只在开关开启时进入 shared path。

- [ ] **Step 4: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_config.py -q`
  - Expected: pass。

### Task 2: 统一 fMP4 分段解析

**Files:**
- Create/Modify: `D:\Project\直播切片多人\lsc\core\services\fmp4_segments.py`
- Modify: `D:\Project\直播切片多人\lsc\core\services\mse_streamer.py`
- Test: `D:\Project\直播切片多人\tests\test_mse_segment_parser.py`
- Test: `D:\Project\直播切片多人\tests\test_frontend_stability_guards.py`

- [ ] **Step 1: 写红测**
  - 测试 partial box 不会提前 emit。
  - 测试 `ftyp+moov` emit `init`。
  - 测试 `moof+mdat` emit `media`。
  - 测试 `last_init_segment` 可 replay。

- [ ] **Step 2: 确认红测失败**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_mse_segment_parser.py -q`

- [ ] **Step 3: 实现 parser 并接回 legacy**
  - legacy `MseStreamer` 不再复制分段逻辑，只调用 parser。
  - WebSocket event name 和 payload shape 不变。

- [ ] **Step 4: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_mse_segment_parser.py tests/test_frontend_stability_guards.py -q`

### Task 3: 建立 shared ingest 和 registry 生命周期

**Files:**
- Create/Modify: `D:\Project\直播切片多人\lsc\core\services\shared_ingest.py`
- Create/Modify: `D:\Project\直播切片多人\lsc\core\services\ingest_registry.py`
- Test: `D:\Project\直播切片多人\tests\test_shared_ingest.py`

- [ ] **Step 1: 写红测**
  - 同一 `room_id` 多次 `get_or_create()` 返回同一个 ingest。
  - `stop_room()` 会停止进程并从 registry 删除。
  - `stop_all()` 会清理所有 ingest。

- [ ] **Step 2: 实现**
  - `SharedIngestRegistry` 用 `RLock` 保护。
  - key 优先使用运行时 `room_id`，没有再 fallback 到 `room_url` 或 `stream_url`。
  - 暴露 `snapshot_counts()` 给资源监控。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_shared_ingest.py -q`

### Task 4: 实现录制 sink 与预览 sink 隔离

**Files:**
- Modify: `D:\Project\直播切片多人\lsc\core\services\shared_ingest.py`
- Test: `D:\Project\直播切片多人\tests\test_shared_ingest.py`

- [ ] **Step 1: 写红测**
  - 预览队列满时丢弃旧分片。
  - 队列溢出不改变 `recording_active`。
  - `last_init_segment` 始终保留最新 init。

- [ ] **Step 2: 实现**
  - 每个 preview subscriber 拥有独立 bounded queue。
  - 队列满采用 `drop_oldest`，保证直播低延迟。
  - 预览 callback 抛错只停止该 preview handle，不停止录制。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_shared_ingest.py -q`

### Task 5: 实现 FFmpeg shared 命令和启动探测

**Files:**
- Modify: `D:\Project\直播切片多人\lsc\core\services\shared_ingest.py`
- Modify: `D:\Project\直播切片多人\lsc\platforms\base.py`
- Test: `D:\Project\直播切片多人\tests\test_shared_ingest.py`

- [ ] **Step 1: 写红测**
  - shared dual-output command 只有一个 `-i`。
  - command 同时包含录制输出 path 和 preview pipe。
  - 本地文件 URL 不带 `-timeout`、`-rw_timeout`、`-reconnect`。
  - HTTP/HTTPS URL 才带网络 reconnect 参数。

- [ ] **Step 2: 实现**
  - dual-output：录制输出 copy 到 MP4，预览输出转为 H.264/AAC fMP4 pipe。
  - preview-only：只输出 H.264/AAC fMP4 pipe。
  - 启动探测等待录制文件出现并有字节，设置 `recording_media_start_mono`。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_shared_ingest.py -q`
  - Run: 本地 FFmpeg 合成视频烟测，确认录制文件与预览分片同时产生。

### Task 6: 录制服务接入 shared 并保留 legacy fallback

**Files:**
- Modify: `D:\Project\直播切片多人\lsc\core\services\recording_service.py`
- Modify: `D:\Project\直播切片多人\lsc\gui\multi_room\manager.py`
- Test: `D:\Project\直播切片多人\tests\test_shared_ingest_integration_guards.py`
- Test: `D:\Project\直播切片多人\tests\test_multi_room_manager.py`

- [ ] **Step 1: 写红测**
  - 开关关闭时仍创建 legacy `StreamCapture`。
  - 开关开启且 shared 成功时不创建 legacy `StreamCapture`。
  - shared 启动失败时 fallback legacy 并返回可用 session。
  - stop shared recording 时文件大小统计在停止 sink 后读取。

- [ ] **Step 2: 实现**
  - `RecordingService.start_recording()` 在开关开启时优先 `_start_shared_ingest_recording()`。
  - `_SharedCaptureAdapter` 满足现有 capture shape：`stop()`、`duration`、`check_health()`、`force_cleanup()`。
  - `MultiRoomManager.start_recording()` 使用同一 registry，保持 room 状态字段。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_shared_ingest_integration_guards.py tests/test_multi_room_manager.py -q`

### Task 7: 后端预览 registry 化，兼容 legacy 和 shared

**Files:**
- Modify: `D:\Project\直播切片多人\python-backend\handlers\room_handler.py`
- Modify: `D:\Project\直播切片多人\lsc\core\services\ingest_registry.py`
- Test: `D:\Project\直播切片多人\tests\test_room_handler_lifecycle.py`
- Test: `D:\Project\直播切片多人\tests\test_server.py`
- Test: `D:\Project\直播切片多人\tests\test_message_bridge.py`

- [ ] **Step 1: 写红测**
  - `enable_preview true` 在 active shared ingest 存在时 attach shared。
  - shared attach 失败时 fallback legacy MSE。
  - `request_mse_init` 对 shared/legacy 都能 replay。
  - `enable_preview false` 不停止正在录制的 shared ingest。

- [ ] **Step 2: 实现**
  - `_mse_streamers` 仍保留作为 backing dict，但访问统一走 `PreviewStreamRegistry`。
  - `PreviewStreamHandle` protocol 统一 `is_running/replay_init/stop`。
  - `mse_init/mse_segment/mse_error` payload 不变。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_room_handler_lifecycle.py tests/test_server.py tests/test_message_bridge.py -q`

### Task 8: 补齐“预览先开，再开始录制”的单上游迁移

**Files:**
- Modify: `D:\Project\直播切片多人\python-backend\handlers\room_handler.py`
- Modify: `D:\Project\直播切片多人\lsc\gui\multi_room\manager.py`
- Test: `D:\Project\直播切片多人\tests\test_room_handler_lifecycle.py`

- [ ] **Step 1: 写红测**
  - 构造 room 已有 legacy preview handle。
  - 开启 shared flag。
  - 调用 `start_recording` 成功创建 active shared ingest。
  - 断言旧 legacy preview handle 被 stop。
  - 断言 preview registry 中的新 handle 是 shared preview handle。

- [ ] **Step 2: 实现迁移 helper**
  - 新增 `_reattach_shared_preview_after_recording_start(room_id, room)`。
  - 只在 `shared_ingest_enabled`、room `preview_enabled=True`、shared ingest active 时执行。
  - attach shared 成功后 replay init，再停止旧 legacy handle。
  - attach shared 失败时保留或恢复 legacy preview，不能影响录制成功。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_room_handler_lifecycle.py::test_start_recording_reattaches_existing_preview_to_shared -q`
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_room_handler_lifecycle.py -q`

### Task 9: shared flag 开启时支持 preview-only 起步

**Files:**
- Modify: `D:\Project\直播切片多人\python-backend\handlers\room_handler.py`
- Modify: `D:\Project\直播切片多人\lsc\core\services\shared_ingest.py`
- Test: `D:\Project\直播切片多人\tests\test_room_handler_lifecycle.py`
- Test: `D:\Project\直播切片多人\tests\test_shared_ingest.py`

- [ ] **Step 1: 写红测**
  - shared flag 开启、未录制时 `enable_preview true` 可以创建 preview-only shared ingest。
  - 随后开始录制时，preview-only 进程会停止，dual-output 进程启动。
  - 进程切换后 preview 收到新的 `mse_init`，旧进程无残留。

- [ ] **Step 2: 实现**
  - `enable_preview` 在 shared flag 开启时优先 `get_or_create()` 并启动 preview-only。
  - `start_recording_and_preview()` 如果已有 preview-only 进程，先停止再启动 dual-output。
  - 切换期间广播 `mse_reconnecting`，成功后广播 `mse_init` 和后续 `mse_segment`。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_shared_ingest.py tests/test_room_handler_lifecycle.py -q`

### Task 10: 保证“直播”按钮可用

**Files:**
- Modify: `D:\Project\直播切片多人\lsc-electron\src\pages\Workbench\index.tsx`
- Modify: `D:\Project\直播切片多人\lsc-electron\src\services\mediaSourcePlayer.ts`
- Modify: `D:\Project\直播切片多人\lsc-electron\src\components\VideoPreview.tsx`
- Test: `D:\Project\直播切片多人\tests\test_frontend_stability_guards.py`

- [ ] **Step 1: 写守护测试**
  - 前端仍存在 `goLive()`。
  - Workbench 仍调用 player registry 中的 `player.goLive()`。
  - 后端仍支持 `request_mse_init`。

- [ ] **Step 2: 实现诊断，不改协议**
  - 点击“直播”时记录 selected room 数量、player 是否存在、buffered start/end。
  - `goLive()` 在 buffer 为空时重置 live-edge 标志并等待下一个 segment。
  - `VideoPreview` 挂载后主动 `request_mse_init`。

- [ ] **Step 3: 验证**
  - Run: `cd D:\Project\直播切片多人\lsc-electron; npx tsc --noEmit`
  - 手动：三个预览打开后拖动任一预览，再点“直播”，全部回 live edge。

### Task 11: 一键对齐兼容与诊断

**Files:**
- Modify: `D:\Project\直播切片多人\lsc-electron\src\utils\previewAudioAligner.ts`
- Modify: `D:\Project\直播切片多人\lsc-electron\src\pages\Workbench\index.tsx`
- Modify: `D:\Project\直播切片多人\python-backend\handlers\room_handler.py`
- Modify: `D:\Project\直播切片多人\lsc\editor\audio_aligner.py`
- Test: `D:\Project\直播切片多人\tests\test_audio_aligner.py`

- [ ] **Step 1: 写三房间 pairwise 回归**
  - 三个房间两两有可靠边时，所有房间都获得 offset。
  - 只有桥接边可靠时，仍能通过连通图推导第三个房间。
  - 快房间 offset 大于慢房间 offset。

- [ ] **Step 2: 实现捕获诊断**
  - 前端发送诊断字段：`current_time`、`buffer_start`、`buffer_end`、`ready_state`、`has_audio_track`、`rms`、`ingest_mode`。
  - 后端记录诊断元数据，禁止记录 `pcm_base64`。
  - 捕获不足时明确指出是无音轨、静音、buffer 空还是样本数不足。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_audio_aligner.py -q`
  - 真实三直播间：对齐后最快房间不再明显领先。

### Task 12: 切片导出时间轴保护

**Files:**
- Modify: `D:\Project\直播切片多人\lsc\core\models.py`
- Modify: `D:\Project\直播切片多人\python-backend\handlers\room_handler.py`
- Modify: `D:\Project\直播切片多人\lsc\core\services\export_service.py`
- Modify: `D:\Project\直播切片多人\lsc\exporter\clip.py`
- Test: `D:\Project\直播切片多人\tests\test_synced_continuous_analysis.py`
- Test: `D:\Project\直播切片多人\tests\test_room_handler_lifecycle.py`

- [ ] **Step 1: 写时间映射红测**
  - `mark_in_wallclock=120`、`recording_media_start_mono=100`、`content_offset=2` 时导出起点为 `18`。
  - 缺 `recording_media_start_mono` 时 fallback `recording_start_mono`。

- [ ] **Step 2: 实现**
  - Room/session 暴露 `recording_media_start_mono`。
  - shared ingest 在录制文件首字节出现时设置该值。
  - 导出统一使用 `recording_media_start_mono` 优先。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_room_handler_lifecycle.py tests/test_synced_continuous_analysis.py -q`

### Task 13: 持续分析与同步高光保护

**Files:**
- Modify: `D:\Project\直播切片多人\python-backend\handlers\room_handler.py`
- Modify: `D:\Project\直播切片多人\lsc\analyzer\pipeline.py`
- Modify: `D:\Project\直播切片多人\lsc\analyzer\round_detector.py`
- Test: `D:\Project\直播切片多人\tests\test_continuous_analysis_guards.py`
- Test: `D:\Project\直播切片多人\tests\test_synced_continuous_analysis.py`
- Test: `D:\Project\直播切片多人\tests\test_analyzer.py`

- [ ] **Step 1: 写守护测试**
  - 持续分析只读取主房间录制文件。
  - mapped highlights 使用 `main.content_offset - target.content_offset`。
  - shared ingest 不改变 highlight 的源时间轴。

- [ ] **Step 2: 实现**
  - growing MP4 可读性作为 shared 录制启动验收条件。
  - 分析任务不消费 preview segment。
  - 资源压力高时延迟分析，不影响录制和预览。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py tests/test_analyzer.py -q`

### Task 14: 生命周期清理、画质切换和重连

**Files:**
- Modify: `D:\Project\直播切片多人\python-backend\handlers\room_handler.py`
- Modify: `D:\Project\直播切片多人\lsc\gui\multi_room\manager.py`
- Modify: `D:\Project\直播切片多人\lsc\core\services\shared_ingest.py`
- Test: `D:\Project\直播切片多人\tests\test_room_handler_lifecycle.py`
- Test: `D:\Project\直播切片多人\tests\test_recording_reconnect_tick.py`

- [ ] **Step 1: 写红测**
  - remove room 会停止 shared ingest。
  - disconnect room 会停止 shared ingest。
  - quality switch 会停止旧 shared ingest。
  - upstream error 会停止录制 sink 并广播 preview error。
  - preview callback error 不停止录制 sink。

- [ ] **Step 2: 实现**
  - 所有 room cleanup 路径统一调用 `_stop_idle_shared_ingest()` 或 `stop_room()`。
  - upstream FFmpeg exit 写 `upstream_error`，preview handle 只回调一次 `mse_error`。
  - 画质切换后必须用新 URL/new headers 创建 ingest。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_room_handler_lifecycle.py tests/test_recording_reconnect_tick.py -q`

### Task 15: 资源监控与日志

**Files:**
- Modify: `D:\Project\直播切片多人\lsc\core\services\resource_monitor.py`
- Modify: `D:\Project\直播切片多人\python-backend\handlers\room_handler.py`
- Modify: `D:\Project\直播切片多人\lsc-electron\src\components\LogViewer.tsx`
- Test: `D:\Project\直播切片多人\tests\test_resource_monitor.py`

- [ ] **Step 1: 写红测**
  - `collect_system_stats()` 可以合并 shared ingest counters。
  - counters 至少包含 `shared_ingests`、`recording_sinks`、`preview_subscribers`、`legacy_mse_streamers`。

- [ ] **Step 2: 实现**
  - 后端诊断快照暴露 shared/legacy 计数。
  - 日志包含 room_id、ingest_mode、recording_active、preview_subscribers、drop_count。
  - 对齐日志禁止写 raw PCM/base64。

- [ ] **Step 3: 验证**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_resource_monitor.py -q`

### Task 16: 全量自动化回归

**Files:**
- Test only:
  - `D:\Project\直播切片多人\tests\test_config.py`
  - `D:\Project\直播切片多人\tests\test_mse_segment_parser.py`
  - `D:\Project\直播切片多人\tests\test_shared_ingest.py`
  - `D:\Project\直播切片多人\tests\test_shared_ingest_integration_guards.py`
  - `D:\Project\直播切片多人\tests\test_audio_aligner.py`
  - `D:\Project\直播切片多人\tests\test_server.py`
  - `D:\Project\直播切片多人\tests\test_message_bridge.py`
  - `D:\Project\直播切片多人\tests\test_room_handler_lifecycle.py`
  - `D:\Project\直播切片多人\tests\test_synced_continuous_analysis.py`
  - `D:\Project\直播切片多人\tests\test_continuous_analysis_guards.py`
  - `D:\Project\直播切片多人\tests\test_resource_monitor.py`
  - `D:\Project\直播切片多人\tests\test_frontend_stability_guards.py`
  - `D:\Project\直播切片多人\tests\test_multi_room_manager.py`

- [ ] **Step 1: 跑后端兼容矩阵**
  - Run: `$env:PYTHONPATH='D:\Project\直播切片多人'; pytest tests/test_config.py tests/test_mse_segment_parser.py tests/test_shared_ingest.py tests/test_shared_ingest_integration_guards.py tests/test_audio_aligner.py tests/test_server.py tests/test_message_bridge.py tests/test_room_handler_lifecycle.py tests/test_synced_continuous_analysis.py tests/test_continuous_analysis_guards.py tests/test_resource_monitor.py tests/test_frontend_stability_guards.py tests/test_multi_room_manager.py -q`
  - Expected: pass。

- [ ] **Step 2: 跑前端类型检查**
  - Run: `cd D:\Project\直播切片多人\lsc-electron; npx tsc --noEmit`
  - Expected: exit code 0。

- [ ] **Step 3: 跑本地 FFmpeg 烟测**
  - 使用 FFmpeg 生成 8 秒测试视频。
  - shared dual-output 同时产出录制文件和 fMP4 preview segment。
  - 停止录制后文件大小稳定。
  - preview-only 重启后无残留 FFmpeg 进程。

### Task 17: 三直播间真实流验收

**Files:** 不一定修改文件；验收日志保存到项目日志目录。

- [ ] **Step 1: 开关关闭基线**
  - `shared_ingest_enabled=false`。
  - 三个直播间预览、录制、直播按钮、一键对齐、切片、持续分析都按旧链路可用。

- [ ] **Step 2: 开关开启，录制先开**
  - 三个直播间开始录制。
  - 打开预览。
  - 每个房间 steady state 只有一个 shared upstream。
  - 关闭预览后录制继续增长。
  - 重新打开预览能收到 `mse_init` 和 `mse_segment`。

- [ ] **Step 3: 开关开启，预览先开**
  - 三个直播间先开预览。
  - 再开始录制。
  - 稳定后没有 legacy MSE 残留。
  - 点击底部“直播”按钮，三个房间都回 live edge。

- [ ] **Step 4: 对齐验收**
  - 对三个直播间执行一键对齐。
  - 日志中每个房间都有有效 capture diagnostics。
  - pairwise 结果覆盖三房间，offset 全部写入。
  - 最快直播间不再大幅领先其他两个。

- [ ] **Step 5: 切片和持续分析验收**
  - 手动 mark in/out 导出三房间同步切片。
  - 主直播间持续分析产出高光。
  - 高光按 offset 映射到其他房间。
  - 导出内容与预览标记位置一致。

- [ ] **Step 6: 故障回退验收**
  - 人为让 shared FFmpeg 启动失败。
  - 后端日志出现 fallback 原因。
  - legacy preview 和 legacy recording 仍可用。

## 分阶段落地门禁

| 阶段 | 开关范围 | 必须通过的门禁 | 失败处理 |
| --- | --- | --- | --- |
| Phase 0: 纯保护测试 | `shared_ingest_enabled=false` | legacy 录制、legacy 预览、直播按钮、一键对齐、切片、持续分析全部通过现有回归 | 不进入 shared 实现；先修复旧链路 |
| Phase 1: 本地 synthetic 流 | 单房间 shared 开启，使用本地 FFmpeg 测试源 | 一条上游同时生成录制文件和 MSE 分片；停止预览不影响录制；停止录制不残留进程 | shared 默认继续关闭，修复 `SharedRoomIngest` |
| Phase 2: 单真实直播间 | 单房间 shared 开启 | 录制先开、预览先开两种顺序都稳定；底部“直播”按钮有效；fallback legacy 可用 | 只保留诊断日志，不扩大到多房间 |
| Phase 3: 三真实直播间 | 三房间 shared 开启 | 每房间 steady state 只有一条上游；三房间 pairwise 对齐覆盖全部房间；最快房间不再大幅领先 | 回退到 legacy，对齐日志必须指出失败房间和捕获原因 |
| Phase 4: 切片和持续分析 | 三房间 shared 开启并录制一段完整素材 | mark in/out 导出准确；主房间持续分析高光能映射到其他房间；导出时间轴无整体偏移 | 禁止发布 shared 默认开启，优先修正时间轴 |
| Phase 5: 打包烟测 | 打包环境 shared 可选开启 | 后端启动、FFmpeg 路径、日志路径、预览、直播按钮、录制、回退均可用 | shared 开关保持隐藏或默认 false |

## 日志与排障要求

每次 shared/legacy 切换、fallback、预览 attach、录制 sink 启停、直播按钮点击、一键对齐捕获失败，都必须记录结构化上下文：

- `room_id`
- `room_name`
- `ingest_mode`: `legacy`、`shared_preview_only`、`shared_recording_preview`、`fallback_legacy`
- `recording_active`
- `preview_enabled`
- `preview_subscribers`
- `stream_url_host`
- `quality`
- `ffmpeg_pid`
- `recording_output_path`
- `recording_media_start_mono`
- `fallback_reason`
- `mse_buffer_start`
- `mse_buffer_end`
- `audio_capture_ready_state`
- `audio_capture_rms`
- `audio_capture_sample_count`

日志禁止记录以下内容：

- 原始音频 PCM。
- `pcm_base64`。
- Cookie 全量值。
- 带鉴权 token 的完整直播 URL。

敏感字段只记录是否存在、host、长度或 hash 前缀，避免日志泄露平台凭据。

## 回滚方案

1. 将配置中的 `shared_ingest_enabled` 改为 `false`。
2. 重启后端。
3. 录制恢复 legacy `StreamCapture`。
4. 预览恢复 legacy `MseStreamer`。
5. 已录制文件仍是普通 MP4/fMP4，旧切片和分析流程可继续读取。
6. `content_offset` 字段语义未变，不需要迁移历史房间数据。

## 完成标准

- 开关关闭时，旧功能行为不变，自动化测试通过。
- 开关开启时，同一个直播间录制和预览 steady state 只使用一条上游。
- 预览先开、再录制，不留下长期 legacy MSE 上游。
- 停止预览不影响录制。
- 停止录制时，如果预览仍开，预览能重连或继续播放，录制文件停止增长并可导出。
- 底部“直播”按钮对 shared/legacy 两种预览都有效。
- 三个及以上直播间一键对齐使用 pairwise 证据覆盖所有房间，`content_offset` 符号不变。
- 切片导出和持续分析使用 `recording_media_start_mono` 与 `content_offset` 后仍准确。
- shared 启动失败或 attach 失败时自动 fallback legacy。
- 后端兼容矩阵、前端 `tsc`、本地 FFmpeg 烟测、三真实直播间验收全部通过。

## 自检

- 影响面覆盖：配置、FFmpeg、录制、预览、MSE、直播按钮、一键对齐、三房间 pairwise、切片导出、持续分析、资源监控、平台 headers、画质切换、清理、WebSocket、前端状态、打包、回滚。
- 兼容契约明确：事件名、payload、`content_offset`、导出映射和持续分析入口均保持稳定。
- 风险均有解决方案：每个风险都有对应测试或真实流验收步骤。
