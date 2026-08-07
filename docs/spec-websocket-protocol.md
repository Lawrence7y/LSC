# WebSocket 通信协议规范（LSC）

> **权威参考**：本规范基于代码实际实现整理，行号引用自当前仓库。
> 修改消息契约时，本文件与下述源码必须同步更新：
> - `python-backend/server.py`（连接层、分发、广播）
> - `python-backend/mse_ws_frames.py`（MSE 二进制帧）
> - `python-backend/ws_auth.py`（鉴权）
> - `python-backend/broadcast_hub.py`（广播队列）
> - `python-backend/handlers/*.py`（各消息 handler）
> - `lsc-electron/src/services/websocket.ts`（前端客户端）
> - `lsc-electron/src/types/index.ts`（前端类型契约 `WSPayloadMap`）

---

## 1. 连接层

### 1.1 地址与端口回退

- 服务器绑定 **loopback**：`127.0.0.1`，主端口 **9876**（`python-backend/main.py:175`）。
- 主端口被占用时自动依次尝试备用端口：**19877, 19878, 19879, 19880**（`server.py:90`）。
- 实际绑定端口通过 stdout 输出 `WebSocket server ready at ws://127.0.0.1:{bound_port}`（`main.py:303-318`），Electron 主进程正则匹配后经 IPC 通知前端；前端 `getBackendWsUrl` 获取真实端口，**不缓存**默认兜底地址（`websocket.ts:133-136`）。
- 纯前端开发模式默认地址 `ws://127.0.0.1:9876`，可用 `VITE_WS_URL` 覆盖（`websocketUrl.ts:1`）。

### 1.2 消息大小与速率限制

| 限制 | 值 | 来源 |
| :--- | :--- | :--- |
| 单条 JSON 消息上限 | **4 MB**（对齐任务单消息约 2.7 MB） | `server.py:19,470` |
| 每连接速率 | 令牌桶 **100 条/秒**，超出丢弃并记 WARNING | `server.py:191-211` |
| 长任务并发 handler | 仅 8 类可并发（`align_preview_audio` / `start_analysis` / `start_analysis_export` / `start_continuous_analysis` / `generate_jianying_draft` / `repair_recording` / `start_recording` / `stop_recording`），`max pending=8`，其余消息严格串行 | `server.py:24-38,273-287` |

> **并发语义**：顺序敏感操作（标记、导出、房间操作）串行执行；分析/对齐等长任务并发执行，使 cancel/status 查询不会被 120 秒长任务堵住。

### 1.3 鉴权（握手后首帧认证）

1. **Origin 白名单**：仅允许 `http(s)://localhost|127.0.0.1|::1`、`null`、`file://`（`ws_auth.py:14-28`）。`file://` 渲染器不发送 Origin 时放行，由 token 兜底。
2. **Token 认证**：连接建立后客户端**必须**以第一帧发送 `{type: "auth", token}`（`server.py:145-172`；`websocket.ts:226-228`）。
   - Token 由 Electron 主进程生成（32 字节 randomBytes base64url），经 `LSC_WS_TOKEN` 注入后端、经 `getBackendWsToken` IPC 提供给前端（`main.ts:721-739`）。
   - 后端校验：`LSC_WS_TOKEN_REQUIRED=1` 时严格比对（hmac.compare_digest）；未设置该变量且无 token 的纯 Python 开发模式放行（仍受 Origin 约束）（`ws_auth.py:52-66`）。
   - 认证失败/超时（5s）：以 close code **1008** 关闭。
3. **Token 不进入 URL**（历史遗留 `extract_token_from_path` 仅为兼容）。

### 1.4 连接建立后的服务端推送（on_connect）

客户端认证成功后，服务端立即推送 3 类消息（`room_handler.py:3249-3308`）：

| 顺序 | 消息 | 内容 |
| :--- | :--- | :--- |
| 1 | `rooms_loaded` | 内存中当前房间列表（不恢复磁盘持久化） |
| 2 | `settings_loaded` | 完整设置对象（含 appSettings 子对象） |
| 3 | `continuous_analysis_status` | 若存在进行中的持续分析任务 |

### 1.5 心跳与断线

- **服务端心跳**：每 5 秒广播 `heartbeat: {ts}`（`server.py:447-457`）。
- **客户端检测**：前端以 5 秒周期检查；任意消息到达都视为存活；**15 秒无任何流量**判定后端崩溃，主动 close(4000) 触发重连（`websocket.ts:353-373`）。
- **服务端 keepalive**：`ping_interval=20, ping_timeout=20` 检测静默 TCP 断连（`server.py:474`）。
- **慢客户端剔除**：JSON 广播发送超时累计 2 次 / 二进制超时 3 次 → close(1013)，触发前端自动重连（`server.py:40-41,340-366`）。
- **前端重连策略**：指数退避，启动阶段 150/300/600/1000ms，其后 `min(2000·2ⁿ, 15000)`；最多 **20 次**，耗尽后发 `reconnect_failed` 并停止，等待用户手动 `reconnect()`（`websocket.ts:328-351,525-532`）。

### 1.6 前端断连入队白名单

断连期间仅以下消息允许入队（上限 100 条），重连后按序重放（`websocket.ts:7-18`）：

```
get_rooms, get_settings, get_system_stats, check_dependencies,   # 幂等读
save_settings, add_room,                                          # 幂等/去重写
```

> 时间敏感或非幂等写操作（`start_recording` / `stop_recording` / `export_clip` / `cancel_export` 等）**绝不入队**，断连时丢弃并提示用户。

---

## 2. 消息框架

所有 JSON 消息统一结构：

```json
{ "type": "<message_type>", "data": { ... } }
```

**请求-响应配对**：

- 请求可携带 `data.request_id`（任意字符串）；响应 `data` 中会原样回填 `request_id`（`server.py:230,234-235`）。
- 正常响应消息名：`<type>_response`，如 `export_clip_response`（`server.py:240`）。
- 未捕获异常：服务端自动回 `{success: false, error: "<str(e)>", request_id?}`（`server.py:244-260`）。
- 未知消息类型：仅记 WARNING，**不回复**（`server.py:227-229`）。
- handler 返回 `None` 时无响应。

---

## 3. 前端 → 后端指令清单

> 行号引用最终生效的 handler（`server.on` 注册覆盖规则下以 `handlers/` 子模块为准；`room_handler.py` 内的旧注册会被子模块注册覆盖，见 `room_handler.py:8641-8684`）。

### 3.1 房间管理

| 消息 | 请求字段 | 响应要点 | 来源 |
| :--- | :--- | :--- | :--- |
| `get_rooms` | — | `{rooms: RoomSession[]}` | `room_handler.py:3310` |
| `refresh_room_status` | `room_id?`（空=全部） | `{success, refreshed}`；仅清错误标记，不阻断运行状态 | `room_handler.py:3323` |
| `save_rooms` | `rooms: [{room_id, room_url}]` | `{success}`；校验 room 必须是对象且含字符串 room_id/room_url | `room_handler.py:3361` |
| `validate_room_url` | `url` | `{success, valid, ...}`，30s 超时 | `room_handler.py:3409` |
| `validate_room_urls` | `urls: string[]`（≤ 上限） | `{success, valid, results[]}`；任一无效整批无效 | `room_handler.py:3418` |
| `add_room` | `url` | `{success, room_id?}`；达上限返回错误；按 room_id 去重 | `room_handler.py:3466` |
| `connect_room` | `room_id` | **异步受理契约**：受理 `{success:true, accepted:true, async:true}`；真正结果由 `room_connect_finished` 广播 | `room_handler.py:3497` |
| `disconnect_room` | `room_id` | `{success}`；软失效时间轴（参考房断开→全组失效并广播 `timeline_invalidated_broadcast`，否则保留公共轴广播 `timeline_room_removed`） | `room_handler.py:3544` |
| `remove_room` | `room_id` | `{success}`；同软失效策略 + 清理 MSE streamer | `room_handler.py:3933` |

### 3.2 预览控制

| 消息 | 请求字段 | 响应要点 | 来源 |
| :--- | :--- | :--- | :--- |
| `enable_preview` | `room_id`, `enabled: bool`, `mode: 'mse'` | MSE 路径返回 `{success, room_id, note?}`；超 4 路上限拒绝；已在运行则重发 init 段 | `room_handler.py:4749,4774` |
| `request_mse_init` | `room_id` | `{success, room_id, note}`；从缓存补发 init 段（竞态修复） | `room_handler.py:5589` |
| `mse_backpressure` | `room_id`, `state: 'pause'\|'resume'`, `pending?` | `{success}`；暂停期间丢 media 段，init 段始终推送 | `room_handler.py:5609` |
| `set_preview_muted` | `room_id`, `muted` | `{success}` | `room_handler.py:3626` |
| `set_preview_quality` | `room_id`, `quality` | `{success}`；保存设置 + 运行中自动重启预览生效 | `room_handler.py:3647` |
| `toggle_play_pause` | `room_id` | `{success}` | `room_handler.py:4112` |
| `seek` | `room_id`, `time: number` | `{success}` | `room_handler.py:4005` |

### 3.3 切片标记（时间线）

| 消息 | 请求字段 | 响应要点 | 来源 |
| :--- | :--- | :--- | :--- |
| `set_mark_in` | `room_id`, `time?: number\|null`, `live: bool=true` | `{success, mark_in}`；`time=null` 删除入点；`live=true` 捕获 `mark_in_wallclock = time.monotonic()` | `room_handler.py:4034` |
| `set_mark_out` | 同 `set_mark_in` | `{success, mark_out}` | `room_handler.py:4074` |

> ⚠️ `live=false`（时间线拖动标记）不捕获 wallclock，导出走降级路径（见 §7.2）。

### 3.4 多房间对齐

| 消息 | 请求字段 | 响应要点 | 来源 |
| :--- | :--- | :--- | :--- |
| `set_content_offset` | `room_id`, `offset: float` | `{success}`；写入 room.content_offset（秒，正=领先基准房） | `alignment_handlers.py:51` |
| `align_preview_audio` | `rooms: [{room_id, sample_rate, pcm_base64, diagnostics?}]` | 成功 `{success, offsets, reference_room_id, method, scores, align_group_id, timeline?}`；失败 `{success:false, error, offsets?, precision}`；计算超时 20s（前端 watchdog 30s） | `alignment_handlers.py:66` |

**对齐输入约束**（`alignment_handlers.py:87-163`）：
- 至少 2 路有效音频，最多 64 路；采样率 8k–48kHz 且各路一致；时长 1s–15s；单路 base64 ≤ 20MB。
- 可信阈值：互相关分数 ≥ **0.3** 才写入 offset/group；可信不足 2 路则不建组并清空旧组。
- 成功时广播 `timeline_ready`（含 timeline dict）。

### 3.5 录制

| 消息 | 请求字段 | 响应要点 | 来源 |
| :--- | :--- | :--- | :--- |
| `start_recording` | `room_id`, `recording_spec?: {encoder, crf, param_mode, bitrate, bitrate_unit, resolution, framerate, audio_bitrate}` | `{success}`；spec 缺省回退 settings；并发限流（Semaphore 2），排队时广播 `recording_queue` | `recording_handlers.py:62` |
| `stop_recording` | `room_id` | `{success}` | `recording_handlers.py:233` |
| `repair_recording` | `room_id` | `{success}`；修复未正常收尾的录制文件 | `recording_handlers.py:268` |

### 3.6 导出

| 消息 | 请求字段 | 响应要点 | 来源 |
| :--- | :--- | :--- | :--- |
| `export_clip` | `room_id, start, end, label?, preset_id?, job_id?, operation_id?, source?, mark_in_wallclock?, mark_out_wallclock?, recording_start_mono?, recording_media_start_mono?, use_room_marks?, content_offset?` | `{success, job_id, operation_id, queued, precision}` | `export_handlers.py:667` |
| `export_clip_by_id` | `clip_id, label?, preset_id?` | `{success, clip_id, job_id, queued}`；校验 recording_id 一致与文件存在；`pre_mapped` 直通不二次扣 offset | `timeline_handlers.py:127` |
| `cancel_export` | `job_id` | `{success}`；支持取消排队中（打标）与执行中（杀 FFmpeg） | `export_handlers.py:709` |
| `get_export_job_status` | `job_ids: string[]`（≤100） | `{success, jobs[]}`；补偿丢失的终态广播 | `export_handlers.py:742` |
| `create_clip_snapshot` | `timeline_id, common_start, common_end, target_room_ids[], source?, source_highlight_id?` | 全组原子成功 `{success, clips[]}`；任一路越界 → `RANGE_UNAVAILABLE`；录制未就绪 → `CLIP_NOT_READY` | `timeline_handlers.py:67` |
| `get_timeline` | `timeline_id` 或 `room_id` | `{success, timeline}` | `timeline_handlers.py:186` |

### 3.7 分析（AI 高光）

| 消息 | 请求字段 | 响应要点 | 来源 |
| :--- | :--- | :--- | :--- |
| `start_analysis` | `room_id, threshold?, mode?('scene'\|'ai'\|'combined'), game?` | `{success}`；房间已有任务或正在持续分析时拒绝 | `analysis_handlers.py:141` |
| `start_analysis_export` | `main_room_id, target_room_ids[], mode?, weights?, threshold?, game?, preset_id?, job_prefix?` | `{success}`；分析完成自动导出 | `analysis_handlers.py:251` |
| `cancel_analysis` | `room_id` | `{success}` | `analysis_handlers.py:403` |
| `get_analysis_results` | `room_id` | 高光结果 | `analysis_handlers.py:416` |
| `start_continuous_analysis` | `main_room_id\|room_id, target_room_ids[], mode?, interval?=60, threshold?, game?, valorant_profile?` | `{success}` | `analysis_handlers.py:459` |
| `stop_continuous_analysis` | `main_room_id\|room_id` | `{success}`；收尾阶段允许停在分析快照房间集内 | `analysis_handlers.py:596` |
| `get_continuous_analysis_status` | — | 完整状态快照（见 `ContinuousAnalysisStatus`） | `analysis_handlers.py:648` |
| `begin_refine_clip` | `room_id, round_key\|clip_id` | `{success}`；冻结切片进入精修 | `analysis_handlers.py:683` |
| `confirm_highlight_clip` | `room_id, round_key\|clip_id, start?, end?, target_room_ids[]?` | `{success}`；确认后解除导出门禁 | `analysis_handlers.py:709` |
| `cancel_refine_clip` | `room_id, round_key\|clip_id` | `{success}` | `analysis_handlers.py:774` |
| `delete_clip` | `room_id, round_key\|clip_id` | `{success, removed}`；记 tombstone 防 OCR upsert 复活 | `room_handler.py:8611` |

### 3.8 设置 / 系统 / 剪映

| 消息 | 请求字段 | 响应要点 | 来源 |
| :--- | :--- | :--- | :--- |
| `get_settings` | — | 完整设置对象；缺省回填 `shared_ingest_enabled`/`ocr_accel`/`jianying_draft_dir` | `room_handler.py:4156` |
| `save_settings` | 全量设置对象（含 `appSettings`） | `{success}`；强校验 `output_dir`（白名单）与 `jianying_draft_dir`，其余透传 | `room_handler.py:4172` |
| `get_disk_usage` | — | 磁盘使用信息 | `room_handler.py:4141` |
| `get_system_stats` | — | `{type, data}` 系统资源快照；另有周期广播版 | `room_handler.py:4147` |
| `check_dependencies` | — | `{python, ffmpeg, ffprobe}` 依赖状态 | `room_handler.py:4593` |
| `render_clip_preview` | `room_id` 等 | 渲染切片预览帧 | `room_handler.py:4654` |
| `get_douyin_cookie_status` / `save_douyin_cookies` | `cookies\|text`（≤1MB） | `{success, configured?, count?}` | `room_handler.py:4202,4212` |
| `get_bilibili_cookie_status` / `save_bilibili_cookies` | 同上 | 同上 | `room_handler.py:4236,4246` |
| `get_huya_cookie_status` / `save_huya_cookies` | 同上 | 同上 | `room_handler.py:4269,4279` |
| `get_jianying_draft_dir` | — | 剪映草稿目录（空=自动探测） | `jianying_handlers.py:215` |
| `generate_jianying_draft` | `room_ids[], clip_ids[], options?{include_recordings, include_clips, text_labels, vertical, draft_name, non_main_volume_zero}, include_pending?, labels?, allow_single_fallback?` | `{success, draft_name?, tracks?, segments?, error?, error_code?}`；`error_code='draft_dir_missing'` | `jianying_handlers.py:227` |

---

## 4. 后端 → 前端广播清单

### 4.1 广播分类（BroadcastHub）

| 分类 | 消息 | 语义 |
| :--- | :--- | :--- |
| **terminal（绝不丢弃）** | `clip_completed`, `clip_failed`, `recording_stopped`, `recording_started`, `reconnect_failed`, `continuous_highlights` | 终态事件；队列满时驱逐 droppable 消息腾位，仍放不下则扩容队列（上限 5000） | `broadcast_hub.py:14-18,128-173` |
| **droppable（满时可丢）** | `rooms_updated`, `mse_segment`, `export_progress`, `analysis_progress`, `system_stats` | 高频可丢失消息；队列满直接丢弃 | `broadcast_hub.py:22-25,121-126` |
| **last-value coalesce** | `rooms_updated`, `system_stats`, `continuous_analysis_status`, `analysis_progress` | drain 时同类型只保留最新一条 | `server.py:507-512` |
| **按 key 合并** | `recording_stopped`, `room_updated`, `clip_completed`, `clip_failed`, `export_progress`, `clip_export_started`, `mse_error`, `mse_reconnecting`, `mse_reconnected`, `mse_init`, `clip_queued`, `highlight_stream`, `timeline_invalidated*`, `timeline_ready`, `continuous_highlights`, `clip_confirm_status` | 按 type+room_id/job_id/clip_id/round_key 分桶合并 | `server.py:514-535` |

- 队列初始 maxsize **1000**，扩容上限 **5000**；每条消息附带 `_seq` 序列号供前端检测丢消息（`broadcast_hub.py:35,107-109,174-195`）。

### 4.2 广播消息明细

| 消息 | 关键字段 | 触发 | 来源 |
| :--- | :--- | :--- | :--- |
| `rooms_updated` | `rooms: RoomSession[]` | 任何房间状态变更（连续合并只发最新） | `room_handler.py:715 等` |
| `rooms_loaded` | `rooms` | 客户端连接时 | `room_handler.py:3260` |
| `room_updated` | `room_id` + patch 字段 | 房间增量更新 | `room_handler.py:3150` |
| `room_connect_finished` | `room_id, success, error` | 房间异步连接完成 | `broadcast_hub.py:69-75` |
| `recording_started` | `room_id, success, error` | 批量录制每房启动完成 | `broadcast_hub.py:77-86` |
| `recording_stopped` | `room_id, reason, message` | 录制停止（含磁盘满/断流） | `broadcast_hub.py:88-94` |
| `recording_queue` | `room_id?, position?, waiting?` | 录制排队状态变化 | `recording_handlers.py:148` |
| `preview_phase` | `room_id, phase: idle\|refreshing_url\|probing\|streaming\|error` | MSE 预览阶段变迁 | `room_handler.py:5037 等` |
| `mse_error` | `room_id, error, reason?('offline'\|'network'\|'disk_full'\|'unknown')` | MSE 流错误 | `room_handler.py:2844` |
| `mse_reconnecting` | `room_id, attempt, max_attempts` | MSE 自动重连中 | `room_handler.py:2915` |
| `mse_reconnected` | `room_id, degraded?, width?, height?, fps?, reason?` | MSE 重连成功 | `room_handler.py:3013` |
| `mse_init` / `mse_segment` | **二进制帧**（见 §5） | fMP4 分片推送 | `room_handler.py:2484` |
| `clip_queued` | `clip_id, room_id, round_key?, start, end, duration, score, label?, deferred?` | AI 高光入切片列表（默认 `deferred=true` 不自动导出） | `room_handler.py:6124` |
| `clip_confirm_status` | `room_id, round_key, confirm_status, start?, end?, label?` | 切片确认状态变更 | `analysis_handlers.py:800` |
| `clip_export_started` | `job_id, room_id?/clip_id?, room_name?` | 导出任务开始 | `export_handlers.py:287` |
| `export_progress` | `job_id, percent, elapsed?, total?, room_id?` | 导出进度（droppable） | `export_handlers.py:322` |
| `clip_completed` | `job_id, output_path, thumbnail_path?, room_id?, label?, start?, end?, room_name?` | 导出成功（terminal） | `export_handlers.py:302` |
| `clip_failed` | `job_id, error, room_id?, clip_id?` | 导出失败（terminal） | `export_handlers.py:243 等` |
| `export_overall_progress` | `total, completed, percent, batch_id` | 批量导出总体进度 | `export_handlers.py:93` |
| `analysis_progress` | `room_id, stage, progress, detail` | 单次分析进度（droppable） | `room_handler.py:5638` |
| `continuous_analysis_status` | 见 `ContinuousAnalysisStatus` 类型 | 持续分析状态（last-value） | `analysis_handlers.py:576` |
| `continuous_highlights` | `room_id, highlights` | 持续分析高光入列（terminal）；多房映射失败时带 `mapping_fallback: true` + `error` | `room_handler.py:8133` |
| `highlight_stream` | `room_id, highlights, start?` | 高光流式推送 | `room_handler.py:8124` |
| `continuous_analysis_complete` | `room_id, total_rounds, confirmed_rounds, exported_rounds, failed_rounds` | 持续分析收尾完成 | `room_handler.py:7040` |
| `timeline_ready` | `timeline` | 一键对齐成功建立公共时间轴 | `alignment_handlers.py:319` |
| `timeline_invalidated` | `timeline_id, reason` | TimelineContext 失效（订阅监听） | `timeline_handlers.py:57` |
| `timeline_invalidated_broadcast` | `message, reason` | 断房/移房导致全组失效的面向用户广播 | `room_handler.py:3604,3981` |
| `timeline_room_removed` | `room_id, message` | 断房/移房后公共轴仍可用 | `room_handler.py:3610,3986` |
| `settings_loaded` | 完整设置 | 客户端连接时 | `room_handler.py:3267` |
| `system_stats` | `cpu_percent, memory, disks` | 周期系统资源广播（droppable） | `room_handler.py:3243` |
| `heartbeat` | `ts` | 每 5 秒 | `server.py:453` |

---

## 5. MSE 二进制帧协议

MSE 数据**不经过 base64 JSON**，直接以二进制帧广播（`mse_ws_frames.py:1-10,30-67`）：

```
magic(3) = b'MSE'
kind(1)  = 1 (init) | 2 (segment)
rid_len(2)             big-endian
room_id (utf-8, rid_len bytes)
payload (fMP4 bytes)
```

- 帧头 6 字节起，big-endian 编码；room_id 长度上限 0xFFFF。
- 前端 `tryParseMseBinaryFrame` 解析后以 `{type: 'mse_init'|'mse_segment', room_id, data: ArrayBuffer}` 分发（`websocket.ts:276-281`）。
- **`broadcast_mse(kind, ...)` 的 kind 只能传 `init`/`segment`（或等价别名 `mse_init`/`mse_segment`/`media`），禁止自行拼接 `mse_` 前缀**（`mse_ws_frames.py:17-27`）。
- init 段仅发一次；前端挂载后可发 `request_mse_init` 补发（`room_handler.py:5589`）。

---

## 6. 日志与性能治理

- **日志降级 DEBUG**：`mse_segment`, `mse_init`, `rooms_updated`, `export_progress`, `medium_tick`（收发双方一致）（`server.py:217-220,407-410`）。
- **防御型截断**：日志打印前 str >200 字符、list >10 项截断（`server.py:45-59`）。
- **序列化**：JSON 编码兼容 numpy 类型（NaN/Inf → null，ndarray → list）（`server.py:62-83`）。

---

## 7. 关键业务契约（导出映射）

### 7.1 墙钟映射公式

```
export_start = mark_in_wallclock  - recording_start_mono - content_offset
export_end   = mark_out_wallclock - recording_start_mono - content_offset
```

- 三条路径统一 `time.monotonic()` 时钟：预览流（音频对齐）→ `content_offset`；标记路径 → `mark_in/out_wallclock`；录制流 → `recording_start_mono`（优先 `recording_media_start_mono` 首帧媒体起点）。
- 实现：`export_handlers.py:115-165`（`_resolve_export_range`）；`pre_mapped=True`（`export_clip_by_id` 路径）时跳过该公式避免二次扣 offset（`timeline_handlers.py:166-178`）。

### 7.2 降级路径

- 无墙钟快照（拖拽标记/旧数据）时用固定 `preview_latency = 2.0s` 近似补偿，返回 `precision='approximate'`（`export_handlers.py:112,155-165`）。
- ⚠️ `mark_in/mark_out`（预览轴 currentTime）**禁止**直接作为 FFmpeg `-ss` 参数。

### 7.3 错误码

平台解析统一错误码（`lsc/platforms/base.py`、CLAUDE.md §4.3）：

| 错误码 | 含义 |
| :--- | :--- |
| `unsupported_url` | 无法识别的直播间链接 |
| `offline` | 未开播 |
| `restricted` | 平台限制访问（地理围栏/禁播） |
| `parse_failed` | 解析逻辑异常 |

其他错误码：`validation_timeout` / `validation_failed`（URL 验证）、`CLIP_NOT_READY` / `RANGE_UNAVAILABLE`（快照）、`draft_dir_missing`（剪映）。

错误文案统一经 `lsc/utils/error_messages.py` 的 `humanize_error()` 转中文友好提示；`is_recoverable_error()` 判断是否值得自动重连。

---

## 8. 修改协议的强制要求

1. **前后端同步**：新增/修改消息必须同时更新 `types/index.ts` 的 `WSPayloadMap` 与后端 handler，否则 TypeScript 编译失败或前端收不到类型。
2. **不破坏兼容**：新增字段只能是可选字段；禁止改变既有字段语义（如 `content_offset` 符号）。
3. **高频消息登记**：新增高频广播必须加入日志降级白名单与 droppable/coalesce 分类，防止日志膨胀与队列堆积。
4. **`mse_` 前缀规则**：`broadcast_mse` 的 kind 只用 `init`/`segment`；广播 message type 层使用 `mse_init`/`mse_segment`。
5. **鉴权不可移除**：任何新连接路径都必须保持「握手后首帧 auth + Origin 白名单」，token 不得进 URL。
