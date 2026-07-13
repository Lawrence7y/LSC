# CLAUDE.md - 项目权威参考与设计规范文档

> **⚠️ 注意**：本文档为 LSC (Live Stream Clipper) 直播切片系统的权威参考。在进行任何代码修改、重构或升级前，必须完整阅读并遵守本文档中的设计意图和架构约束，以防止偏离核心功能或误改核心机制。

---

## 1. 项目概述与核心架构

LSC 是一个多直播间录制切片系统，支持最多 **12路并发录制** 和 **4路并发预览**。其核心定位是高效的**直播录制与快速切片工具**，专注于多路同步录制、跨房间同步预览与选区标记、一键导出片段。明确不做多轨道非线性编辑、特效/转场/调色、实时直播推流等复杂功能。

### 1.1 三层分层架构

系统采用三层分离架构设计，各层职责边界清晰：

```
+----------------------------------------------------------------------------------------+
| 1. 前端层 (Electron Render)                                                           |
|    - React + TypeScript + Vite + Ant Design + Zustand                                  |
|    - 职责：用户交互、多房间工作台 UI、MSE 预览播放器、快捷键分发、导出队列管理                 |
+----------------------------------------------------------------------------------------+
                                         │ 
                                   WebSocket
                                         │ 
+----------------------------------------------------------------------------------------+
| 2. 桥接与服务层 (Python Backend)                                                       |
|    - Qt 事件循环 (主线程) + WebSocket 服务器 (工作线程)                                   |
|    - 职责：维护多线程安全的消息桥接器，承上启下，实现前后台通信及生命周期管理                 |
+----------------------------------------------------------------------------------------+
                                         │
                                     Qt 槽调用
                                         │
+----------------------------------------------------------------------------------------+
| 3. 核心业务层 (lsc 核心 Python 包)                                                     |
|    - 业务逻辑、平台解析适配器、FFmpeg 录制/导出服务、音频对齐算法                      |
|    - 职责：底层录制进程的封装与控制、视频流剪辑与对齐处理、直播解析                    |
+----------------------------------------------------------------------------------------+
```

### 1.2 关键目录结构与职责说明

*   `lsc/`：核心 Python 包，提供业务逻辑与底层驱动。
    *   `core/models.py`：纯 dataclass 领域模型，定义 DTO 契约。
    *   `core/services/`：录制服务、导出服务、MSE 视频流分片转码服务。
    *   `platforms/`：平台解析适配器层，负责从直播地址抓取原始视频流和元数据。
    *   `recorder/`：FFmpeg 录制控制与文件有效性验证。
    *   `exporter/`：FFmpeg 切片剪辑与直拷/转码导出。
    *   `editor/audio_aligner.py`：基于音频信号互相关的对齐补偿模块。
    *   `gui/multi_room/manager.py`：多房间管理的核心编排层（运行在 Qt 主线程）。
*   `python-backend/`：桥接服务，协调工作线程与 Qt 主线程。
    *   `main.py`：后端入口，管理进程初始化及双线程启动。
    *   `server.py`：基于 asyncio 的 WebSocket 服务器，处理来自前端的 JSON 指令。
    *   `message_bridge.py`：利用 Qt 信号槽实现线程安全的跨线程调用及广播分发。
    *   `persistence.py`：本地房间配置的持久化存储。
*   `lsc-electron/`：前端桌面包。
    *   `electron/main.ts`：Electron 主进程，控制窗口、系统托盘、自动启动、Python 进程检测与生命周期保护。
    *   `src/store/appStore.ts`：基于 Zustand 的全局前端状态管理。
    *   `src/services/mediaSourcePlayer.ts`：MSE 播放器核心，解析 fMP4 分片并送入 SourceBuffer 播放。
    *   `src/pages/Workbench/`：核心工作台界面，承载多房间同步预览、时间线、切片控制。

---

## 2. 技术通信与数据流动

### 2.1 跨线程双层桥接机制

由于 `MultiRoomManager` 基于 PySide6 的 Qt 事件循环运行，必须驻留在 **Qt 主线程**，而 WebSocket 服务器运行在**工作线程**以避免网络阻塞 UI，因此系统实现了 `QtManagerBridge` 作为通信桥梁。

```
[WebSocket Handler 线程]                                    [Qt 主线程]
          │                                                    │
    1. bridge.call(fn, *args)                                  │
          │ ─── 2. 发射 Qt 信号 (_execute) ──────────────────> │
          │                                                    │ 3. 执行核心逻辑 (fn)
          │ <── 5. 事件被唤醒 (req.event.set()) ───────────────│ 4. 处理完成/捕获异常
    6. 返回结果/抛出异常                                       │
```

*   **同步调用原语**：WebSocket 接收到前端请求后，调用 `bridge.call()`。该方法内部会创建一个 `_CallRequest` 对象并将其通过 Qt 信号 `_execute` 发射。主线程被唤醒并执行该函数，执行完毕后触发 `threading.Event` 唤醒工作线程。支持配置 `timeout`（默认 10.0 秒）。
*   **状态广播分发**：主线程的状态更新（如房间连接完成、录制进度等）不能直接调用 asyncio，需要调用 `bridge.queue_broadcast(msg)`，将其写入线程安全的 FIFO 队列中。WebSocket 服务线程中的 `_broadcast_coroutine` 循环以 100ms 的频率从队列中读取并推送至前端。

### 2.2 WebSocket 协议规范

WebSocket 统一绑定在 `localhost`，主端口为 `9876`。
*   **端口自动回退**：若主端口被占用，会自动依次尝试备用端口 `19877`, `19878`, `19879`, `19880`。
*   **高频消息优化**：
    *   在 Qt 主线程快速触发房间状态变更时，`rooms_updated` 消息会被连续合并（若队列里有连续的多条 `rooms_updated`，只广播最新的一条），以极大缓解前端 JSON 序列化与 React 重渲染的负载。
    *   高频消息（如 `mse_segment`、`mse_init`、`rooms_updated`、`export_progress`）在日志中会被降级为 DEBUG 级别，防止 backend 日志膨胀。
    *   对于大容量的传输数据字段，在 `server.py` 日志打印前会自动进行字符长度或数组大小截断，防御型打印。

---

## 3. 核心领域模型与持久化参数

### 3.1 核心 DTO 数据类 (`lsc/core/models.py`)

系统定义了 5 个核心 DTO（数据传输对象），除 `slots=True` 外不包含任何复杂方法：

1.  **StreamQuality**：表示单个流的画质和物理链接。
    *   `name`: str (例如 "原画", "高清")
    *   `url`: str (对应流地址)
2.  **RoomInfo**：适配器流解析后的完整元数据。
    *   `platform`: str, `room_url`: str, `stream_url`: str, `title`: str, `streamer`: str, `is_live`: bool, `qualities`: list[StreamQuality], `selected_quality`: str, `headers`: dict[str, str], `error`: str, `error_code`: str, `raw`: dict
3.  **RecordingSession**：一次完整的录制过程上下文快照。
    *   `session_id`: str, `room_url`: str, `output_dir`: str, `output_path`: str, `status`: RecordingStatus, `start_time`: datetime, `end_time`: datetime, `duration_sec`: float, `file_size_mb`: float, `encoder`: str, `crf`: int, `bitrate`: str, `last_error`: str, `reconnect_attempts`: int, `max_reconnect_attempts`: int
4.  **Clip**：视频片段，也是时间线高光与切片位置的核心定义。
    *   `clip_id`: str, `title`: str, `start_sec`: float, `end_sec`: float, `source_video`: str, `output_path`: str, `thumbnail_path`: str, `duration_sec`: float, `file_size_mb`: float, `score`: float, `exported`: bool, `error`: str
    *   `mark_in_wallclock`: float, `mark_out_wallclock`: float (墙钟时间，用于亚毫秒级无损裁剪定位)
    *   `content_offset`: float (内容偏移量，用于多房间多视角同步导出时的音轨物理对齐)
5.  **ExportOptions**：剪辑导出时的 FFmpeg 编码参数。
    *   `codec`: str, `crf`: int, `preset`: str, `audio_bitrate`: str, `rate_mode`: str, `video_bitrate`: str, `resolution`: str, `fps`: float, `vertical_crop`: bool, `generate_thumbnail`: bool

### 3.2 本地持久化与默认参数配置

后端通过本地 JSON 进行运行配置存储，路径定义在 `persistence.py` 中：
*   **持久化目录**：房间列表存放在项目根目录下的 `data/` 目录中；设置和历史记录存放在项目根目录。
*   **房间列表存储**：`data/rooms.json`，格式为 `{"rooms": [...]}`。采用临时文件写入 (`.tmp`) + 原子替换 (`replace`) 机制，防止断电导致文件损坏。
*   **设置持久化**：项目根目录 `settings.json` 保存录制和通用参数。
*   **录制历史**：项目根目录 `recording_history.json` 保存录制会话历史。

#### settings.json 默认及合法配置表：

| 配置键 | 默认值 | 可选范围 / 说明 |
| :--- | :--- | :--- |
| `output_dir` | `~/LSC/recordings` | 录制产物主目录。支持 Windows 绝对路径或带 `~` 的家目录。 |
| `encoder` | `h264_nvenc` | `libx264` (CPU H264), `libx265` (CPU H265), `h264_nvenc` (Nvidia), `h264_qsv` (Intel), `h264_amf` (AMD), `copy` (直接拷贝) |
| `crf` | `23` | `0` 到 `51`，越低画质越高。 |
| `param_mode` | `"CRF 质量"` | `"CRF 质量"` 或 `"自定义码率"` |
| `bitrate` | `"8000"` | 单位：kbps。在自定义码率模式下起效。 |
| `bitrate_unit`| `"kbps"` | 视频码率单位，一般为 `"kbps"` |
| `quality` | `"原画"` | 平台流抓取画质候选等级：`"原画"`、`"高清"`、`"流畅"` |
| `resolution` | `""` | e.g. `"1920x1080"`，留空表示不进行分辨率缩放。 |
| `framerate` | `"原画"` | 帧率。可选 `"60"`、`"30"`、`"24"` 或 `"原画"` (保持不变) |
| `audio_codec` | `"AAC 128k"` | 音频参数配置。 |
| `audio_bitrate`| `"128k"` | 导出音频码率：`"96k"`、`"128k"`、`"256k"` 等。 |
| `preview_quality`| `"高清"` | MSE 预览的转码分辨率参考预设。 |
| `shared_ingest_enabled` | `False` | 是否启用共享进样模式（单 FFmpeg 进程同时输出录制和预览）。`True` 开启，`False` 使用独立双进程。 |
| `shared_ingest_preview_crf` | `23` | 共享进样模式下预览流的 CRF 值（0-51），越低画质越高。默认 23。 |
| `shared_ingest_preview_preset`| `"veryfast"` | 共享进样模式下预览流的编码预设。可选 `ultrafast`/`superfast`/`veryfast`/`faster`/`fast`/`medium` 等。 |

---

## 4. 平台适配系统

平台适配器采用 **Protocol + Registry (协议与注册中心) 模式**，具备高度的解耦与可扩展性。

### 4.1 协议规范与无状态约束 (`lsc/platforms/base.py`)

所有适配器必须实现 `PlatformAdapter` 协议（Python 3.10+ Protocol）：
*   `platform`: str - 唯一标识符（例如 `"bilibili"`）。
*   `display_name`: str - 面向用户的友好平台名。
*   `can_handle(self, url: str) -> bool` - 判识该 URL 是否属于此平台。
*   `parse(self, url: str) -> StreamInfo` - 解析直播间或流链接。

> [!IMPORTANT]
> **无状态约束**：为保证多线程环境下并发解析的安全，所有 PlatformAdapter 实例必须是**无状态**的。`parse()` 方法不得修改实例属性，且不能依赖跨调用共享的可变状态。所有的解析上下文（如 headers、临时 HTTP 响应）必须使用局部变量，并最终填充在 `StreamInfo` 返回。

### 4.2 缓存与性能控制

*   **TTL 缓存控制**：`registry.py` 内置线程安全的 `_ParseCache`，提供不同级别的生存时间 (TTL) 防御机制：
    *   **解析成功**：TTL = `30.0` 秒。防止高频轮询直播流导致平台 API 熔断。
    *   **解析失败**：TTL = `10.0` 秒。防止前端短时间内紧密重试压垮网络。
    *   **定时清理**：每访问 `20` 次触发一次守护线程后台清理过期缓存。
*   **平台快速路由**：内置 `_URL_ROUTER` 路由映射表。`parse_stream()` 会优先提取 URL 的 host 路由到特定适配器，跳过无关平台，避免盲目尝试网络请求。若路由未匹配，才会进行全局线性扫描。

### 4.3 平台支持列表与错误码

目前已实现的适配器有：
1.  `DirectAdapter` (直链解析)
2.  `DouyinAdapter` (调用 `scripts/douyin_record.py` 的精简签名机制)
3.  `BilibiliAdapter` (API 解析，支持带 Cookie/BiliSession 鉴权)
4.  `HuyaAdapter` (支持通过原始 JS 签名函数匹配生成流地址)
5.  `KuaishouAdapter`、`DouyuAdapter`、`XiaohongshuAdapter`、`WeiboAdapter`
6.  `GenericPageAdapter` (未知链接兜底，尝试匹配通用 HTML `<video>` 流标签)

**统一错误代码清单**：
*   `unsupported_url`：无法识别的直播间链接或地址。
*   `offline`：未开播。
*   `restricted`：被平台限制访问（如地理围栏、禁播）。
*   `parse_failed`：解析逻辑异常。

---

## 5. 后端录制、导出与音频对齐技术

### 5.1 录制引擎 (`StreamCapture` & `RecordingService`)

录制服务以 `RecordingService` 作为门面，底层是对 FFmpeg 子进程进行生命周期维护的 `StreamCapture`：
*   **FFmpeg 参数控制**：通过 `LscConfig` 内的参数动态拼装命令：
    *   如果编码器为 `copy`，则使用 `-c:v copy -c:a copy` 直出。
    *   非 copy 模式下，注入 `-c:v {codec}`、恒定质量因子（NVENC 下为 `-rc vbr -cq {crf}`；CPU 下为 `-crf {crf}`）以及预设参数 `-preset {preset}`。
*   **磁盘满保护防线**：在录制状态轮询中（每 5 秒），系统会检测录制输出所在磁盘的可用空间，若**剩余空间低于 2GB** (`_MIN_FREE_BYTES_WHILE_RECORDING`)，则强制安全关停 FFmpeg 录制并抛出错误状态，确保操作系统及录制数据不损毁。

#### 5.1.1 录制文件三层验证机制 (`validate_recording`)

录制停止或发生重连时，必须通过三层严格的校验规则才会被判定为有效录制，否则会抛出错误状态：
1.  **路径验证**：目标路径不能为空且物理文件确实存在。
2.  **大小验证**：文件体积必须大于 **0.1MB**。小于此大小直接判定为录制失败。
3.  **格式特征验证**：读取文件头部的字节，检查特定平台的视频格式签名：
    *   **MP4**：偏移量 4 开始的 4 字节应为 `ftyp` 字符。
    *   **FLV**：前 3 字节应为 `FLV` 字符。
    *   **MKV**：前 4 字节应为 EBML 头签名 `0x1A45DFA3`。

### 5.2 视频导出与切片 (`ExportService`)

视频导出通过 `ClipExporter` 调用 FFmpeg 进行精确裁剪。
*   **并发控制**：`ExportService` 通过 `ThreadPoolExecutor` 维护导出任务队列，**最大并发数严格限制为 2** (`_DEFAULT_MAX_CONCURRENT`)，以防多路转码输出压垮 CPU 或显卡硬件编码芯片。
*   **竖屏裁剪算法**：当选择“竖屏裁剪 (9:16)”时，FFmpeg 滤镜参数会动态转换为：
    `crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920`
    该公式提取画面中心区域并缩放到标准的 1080x1920。
*   **错误友好转化**：后端内置了面向用户的友好错误友好化映射工具 (`utils/error_messages.py`)。它包含 19 组正则表达式（含 2 组保留原始路径的 `_PRESERVE_RAW_PATTERNS` 和 17 组 `_PATTERNS`），捕获 FFmpeg 的底层报错（如 "Server returned 403 Forbidden"、"Connection refused"、"磁盘空间不足" 等中英文错误）并自动转换为中文友好提示。同时提供 `is_recoverable_error()` 判断是否值得自动重连。

### 5.3 音频对齐技术 (`audio_aligner.py`)

多直播间录制时，不同房间的 CDN 拉流延迟不一致，直接裁切会导致各个房间的导出视频画面内容在时间轴上无法同步。LSC 提供了**音频互相关对齐算法**：

```
                    ┌───────────────┐
                    │  提取音频数据 │ (16kHz, mono, float32 PCM)
                    └───────┬───────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
      [房间 A 音频 (3.0s)]         [房间 B 音频 (3.0s)]
              │                           │
              └─────────────┬─────────────┘
                            ▼
                    ┌───────────────┐
                    │ 互相关(FFT)   │ np.correlate(A, B)
                    └───────┬───────┘
                            │
                            ▼
               找出互相关系数最大值的峰值位置
                 (抛物线插值实现亚毫秒精度)
```

*   **采样规范**：提取各房间音频的 PCM 信号，采样率为 `16000Hz`（Mono，单声道，float32 编码），长度恒定为 **3.0秒** (`AUDIO_DURATION`)。
*   **⭐ 音频来源（关键澄清）**：实际运行时的音频数据来自**预览流**，而非录制文件。前端通过 Web Audio API（AudioWorklet）从 `<video>` 元素捕获 3.0s 的 PCM 数据，base64 编码后通过 `align_preview_audio` WebSocket 消息发送给后端。后端 `room_handler.py:handle_align_preview_audio` 解码后直接调用 `compute_offset` 计算互相关。
    *   `audio_aligner.py` 中虽存在 `align_rooms` 函数（支持从录制文件用 FFmpeg 提取音频），但**该函数从未被业务代码调用**（死代码）。
    *   即：对齐算法的输入永远是**预览流的实时音频**，这与"预览流和录制流完全独立"的核心约束一致。
*   **对齐基准选择**：通过 FFT 互相关计算，以**拉流进度最慢（内容最新、延迟最大）**的房间作为基准房间。
*   **偏移量补偿公式**：
    计算各房间对于基准房间的相对偏移量 $content\_offset$ (秒)。
    *   正值表示该房间的视频内容处于“领先”（进度快于基准），导出时切片的 mark_in 时间需增加此偏移量；
    *   负值则反之。
    在导出时通过调整 FFmpeg `-ss` 参数，使多视角切片达到画面级严格对齐。
*   **置信度防线**：互相关计算的最大相似度必须大于 `0.1` 阈值，否则会被判定为内容不相关，自动降级至 0 偏移，防止无声视频或不同内容视频强行对齐导致错误。

## 6. 前端 UI 设计与设计系统

前端设计严格遵循 Apple Human Interface Guidelines (HIG) 规范，致力于提供精致的暗色调多屏监控台体验。

### 6.1 CSS 变量与设计令牌体系 (`tokens.css` & `global.css`)

系统定义了完备的语义设计令牌 (Design Tokens)：
*   **主色调与状态色**：
    *   主品牌色：`--brand-500` = `#007aff` (苹果蓝)，高亮态 `--brand-400` = `#2e8dff`。
    *   成功态：`--state-success-dark` = `#30d158` (绿)。
    *   错误/危险态：`--state-error-dark` = `#ff453a` (红)。
    *   警告态：`--state-warning-dark` = `#ff9f0a` (橙)。
*   **背景分层 (暗色模式)**：
    *   底座背景：`--bg-primary` = `#000000` (纯黑)。
    *   面板卡片：`--bg-secondary` = `#1c1c1e` (磨砂深灰)。
    *   弹出层/浮窗：`--bg-tertiary` = `#2c2c2e`。
*   **字体与圆角**：
    *   无衬线字体族：`'SF Pro Display', 'PingFang SC', system-ui, -apple-system, sans-serif`。
    *   圆角梯级：`--radius` = `14px` (卡片级)；`--radius-sm` = `10px`；`--radius-lg` = `18px`。
    *   过渡动画：切换暗/亮主题时，全局挂载 `.theme-transition` 类，在 `0.3s` 内使用 `ease` 曲线平滑过渡背景色、文字颜色、边框色与阴影。

> [!CAUTION]
> **Ant Design 强样式覆盖**：由于 Ant Design 的 CSS-in-JS 在运行时注入样式的优先级不稳定，全局 `global.css` 中对于 `.ant-card`, `.ant-btn-primary`, `.ant-input`, `.ant-select-selector` 等组件均使用了 `!important` 强制约束主题渲染。在后续优化中，若切换为 Antd `ConfigProvider` token，必须确保在亮暗主题下各组件对比度和边框过渡无断层。

---

## 7. 预览与播放器系统

系统对预览提供了两套实现方案，以自适应不同的运行环境：

### 7.1 原生桌面预览 (libmpv) — ⚠️ 已弃用

> [!CAUTION]
> PySide6 GUI 已弃用，Electron 为唯一前端。以下保留供历史参考，不再维护。

针对 Python 原生 GUI 启动方式，曾采用 `python-mpv` 调用本地 `libmpv`，将其渲染句柄嵌入到 PySide6 的 `MpvWidget` 中。该方案具备极低延迟与对硬件解码的完美支持，但现已弃用。

### 7.2 前端 Electron 预览 (MSE fMP4 方案)

在打包后的 Electron 应用中，为了在 Web 环境无插件播放多路直播，设计了 **MSE (Media Source Extensions)** 流式分片转码方案：

```
[直播流 / 录制文件] ──> [FFmpeg 进程] ──(stdout)──> [MseStreamer (Python)]
                                                         │ (捕获 fMP4 原始 Box)
                                                         ▼
[React MSE 播放器] <──(WebSocket `mse_segment` 消息)── [WebSocket Server]
```

#### 7.2.1 fMP4 管道检测核心逻辑 (`lsc/core/services/mse_streamer.py`)

1.  **分片转码**：启动 FFmpeg 子进程，将输入流实时转码为 fragmented MP4 格式，命令参数关键：
    `-f mp4 -movflags empty_moov+default_base_moof+frag_keyframe`。
2.  **硬解加速检测**：在进程生命周期内执行一次 `_check_nvenc`，若显卡硬件支持 `h264_nvenc` 则优先调用，否则降级为 `libx264` 软件编码。
3.  **Box 分解与边界检测**：
    *   读取 stdout 字节流，定位 `ftyp` 和 `moov` 标签，合并生成 **初始化分片 (Init Segment)**，该片段只发送一次。
    *   实时检测 `moof`（Movie Fragment Header）和 `mdat`（Media Data）Box，根据特征拼接为单独的 **数据分片 (Media Segment)**。
    *   如果连续读取未检测到分片边界且累积超过 **512KB** (`_MAX_SEGMENT_BYTES`)，则强制切分，防止缓冲区溢出。
4.  **前端消费机制**：前端通过 `mediaSourcePlayer.ts` 接收 WebSocket 消息。首帧写入 `SourceBuffer.appendBuffer(initSegment)`，后续高频追加 `mediaSegment`，当缓冲区超出时间阈值时，自动执行清理以保证预览低延迟。

### 7.3 预览流与录制流架构模式

系统支持两种预览/录制架构模式，通过 `LscConfig.shared_ingest_enabled` 配置开关控制：

#### 7.3.1 独立双进程模式（默认，`shared_ingest_enabled=False`）

> [!NOTE]
> 默认模式下，预览和录制是两条完全独立的 FFmpeg 进程和独立的直播流连接，互不干扰。

```
直播 CDN
    │
    ├── 连接 1 → FFmpeg 进程 A (StreamCapture)      → 写入本地磁盘录制文件
    │
    └── 连接 2 → FFmpeg 进程 B (MseStreamer, stdout 管道)
                   → fMP4 分片 → WebSocket → 前端 MSE 播放器 (<video>)
```

*   **录制进程**：由 `MultiRoomManager.start_recording()` 启动，输出到本地磁盘文件（MP4/FLV/MKV），使用 `-c copy` 直拷或选定硬件/软件编码器。
*   **预览进程**：由 `_handle_mse_preview()` → `MseStreamer.start()` 启动，**独立**从直播 CDN 拉流，转码为 fMP4 并通过 stdout 管道推送到 WebSocket，**完全不涉及录制文件**。

#### 7.3.2 共享进程模式（`shared_ingest_enabled=True`）

> [!WARNING]
> 开启后，预览和录制共享同一个 FFmpeg 进程。录制故障会同时导致预览中断，预览转码负载可能影响录制稳定性。预览画质由 `shared_ingest_preview_crf` 控制，与录制画质（`-c copy`）不同。

```
直播 CDN
    │
    └── 单个 FFmpeg 进程 (SharedRoomIngest)
         │
         ├── 输出1: -c copy → 录制文件（磁盘）
         │
         └── 输出2: libx264 -crf {shared_ingest_preview_crf} → pipe:1 → SharedPreviewHandle → WebSocket → MSE播放器
```

*   **核心实现**：`lsc/core/services/shared_ingest.py` — `SharedRoomIngest` 类，单进程双输出 FFmpeg 命令。
*   **录制输出**：`-c copy` 直拷到磁盘文件，保证录制画质无损。
*   **预览输出**：`libx264` 软件编码，参数由 `shared_ingest_preview_crf`（默认 23）和 `shared_ingest_preview_preset`（默认 `veryfast`）控制。
*   **进程生命周期**：`start_recording_and_preview()` 启动双输出；`stop_recording_sink()` 停止录制后自动重启为纯预览模式；`start_preview_only()` 启动纯预览进程。
*   **错误检测**：`_read_preview_stdout_loop` 通过 `_planned_stop` 标志区分计划内关闭和意外退出，确保预览独享模式下进程死亡也能正确触发 `mse_error` 广播。
*   **重连优化**：`_attempt_recording_reconnect` 对共享进程走快速路径（直接终止旧进程 + 创建新进程），避免 `stop_recording_sink` → preview-only → dual-output 的双重重启链。

#### 7.3.3 通用约束（两种模式均适用）

*   **独立启动时机**：用户点击"开始录制"触发 `start_recording`；用户点击"预览"触发 `enable_preview {mode: "mse"}`。两者完全解耦，可以只录不看、只看不录、或同时进行。
*   **并发上限**：
    *   预览最多 **4路**（`MAX_CONCURRENT_PREVIEWS`），≥6 路时动态降分辨率 ≤ 854×480，≥8 路时限制 ≤ 640×360。
    *   录制最多 **12路**（`MAX_CONCURRENT_RECORDINGS`），启动并发用 `asyncio.Semaphore(2)` 限制，防止多路 HTTP 刷新同时阻塞。
*   **init 段竞态修复**：`mse_init` 消息可能早于 `rooms_updated` 到达前端（前端 VideoPreview 组件尚未挂载）。`SharedRoomIngest` 内部缓存最近一次 init 段（`last_init_segment`），前端挂载后主动发送 `request_mse_init`，后端通过 `replay_init()` 补发，解决竞态。
*   **MSE 启动流程时序**：
    1.  前端发送 `enable_preview {mode: "mse"}`
    2.  后端 `_handle_mse_preview` 调用 `mgr.refresh_stream_url()` 刷新流地址（B站等平台耗时可达 10s+，在 `_recording_executor` 线程池执行，不阻塞 Qt 主线程）
    3.  创建 `MseStreamer` 并调用 `start()`，FFmpeg 启动探测超时 2~8 秒（B站更长）
    4.  FFmpeg 首先输出 init 段（`ftyp+moov`），后端通过 `asyncio.run_coroutine_threadsafe` 异步广播 `mse_init` WebSocket 消息
    5.  后续 media 段（`moof+mdat`）持续以 `mse_segment` 消息推送，帧率约 30fps，每个 Fragment Duration = 1000ms

---

## 8. 切片系统完整运行机制

切片（Clip）是 LSC 的核心生产工具。其完整流程分为「标记」、「映射」、「导出」三个阶段，涉及两条独立时间轴的对齐计算。

### 8.0 三流汇合数据流总览

切片系统的准确性依赖**三条独立路径的精确汇合**：预览流（音频对齐用）、标记路径（墙钟基准）、录制流（切片导出用）。三者通过 `time.monotonic()` 单调时钟统一对齐。

```
┌─────────────────────────────────────────────────────────────────┐
│ 路径 1：预览流（用于音频对齐）                                    │
│                                                                  │
│  直播 CDN ──> FFmpeg B (MseStreamer) ──> <video> ──> Web Audio   │
│                                            │                     │
│                                            └─> 3s PCM (base64)   │
│                                                  │               │
│                          后端 compute_offset <──┘               │
│                                  │                               │
│                                  └─> content_offset               │
└──────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ 路径 2：标记路径（墙钟基准）                                      │
│                                                                  │
│  用户按 i/o 键 ──> mark_in_wallclock  = time.monotonic()        │
│                   mark_out_wallclock = time.monotonic()          │
│                                                                  │
│  ⚠️ 基准时间是「按下 i/o 键标记入出点的墙钟时间」                  │
│     而非「点击添加到切片列表的时间」                               │
└──────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ 路径 3：录制流（用于切片导出）                                    │
│                                                                  │
│  直播 CDN ──> FFmpeg A (StreamCapture) ──> 磁盘录制文件           │
│  录制启动时记录 recording_start_mono = time.monotonic()          │
└──────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ 导出映射（三条路径汇合）                                          │
│                                                                  │
│  export_start = mark_in_wallclock                                │
│               - recording_start_mono   ← 路径 3 录制流基准       │
│               - content_offset          ← 路径 1 预览流对齐结果   │
│                                                                  │
│  FFmpeg -ss {export_start} -to {export_end} -i 录制文件 → 切片    │
└──────────────────────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> 三条路径的关联点全部是 `time.monotonic()` 单调时钟时间戳。预览流提供 `content_offset`（多房间对齐补偿），标记路径提供 `mark_in/out_wallclock`（用户操作时刻），录制流提供 `recording_start_mono`（录制起点）。三者做差即可将预览标记精确映射到录制文件的物理位置。

### 8.1 切片标记阶段（mark_in / mark_out）

用户按 `i` / `o` 键（或前端按钮）分别触发 `set_mark_in` / `set_mark_out` WebSocket 消息，后端 handler 将入/出点信息写入 `RoomSession`：

```python
# room_handler.py 核心写入逻辑
room.mark_in = float(time_value)            # MSE 播放器 currentTime（预览时间轴秒位置）
room.mark_in_wallclock = time.monotonic()   # 标记时刻的单调时钟绝对时间戳（桥接关键）

room.mark_out = float(time_value)
room.mark_out_wallclock = time.monotonic()
```

*   `mark_in/mark_out`：对应 MSE 播放器的 `currentTime`，是**预览时间轴**上的位置，与录制文件的物理时间轴不直接同步（因为预览流有独立的启动时间 + CDN 拉流抖动）。
*   `mark_in_wallclock/mark_out_wallclock`：关键桥接值，使用 `time.monotonic()` 单调时钟记录用户标记时刻的绝对物理时间，用于跨时间轴精确对齐。

### 8.2 时间轴对齐映射（导出前核心计算）

触发 `export_clip` 时，后端通过**墙钟差映射**（Wallclock Mapping）将预览标记时间转换为录制文件中的精确位置：

```
export_start = mark_in_wallclock  - recording_start_mono - content_offset
export_end   = mark_out_wallclock - recording_start_mono - content_offset
```

*   `recording_start_mono`：录制进程启动时记录的 `time.monotonic()` 时间戳（存储在 `room.recording_start_mono`）。
*   `content_offset`：由**音频互相关对齐算法**（`audio_aligner.py`）计算的多房间内容偏移，单位秒。正值表示该房间领先基准房间，导出时相应调整 `-ss` 参数使多视角对齐。
*   **降级方案**：若墙钟时间戳不可用（旧版本数据），降级使用固定 `preview_latency` 偏移（默认 2.0 秒）补偿延迟。

> [!CAUTION]
> `mark_in/mark_out`（前端 MSE `currentTime`）**不能**直接作为 FFmpeg `-ss` 参数，因为预览流是独立 FFmpeg 进程，其时间轴与录制文件的物理时间轴存在不定延迟。**必须**通过墙钟时间戳差值映射，否则导出片段会有几秒的系统性偏移。

### 8.3 导出阶段（FFmpeg 精确裁切）

`manager.start_export()` 将任务提交到 `ExportService` 的线程池（**最大并发数严格限制为 2**），调用 `ClipExporter` 执行 FFmpeg 精确裁切：

```
[录制文件 (.mp4/.flv)] ──FFmpeg -ss {start} -to {end}──> [导出文件]
```

*   进度回调通过 `asyncio.run_coroutine_threadsafe` 异步广播 `export_progress` WebSocket 消息，前端实时更新进度条。
*   导出完成后广播 `clip_completed`，包含 `output_path`、`thumbnail_path`（若启用缩略图）、`job_id` 等字段，前端据此完成队列状态更新。
*   用户可随时发送 `cancel_export {job_id}` 中止进行中的导出任务，后端通过 `manager.cancel_export(clip_id)` 强杀对应的 FFmpeg 进程。

### 8.4 多房间批量切片与对齐

当用户同时对多个房间标记相同事件（如同一精彩片段的多视角），系统通过以下方式实现对齐批量导出：

1.  用户点击工作台"一键对齐"按钮，前端通过 Web Audio API 从各房间的 `<video>` 元素（**预览流**）捕获 3.0s PCM 音频，base64 编码后发送 `align_preview_audio` 消息到后端。
2.  后端 `handle_align_preview_audio` 调用 `audio_aligner.py` 的 `compute_offset` 计算各房间相对于"进度最慢"基准房间的 `content_offset`，返回给前端。
3.  前端将 `content_offset` 通过 `set_content_offset` 消息回传后端，存入 `room.content_offset` 字段。
4.  导出时，每个房间使用各自的 `content_offset` 调整导出入/出点（通过墙钟映射公式 + FFmpeg `-ss` 参数），使导出的多路视频在画面内容层面完全对齐。
5.  所有房间并行提交导出任务，受 `_DEFAULT_MAX_CONCURRENT = 2` 限制自动排队。

> [!NOTE]
> 对齐算法的音频输入**始终来自预览流**（前端 `<video>`），而非录制文件。`audio_aligner.py` 中的 `align_rooms` 函数（支持从录制文件提取音频）从未被业务代码调用。这与"预览流和录制流完全独立"的核心约束一致。

---

## 9. 版本检测与更新机制

LSC 实现了**手动触发的轻量更新检测**，通过直接调用 GitHub API 比较版本号，无需 `electron-updater`。

*   **监测仓库**：[https://github.com/Lawrence7y/LSC](https://github.com/Lawrence7y/LSC)
*   **API 端点**：`https://api.github.com/repos/Lawrence7y/LSC/releases/latest`

### 9.1 实现架构

```
[设置页 "检查更新" 按钮]
        │
        │ ipcRenderer.invoke('check-for-update')
        ▼
[electron/main.ts — registerWindowIpc()]
        │
        │ Node.js https.request (User-Agent: LSC-App/x.x.x)
        ▼
[api.github.com/repos/Lawrence7y/LSC/releases/latest]
        │
        │ 返回 release.tag_name (e.g. "v1.2.0")
        ▼
[compareVersions(localVersion, remoteVersion)]
        │
        ├── 有新版本 → send('update-status', { type: 'available', version, releaseUrl })
        ├── 已最新   → send('update-status', { type: 'not-available', version })
        └── 出错     → send('update-status', { type: 'error', message })
```

### 9.2 关键实现细节

*   **触发方式**：纯手动——用户在设置页点击"检查更新"按钮。应用启动时**不**自动检测，避免拖慢启动速度或消耗流量。
*   **版本比较**：`compareVersions(local, remote)` 将版本号（去除前缀 `v`）拆分为 `[major, minor, patch]` 三元组逐段比较，支持标准 semver 格式（`v1.2.3` / `1.2.3`）。
*   **结果缓存**：`_lastUpdateCheck` 缓存最近一次检测结果（TTL = **5 分钟**），同一会话内短时间多次点击直接命中缓存，避免频繁触发 GitHub API 限流（未认证 IP 限速 60 次/小时）。
*   **超时保护**：HTTP 请求超时 **10 秒**（`timeout: 10000`）。超时时 `req.destroy()` 中断连接，向前端推送友好中文错误提示。
*   **网络错误分类**：
    *   `ENOTFOUND` → "无法连接到 GitHub，请检查网络连接"
    *   HTTP 403/429 → "GitHub API 请求过于频繁，请稍后重试"
    *   HTTP 404 → "未找到发布版本（仓库可能尚未发布 Release）"
    *   超时 → "检查更新超时（可能是网络问题或 GitHub 访问受限），请稍后重试"
*   **下载方式**：发现新版本后，"前往下载"按钮调用 `shell.openExternal(releaseUrl)` 跳转浏览器打开对应 GitHub Release 页，由用户手动下载安装包。**不实现自动下载/自动安装**，避免未签名安装包触发系统安全拦截。

### 9.3 WebSocket 状态消息格式

主进程通过 `mainWindow.webContents.send('update-status', payload)` 推送更新状态，preload.ts 通过 `onUpdateStatus` 回调转发到 React 组件：

| `type` | 附加字段 | 含义 |
| :--- | :--- | :--- |
| `checking` | — | 正在向 GitHub API 发起请求 |
| `available` | `version`, `releaseUrl`, `releaseNotes`, `assets` | 发现新版本 |
| `not-available` | `version` | 已是最新版本 |
| `error` | `message` | 检测失败，附带中文错误原因 |

### 9.4 涉及文件

| 文件 | 作用 |
| :--- | :--- |
| [`electron/main.ts`](file:///d:/Project/直播切片多人/lsc-electron/electron/main.ts) | `fetchGitHubLatestRelease()`、`compareVersions()`、三个 IPC handler |
| [`electron/preload.ts`](file:///d:/Project/直播切片多人/lsc-electron/electron/preload.ts) | `checkForUpdate`、`downloadUpdate`、`onUpdateStatus` 等 IPC 桥接 |
| [`src/pages/Settings/index.tsx`](file:///d:/Project/直播切片多人/lsc-electron/src/pages/Settings/index.tsx) | "关于"卡片内的检查更新 UI + 状态展示 |

### 9.5 核心实现代码与算法逻辑

#### 9.5.1 API 请求构造 (`main.ts`)
使用 Node.js 原生 `https` 发起 GET 请求以保证打包后体积最小，并显式配置 `User-Agent` 与 `Accept` 头，避免由于空 User-Agent 遭到 GitHub CDN 拒绝访问：
```typescript
const options = {
  hostname: 'api.github.com',
  path: '/repos/Lawrence7y/LSC/releases/latest',
  method: 'GET',
  headers: {
    'User-Agent': `LSC-App/${app.getVersion()}`,
    'Accept': 'application/vnd.github+json',
  },
  timeout: 10000, // 10秒超时
}
```

#### 9.5.2 Semver 版本号比对算法 (`main.ts`)
通过过滤掉非数字符号和 `v` 前缀，使用三元组 `[major, minor, patch]` 逐位进行数值比较：
```typescript
function compareVersions(local: string, remote: string): number {
  const normalize = (v: string) => v.replace(/^v/, '').split('.').map(Number)
  const [la, lb, lc] = normalize(local)
  const [ra, rb, rc] = normalize(remote)
  if (ra !== la) return ra > la ? 1 : -1
  if (rb !== lb) return rb > lb ? 1 : -1
  if (rc !== lc) return rc > lc ? 1 : -1
  return 0
}
```

#### 9.5.3 5分钟短周期内存防抖缓存 (`main.ts`)
使用全局变量记录上一次请求的时间戳，避免快速重复点击产生大量无用 GitHub 请求被限制 IP：
```typescript
let _lastUpdateCheck: { time: number; result: object } | null = null
const _UPDATE_CACHE_MS = 5 * 60 * 1000 // 5分钟

// check-for-update 内部判断：
if (_lastUpdateCheck && Date.now() - _lastUpdateCheck.time < _UPDATE_CACHE_MS) {
  mainWindow.webContents.send('update-status', _lastUpdateCheck.result)
  return { success: true }
}
```

---

## 10. 运行与构建命令指南

### 10.1 依赖安装

```bash
# Python 依赖安装
pip install -r requirements.txt

# Electron 前端依赖安装
cd lsc-electron && npm install
```

### 10.2 开发启动方式

1.  **纯 Python 后端开发调试**（无 GUI，仅启动 WebSocket 服务与 Qt 事件循环）：
    ```bash
    cd python-backend && python main.py
    ```
2.  **前端 Vite 开发服务独立启动** (无 Electron 壳，常用于纯 UI 页面调整)：
    ```bash
    cd lsc-electron && npx vite --config vite.dev.config.ts
    ```
3.  **Electron 开发模式** (一键拉起后端与前端)：
    ```bash
    cd lsc-electron && npm run dev
    ```

### 10.3 打包构建系统

Electron 应用使用 `electron-builder` 进行打包：
*   **PowerShell 自动化打包**（首选，带状态提示与编码净化）：
    ```powershell
    cd lsc-electron
    .\build-installer.ps1
    ```
*   **生产构建三步命令**：
    ```bash
    npx tsc --noEmit
    npx vite build
    npx electron-builder
    ```

#### 10.3.1 打包资源路径映射规则

生产环境下，Electron 使用 `asar` 格式归档。资源提取和 Python 后端运行路径必须严格遵守以下条件：
*   `lsc/`、`python-backend/` 和 `scripts/` 打入 `extraResources`。
*   路径检测判断：
    ```typescript
    const isPackaged = process.resourcesPath !== undefined;
    const backendDir = isPackaged
      ? path.join(process.resourcesPath, 'python-backend')
      : path.join(__dirname, '../../../python-backend');
    ```
*   HTML 入口加载判定：
    ```typescript
    if (isPackaged) {
      mainWindow.loadFile(path.join(process.resourcesPath, 'app.asar', 'dist', 'index.html'));
    } else {
      mainWindow.loadFile(path.join(__dirname, '../../dist/index.html'));
    }
    ```

## 11. 安全防御与防御性工程设计

为防止在后续代码修改中引入安全漏洞或引发进程崩溃，系统实现了一整套防御性策略：

### 11.1 文件系统安全屏障 (`_isSafePath`)

主进程在执行 `open-path` (打开文件) 和 `show-item-in-folder` (在资源管理器中定位文件) 时，必须强制调用安全沙箱检验函数 `_isSafePath(p)`。任何文件操作修改必须通过该防线：
1.  **路径白名单**：目标路径解析 (`path.resolve`) 后的根目录必须在允许的范围内：
    *   Electron 数据目录：`app.getPath('userData')`
    *   用户家目录下的 LSC 目录：`~/LSC`
2.  **可执行黑名单**：严禁打开包含可执行后缀的文件，防范 RCE（远程代码执行）漏洞。黑名单为：
    `['.exe', '.bat', '.ps1', '.cmd', '.vbs', '.scr']`。

### 11.2 子进程运行安全设计

*   **进程环境变量白名单**：启动 Python 后端子进程时，严禁污染或直接透传全部父进程环境变量。只透传精简的安全环境变量：
    `PATH`、`USERPROFILE`、`APPDATA`、`LOCALAPPDATA`、`TEMP`、`TMP`、`HOME`、`SYSTEMROOT`、`PATHEXT`、`PYTHONUNBUFFERED=1` 及 `PYTHONPATH`。
*   **非阻塞管道防死锁**：在 MSE 转码或录制捕获子进程读取数据时，必须对 stdout/stderr 描述符调用 `_set_stream_nonblocking()`（Windows 下利用 `msvcrt` 接口，POSIX 下使用 `fcntl` 接口设置 `O_NONBLOCK`），保证在高负载下读写管道不发生物理死锁。
*   **Windows 权限防御**：在 Windows 平台下启动 Python 后端子进程时，必须将 `detached` 参数设为 `true`，以脱离父进程的受限 Token，防范由于 `WinError 5` 权限不足导致写入用户主目录失败。

### 11.3 崩溃容错与日志滚动体系

*   **未处理异常捕获**：后端入口 `main.py` 安装了 `sys.excepthook`，任何后台线程或主线程发生的未捕获异常，都会完整格式化 traceback 写入日志，防止服务瞬时悄无声息崩溃退出。
*   **Qt 消息重定向**：安装了 `qInstallMessageHandler`，将 PySide6 的底层内核 Critical/Fatal 警告无缝桥接到 Python logging 体系，防止 Qt 异常直接 abort 进程。
*   **日志轮转机制**：日志文件大小限制为 **2MB**，最大备份数为 **5个**，采用 `RotatingFileHandler` 滚动覆盖，确保不会因长期挂机导致磁盘爆满。
*   **优雅停机保护**：
    *   主进程销毁时，Windows 环境下使用 `taskkill /T /F` 强杀子进程树，防止 FFmpeg 僵尸进程残留挂载占用端口。
    *   POSIX 环境下先发 `SIGTERM`，如果 3 秒内未退出再调度 `SIGKILL`，采用非阻塞的轮询检测，防止同步忙等待阻塞 Electron UI 主线程。

### 11.4 错误处理与异常捕获规范

> [!IMPORTANT]
> 本节为 2026-07-05 错误处理全面审查后确立的规范。所有新增代码必须遵守。

#### 核心原则

1.  **禁止静默吞异常**：`except Exception: pass` 是**禁止**的，除非是资源清理路径（如 `streamer.stop()`），且必须添加 `_log.debug` 日志。
2.  **禁止 `assert` 用于运行时校验**：`assert` 在 Python `-O` 模式下会被移除。运行时类型检查必须使用显式 `if` + `raise ValueError` 或返回错误结果。
3.  **异常分类捕获**：避免过宽的 `except Exception`。应优先捕获具体异常类型（如 `HTTPError`、`OSError`、`JSONDecodeError`），对可区分的错误给出精确提示。

#### 日志级别规范

| 级别 | 适用场景 | 示例 |
| :--- | :--- | :--- |
| `DEBUG` | 高频消息、清理路径异常、可忽略的失败 | `streamer.stop()` 失败、进度回调异常 |
| `INFO` | 业务关键节点、重连尝试 | 房间添加、录制启动、MSE 重连尝试 N/M |
| `WARNING` | 可恢复异常、配置回退、降级操作 | settings.json 损坏回退默认值、API 超时 |
| `ERROR` | 不可恢复异常、handler 崩溃（需 traceback 时加 `exc_info=True`） | 保存设置失败、MSE 重连耗尽 |

#### import 规范

*   **统一顶部 import**：标准库 `import`（如 `subprocess`、`json`、`os`）必须在文件顶部统一导入。禁止在函数内使用局部 `import` 来规避命名问题——这正是 `handle_check_dependencies` P0 BUG 的根因。
*   **例外**：重量级或可能不可用的第三方库（如 `numpy`、`PySide6`）可以延迟到函数内 import，但必须在文档中注明原因。

#### WebSocket Handler 错误响应

*   handler 抛异常时，`server.py` 自动发送 `{success: False, error: str(e)}` 响应。
*   对于可预见的错误（如磁盘满、权限不足），handler 应主动 try/except 并通过 `humanize_error()` 转化为友好提示：
    ```python
    try:
        save_settings(data)
    except OSError as exc:
        _log.error("保存设置失败: %s", exc)
        return {'success': False, 'error': humanize_error(str(exc))}
    ```

#### 错误友好化系统 (`lsc/utils/error_messages.py`)

*   `humanize_error(raw)` — 将原始错误字符串映射为中文友好提示。
*   `is_recoverable_error(raw)` — 判断是否值得自动重连（网络抖动=True，权限/磁盘=False）。
*   `_PRESERVE_RAW_PATTERNS` — 权限/磁盘类错误，追加原始信息便于定位路径。
*   **中文 Windows 错误覆盖**：正则必须同时覆盖中英文错误信息（如 "磁盘空间不足" / "disk full"、"连接超时" / "Connection timed out"）。

#### MSE 重连控制流

*   `_on_mse_error` 采用 **while 循环**（非递归）实现重连控制，避免异步递归控制流难以推理。
*   最大重连次数 `_MSE_MAX_RECONNECT = 3`，指数退避 `2s → 4s → 8s`（上限 30s）。
*   on_error 回调仍可调用 `_on_mse_error`，但首次进入即开始循环，不形成递归链。

#### 前端通知策略

*   窗口聚焦时跳过非关键通知（如"录制已开始"）。
*   **关键错误事件**（`clip_failed`、`recording_started` 失败、`room_connect_finished` 失败、`reconnect_failed`）始终通知，即使窗口聚焦。
*   MSE segment watchdog：前端检测到预览流超过 10s 无数据时自动触发 WebSocket 重连。

---

## 12. 交互设计与辅助特性

### 12.1 全局快捷键约束表 (`useKeyboardShortcuts.ts`)

为了提升多视角的剪辑效率，系统注册了键盘快捷键：
*   **焦点判定原则**：当输入焦点在 `input`、`textarea` 或 `select` 等交互控件上时，必须被自动拦截释放，避免用户打字搜索时误触发切片操作（Ctrl+1/2/3 页面导航及 F5 刷新不受此限制）。
*   **快捷键对照表**：

| 功能 | 快捷键 | 动作 ID |
| :--- | :--- | :--- |
| **切换页面：Dashboard** | `Ctrl + 1` | `page:dashboard` |
| **切换页面：工作台** | `Ctrl + 2` | `page:workbench` |
| **切换页面：设置** | `Ctrl + 3` | `page:settings` |
| **播放 / 暂停** | `Space` (空格) | `play:toggle` |
| **标记时间轴入点** | `i` | `mark:in` |
| **标记时间轴出点** | `o` | `mark:out` |
| **切换当前录制状态** | `r` | `record:toggle` |
| **静音 / 取消静音** | `m` | `mute:toggle` |
| **全屏预览** | `f` | `fullscreen` |
| **一键批量启动录制** | `Ctrl + r` | `batch:record` |
| **一键批量停止录制** | `Ctrl + Shift + r` | `batch:stop` |
| **多房间卡片全选** | `Ctrl + Shift + A` | `select:all` |
| **触发当前导出** | `Ctrl + e` | `export:clip` |
| **刷新页面** | `F5` | `page:reload` |

---

## 13. 开发者常用维护指令清单

### 13.1 Python 测试与代码检查
```bash
# 执行完整测试套件（测试时需在 CI/本地设置 QT_QPA_PLATFORM=offscreen）
pytest -v

# 检查特定模块覆盖率
pytest -v --cov=lsc --cov-report=term

# Ruff 代码静态检查
ruff check lsc/

# Mypy 静态类型校验
mypy lsc/
```

### 13.2 前端类型与打包检查
```bash
# 静态类型非编译校验
npx tsc --noEmit
```

### 13.3 本地调试与日志定位
*   **前端渲染进程控制台**：`Ctrl + Shift + I` 唤出 Chrome DevTools。
*   **本地日志存储绝对路径**：
    *   Windows 后端：`%APPDATA%\lsc-electron\logs\backend.log` 滚动日志。
    *   主进程与日志记录器：`%APPDATA%\lsc-electron\logs\debug.log`。

