# LSC 全程序深度排查报告（2026-08-07）

> 排查对象：直播切片系统三层架构（lsc-electron 前端 / python-backend 桥接 / lsc 核心）
> 排查方法：运行日志深挖（backend.log / backend-stdout.log / debug.log 全系列）+ 三层代码并行剖析（7 个智能体，117 万 token）+ 日志问题人工源码验证
> 约束：不加新功能、不大改 UI、不降低用户体验、不影响现有功能
> 当前分支：`feat/simplify-continuous-analysis-ocr`（工作区有大量未提交 OCR 简化改动，排查与优化均未触碰该主线语义）

---

## 1. 排查结论摘要

- 共收集 **70 条 findings**（日志深挖 3 路 + 代码剖析 4 路），去重后按严重度分类为 **8 项 P1、19 项 P2、20 项 P3**。
- **已实施 16 项优化**（批次 A 稳定性防御 / 批次 B 前端性能 / 批次 C 功能修复），全部保持最小侵入，**回归测试与基线完全一致（16 failed / 1108 passed / 4 errors，零新增失败）**。
- 确认 **3 个运行中已爆雷的高危点**（GBK 子进程解码崩溃、cuda_full GPU 预览 36 次全部启动失败、AnalysisProgress React hook 条件调用崩溃），均已修复。
- 另发现 **CLAUDE.md 文档与实现不一致**（stop.signal 优雅停机机制在代码中不存在，后端每次退出被 taskkill 强杀，退出码恒为 1），需产品决策是否补齐。

---

## 2. 运行日志暴露的问题（已逐条源码验证）

日志目录：`%APPDATA%\lsc-electron\logs\`（backend.log 886KB / backend-stdout.log 79MB / debug.log 1.3MB + 轮转文件）。

### 2.1 P1 高危（已修复）

| # | 问题 | 日志证据 | 根因 | 状态 |
|---|------|----------|------|------|
| 1 | **GBK 解码崩溃**：文本模式子进程 `_readerthread` UnicodeDecodeError | backend.log:389/1126/1326，共 5 次，字节恒为 `0xb9@68` | Windows 中文区 `subprocess(text=True)` 未指定 encoding，FFmpeg 输出 UTF-8 中文元数据（录制路径含中文房间名）触发 GBK 解码失败，读线程崩溃导致 `communicate()` 输出被截断、时长探成 0.0 | **已修复**（见 §4-A1） |
| 2 | **cuda_full GPU 预览管线必然失败**：`scale_cuda` 输出 cuda 帧无法经 `auto_scale` 转 `-pix_fmt yuv420p` | 日志 36 次 `FFmpeg exited immediately: Impossible to convert between the formats supported by the filter 'Parsed_scale_cuda_0' and 'auto_scale_0'` + 40 次 `MSE hwaccel(cuda_full) startup failed` | `-hwaccel cuda -hwaccel_output_format cuda` + `scale_cuda` + 无条件 `-pix_fmt yuv420p`，帧留在 GPU 无法转系统内存 yuv420p，每次预览白启动一次 FFmpeg 再回退 CPU | **已修复**（见 §4-C4） |
| 3 | **后端 stdout 中文全乱码**：Python 以 GBK 写 stdout，Electron 用 UTF-8 解码 | backend-stdout.log 中文全部 `�`（U+FFFD） | `electron/main.ts:271` `data.toString()` 默认 UTF-8，而 `safeEnv`（:710）无 `PYTHONIOENCODING`，Python 3.12 用区域编码 cp936 | **已修复**（见 §4-A2） |

### 2.2 P2（部分已修复 / 部分已存在机制）

| # | 问题 | 日志证据 | 状态 |
|---|------|----------|------|
| 4 | **B站预览 403**：`refresh_stream_url` 命中 120s 内缓存复用过期 URL，FFmpeg 403 后重试与前端再 enable_preview 仍复用，预览持续失败约 10s | backend.log:1256-1287 | **已部分处理**：MSE 重连路径（room_handler.py:2713-2740）已有 403 → mark_cdn_bad + force refresh；首次 enable_preview 复用缓存窗口为残余问题，改 orchestrator 缓存核心风险高，记录建议 |
| 5 | **request_mse_init 高频轮询**：88 次请求 72% not-ready，预览已死仍连轮 50s | backend.log:1188-1298 | **已存在机制**：useWebSocket.ts:492-530 已有 `count*1000` 退避 + 10 次上限；findings 描述不完全准确，改动风险>收益，记录建议 |
| 6 | **持续分析 5s 大轮询**：`get_continuous_analysis_status` 每 5s + 每 tick 全量重发 listed_clips（最多 34 段约 11KB） | backend.log:19:46:34-19:47:05 | **已修复**（见 §4-B3） |
| 7 | **INFO 打印完整 WS 响应含 stream_url 签名**：`_truncate_for_log` 只截顶层不递归，`sign/volcSecret` token 落盘 | backend-stdout.log 多处 | 未实施（日志/安全权衡，记录建议） |
| 8 | **backend-stdout.log 永不轮转**：单文件 79MB、历史 .1 达 294MB | main.ts:672 createWriteStream 无轮转 | 未实施（记录建议） |
| 9 | **CDN 403 触发 MSE 重连风暴**：全日志 1571 条重连、8/1 单日 431 次，每 ~14s spawn 双 FFmpeg | backend-stdout.log:57446 | 未实施（重连退避策略，需谨慎，记录建议） |

### 2.3 P3（记录）

- 抖音解析代理未开时 `WinError 10061` 挂 14s 才报错（lsc/platforms/douyin.py，建议缩短连接超时）
- 停录后 ffprobe 探测时长虚高 33%（995.6s vs 墙钟 744.7s），已被 `钳制` 兜底掩盖（建议停录最终扫描用文件最终时长）
- 虎牙录制 FFmpeg code=0 静默退出被标记 non-recoverable 不再重连（建议 code=0 提前退出触发一次快速重连）
- 渲染进程高频 INFO 日志（MsePlayer append/updateend 每段 2 行）未降级，建议参照后端降 DEBUG
- debug.log 重复 key 警告 192 次（8/6 集中爆发），**根因已定位并修复**（见 §4-B1）

---

## 3. 三层代码深度剖析发现

### 3.1 前端 lsc-electron（剖析 12 个文件）

**P1 崩溃点（已修复）**：
- `AnalysisProgress.tsx` 在 early return 之后条件性调用 `useState/useEffect`，status 从 null→运行 时 hook 数 0→2，React 18 抛 `Rendered more hooks than during the previous render` 崩溃（被 ErrorBoundary 捕获）。**已修复**（hook 移到组件顶部无条件声明）。

**P2 性能（已修复）**：
- **RoomCard memo 击穿**：Workbench 每次渲染新建 `detectedRounds` 数组，RoomCard 比较器按引用比较恒不相等，所有房间卡随 500ms/1s 通道全量重渲染。**已修复**（useMemo 预生成每房稳定 Map）。
- **Timeline 重复 key `0-0`**：ControlBar `roomClips` 用 `Math.max(0, c.start - windowStart)` 钳制，多条已越窗 clip 塌缩成 `(0,0)`，`clipKey = ${start}-${end}` 重复；持续分析累积大量早期 AI 切片后必然触发，8/6 曾引发 650 次/秒渲染风暴致 UI 冻结 56 秒。**已修复**（稳定 uid 作 key + 跳过完全越窗切片）。
- **continuous_analysis_status 大 payload**：前端 store 无守卫每次强制 Workbench 重渲染，且携带前端从不消费的 `listed_clips`（≤512 条完整 clip dict）。**已修复**（store 字段级守卫 + 剔除大数组；后端 tick 广播瘦身）。
- **onHighlightClick 内联箭头**：每次渲染新建函数击穿 ControlBar memo 比较器。**已修复**（useCallback）。
- **usePlayheadSampling rAF 空转**：无预览时 60fps 永续空转。**已修复**（无活跃 player 时停 rAF，降 500ms 低频探测）。

**P3（记录）**：
- `timelineTick` 1s setState 但 `recordingTick` 每 4s 才变，建议仅在 recordingTick 变化时 setState
- `websocket.ts` DEV 下对每条非 mse 消息 `JSON.parse(JSON.stringify())` 深拷贝用于日志截断
- `mseSeek` 函数式 setState 与采样循环直接快照 setState 写入基准不一致
- `localTimeline` useMemo 在 timelineView 存在时仍白算 O(n) 过滤

### 3.2 后端 python-backend（剖析 server.py / room_handler.py 8112 行 / main.py / broadcast_hub.py）

**P1（已修复）**：
- `check_dependencies` 在 WS 事件循环内同步执行 ffmpeg/ffprobe -version（各 5s 超时）+ _check_nvenc（10s），最长阻塞 20s，且前端启动发两遍。**已修复**（30s TTL 缓存，重复请求命中缓存不再跑子进程）。

**P2（记录建议）**：
- `_broadcast_coroutine`（main.py:264）对广播用裸 `client.send(data)` 无超时，慢客户端卡死整个广播协程（对比 server.py broadcast 的 1s 超时+剔除）
- BroadcastHub `_seq` 自增非原子 + 合并后 `_seq` 空洞致前端误判丢消息触发全量 get_rooms
- `on_connect` 同步执行 `_rooms_list(manager)`（跨线程 orchestrator.call 最长 10s 阻塞）且用裸 json.dumps
- 多个 handler 被后注册模块覆盖成死代码（align_preview_audio / set_content_offset / 录制 / 分析，共约 700 行），改动其中任何一处静默无效
- `get_disk_usage` 在事件循环内同步 `shutil.disk_usage` 且每次 makedirs 带写副作用

### 3.3 核心 lsc（剖析 orchestrator 3087 行 / mse_streamer / shared_ingest / capture / exporter / audio_aligner / frame_capture）

**已修复**：
- GBK 解码（§4-A1）、`_check_nvenc` 超时永久缓存 False（§4-A6）、`orphaned_pid` 变量名 bug（§4-A4）、frame_capture 管道 fd 泄漏（§4-A5）、stderr executor 引用计数失衡（§4-A4b）、cuda_full 滤镜（§4-C4）。

**P1 记录建议**：
- 录制重连在编排线程同步执行网络流解析（10s+），冻结整个 tick 循环（CLAUDE.md §7.3.3 要求放 executor）
- `shared_ingest._write_all` 对 stdin 阻塞写无超时保护，录制端不排空时写满管道整链冻结
- `extract_frames_cancellable` 一次解码整窗全部帧驻留内存（480s 窗 ~334MB，全文件 2h ~5GB，OOM 风险）

**P3 记录建议**：
- `audio_aligner._best_normalized_lag` 纯 Python 逐 lag 循环 O(N²)，建议 numpy 向量化
- 单次导出多次未缓存 ffprobe（2-3 个子进程）
- `_check_nvenc` 1 帧探测超时即全会话禁用 NVENC（已修复超时不落缓存）

### 3.4 持续分析管线（OCR 简化主线）

**已修复**：
- 录制文件切换后 `ocr_runtime_state` 未重置，旧文件 `last_processed_ts` 过滤新文件前 N 秒帧致静默漏检（§4-C1）。
- `_build_continuous_status_payload` 高频 tick 广播瘦身（§4-B3）。

**P1 记录建议**：
- OCR 微基准（ocr_accel auto 三后端串行）在首个扫描窗内同步执行，吞 scan_timeout 预算
- `extract_frames_cancellable` 内存 OOM（同上）

**P2 记录建议**：
- OCR 运行期推理持续失败无自动回退（逐帧吞异常静默产出 0 回合）
- 每帧固定 2 次 OCR（顶部 + 中央横幅），帧差预筛未接入，HUD 静止帧全量推理
- `confirm_highlight_clip` 不回写 `listed_clips` 权威快照，重连恢复确认状态陈旧
- `_drop_open_tail_rounds` 检查 `tail_by` 但 FSM 产出 `end_by="open_tail"`，分支是死代码

---

## 4. 已实施的优化清单

### 批次 A：稳定性爆雷点防御修复（纯防御，不改变行为）

| 项 | 改动 | 文件 |
|----|------|------|
| A1 | `text=True` 子进程统一补 `encoding='utf-8', errors='replace'` | `lsc/utils/helpers.py:45`、`lsc/analyzer/valorant_ocr_rounds.py:82`、`lsc/analyzer/scene_analysis.py:50`、`python-backend/handlers/room_handler.py:1901`、`lsc/core/services/mse_streamer.py:76`、`lsc/cli.py`（2 处） |
| A2 | 后端 stdout 编码对齐：`safeEnv` 加 `PYTHONIOENCODING='utf-8'` | `lsc-electron/electron/main.ts` |
| A3 | AnalysisProgress hook 移到组件顶部（修 React 崩溃） | `lsc-electron/src/components/AnalysisProgress.tsx` |
| A4 | `orphaned_pid_final` 变量名 bug（孤儿进程检测恒不触发） | `lsc/recorder/capture.py:542` |
| A4b | stderr executor 引用计数失衡：仅 acquire 过的实例允许 release | `lsc/recorder/capture.py` |
| A5 | frame_capture 反复重启时管道 fd 泄漏 | `lsc/core/services/frame_capture.py` |
| A6 | `_check_nvenc` 超时不落缓存（避免一次 GPU 慢初始化全会话禁用 NVENC） | `lsc/core/services/mse_streamer.py` |

### 批次 B：前端性能与点击体验优化

| 项 | 改动 | 文件 |
|----|------|------|
| B1 | Timeline 重复 key `0-0` 修复：稳定 uid（round_key/clip_id）+ 跳过完全越窗切片 | `lsc-electron/src/utils/timelineViewModel.ts`、`ControlBar.tsx`、`components/Timeline/index.tsx` |
| B2 | RoomCard memo 击穿修复：detectedRounds useMemo 稳定 Map | `lsc-electron/src/pages/Workbench/index.tsx` |
| B3 | continuous_analysis_status 瘦身：前端 store 字段级守卫+剔除 listed_clips；后端 tick 广播 `include_listed=False`（GET/恢复保留权威快照，测试契约不变） | `lsc-electron/src/store/appStore.ts`、`python-backend/handlers/room_handler.py` |
| B4 | usePlayheadSampling 无预览时停 rAF，降 500ms 低频探测 | `lsc-electron/src/hooks/usePlayheadSampling.ts` |
| B5 | onHighlightClick useCallback 稳定化 | `lsc-electron/src/pages/Workbench/index.tsx` |

### 批次 C：功能缺陷修复（低风险）

| 项 | 改动 | 文件 |
|----|------|------|
| C1 | 录制文件切换后重置 `ocr_runtime_state`（修新文件前几分钟静默漏检） | `python-backend/handlers/room_handler.py` |
| C3 | check_dependencies 30s TTL 缓存（启动重复请求不再跑子进程） | `python-backend/handlers/room_handler.py` |
| C4 | cuda_full 滤镜链加 `hwdownload,format=nv12`，pix_fmt 条件化（修 GPU 预览必然失败） | `lsc/core/services/mse_streamer.py` |

---

## 5. 验证结果

| 验证项 | 基线（优化前） | 优化后 | 结论 |
|--------|----------------|--------|------|
| pytest 全量 | 16 failed / 1108 passed / 4 errors | **16 failed / 1108 passed / 4 errors** | 零新增失败，零破坏既有功能 |
| tsc --noEmit | 12 errors（测试文件引用未导出函数 10 + Workbench 2） | 12 errors（全部 pre-existing） | 未引入新类型错误 |
| vitest | 31 failed / 41 passed（4 文件，均因 import 未导出内部函数/测试环境，pre-existing） | 同基线 | 未引入新失败 |
| ruff（改动文件） | — | 我引入的 E501 已清零，剩余为 pre-existing | 无新增静态检查问题 |
| Python 语法 | — | 全部 ast.parse 通过 | 无语法错误 |

> 注：基线中的 16 个 pytest 失败与 4 个 vitest 失败均为 `feat/simplify-continuous-analysis-ocr` WIP 分支既有状态（测试引用未导出的内部函数、组件实现与测试断言不匹配），非本次排查引入，也非本次范围（避免触碰主线语义）。

---

## 6. 建议但未实施的优化（风险/收益评估）

以下为已定位、但受"不加新功能、不破坏现有功能、不大改"约束未在本次实施的高价值项，按实施优先级排列：

| 优先级 | 建议 | 风险 | 收益 |
|--------|------|------|------|
| P1 | **补优雅停机**：CLAUDE.md 声称的 stop.signal 机制在代码中不存在，后端每次退出被 `taskkill /T /F` 强杀（退出码恒 1），rooms.json 可能未落盘、FFmpeg 可能没优雅停。最小侵入：Electron before-quit 写 stop.signal + 后端轮询后 stop() | 中（生命周期改动） | 高（数据完整性） |
| P1 | **录制重连 executor 化**：`orchestrator._attempt_recording_reconnect` 同步网络解析冻结 tick 循环，orchestrator.call 超时 | 高（编排核心） | 高 |
| P2 | **广播协程超时+慢客户端剔除**：`main.py:_broadcast_coroutine` 裸 send 无超时，慢渲染器卡死全站广播 | 中 | 中 |
| P2 | **handler 死代码清理**：约 700 行被后注册模块覆盖的重复 handler | 中（需确认生效模块） | 中（消除误导） |
| P2 | **OCR 帧差预筛**：接入已有 `use_frame_diff`，静止帧跳过 OCR | 中（算法语义） | 高（CPU/延迟） |
| P2 | **extract_frames 流式化**：分块消费帧，峰值内存从 GB 级降常量 | 中（重构） | 高（OOM 防线） |
| P2 | **403 首启缓存失效**：首次 enable_preview 复用过期 URL 的 10s 窗口 | 中 | 中 |
| P3 | backend-stdout.log 轮转、日志敏感字段打码、renderer 高频日志降级、抖音解析连接超时缩短、`_drop_open_tail_rounds` 死代码、`_seq` 原子性、add_room 超时语义、audio_aligner 向量化、ffprobe 导出缓存 | 低-中 | 低-中 |

---

## 7. 易爆雷点清单（后续改动红线）

1. **`text=True` 子进程必带 `encoding='utf-8', errors='replace'`**（Windows GBK 陷阱，历史已爆 5 次）
2. **AnalysisProgress 类组件：hook 必须无条件声明**，禁止在 early return 后调 useState/useEffect
3. **React key 必须用稳定唯一标识**（clip_id/round_key），禁止用 clamp 后坐标拼接（历史渲染风暴根因）
4. **`_build_continuous_status_payload` 的 include_listed 参数**：GET/恢复路径须保留权威快照（测试契约 `test_continuous_status_contains_authoritative_listed_clip_snapshot`），仅 tick 广播瘦身
5. **`_check_nvenc` 探测失败不落缓存**（尤其超时路径）
6. **子进程管道必须显式关闭**（capture / mse_streamer / frame_capture 一致做法），FFmpeg 反复重启时 fd 泄漏累积
7. **共享 stderr executor 引用计数**：未 acquire 不得 release
8. **录制文件切换必须重置 OCR 跨窗口状态**（`ocr_runtime_state`），否则新文件前几分钟静默漏检
9. **check_dependencies 缓存**：依赖探测结果 30s TTL，禁止每次请求重跑子进程阻塞事件循环
10. **cuda_full 滤镜链**：scale_cuda 后必须 hwdownload,format=nv12，pix_fmt 按模式条件化（勿恢复无条件 yuv420p）
11. **或chestrator/capture 等核心的改动**须通过全量 pytest（1128 测试）回归，基线失败集不得扩大

---

## 附：排查数据与方法

- 运行日志：`%APPDATA%\lsc-electron\logs\` 全系列（backend.log 886KB / backend-stdout.log 79MB / debug.log 1.3MB + 轮转）
- 深度剖析：7 个并行智能体（3 路日志 + 4 路代码），117 万 token、476 次工具调用
- 基线：pytest 1128 收集、vitest 8 文件、tsc 全仓
- 优化改动：16 项，全部最小侵入，未触碰 OCR 简化主线语义
