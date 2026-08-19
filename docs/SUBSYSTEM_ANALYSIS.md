# LSC 直播切片系统 · 子系统深度分析报告

> 生成方式：10 个独立子代理并行深读实际代码（每个代理从零开始、独立阅读），分两批各 5 个并行。
> 项目：LSC v3.0.22（Electron + Python + FFmpeg 多直播间录制切片系统），根目录 D:/Project/直播切片多人

## 编排核心与消息桥接（RoomOrchestrator + WebSocket 服务）

**实际阅读范围**：D:/Project/直播切片多人/lsc/core/orchestrator.py；D:/Project/直播切片多人/lsc/core/events.py；D:/Project/直播切片多人/lsc/core/session.py；D:/Project/直播切片多人/python-backend/server.py；D:/Project/直播切片多人/python-backend/broadcast_hub.py；D:/Project/直播切片多人/python-backend/ws_auth.py；D:/Project/直播切片多人/python-backend/main.py；D:/Project/直播切片多人/python-backend/handlers/room_handler.py（桥接触发入口，抽样阅读）

**职责**：在单一线程（RoomOrchestrator daemon 线程）上以 actor 模型编排最多 12 房间的会话生命周期：连接/重连/预览/录制/共享进样/磁盘守卫/音频对齐/导出，并通过分层心跳 tick 驱动。同时承载前后端桥接：WebSocket 服务器（asyncio，工作线程）接收前端 JSON 指令，经 orchestrator.call 同步投递到编排线程执行，结果回传；编排侧的异步状态更新经 BroadcastHub 线程安全 FIFO 入队，由广播协程消费推送前端。

**架构**：单例 actor：RoomOrchestrator 持有 RoomSession 字典 + _cmd_queue（有界 1024）+ 线程池（orch-worker×6）+ EventBus。call() 创建 _CallRequest 入队并阻塞等待 threading.Event（默认超时 10s）；fire-and-forget 走 submit()。app 默认无 GUI controller（_create_controller 回退 None），改用 shared_ingest_registry 协作。EventBus 为同步总线（emit 强制编排线程）。WebSocket 层：server.py 的 LSCWebSocketServer 用 websockets.serve，handle_client 做 origin/首帧 token 双认证→逐消息 dispatch（顺序处理保证 in-order）→按 {type}_response 包装返回；broadcast() 带 1s 慢客户端剔除。BroadcastHub 订阅 EventBus 事件并对接 asyncio Event 唤醒广播协程，drain 时做 last-value coalesce。main.py 双线程：主线程阻塞 wait，WS 线程（loop asyncio）跑 server + _broadcast_coroutine + 导出队列；parent 死亡 watchdog 触发 stop()。

**关键机制**：
- 同步调用原语 orchestrator.call()：_CallRequest 入 _cmd_queue，编排线程执行后 event.set() 唤醒；超时仅置 cancelled 抛 TimeoutError，结果未知（orchestrator.py:694/711）
- EventBus 线程约束：emit 仅限编排线程，跨线程用 submit() 代理，防止总线忙状态与乱序（events.py:46）
- 分层心跳 _on_global_tick：3s 触发，high=tick()/位置同步，medium=SizeUpdateJob+watchdog（按 _STAGGER_GROUPS 交错），low=磁盘<2GB 强停+流URL 过期重连（orchestrator.py:3805）
- BroadcastHub 分级丢包：队列 1000，满时 droppable 类型先丢/驱逐，terminal(_TERMINAL_TYPES) 必保，必要时扩容至 5000 并阻塞 5s（broadcast_hub.py:124）
- drain_merge_broadcasts 按 type(+room/job id) last-value coalesce，rooms_updated/system_stats 合并、recording_stopped/clip_* 分桶（server.py:402）
- rooms_updated 双级节流：_RoomsThrottle 300ms 合并窗口 + drain coalesce（room_handler.py:2791/2935）

**数据流**：入站：前端 WS 消息 → server.dispatch 顺序处理（令牌桶 100/s）→ handler 内通过 run_in_executor(_bridge_executor/thread) 调用 bridge.manager.call() 同步投递到编排线程执行（如 set_mark_in 走 _mark 写 wallclock），返回 dict 被包装为 {type}_response 并按 request_id 关联。出站：编排线程 tick/事件经 EventBus.emit → BroadcastHub 订阅回调 queue_broadcast(msg[+_seq]) 入队 + notify_broadcast（call_soon_threadsafe(event.set)）→ WS 线程 _broadcast_coroutine 用 drain_merge_broadcasts 排空并 coalesce → server.broadcast()（numpy 兼容 _json_dumps）推前端。核心消息：入站 set_mark_in/out、enable_preview、start_recording、align_preview_audio、export_clip、request_mse_init；出站 rooms_updated、mse_segment/mse_init（广播直接 binary 帧走 broadcast_bytes）、recording_stopped、export_progress、heartbeat（5s）、runtime_event。

**亮点**：
- 单编排线程 + 有界队列 + EventBus 线程约束，规避了所有房间共享状态的并发竞争，heartbeat/重连/磁盘守卫异常均有 _safe_on_deadlines 兜底不会杀死线程
- 分级广播丢弃与扩容策略：droppable 丢失/驱逐、terminal 终态必保，且排空重放竞争时丢弃幸存者而非抛异常（注释明确），避免 executor 线程被打死
- rooms_updated 双重节流（300ms 合并 + last-value coalesce），直击 12 房高频重渲染与日志膨胀痛点
- 端口回退、Origin+首帧 Token 双认证、慢客户端 1s 超时剔除、parent watchdog、优雅停机 join 带超时等健壮性设计完整
- call() 的『超时=结果未知』语义在注释与 handler 层（4312 附近注释）被显式贯彻，禁止盲目重试重复副作用

**问题/风险**：
- ports 不一致（待核实）：server.py 全局 `server=LSCWebSocketServer()` 默认 port=19876，主入口 main.py 另建 port=9876 的实例，代码存在两套 WS 引导路径（server.main() vs LSCWebSocketBackend），易留双端口/双广播源隐患
- call() 超时默认 10s，但 handler 大量用 run_in_executor(_bridge_executor, …) 包裹调用，桥接线程池(×8)与编排队列(1024)两层限流；高并发时 call 直接抛 too busy 而被 handler 捕获返回 error，无重试语义，需调用方自我处理
- EventBus emit 强制编排线程，但 BroadcastHub 的 queue_broadcast 可在任意线程调用并假定线程安全——依赖 Python queue 与 notify 的 loop.call_soon_threadsafe，若 loop 关闭仅 debug 级日志，广播可能静默丢失
- rooms_updated 节流 _RoomsThrottle 状态与 drain coalesce 两处独立实现，且 force 路径依赖取消 _flush task，代码在 _pending/_last_send_time 有手工重置，维护成本偏高（潜在竞态窗口）

**总结**：编排核心采用「单编排线程 actor + 同步 call 桥接 + 线程安全广播队列 + 双级 coalesce」分层模型，职责清晰、健壮性好；主要风险为双 WS 入口/端口不一致及多层限流叠加的调用方超时语义依赖。

## 平台适配层（多直播平台解析）

**实际阅读范围**：lsc/platforms/base.py；lsc/platforms/registry.py；lsc/platforms/resolver.py；lsc/platforms/models.py；lsc/platforms/probe.py；lsc/platforms/lease_manager.py；lsc/platforms/capabilities.py；lsc/platforms/candidate_health.py；lsc/platforms/failure.py；lsc/platforms/credentials.py；lsc/platforms/cookie_helper.py；lsc/platforms/redaction.py；lsc/platforms/signature_family.py；lsc/platforms/url_policy.py；lsc/platforms/recovery_policy.py；lsc/platforms/acceptance.py；lsc/platforms/bilibili.py；lsc/platforms/douyin.py；lsc/platforms/huya.py；lsc/platforms/douyu.py；lsc/platforms/kuaishou.py；lsc/platforms/xiaohongshu.py；lsc/platforms/weibo.py；lsc/platforms/direct.py；lsc/platforms/generic.py；python-backend/handlers/room_handler.py（入口）；lsc/core/services/recording_service.py（入口）；lsc/core/orchestrator.py（V2 接入/恢复）；lsc/config.py（feature gate）

**职责**：识别直播间/直链 URL 所属平台，抓取平台页面/API/SSR 数据，解析出可播放的流地址与画质候选、标题/主播等元数据；负责 Cookie 登录态获取与注入、签名流地址生成、鉴权失败分类；为录制/预览/进样提供统一流信息与画质选择，并作为多平台错误分类与恢复策略的来源。

**架构**：分层：①基础层 base.py 定义 PlatformAdapter(Protocol)/BasePlatformAdapter、统一 fetch_url/fetch_json/fetch_head、SSRF 防护 opener 与 FFmpeg 参数转换；②适配器层 9 个无状态单例（direct/douyin/bilibili/huya/kuaishou/douyu/xiaohongshu/weibo/generic），实现 can_handle/parse/(parse_with_context)；③注册层 registry.py 用 _URL_ROUTER host 路由 + 线性扫描回退，_ParseCache TTL 缓存(成功30s/失败10s)；④V2 未来层 resolver.py+models.py+probe.py+lease_manager.py 把解析归一为候选/探测/租约模型。对外接口：room_handler、recording_service、orchestrator 调用 parse_stream 或经 feature gate 后的 resolve_stream_v2→probe→lease；capabilities/get_platform_capabilities 供 runtime_health/shared_ingest 消费；acceptance.py 是独立验收运行器（CI/脚本）。

**关键机制**：
- SSRF 防护（base.py _is_private_ip/_validate_network_url/_SafeRedirectHandler，url_policy.py validate_public_url/validate_redirect_chain）：拒私有IP/环回/云元数据，fetch 与重定向每跳校验
- Host 快速路由+缓存（registry.py _URL_ROUTER/_ParseCache）：路由命中的 host 只探该平台适配器，缓存区分成功/失败 TTL 且检测签名 URL 过期
- 画质预设匹配（registry.py select_quality + QUALITY_PRESET_CANDIDATES）：按 原画/高清/标清/流畅 候选顺序取首个可用流地址
- 签名流生成（huya.py build_huya_live_query / douyu.py MD5 preview / bilibili _replace_qn / signature_family.py 指纹）：按平台反爬要求重建 wsSecret/auth/签名
- 结构化失败分类（failure.py FailureKind/classify_failure 正则规则表）+ LeaseManager 失败预算/刷新窗口
- V2 候选/探测/租约（models.py+probe.py+lease_manager.py）：candidate 指纹去重、ffprobe 真实探测选通、lease 生命周期管理，经 lsc/config.py platform_pipeline_v2_enabled(默认False) feature gate

**数据流**：前端 validate_room_url/refresh_stream_url → room_handler._parse_room_url → ①gate 关闭走 parse_stream(url)：_candidate_platforms_for_url 按 host 路由→adapter.can_handle→adapter.parse→StreamInfo→_ParseCache；经 _stream_info_to_room_info 成 RoomInfo 供 GUI/录制。②gate 开启走 resolve_stream_v2(ResolveRequest)→detect_platform→CredentialProvider.get_context→adapter.parse_with_context(带 scoped headers)→_candidates_from_stream_info/健康 enrich→_candidates；orchestrator._resolve_v2_stream_info 再 probe_candidates(ffprobe) 或 select_ingest_lease→select_stream_lease 出 StreamLease；核心层通过 resolve_result_to_stream_info 桥回 StreamInfo。失败经 failure.classify_failure→PlatformError，recovery_policy.recovery_action/mark_failed_candidate 决定 rotate_lease/invalidate_family/quarantine_cdn。WebSocket 消息：validate_room_url、refresh_stream_url、connect_room、set_preview_quality。

**亮点**：
- 纵深脱敏：redaction.py 对 URL query 敏感键/请求头/命令参数/to_legacy_dict 统一脱敏，StreamCandidate.redacted() 永不外泄签名 URL，LeaseManager 指纹仅 SHA 摘要
- 无状态+Feature Gate 双演进：适配器保持 Parse 无协议状态可模块级单例并发复用；V2 resolver/probe/lease 通过 config.platform_pipeline_v2_enabled(默认False) 门控，可安全灰度不毁legacy
- 真实媒体探测选通：probe.py 用网络 timeout/protocol_whitelist/read_intervals=+1 检测 timestamp 推进，避免选中 403 或静态URL；huya 单连接串行探测避免签名浪费
- 平台能力声明式（capabilities.py）+恢复策略(uses_ingest_probe/selection)解耦：平台名不泄漏进 orchestrator 分支
- 错误分类正则表(failure.py _RULES)覆盖中英文/HTTP状态，提取 Retry-After 上限300s，用户中文消息完整

**问题/风险**：
- 文档与代码不一致：registry.py 统一错误码实现为 ERROR_UNSUPPORTED_URL/offline/restricted/parse_failed，而 V2 PlatformError 另有全套 typed FailureKind；CLAUDE.md §4.3 只列 legacy 四码，未包含 V2 语义——文档滞后于实现
- 依赖重、单点：douyin.py 依赖私有 scripts/douyin_record.py 动态 import（_load_script_module 按路径加载），该第三方脚本缺失或签名失效即解析失败，且无法从 V2 缓存/租约路径隔离
- V2 默认关闭导致真实运行走 legacy：config.py platform_pipeline_v2_enabled 默认 False，probe/lease/健康历史(candidate_health)在生产默认配置下不生效，功能空转（待核实是否设计如此）
- 潜在线程/进程边界：cookie_helper.py 直接以 sqlite3 复制 Chrome/Edge Cookies 库与 DPAPI 解密(win32crypt)，依赖浏览器路径/文件锁，失败仅告警降级；win32crypt v20 失败回退文件 Cookie，可靠性平台相关
- acceptance.py 依赖真实平台网络与 FFmpeg，scripts/platform_acceptance.py/_run_lifecycle 强耦合 IngestSupervisor/SharedRoomIngest，验收失败无法完全离线复制

**总结**：平台适配层以 Protocol+Registry 无状态适配器为核心，配合 SSRF/脱敏纵深防护与双层(legacy StreamInfo + feature-gated V2 候选/探测/租约)架构，是 LSC 多平台解析与恢复策略的权威来源；V2 层默认关闭，实际运行走 legacy parse_stream。

## 录制引擎（多路并发录制与重连）

**实际阅读范围**：lsc/recorder/capture.py；lsc/recorder/segmented.py；lsc/recorder/manifest.py；lsc/recorder/session.py；lsc/recorder/timeline.py；lsc/recorder/assets.py；lsc/core/services/recording_service.py；python-backend/handlers/recording_handlers.py；lsc/utils/recording_repair.py；lsc/core/orchestrator.py（重连与并发编排，范围外但为实际核心）；lsc/gui/multi_room/manager.py

**职责**：负责多路（上限12路）直播流并发的 FFmpeg 录制生命周期：直播解析→启动录制→健康监控→自动重连→停止校验。含磁盘满防护、录制文件三层校验、moov 修复、以及可选的分段录制（MKV 分片+崩溃安全 manifest）与分段资产时间线映射，供后续分析/导出消费。

**架构**：WebSocket handler 层（recording_handlers.py）通过 server.on 注册 start_recording/stop_recording/repair_recording；经 asyncio Semaphore(2)+recording_executor 线程池把操作投递给 manager（MultiRoomManager→RoomOrchestrator.call 跨线程）。orchestrator.start_recording 做预检/刷新URL/建目录后分两条底层路径：非共享走 RecordingController 持有的 StreamCapture（capture.py，直接 Popen FFmpeg + -reconnect 参数 + 启动探测 + 三级优雅停止）；共享进样/分段走 SharedRoomIngest/IngestSupervisor（segmented=True 写 MKV 分片 + manifest，仅 feature-flag segmented_recording_v2 启用）。重连由 orchestrator._attempt_recording_reconnect + _run_reconnect_landing 实现。RecordingService 为面向测试/批量 API 的 Facade，仅其 preflight_check 被运行期使用；RecordingAsset/ManifestStore/TimelineMapper 供分析/导出消费。

**关键机制**：
- FFmpeg 自愈参数（capture.py:373）HTTP 流注入 -reconnect/-reconnect_streamed/-reconnect_delay_max 3/-timeout 20s，FFmpeg 层先做快速重连
- 启动探测（capture.py:_wait_for_startup_data）8s 轮询输出文件首字节，失败 force_kill+ERROR；无 moov 壳优化 OCR seek
- 三级优雅停止（capture.py:499）stdin 'q'→5s→terminate→3s→kill→5s，孤儿 PID 标 ERROR 防重试循环；stop_async 后台线程避阻塞
- 健康监控（capture.py:703 check_health）进程退出+连续6次文件不增长判卡死；orchestrator tick 每3s错峰（_STAGGER_GROUPS=3）并 ~12s 磁盘<2GB 强停
- 指数退避重连（orchestrator.py:3396）max=3 次、2s 起翻倍上限30s、is_recoverable_error 过滤 + 虎牙换线策略，前台 watchdog 触发
- 文件三层校验 validate_recording（capture.py:76）路径+>0.1MB+头部签名(ftyp/FLV/EBML)；recording_repair.repair_recording 用 -c copy +faststart 重封装 moov

**数据流**：前端 WebSocket start_recording{room_id,recording_spec} → (recording_handlers.py) recording_starting 去重 + recording_semaphore 限流(2) → run_in_executor(recording_executor) → RoomOrchestrator.start_recording：RecordingService.preflight_check 磁盘预检(8GB/路,重连降2GB) → _refresh_room_stream_for_recording 刷新CDN URL → 建房间子目录 → 非共享走 controller.start_recording_with_crf(StreamCapture.start Popen) 或共享进样 start_recording(分段写 manifest)；成功后读 get_recording_status 校验、写 recording_history、reaattach_shared_preview。停止/修复同理回 handler 返回 {success,error}，server.py 包装 *_response；停止与修复失败返 humanize_error。录制异常经 room_handler/tick watchdog 推 _attempt_recording_reconnect 后台落地（原地恢复→stop→刷新→重启，_reconnect_in_progress 自复位），recording_stopped/recording_queue 广播驱动前端。

**亮点**：
- 三层防御纵深：FFmpeg 内置重连(秒级)+系统级指数退避重连(2-30s)+URL 主动过期刷新(_STREAM_URL_REFRESH_THRESHOLD_SEC=60)，且区分可恢复/不可恢复错误与虎牙换线策略，避免死循环
- 磁盘满防护双闸：启动预检(8GB/路,带 ~/.lsc/output 回退链) + 运行中 ~12s 轮询<2GB 强停并广播 disk_full，防写坏系统
- 崩溃安全设计：分段录制 manifest 原子写+fsync+recover 幂等扫描，LegacyRecordingAsset 兼容视图让旧 MP4 无缝进入 manifest 契约；三级停止+孤儿PID标记防 FFmpeg 僵尸
- 跨层资源解耦：stderr 读取共享线程池(上限4)控制 Windows 每线程~8MB 栈；orchestrator tick 3s 错峰 + Semaphore(2) 防 12 路录制 I/O/HTTP 峰
- 时间线契约：recording_start_mono/media_start_mono + content_offset 墙钟映射，重连新 epoch 时清对齐组防复用陈旧 offset（orchestrator.py:2340）

**问题/风险**：
- RecordingService 基本只服务测试/批量旁路：运行期串联录制仅用其 preflight_check，start_recording/stop/check_health/_build_extra_args 等未走 12 路工作台真实路径，存在维护面与运行面双份逻辑的漂移风险（RecordingService 复用 CaptureResult 形状，与 _SharedCaptureAdapter 形状并不一致）
- 修复工具单点：recording_repair.repair_recording 用 subprocess.run capture_output 同步阻塞，timeout=600s 且经 recording_executor 排队，长时间修复会占用并发槽位的 executor（待核实是否影响其它录制操作）
- SegmentRecorder(segmented.py) 被 feature-flag 独立于主要运行路径：真正运行沿是 SharedRoomIngest/IngestSupervisor 自己的 build_segmented_recording_command + _finalize_segmented_manifest，segmented.py 的 start/stop 多数场景未走通，两套分段实现并存有分叉风险（待核实 acceptance 与 v2 是否同构）
- 重连状态机分散：可恢复性判断跨 capture.py/_friendly_ffmpeg_message、error_messages.is_recoverable_error、recovery_policy.should_force_recovery 三层，新增平台错误需多点同步修改才不致误判
- 12 路上限仅为 recording_executor/Semaphore 软约束的分流，真正上限依赖 FlFmpeg 并行进程稳定性与磁盘吞吐，无显式进程数强校验兜底（待核实超限是否被 handler 层 forcheck）

**总结**：录制引擎以 FFmpeg 自愈参数+系统级指数退避重连+磁盘/健康双守卫为骨架，通过三层层叠（handler→orchestrator 编排→StreamCapture/SharedIngest 进程）支撑 12 路并发，并提供分段 manifest 崩溃安全的资产化出口，设计稳健但存在运行面与 Facade 双份逻辑及分段实现分叉的技术债。

## MSE 预览与共享进样（fMP4 低延迟推流）

**实际阅读范围**：lsc/core/services/mse_streamer.py；lsc/core/services/shared_ingest.py；lsc/core/services/ingest_supervisor.py；lsc/core/services/ingest_registry.py；lsc/core/services/mse_sender.py；lsc/core/services/fmp4_segments.py；lsc/core/services/frame_capture.py；lsc/core/services/resource_monitor.py；lsc-electron/src/services/mediaSourcePlayer.ts；lsc-electron/src/hooks/useWebSocket.ts；lsc-electron/src/utils/mseBinary.ts；python-backend/handlers/room_handler.py(MSE 相关)；python-backend/server.py(broadcast_mse)；python-backend/mse_ws_frames.py

**职责**：将直播流实时转码为 fMP4 分片，经 WebSocket 二进制帧推送给 Electron 前端 MSE 播放器实现低延迟预览。支持两种进样架构：独立 MseStreamer 双进程、以及共享进样（Single upstream FFmpeg + 独立录制/预览 sink）。负责分片边界解析、init/media 段分发、画质/分辨率动态降级、资源压力分级、客户端背压暂停、断流自动重连与 init 段补发，保障录制与预览互不影响。

**架构**：分三层：①核心服务层 — MseStreamer(独立进程 fMP4)、SharedRoomIngest(共享上游+双 sink)、IngestSupervisor(生命周期/恢复串行化)、Fmp4SegmentParser(Box 分解)、PreviewSubscriber(有界队列)、MseSender(背压发送器，仅测试使用)、resource_monitor(压力分级)。②后端桥接层 — room_handler 的 _handle_mse_preview 按 shared_ingest_enabled/_shared_ingest_v2_enabled 选择双路径；_push_mse_segment 经 server.broadcast_mse→pack_mse_frame 发二进制帧。③前端层 — mediaSourcePlayer.ts(MsePlayer 喂 SourceBuffer)、useWebSocket 缓存+watchdog、mseBinary 解帧。跨线程：FFmpeg 回调→asyncio.run_coroutine_threadsafe 调 WS 协程；状态经 orchestrator.call 回编排线程。前端只消费 mse_init/mse_segment/preview_phase，上报 mse_backpressure/request_mse_init。

**关键机制**：
- fMP4 Box 分解：fmp4_segments.py 的 _extract_box_pair 按 ftyp+moov→init、moof+mdat→media 切片，mse_streamer 对超 512KB 强制切分
- 共享上游+独立 sink：shared_ingest 的 _dispatch_ts_batch 将同一 MPEG-TS 扇出到录制/预览两个独立 FFmpeg，单 sink 故障不清健康 sink(_stop_upstream_if_idle)
- 前端多级背压：backpressurePauseAt=10/ResumeAt=3→mse_backpressure 消息→room_handler._mse_push_paused 丢弃 media 段仍读管道防死锁
- init 竞态消除：后端 replay_init() 缓存 init 段，前端 request_mse_init 补发 + useWebSocket._mseInitCache 5min 缓存双保险
- 断流多段恢复：MseStreamer watchdog 15s 无数据强杀；shared _PREVIEW_STDOUT_STALL_SEC=15s；后端 _MSE_MAX_RECONNECT=3 指数退避；前端 _MSE_WATCHDOG_TIMEOUT_MS=10s
- 压力降级：_compute_preview_quality_params 按 active_mse_count≥3→854x480@20 / ≥4→640x360@15，critical 拒绝高成本任务

**数据流**：输入：enable_preview{mode:mse} → _handle_mse_preview 按配置选路径。共享路径 get_or_create SharedRoomIngest → _ensure_upstream_started（FFmpeg → pipe:1 MPEG-TS）→ _read_upstream_stdout_loop → _dispatch_ts_batch 按 TS_PACKET_SIZE=188 对齐分扇出到录制/预览 input 队列；预览 sink FFmpeg(scale+libx264) → fMP4 stdout → _read_preview_stdout_loop(Fmp4SegmentParser) → publish_preview_segment 入 PreviewSubscriber → SharedPreviewHandle.drain → 回调 _push_mse_segment → asyncio 调度 broadcast_mse(pack_mse_frame 二进制) → 前端 websocket 解帧(useWebSocket on mse_init/mse_segment) → VideoPreview.feedInit/feedMedia → MsePlayer.appendBuffer。反向：前端 mse_backpressure{pause|resume} → 后端 _mse_push_paused 置位；request_mse_init → replay_init() 补发；preview_phase 广播 streaming/error/idle。

**亮点**：
- 共享进样真正单上游：upstream FFmpeg 只拉一次远端流，录制/预览各自独立 sink FFmpeg 经 TS 管道消费，既省 CDN 连接又保证单 sink 故障隔离（_dispatch_ts_batch 按 generation 校验弃过期字节）
- 二进制帧传输：pack_mse_frame/tryParseMseBinaryFrame 用 MSE magic+kind+rid 头避免 base64 膨胀，比 docstring 描述的编码更高效
- 端到端背压可归因：前端 pending 阈值→WS 消息→后端推流暂停三处闭环，且暂停时仍读 FFmpeg 管道防止 OS 管道写满死锁
- init 竞态双保险：前端模块级缓存+后端 replay_init 补发，消除 mse_init 早于 rooms_updated 的黑屏；且 media 缓存(10段/64MB)重放避免挂载掉帧
- 资源压力感知降级与 critical 限流，量化到分辨率/fps，避免多路预览拖垮录制；resumePlayback/stall recovery 多种主线程卡顿自愈

**问题/风险**：
- 技术债：MseSender(mse_sender.py) 仅 tests 引用，生产实际走 binary 帧，属死代码，CLAUDE.md 部分文字仍描述 base64 编码路径（文档与实现不一致）
- 复杂度高：room_handler 的 _handle_mse_preview 同时承载 legacy/shared/force_restart/reconnect/code=0重试数条路径，嵌套 try/executor 分发，分支达数百行，维护成本大
- 可配置项多且默认 hidden：shared_ingest_preview_crf/preset 默认23/veryfast 仅体现在 build_preview_command，与 MseStreamer 硬编码 crf28/veryfast 不一致，两套画质基线并存（待核实前端无感知）
- Player 状态终态化：_handleError 置 error 后 feedInit/media 直接忽略，依赖用户/外部重开，无自动恢复（前端），需后端 mse_segment watchdog 配合

**总结**：MSE 预览与共享进样子系统通过独立/共享双路径将直播流实时转码为 fMP4 二进制帧低延迟推送给前端 MSE 播放器，具备端到端背压、init 竞态消除与多级断流恢复，设计扎实；主要技术债是生产未使用的 MseSender 与高度复杂的预览路径处理。

## 切片与时间线系统（标记 / 映射 / 导出三阶段）

**实际阅读范围**：python-backend/handlers/timeline_handlers.py；python-backend/handlers/export_handlers.py；python-backend/handlers/room_handler.py（set_mark_in/out、set_content_offset、align_preview_audio、_map_highlight_to_room、register 装配）；lsc/core/services/timeline_service.py；lsc/core/models.py（Clip/ExportOptions/RoomTimeSnapshot/TimelineContext/ClipSnapshot）；lsc/editor/audio_aligner.py（align_audio_map/compute_offset）；lsc-electron/src/utils/timelineCoords.ts；lsc-electron/src/pages/Workbench/components/ControlBar.tsx；lsc-electron/src/pages/Workbench/components/ClipList.tsx；lsc-electron/src/pages/Workbench/index.tsx（handleControlAddClip/handleConfirmExport/handleConfirmAndExport）

**职责**：承担直播切片全链路：①用户按 i/o 或拖拽时间线标记入出点（实时标记捕获 wallclock 墙钟，拖动不捕获）；②将多房间预览轴/录制轴统一到一条公共时间轴（公共轴 + RoomTimeSnapshot 双向 delta 换算）；③把公共入出点原子映射到各录制文件物理位置并生成 ClipSnapshot；④统一导出队列调度 FFmpeg 精确裁剪，广播进度/完成/失败，支持取消与并发限流。

**架构**：分层：前端(Workbench/index.tsx + ControlBar/ClipList + timelineCoords.ts 三轴换算) ↔ WebSocket ↔ python-backend/handlers（timeline_handlers 管时间线快照、export_handlers 管导出队列、room_handler 管标记/对齐）↔ 核心 lsc：timeline_service.py 持 TimelineContext 生命周期（纯内存单例，RLock 线程安全）、audio_aligner.py 计算 content_offset、models.py 定义 DTO。外部接口：WS 消息 set_mark_in/out、align_preview_audio、set_content_offset、create_clip_snapshot、export_clip(_by_id)、cancel_export、get_timeline；广播 timeline_ready/timeline_invalidated/clip_export_started/export_progress/clip_completed/clip_failed。跨线程经 bridge.manager.call() 入编排线程，导出 worker 池常驻消费 asyncio.Queue。

**关键机制**：
- 三阶段墙钟映射：export_start = mark_in_wallclock − recording_start_mono − content_offset（export_handlers._resolve_export_range），as 降级路径用固定 2.0s 预览延迟补偿
- 公共轴锚定：build_room_snapshots_from_align 用 align_mono−origin_mono−preview_current_time + 相对偏移建立 preview_to_common_delta，origin_mono=最早录制起点（timeline_service.py）
- 原子 TimelineContext：所有房间置信度≥0.3 且 required_room_ids 齐备才 create_timeline，clip_ready 仅当全部 recording_id 非空
- epoch 失效：预览重建 on_preview_epoch_change / 录制重连 on_recording_id_change 整体 invalidate_timeline 并广播 timeline_invalidated；ClipSnapshot 冻结旧 recording_id 仍可导
- 全局导出队列：export_handlers 常驻 4 worker + asyncio.Semaphore(1/2) 限流，Semaphore 热更新仅空队列&无在途时替换；支持 cancel 排队中/执行中
- 前端三轴换算 timelineCoords.ts：previewToCommon/commonToRecording 等，ControlBar windowStart 只允许用播放头同轴 elapsed（§8.7 契约）

**数据流**：i/o 按键→WS set_mark_in/out(live=true)→room_handler 写 room.mark_in/_wallclock=time.monotonic()→rooms_updated 广播。一键对齐：前端 AudioWorklet 采集 ~8s 预览音频→WS align_preview_audio→audio_aligner.align_audio_map 两两互相关+加权图→create_timeline→广播 timeline_ready，offset 存 room.content_offset。添加切片：公共轴选中区→WS create_clip_snapshot→timeline_service 逐房创建 ClipSnapshot（同 group_id）→返回 clip_ids。导出：批量 onSubmit 走 export_clip_by_id（有 snapshot）或 export_clip（manual，带 wallclock/rec_start/content_offset）→queue_export→_resolve_export_range→入队→worker 调 manager.start_export→FFmpeg -ss/-to→广播 export_progress/clip_completed/clip_failed。<br>AI 高光：主房分析→_map_highlight_to_room(用 media_start_mono 差 + content_offset 差) 映射副房→clip_queued 入列表。

**亮点**：
- 真实代码验证：所有关键换算建立在 time.monotonic() 单一时钟基座上，三路径（预览对齐/标记墙钟/录制起点）只做差值，不存在两套时钟漂移
- 原子性与回滚完整：create_clip_snapshot 任意房失败即 delete 已建 snapshots 整组回滚，导出排队前冻结 content_offset 快照避免重对齐污染历史切片
- Timeline 失效驱动模型清晰：预览重建/录制重连/重新对齐统一 invalidate + 广播，ClipSnapshot 与 TimelineContext 解耦，失效能保留冻结切片可导
- 线程安全严谨：TimelineService 全部方法 RLock 保护；align(MSE 大 PCM base64 20MB/路由限)——互相关 FFT 卸载到线程池避免阻塞 WS 事件循环
- 导出队列防御完整：6h 兜底超时防护 worker 永久占用槽位、精确定位/近似定位分级降级并在前端提示可能偏差数秒

**问题/风险**：
- 死代码：audio_aligner.align_rooms（从录制文件提音频）仅在测试/文档引用，业务全走预览流 align_audio_map，与 CLAUDE.md 断言一致需清理或明确废弃
- 双通道对齐实现：room_handler.handle_align_preview_audio 与 alignment_handlers.py 各有一套（后者同样 create_timeline），存在两份并行对齐逻辑，逻辑重复有漂移风险（待核实二者是否都被前端调用）
- export_handlers 内 _deferred_export_jobs 明确标注死代码（无人 append），与 room_handler 内的延后列表重复，容易误导后续接线
- session-scope TimelineService 纯内存、不跨重启，且无定时清理——长期挂机若 timeline_id 无限增长且不失效会累积内存（需确认是否由 epoch 事件驱动回收）
- create_clip_snapshot 对 mark_in/out 无时间上限校验，仅校验 0<start<end，越界后依赖导出端 -ss 软性处理

**总结**：切片子系统以 time.monotonic() 单一时钟为锚，用 TimelineContext+ClipSnapshot 实现「预览轴/录制轴→公共轴」双向映射与精确导出，架构分层清晰、epoch 失效与原子回滚设计严谨，但存在 align_rooms、双对齐通道、延后导出等死代码/重复实现需收敛。

## 音频对齐子系统（多视角 FFT 互相关）

**实际阅读范围**：lsc/editor/audio_aligner.py；python-backend/handlers/alignment_handlers.py；python-backend/handlers/room_handler.py（register_room_handlers 内 4705-5010 死代码区 + 8837 注册点 + 1408/1421/6589/6640 映射消费）；python-backend/server.py（on() 覆盖语义 + 响应包装 211-246）；lsc/core/services/timeline_service.py（build_room_snapshots_from_align / create_timeline）；python-backend/handlers/timeline_handlers.py；lsc-electron/src/utils/previewAudioAligner.ts；lsc-electron/src/pages/Workbench/index.tsx（captureAndSendAlignment 1970-2087 及 align_preview_audio_response 1771-1824、后台复核 2175-2224）

**职责**：对多直播间预览流进行内容级时间对齐：前端用 Web Audio API(AudioWorklet)捕获各房<video>约8秒16kHz PCM→base64→WS送给后端；后端经FFT互相关/瞬态包络/跨语言一致性计算各房相对"最慢基准房"的content_offset；按信任阈值0.3写回RoomSession(offset+align_group_id)，构建并失效公共时间轴(TimelineContext)；持续分析期间每10分钟后台复核、finalizing时立即复核，保证副房高光映射使用新offset。

**架构**：四层联动。①前端 previewAudioAligner.ts 单例：AudioWorklet('pcm-recorder')+captureStream回退ScriptProcessor，静音检测+峰值归一化+抗锯齿降采样到16kHz，base64发送。②后端 alignment_handlers.py 注册 'align_preview_audio'/'set_content_offset'：解码校验后把 align_audio_map 投到 recording_executor 线程池执行（asyncio.wait_for 20s），结果经 bridge.manager.call 跨线程写 RoomSession 及构建 timeline。③算法核心 lsc/editor/audio_aligner.py：compute_offset 原始波形相关 + _compute_transient_envelope_offset 瞬态包络 fallback + 跨语言一致性升格；≥3路时 align_audio_map 全两两组合建图(_PairwiseEdge)，连通分量+加权最小二乘(_solve_component_offsets)求全局偏移。④timeline_service.build_room_snapshots_from_align 生成 preview/recording 双轴 delta，create_timeline 原子建公共轴。外部接口仅为 WS 消息；align_rooms/extract_audio_pcm(录制文件路径)为死代码，仅有测试调用。

**关键机制**：
- FFT互相关 compute_offset：用 irfft(rfft(ref)*rfft(other[::-1])) 等价 full 卷积互相关，抛物线插值 _parabolic_interpolation 亚毫秒精度；audio_aligner.py:148/171-201
- 跨语言一致性升格 _CROSS_LANGUAGE_*：要求波形分≥0.015、包络分≥0.35、两候选偏移差≤60ms、峰值比≥1.8 同时成立才升格，禁单纯降阈值；audio_aligner.py:347-371
- 全局两两对齐 align_audio_map/_solve_component_offsets：≥3房全组合、可靠边(_CORRELATION_THRESHOLD)构连通分量、加权最小二乘求相对基准偏移，免单参考被脏音频拖垮；audio_aligner.py:518-628
- 可信阈值0.3建组：trusted≥2房才写 room.content_offset+align_group_id=align_{ts} 并创建/失效 TimelineContext，不足则清除对齐组并精度标记 buffer_only；alignment_handlers.py:224-323
- 双轴锚点 build_room_snapshots_from_align：preview_to_common_delta=(align_mono-origin_mono-preview_current_time)+rel_delta，录制轴只含相对偏移；timeline_service.py:44-113
- 后台漂移复核：持续分析 running 时每10min refreshAlignment(仅全房在直播沿，allowedLag≥offset+1.5)，finalizing 立即复核一次；Workbench/index.tsx:2175-2224

**数据流**：用户点"一键对齐" → Workbench captureAndSendAlignment 逐房 captureAudio(8s) → align_preview_audio {rooms:[{room_id,sample_rate:16000,pcm_base64,diagnostics:{current_time,…}}]} → 后端 handle_align_preview_audio 校验(≤64房,20MB/房,采样率8k-48k一致,1-15s) → recording_executor 跑 align_audio_map → 返回 offsets/scores/reference_room_id → trusted≥2 时跨线程写 RoomSession(content_offset,align_group_id) + build_room_snapshots_from_align→create_timeline → 广播 timeline_ready → 返回 align_preview_audio_response(含 offsets/timeline)。前端据此 applyOffsetWithDriftCorrection；导出时 room_handler 6589/6640 用 content_offset 差值把主房回合映射到副房。旧路径 set_content_offset 由前端回传（现仅作兼容）。基线响应 server.py:223-226 包装成 {type:xxx_response}。

**亮点**：
- 多级置信防线：主波形相关(≥0.3)→瞬态包络(需 peak_ratio≥3.0)→跨语言一致性(双证据同lag)三级递进，避免无关音频/环境噪声强行对齐；compute_offset 设计严谨
- 多房图优化：≥3房不用单一参考，全两两建图+加权最小二乘，容忍个别房间脏音频；低分边经连通分量残差一致性和支持度校验才作桥接边纳入
- 双轴锚点同步：把预览PTS(currentTime)基座与录制轴(media_start_mono)统一到公共轴(origin_mono=最早录制起点)，避免播放头与切片错位及启动长时基问题
- 阈值与输入防御完备：可信建组0.3、输入校验(采样率/时长/大小/重复房)、20s计算超时(早于前端30s watchdog)、计算卸载线程池防阻塞WS
- 前端采集健壮：AudioWorklet+CSP回退ScriptProcessor+共享MediaElementSource优先，静音检测/峰值归一化/降采样，后台复核不打扰观看(无声采集)

**问题/风险**：
- 死代码并存：room_handler 内 4705-5010 保留整套旧 handle_align_preview_audio/set_content_offset，靠 register 顺序后注册覆盖生效(server.on 字典覆盖)；有人改动旧副本会误改无效代码，维护风险高
- align_rooms/extract_audio_pcm 录制文件路径仅被测试调用，运行时永远走 preview_audio；文档§5.3已指出，属已知死代码，但保留占用维护与测试面
- set_content_offset 作为独立WS handler仍存在，与 align_preview_audio 内建写back并存两条写入路径，语义重叠（虽均写 room.content_offset），存在被旧前端误用致 offset 无 align_group_id 的隐患（待核实实际前端是否仍调用）
- 跨语言升格的最小证据阈值(波形0.015/包络0.35)为经验值，offline/低音量场景未验证稳定性；consensus_score=max(0.3,…) 会人为抬高0.3可信线而实际两特征分数偏低，可能高估置信度（待核实）
- 对齐成功但 create_timeline 失败的多次尝试仅清除对齐组并返回 buffer_only，无自动重试/重收集机制，需用户重按一次对齐

**总结**：预览流驱动的多阶段FFT互相关音频对齐子系统已在后端充分落地(多房图优化+双轴锚点+信任建组)，仅录制文件路径与旧handler为死代码，需维护时注意修改 alignment_handlers.py 而非旧副本。

## AI 持续分析（Valorant OCR 回合检测）

**实际阅读范围**：lsc/analyzer/valorant_ocr_rounds.py；lsc/analyzer/ocr_detector.py；lsc/analyzer/ocr_accel.py；lsc/analyzer/pipeline.py；lsc/analyzer/scene_analysis.py；lsc/analyzer/sound_detector.py；lsc/analyzer/registry.py；lsc/analyzer/base.py；lsc/analyzer/generic_plugin.py；lsc/analyzer/valorant_plugin.py；python-backend/handlers/analysis_handlers.py；python-backend/handlers/room_handler.py（关键函数：_continuous_analysis_loop/_continuous_valorant_worker/_export_and_broadcast/_auto_export_highlights/_analyze_scene_or_rounds 及常量区）；lsc/core/services/runtime_health.py

**职责**：对主房录制文件做边录边分析的「持续分析」：按增量预算抽帧，纯 OCR（顶部计分板+回合计时器、中央横幅）通过相位状态机 FSM 检测 Valorant 回合的入/出点，跨窗口持久化 FSM/锚点状态；产出回合入 6fps 切片列表并映射到多副房；同时承担录制后全量文件分析与 scene 通用分析回退；运行时输出 OCR 加速后端（auto/dml/cuda/cpu）选择与 FFmpeg 硬解抽帧。核心生产者 worker 在后台循环执行 detect_valorant_rounds_ocr。

**架构**：插件范式分层：base.py 定义 AnalyzerPlugin 无状态协议与 ScanWindow/capabilities；registry.py 按 game 注册 Generic/Valorant 插件并提供默认导出值；valorant_plugin.py 经 compute_valorant_scan_budget 计算增量窗口（回看30s+自适应追赶45-480s），统一调用 valorant_ocr_rounds.detect_valorant_rounds_ocr 纯 OCR 检测器。检测器内部：extract_frames_cancellable 子窗口(≤60s)分块抽帧@1fps→OCR 双区域读信号→相位判定（锚点/冻结/结算抑制）→相位循环先验平滑→OcrRoundFSM.feed 状态机闭合回合→边界±3s@10fps 密扫精修。ocr_detector/ocr_accel 是相对独立分支（击杀检测、加速探针），持续分析主路径仅用其 _get_ocr/create_ocr/ffmpeg_hwaccel_args。后端：WebSocket handler(analysis_handlers)只做启停/状态/精修，真正的循环在 room_handler._continuous_analysis_loop（产消模式）+ _continuous_valorant_worker（后台 worker 持 _analysis_semaphore）+ _export_and_broadcast/_auto_export_highlights。runtime_health 与 OCR 检测无直接耦合，仅提供平台摄入健康投影。对外接口：WebSocket 消息 + clip_queued/highlight_stream/continuous_highlights/continuous_analysis_status 广播。

**关键机制**：
- 相位状态机 OcrRoundFSM.feed(WAIT/PREP/COMBAT/SETTLE)：入点=combat，出点契约=真·下回合准备→vision_confirmed，next_combat/open_tail→pending（valorant_ocr_rounds.py:OcrRoundFSM._close）
- 相近相似/循环两条 OCR 先验：计时器外推(last_timer)与冻结读数忽略、结算后 post_settle_hold/gap 抑制残余钟，防回放残留误开回合（valorant_ocr_rounds.py 主扫描循环）
- 跨窗口状态持久化于 state['ocr_runtime_state']（ocr_fsm/last_timer/combat_anchor/score_pending 等），文件切换时清空防静默漏检（room_handler._continuous_analysis_loop + valorant_ocr_rounds 尾部回写）
- 产消循环：主循环期建 scan_requested，worker 持 _analysis_semaphore 执行，scan_result_container 传结果、scan_done_event 唤醒；worker 崩溃重建≤3次、超时指数退避、压力让路/降级追赶（room_handler:7206/6890/_WORKER_MAX_RESTARTS）
- 增量回合合并 _merge_round_windows 按 round_key/时间重叠去重，新窗覆盖旧边界，OCR 回合优先不被纯音频覆盖（room_handler.py:1880）
- 自适应 OCR 加速 run_probe_if_needed：微基准(ms)探针选 dml/cuda/cpu 并缓存 7 天，FFmpeg 抽帧 hwaccel d3d11va/cuda 失败回退软解（ocr_accel.py:86,276,346）

**数据流**：前端发 start_continuous_analysis{main_room_id,target_room_ids,mode:'valorant_round',game:'valorant'} → analysis_handlers.handle_start_continuous_analysis 校验录制状态/任务排他 → asyncio.create_task(_continuous_analysis_loop)。循环内：ffprobe/码率估算取 current_dur（录制中墙钟为准留15s写缓冲）→ compute_valorant_scan_budget 得 scan_range → state['scan_requested']=True → worker 持 semaphore 在 _ai_executor 调 ValorantAnalyzerPlugin.scan_window → detect_valorant_rounds_ocr 抽帧/OCR/FSM 产 closed_rounds（粗边界）→ 写 scan_result_container + set(scan_done_event) → 主循环消费：_merge_round_windows 去重 → _export_and_broadcast 广播 highlight_stream(逐回合)/continuous_highlights(全量+多房映射) → _auto_export_highlights 调 _map_highlight_to_room(recording_start_mono+content_offset) 映射副房 → list_only 广播 clip_queued(export_deferred) 入切片列表；vision_confirmed 待收尾 flush 后 queue_export，pending 留用户 confirm_highlight_clip。状态经 continuous_analysis_status 每 tick 广播，完成/失败发 continuous_analysis_complete。录制停止→finalize 扫描→completed。注：server.on 用 handlers[type]=fn 覆盖式注册。

**亮点**：
- 自动化生产格局完整：产消解耦+worker崩溃重建(≤3次)+压力让路/降级追赶+超时指数退避+文件切换游标复位，适配边录边分析实时性（room_handler 全链路）
- OCR 先验工程扎实：冻结读数/结算抑制/两帧确认/中段切入组合防误判，纯 OCR 去掉 ONNX 大模型与音频钟声依赖，子窗分块把帧驻留内存从 ~330MB 降到 ~40MB
- 抽帧到 640×360 + rawvideo 内存管道直通 numpy + GPU scale 回退软解，兼顾吞吐与兼容（valorant_ocr_rounds.extract_frames_cancellable）
- 多房映射有对齐组(align_group_id)/副房停录时长越界/epoch 失效等完整防御，主副房解耦失败回退不吞整轮结果（_auto_export_highlights/_merge_round_windows）
- analyzer 层纯 OCR 检测器与 room_handler 回合合并/入列出点契约(boundary_source/round_key/confirm_status)严格对应，边界稳定 key(round-{start/10})吸收 OCR 漂移

**问题/风险**：
- 代码重复+注册覆盖风险：room_handler.py L8382/8568 仍自带 @server.on('start_continuous_analysis'/'stop_continuous_analysis') 实现，但 register_analysis_handlers(L8887) 在函数体后段执行，server.on 用 handlers[type]=fn 覆盖注册，room_handler 两份实为死代码且双份易漂移（已读 server.py:93 确认覆盖语义）
- detect_valorant_rounds_ocr 非严格纯函数：函数尾部读写调用方传入的 state/runtime_state dict（含读 last_processed_ts、回写 ocr_fsm/combat_anchor/score_pending 等 20+ 键），与注释『会话状态放 state dict，禁止写实例字段』的无状态契约有张力，共享状态全靠调用约定（valorant_ocr_rounds.py:639-940）
- scene 回退链路（scene_analysis.run_scene_analysis + 音频 RMS/OCR 补充）与 sound_detector 属遗留分支，持续分析主路径不再使用但仍保留近 900 行；generic_plugin.scan_window 恒返回 []，plan 只更新游标，是明显技术债
- worker 超时结构复杂：run_in_executor 外层再包 asyncio.wait_for(shield(fut)) 两层嵌套，超时后仍须等线程经 cancel_check 释放 semaphore，TimeoutError 语义易误读、急停路径易延滞（room_handler.py:6993-7021）

**总结**：持续分析子系统以纯 OCR+相位状态机精确切分 Valorant 回合，产消双向循环与多房映射防御设计扎实、实时与精度平衡良好；主要技术债是 room_handler 与 analysis_handlers 的 handler 重复注册死代码及 scene/sound 遗留分支。

## 导出与剪映（FFmpeg 切片导出 + JianYing 草稿生成）

**实际阅读范围**：lsc/exporter/clip.py；lsc/exporter/jianying_draft.py；lsc/core/services/export_service.py；python-backend/handlers/export_handlers.py；python-backend/handlers/jianying_handlers.py；python-backend/handlers/timeline_handlers.py (export_clip_by_id, create_clip_snapshot)；python-backend/handlers/room_handler.py (queue_export 委托层/注册), lsc/core/orchestrator.py (start_export/cancel_export, 3164-3224)；lsc/gui/pages/recording_controller.py (ExportWorker, 90-188)；lsc/utils/cancellable_ffmpeg.py；lsc/utils/gpu_ffmpeg.py；lsc/utils/error_messages.py；lsc/utils/error_stats.py；python-backend/server.py (on/broadcast 契约)

**职责**：负责将录制的直播文件按时间线精确切成 MP4 片段（FFmpeg 编码/直拷），管理全局异步导出队列与并发限流、进度回推、取消、任务状态补偿广播；对接多房间音频对齐坐标做时间轴映射；并可将多房录制+切片生成剪映(JianYing)草稿工程。对外通过 WebSocket handler 收导出/剪映指令、经房间编排器回调 FFmpeg、广播进度/结果给前端。

**架构**：三层：①WS handler 层（export_handlers.py 全局 asyncio 导出队列 + jianying_handlers.py + timeline_handlers.export_clip_by_id），统一入队；②编排层 orchestrator.start_export→RecordingController.create ExportWorker；③底层 ClipExporter（FFmpeg 调用）。外部接口：前端 WS 消息入、server.broadcast 出（自动加 _response 后缀）；跨线程经 bridge.manager.call；进度经 asyncio.run_coroutine_threadsafe 回 WS 循环。另存在独立 ExportService（线程池封装，仅测试使用，未接入本队列）与 JianYing 生成链（layer：jianying_handlers→build_session_draft→pyJianYingDraft）。

**关键机制**：
- 统一导出队列：export_handlers._export_queue(100)+_EXPORT_WORKERS(4)+_export_semaphore限制 export_max_concurrent(1|2)，热更新用 _export_semaphore_limit 比对，禁止读 _waiters（禁区有测试守护）。
- 墙钟映射：_resolve_export_range 用 mark_in_wallclock-room_recording_media_start_mono-content_offset 求精确导出区间，无快照降级减去 _PREVIEW_LATENCY_FALLBACK=2.0 得 approximate 精度。
- 编码决策：clip.py Copy 模式遇滤镜/start_sec>0 自动回退 NVENC/libx264；GPU 滤镜(scale_cuda)失败由结果触发 _export_cpu_fallback；_optimize_profile_filters 探测源流移除等值 scale/fps 滤镜。
- 原子写入+进程护卫：uuid 临时文件写入后 os.replace；watchdog 按分辨率/软硬编动态超时 kill；进度 _progress pipe:1 解析 out_time_us 节流(≥1% 或≥200ms)。
- 终态兜底：ExportWorker.run try 包裹 on_done 必触发；_process_export_job_impl 用 done_event+6h wait_for；_set_export_job_state 缓存状态并入 get_export_job_status 供前端补丢广播。
- 剪映草稿：build_session_draft 公共轴 origin=min(delta)，按房分轨+文本标签，SegmentOverlap 防御跳过，失败 shutil.rmtree 清半成品目录防剪映损坏。

**数据流**：前端 send `export_clip`(或 `export_clip_by_id`/`create_clip_snapshot`) → handler parse mark_in/out_wallclock+content_offset → queue_export 内 _resolve_export_range 求起始/精度 → _build_export_profile → job 入 _export_queue → worker 取号持 _export_semaphore → run_in_executor(bridge.manager.call) 到编排线程 → manager.start_export→ExportWorker.run→ClipExporter.export_clip 起 FFmpeg(-ss/-t/-movflags faststart)，-progress pipe:1 解析进度 → 回调经 asyncio.run_coroutine_threadsafe 广播 `export_progress`/`clip_completed`/`clip_failed`/`clip_export_started`/`export_overall_progress`；取消走 `cancel_export`→manager.cancel_export→proc.kill。剪映：`generate_jianying_draft`→jianying_handlers 组装 RoomDraftSource/ClipDraftSource→build_session_draft 生成草稿目录。

**亮点**：
- 导出队列三层防御扎实：常驻 worker 池 + semaphore 限流 + 6h done_event 兜底超时 + job 状态缓存供 get_export_job_status 补偿丢广播，杜绝槽位挂死或进度丢失。
- 编码智能：Copy 回退/GPU 滤镜失败 CPU 兜底/等值 scale/fps 滤镜移除（_optimize_profile_filters，减少 GenLoss），AVC 硬解输入参数按 codec 精确匹配（cuvid_decoder_name+input_hwaccel_args）。
- 路径与进程安全：文件名消毒(非法字符+保留字 CON/NUL)+realpath 防路径遍历+uuid 临时文件原子替换；watchdog 按分辨率/软硬编分级超时并 kill。
- 剪映草稿生成对异常健壮：SegmentOverlap 防御跳过+同轨文本去重+失败 rmtree 半成品，保证草稿可保存/打开；多画面映射用 recording_to_common_delta 与 origin。
- 错误友好化三组正则(_PRESERVE_RAW/_PATTERNS/_RECOVERABLE)覆盖中英文，权限/磁盘类保留原始路径利于定位。

**问题/风险**：
- error_stats.py(ErrorStats/全局实例 get_error_stats)未被任何业务代码调用，仅测试引用——重复记录错误统计的预留模块，实为死代码。
- ExportService(export_service.py)带线程池/取消/回调的门面封装未被产品导出路径使用（仅 core/__init__ 导出与测试），真实导出统一走 asyncio 队列，二者并存易造成维护的人误导。
- CancellableFFmpeg(cancellable_ffmpeg.py)仅测试使用，产品导出仍用裸 subprocess.Popen+自绘 watchdog，一套能力两套实现，属冗余。
- 导出执行通过 bridge.manager.call 跨线程排队，但 taskkill/Kill 与 _last_export_error 读取依赖 controller 内部状态，取消返回语义对‘进程已由 watchdog 杀掉’的 job 可能误报，边界易脆（待核实：cancel 在 watchdog 已 kill 后若 _cancelled 已置位返回 False）。
- batch 批量计数 _export_total/_export_completed 为模块级全局，多次批量导出并发时不区分 batch，整体进度可能互相串扰（_notify_export_overall 无 batch_id 隔离重置）。

**总结**：子系统实现了一套健壮的「墙钟映射→异步导出队列→FFmpeg 精确切片→WS 进度/终态广播」链与剪映草稿生成，防御性设计与文档契约一致(含 semaphore 热更新禁区)；主要风险是三处仅测试引用的重复能力(error_stats/ExportService/CancellableFFmpeg)与计数类全局状态。

## 前端应用壳（Electron 主进程 + React 前端）

**实际阅读范围**：lsc-electron/electron/main.ts；lsc-electron/electron/preload.ts；lsc-electron/electron/backendUrl.ts；lsc-electron/src/main.tsx；lsc-electron/src/App.tsx；lsc-electron/src/store/appStore.ts；lsc-electron/src/hooks/useWebSocket.ts；lsc-electron/src/hooks/useKeyboardShortcuts.ts；lsc-electron/src/services/websocket.ts；lsc-electron/src/services/websocketUrl.ts；lsc-electron/src/services/exportPresets.ts；lsc-electron/src/pages/SplashScreen/index.tsx；lsc-electron/src/pages/Workbench/index.tsx；lsc-electron/src/pages/Settings/index.tsx；lsc-electron/src/i18n/core.ts；lsc-electron/src/i18n/index.ts；lsc-electron/src/i18n/en.ts；lsc-electron/src/utils/previewAudioAligner.ts；lsc-electron/vite.config.ts；lsc-electron/tsconfig.electron.json

**职责**：①Electron 主进程壳：窗口/托盘/开机自启/最小化到托盘管理；Python 后端进程生命周期（检测→依赖安装→spawn→健康看护→优雅停机/孤儿清理）；把渲染层请求经 IPC 桥接到系统能力。②React 渲染层：多房间工作台 UI、MSE 预览消费、Zustand 全局状态、全局快捷键、导出队列提交、i18n 双语、启动依赖安装进度页。

**架构**：渲染层（React18+AntD+Zustand，App.tsx 挂 useWebSocket 单例）←→ preload contextBridge(window.electronAPI/window.app) ←→ 主进程 main.ts（IPC handler 集群+后端进程管理）。渲染层再经单例 wsClient 走 WebSocket(9876 回退端口) 与 Python 后端通信：后端动态分配端口，主进程解析 stdout 正则提取并经 backend-ready IPC 通知渲染层。i18n 为 key=中文原文+en.part 分片字典的轻量模块(core.ts)。外部接口：IPC(invoke/on) + WebSocket(auth-token 首帧认证)。渲染/主进程由 vite-plugin-electron 分别打包到 dist-electron/(main|preload)。

**关键机制**：
- 批量日志写入+2MB 轮转，backend-stdout 常驻流按 500 次节流轮转（main.ts:84-98,305-317）
- WS 首帧 auth token 认证，认证前业务消息强制入队，防抢占第一帧（websocket.ts:151-226）
- 共享 handler 单一挂载 refcount，全局写 store，Workbench 用 on() 仅做 UI 反馈（useWebSocket.ts:12-17,299）
- rooms_updated _seq 续号监控丢消息自动 get_rooms 全量同步 + roomsShallowEqual 防整树重渲染（appStore.ts:102-134）
- runtime_event 按 generation/occurredAt 判 stale，防旧事件回滚健康态（useWebSocket.ts:27-49）
- i18n key=中文原文，en-US 未命中回退中文，动态错误按前缀/片段结构翻译（core.ts:84-118）

**数据流**：启动：ensureDependencies→checkDependencies→缺失 installDependencies(进度经 dependency-progress IPC→SplashScreen)→writeDependencyMarker→spawnBackend；后端端口从 stdout 正则提取→backend-ready→wsClient.connect。业务：useWebSocket.send(type,data)→wsClient(断连入队列/重连 flush)→auth 首帧后业务帧→后端 server.py 包装{type}_response/广播→wsClient.on 分发→共享 handler 写 appStore(rooms_updated/mse_*/settings_loaded/export_progress 等)+Workbench 局部 on() 做 toast/状态。退出：before-quit→cleanup-all-rooms→渲染层发 stop_*→killBackendAndWait。快捷键→useKeyboardShortcuts→set_mark_in/out、export_clip 等 WS 消息。

**亮点**：
- 双通道状态分发：全局 store 更新与组件 toast 解耦，共享 handler 用 refcount 保证唯一挂载，从根上规避 MSE 分片重复投喂
- 断连韧性完备：消息队列(≤100)重连 flush、指数退避重连(启动快速探测→15s 封顶)、connect 超时与心跳 backend_crashed 检测
- MSE init/media 段模块缓存+回放 + mse watchdog(<10s stall 自动恢复，3 次耗尽置 error) 消除挂载与断流竞态
- 安全纵深：openPath 路径白名单+可执行扩展黑名单、renderer CSP/will-navigate/window.open 拦截、后端环境变量白名单透传
- 日志体系健壮：主进程批量节流写+双份轮转、renderer console-message 中转、read-log-file 白名单访问

**问题/风险**：
- settings 双写源：主进程 app-settings.json(autoLaunch/minimizeToTray/locale) 与后端 lsc_config.json 并行持久化，Settings 页 save_settings 只走后端，主进程设置经 app:set-locale 独立通道，存在漂移（待核实同步逻辑）
- i18n/前端 settings 中文字段(quality/resolution/param_mode 的 value 用中文)与后端硬编码耦合，新增选项需同步多处
- installDependencies 一次性阻塞 Promise+taskkill 取消，无断点续传/后台队列，失败需全量重装
- WS 断连队列仅白名单 4 类(get_rooms/get_settings/get_system_stats/check_dependencies)，用户断连期间点录制/导出被静默丢弃仅 toast
- Workbench/index.tsx 达 4653 行，WS 订阅/快捷键/时间线换算交织在单文件，局部状态分散于大量 useRef，可维护性承压

**总结**：前端应用壳（主进程+渲染层+WS 客户端三环）通过单例连接、共享 handler 唯一挂载与模块级 MSE 缓存回放，实现了高韧性的后端进程管理与多房间实时预览录制桥接；核心契约以代码为准，与 CLAUDE.md 基本一致。

## 持久化与安全防御（配置存储 + 进程安全 + 日志）

**实际阅读范围**：python-backend/persistence.py；python-backend/dependency_manager.py；python-backend/ws_auth.py；python-backend/main.py；python-backend/handlers/room_handler.py（load_settings/save_settings/_is_allowed_output_dir/_room_to_dict/save_rooms与save_settings WS入口/shutdown_room_handlers）；python-backend/server.py（_truncate_for_log/_ 响应包装）；lsc/config.py；lsc/utils/process_launcher.py；lsc/utils/helpers.py；lsc/core/services/runtime_health.py；lsc/platforms/redaction.py

**职责**：维护本地配置的持久化与恢复（房间列表/应用设置/录制历史/高光分析结果），运行时依赖检测与安装（Python 包+FFmpeg），跨平台子进程安全启动与统一强杀，异常/错误脱敏与日志滚动，WebSocket 连接鉴权边界，以及只读的房间管线健康度投影（pipeline 五维状态）供前端监控。

**架构**：三层：①存储层 persistence.py 提供 rooms/settings/analysis 的原子写（.tmp+replace）、.bak 恢复、1s 写合并调度；②业务封装层 room_handler（WS handler 同文件）对前端暴露 save_rooms/save_settings/load_settings，做校验后委托 persistence，并把运行时 LscConfig 与 settings 同步；③运行期基础 lsc/config.py 单例配置（类型白名单校验+JSON overrides）、process_launcher 进程安全包装、runtime_health 纯投影。外部接口：WebSocket 经 server.py dispatch（统一 {type}_response 包装+_truncate_for_log+_redact_public_payload），ws_auth 校验 Origin+token；Electron 经 IPC checkDependencies/installDependencies 驱动 dependency_manager 的 stdout JSON 进度行。与前端、platforms（redaction/capabilities）、orchestrator 解耦。

**关键机制**：
- 原子写+备份恢复：persistence.py save_rooms/save_settings 写 .tmp→os.replace，覆盖前 copy .bak，装载主文件损坏时回退 .bak
- 写合并+延迟 fsync：schedule_save_rooms 1s 内合并，flush_pending_room_saves 显式刷盘，每 5 次写才 fsync（_FSYNC_EVERY_N_WRITES）
- 配置单例+类型白名单：config.py load_config 用 get_type_hints 白名单校验 overrides，LIU 项类型不符即丢弃；硬性阻断 _V2_PLATFORM_HARD_BLOCKLIST 不被 allowlist 覆盖
- 进程环境白名单+去冲突：process_launcher _ENV_WHITELIST（PYTHONPATH 已移出），PATH 剔除含 avcodec DLL 的目录，CREATE_NO_WINDOW+ClearDllDirectory
- 红act脱敏：redaction.py redact_url/redact_text/redact_command，server.py _truncate_for_log 与 _redact_public_payload，防止敏感 URL/Cookie 落入日志与前端快照
- 健康投影+变更日志：runtime_health build_room_health 5 维状态，log_room_health_if_changed 仅在快照变化时写精简日志

**数据流**：前端 save_rooms{rooms}→server.py dispatch→room_handler.handle_save_rooms（校验 rooms 列表/room_id/room_url 类型）→persistence.save_rooms（临时文件+备份+replace）→返回{success}_response；restore_persisted_rooms 启动时 load_rooms→manager.add_room。前端 get_settings→load_settings（mtime 5s 缓存）→返回；save_settings→handle_save_settings（_is_allowed_output_dir 黑名单校验）→save_settings→同步 LscConfig、invalidate_ocr→OK。房间状态经 _room_to_dict（redact_sensitive）→_rooms_list；WS 广播 rooms_updated 前经 _redact_public_payload。依赖：Electron installDependencies→dependency_manager._emit(JSON 进度行)→check→install。健康：build_room_health→rooms_updated 的 pipeline_health 字段→前端；log_room_health_if_changed。

**亮点**：
- persistence 的 .tmp+replace+.bak 三级防损坏，且提供 1s 写合并+延迟 fsync+flush_pending_room_saves，兼顾一致性与高频读写性能
- config.py 配置加载含类型白名单校验与 V2 硬性阻断列表，防止越界/乱值注入；load_config 单例+锁避免多线程竞态
- process_launcher 环境白名单（PYTHONPATH 已移除）+avcodec 冲突路径剔除+CREATE_NO_WINDOW，防御 DLL 冲突与子进程渲染污染
- 统一脱敏（redaction）贯穿 room 快照、日志、WS 响应，签名流地址即使持久化也不外泄（_room_to_dict 即使 redact_sensitive=False 也 redact stream_url）
- 日志体系：2MB×5 gzip 压缩滚动+sys/threading excepthook+可裁剪大 payload，兼容器量控制与崩溃排查

**问题/风险**：
- 文档-代码不一致：CLAUDE.md §11.2 声称 PYTHONPATH 在白名单，且 Electron 透传，但 process_launcher _ENV_WHITELIST 已移除 PYTHONPATH（注释即说明）；audit-report 也指出 stop.signal 优雅停机机制在代码中不存在（实为 LSC_PARENT_PID watchdog 轮询 + kill_process_tree 强杀，退出码常 1）
- 默认输出目录不一致：persistence 写入/room_handler load_settings 默认 output_dir 为 ~/LSC/output，而 CLAUDE.md §3.2 与 config.py LscConfig 默认 ~/LSC/recordings（config.py 第 244 行）——同一配置两处默认不一致
- flush_pending_room_saves 目前仅在 tests 被测（tests/test_persistence_coalesce.py），未有生产调用点；shutdown_room_handlers/stop 未显式刷 pending rooms，正逢定时合并未触发时退出可能有丢失窗口（待核实 electron 侧是否有兜底 flush）
- depends_manager.py 第 352 行与 372 行用 assert proc.stdout is not None（Popen stdout=PIPE 确非 None，调用成立，但违反 CLAUDE.md §11.4「禁止 assert 作运行时校验」的精益原则，属轻微风格债）
- save_rooms WS 入口仅校验三个字段类型，settings/房间 JSON 内容不做深校验直接整体落盘，异常/半写数据仍会覆盖磁盘（依赖 .bak 兜底）

**总结**：持久化与安全防御子系统实现扎实：原子写+备份恢复、类型白名单配置、进程环境净化与统一脱敏构成有效防御，但存在 PYTHONPATH/输出目录默认值/stop.signal 三处「文档≠代码」差异及 flush_pending_room_saves 无生产调用点的技术债。
