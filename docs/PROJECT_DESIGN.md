# LSC 直播切片多人系统 - 完整程序设计文档

> 本文档基于代码现状生成，覆盖系统的全部模块、架构、数据流和实现细节。
>
> 生成日期：2026-06-30

---

## 第一部分：项目概述

### 1.1 项目定位

LSC（Live Stream Clipper）是一个**多直播间录制与切片工具**，核心能力：
- 最多 **12 路并发录制**
- 最多 **4 路并发预览**
- 跨房间同步预览与选区标记
- 一键导出多视角对齐片段

明确**不做**：非线性编辑、特效转场、实时推流、调色。

### 1.2 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Electron + React + TypeScript + Vite + Ant Design + Zustand |
| 桥接 | Python (PySide6 Qt) + asyncio WebSocket |
| 核心 | Python (FFmpeg 子进程控制 + NumPy 音频处理) |
| 编解码 | FFmpeg (libx264/libx265/h264_nvenc/h264_qsv/h264_amf/copy) |

### 1.3 目录结构

```
直播切片多人/
├── lsc/                          # 核心业务层 (Python 包)
│   ├── core/
│   │   ├── models.py             # 5 个核心 dataclass DTO
│   │   └── services/
│   │       ├── recording_service.py   # 录制门面
│   │       ├── export_service.py      # 导出门面
│   │       ├── mse_streamer.py        # MSE fMP4 转码
│   │       └── frame_capture.py       # 帧捕获
│   ├── editor/audio_aligner.py   # 音频互相关对齐
│   ├── exporter/clip.py          # FFmpeg 切片导出
│   ├── platforms/                # 平台适配器 (9 个)
│   ├── recorder/capture.py       # FFmpeg 录制控制
│   ├── gui/multi_room/manager.py # 多房间编排核心
│   ├── utils/                    # 错误友好化、进程启动器
│   └── config.py                 # LscConfig + ExportProfile
├── python-backend/               # 桥接服务层
│   ├── main.py                   # 后端入口 (双线程)
│   ├── server.py                 # WebSocket 服务器
│   ├── message_bridge.py         # Qt 信号槽桥接
│   ├── persistence.py            # rooms.json 持久化
│   ├── handlers/room_handler.py  # WebSocket 指令处理
│   └── settings.json             # 运行时设置
├── lsc-electron/                 # 前端桌面包
│   ├── electron/main.ts          # Electron 主进程
│   ├── electron/preload.ts       # IPC 桥接
│   └── src/
│       ├── store/appStore.ts     # Zustand 全局状态
│       ├── services/
│       │   ├── mediaSourcePlayer.ts   # MSE 播放器
│       │   ├── websocket.ts          # WebSocket 客户端
│       │   └── exportPresets.ts       # 导出预设
│       ├── pages/
│       │   ├── Dashboard/             # 仪表盘
│       │   ├── Workbench/             # 工作台 (核心)
│       │   └── Settings/              # 设置页
│       ├── components/
│       │   ├── VideoPreview.tsx       # 视频预览
│       │   ├── Timeline/              # 时间线
│       │   ├── ExportQueue/           # 导出队列
│       │   └── Layout/MainLayout.tsx
│       ├── hooks/
│       │   ├── useWebSocket.ts
│       │   └── useKeyboardShortcuts.ts
│       └── utils/previewAudioAligner.ts  # 前端音频捕获
├── data/rooms.json               # 房间列表持久化
└── docs/superpowers/             # 设计文档
```

---

## 第二部分：三层架构设计

### 2.1 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 前端层 (Electron Render)                                 │
│    React + TS + Zustand                                     │
│    职责：UI 交互、MSE 预览播放器、快捷键、导出队列管理         │
└──────────────────────────┬──────────────────────────────────┘
                           │ WebSocket (localhost:19876~19880)
┌──────────────────────────┴──────────────────────────────────┐
│ 2. 桥接与服务层 (python-backend/)                           │
│    Qt 主线程 (MultiRoomManager) + 工作线程 (WebSocket)        │
│    职责：跨线程消息桥接、生命周期管理、状态广播               │
└──────────────────────────┬──────────────────────────────────┘
                           │ Qt 信号槽 (_execute)
┌──────────────────────────┴──────────────────────────────────┐
│ 3. 核心业务层 (lsc/)                                        │
│    平台解析、FFmpeg 录制/导出、MSE 转码、音频对齐             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 为什么需要三层分离

**核心矛盾**：PySide6 的 GUI 对象和 `MultiRoomManager` 必须运行在 **Qt 主线程**（否则控件崩溃），但 WebSocket 服务器需要处理网络 I/O，如果在 Qt 主线程会阻塞 UI。

**解决方案**：
- WebSocket 服务器运行在**工作线程**（asyncio 事件循环）
- `MultiRoomManager` 运行在 **Qt 主线程**
- 两者通过 `QtManagerBridge` 通信

---

## 第三部分：跨线程通信机制

### 3.1 QtManagerBridge 双层桥接

**位置**：`python-backend/message_bridge.py`

#### 同步调用原语 `bridge.call(fn, *args)`

工作线程发起调用 → Qt 信号派发到主线程执行 → `threading.Event` 阻塞等待结果：

```
[WebSocket Handler 线程]                                    [Qt 主线程]
          │                                                    │
    1. bridge.call(fn, *args)                                  │
          │ ─── 2. 发射 Qt 信号 _execute ───────────────────> │
          │                                                    │ 3. 执行 fn
          │ <── 5. event.set() 唤醒 ───────────────────────── │ 4. 完成/异常
    6. 返回结果/抛出异常                                       │
```

- 默认超时 **10 秒**
- 超时后工作线程恢复，但主线程可能仍在执行（造成命令乱序风险）

#### 状态广播队列 `queue_broadcast(msg)`

主线程状态更新（房间连接、录制进度）不能直接调用 asyncio，写入线程安全 FIFO 队列：

```
[Qt 主线程]                     [WebSocket 工作线程]
   queue_broadcast(msg) ──> FIFO 队列 ──> _broadcast_coroutine
                                              │ 100ms 轮询
                                              ▼
                                          合并 rooms_updated
                                              │
                                              ▼
                                          ws.send(msg)
```

### 3.2 消息合并优化

**位置**：`python-backend/main.py:197-234`

`_broadcast_coroutine` 每 100ms 消费队列，对连续的 `rooms_updated` 消息做**合并**：多条只广播最新一条，避免前端 JSON.parse + React 重渲染爆炸。

### 3.3 高频消息日志降级

以下消息在日志中降级为 DEBUG：
- `mse_segment`（30fps 分片推送）
- `mse_init`（初始化段）
- `rooms_updated`（房间状态）
- `export_progress`（导出进度）

### 3.4 WebSocket 端口回退

**位置**：`python-backend/server.py:193-213`

主端口 `19876` 被占用时自动尝试 `19877 → 19878 → 19879 → 19880`。`_bound_port` 记录实际端口，后端启动时打印 `WebSocket server ready at ws://localhost:PORT`，Electron 主进程通过 stdout 正则匹配捕获实际端口。

---

## 第四部分：核心数据模型

### 4.1 五个核心 DTO

**位置**：`lsc/core/models.py`

全部使用 `@dataclass(slots=True)`，无业务逻辑，纯数据传输对象。

#### ① StreamQuality

```python
@dataclass(slots=True)
class StreamQuality:
    name: str       # "原画"、"高清"
    url: str        # 流地址
```

#### ② RoomInfo

适配器流解析后的完整元数据：

```python
@dataclass(slots=True)
class RoomInfo:
    platform: str               # "bilibili"
    room_url: str               # 直播间地址
    stream_url: str             # 实际流地址
    title: str
    streamer: str
    is_live: bool
    qualities: list[StreamQuality]
    selected_quality: str
    headers: dict[str, str]     # 平台鉴权头
    error: str
    error_code: str             # unsupported_url | offline | restricted | parse_failed
    raw: dict                   # 原始响应
```

#### ③ RecordingSession

一次录制过程上下文快照：

```python
@dataclass(slots=True)
class RecordingSession:
    session_id: str
    room_url: str
    output_dir: str
    output_path: str
    status: RecordingStatus     # idle|connecting|recording|stopped|error
    start_time: datetime
    end_time: datetime
    duration_sec: float
    file_size_mb: float
    encoder: str
    crf: int
    bitrate: str
    last_error: str
    reconnect_attempts: int
    max_reconnect_attempts: int
```

#### ④ Clip（核心）

视频片段，时间线高光与切片位置的核心定义：

```python
@dataclass(slots=True)
class Clip:
    clip_id: str
    title: str
    start_sec: float             # 预览时间轴入点
    end_sec: float               # 预览时间轴出点
    source_video: str
    output_path: str
    thumbnail_path: str
    duration_sec: float
    file_size_mb: float
    score: float
    exported: bool
    error: str

    # ⭐ 关键：墙钟时间戳（跨时间轴对齐桥接）
    mark_in_wallclock: float     # time.monotonic() 入点
    mark_out_wallclock: float    # time.monotonic() 出点

    # ⭐ 关键：内容偏移量（多房间导出对齐）
    content_offset: float        # 音频互相关计算结果
```

> **设计要点**：Clip 有两套时间戳。`start_sec/end_sec` 是前端 MSE `currentTime`，与录制文件时间轴不同步（预览流是独立 FFmpeg 进程）。`mark_in/out_wallclock` 用单调时钟记录标记时刻，用于导出时精确映射到录制文件。

#### ⑤ ExportOptions

```python
@dataclass(slots=True)
class ExportOptions:
    codec: str                   # libx264|h264_nvenc|copy...
    crf: int                     # 0-51
    preset: str                  # medium|fast|p4...
    audio_bitrate: str           # "128k"
    rate_mode: str                # crf|bitrate|unrestricted
    video_bitrate: str
    resolution: str              # "1920x1080" 或 ""
    fps: float                   # 0=保持源帧率
    vertical_crop: bool          # 9:16 竖屏裁剪
    generate_thumbnail: bool
```

### 4.2 ExportProfile（编码配置）

**位置**：`lsc/config.py`

```python
@dataclass
class ExportProfile:
    crf: int = 23
    codec: str = "libx264"
    preset: str = "medium"
    audio_bitrate: str = "128k"
    vertical_crop: bool = False
    rate_mode: str = "crf"           # crf|bitrate|unrestricted
    video_bitrate: str = "8000k"
    resolution: str = ""             # 空=不缩放
    fps: float = 0.0                 # 0=保持源
```

**关键方法**：
- `ffmpeg_video_args()` → 构造 `-c:v` 参数（NVENC 用 `-rc vbr -cq`，CPU 用 `-crf`）
- `ffmpeg_audio_args()` → 构造 `-c:a` 参数（copy 模式直拷，否则 AAC）
- `ffmpeg_filter_args()` → 构造 `-vf` 滤镜（scale + fps + crop）
- `_hardware_preset()` → libx264 preset 映射到硬件 preset（NVENC: p1-p7，AMF: speed/balanced/quality）

---

## 第五部分：平台适配系统

### 5.1 Protocol + Registry 模式

**位置**：`lsc/platforms/base.py`, `lsc/platforms/registry.py`

#### Protocol 定义

```python
class PlatformAdapter(Protocol):
    platform: str              # 唯一标识
    display_name: str          # 用户友好名

    def can_handle(self, url: str) -> bool: ...
    def parse(self, url: str) -> StreamInfo: ...
```

#### ⭐ 无状态约束（关键设计）

所有适配器实例必须**无状态**：
- `parse()` 不得修改实例属性
- 所有解析上下文用局部变量
- 最终填充到 `StreamInfo` 返回

这保证模块级单例可被多房间并发安全复用，无需加锁。

### 5.2 Registry 缓存与路由

#### TTL 缓存控制

| 场景 | TTL | 目的 |
|------|-----|------|
| 解析成功 | 30s | 防止高频轮询熔断平台 API |
| 解析失败 | 10s | 防止前端短时间重试压垮网络 |

每访问 **20 次**触发后台守护线程清理过期缓存。

#### URL 快速路由

`_URL_ROUTER` 按 host 直接路由到对应平台：
```
live.bilibili.com → BilibiliAdapter
www.douyin.com   → DouyinAdapter
...
```

未匹配才全局线性扫描，避免无关平台的 HTTP 探测。

#### 画质预设候选

`QUALITY_PRESET_CANDIDATES` 定义"原画/高清/流畅"候选 quality key 顺序，按可用性首个命中。

### 5.3 已实现适配器

| 适配器 | 平台 | 鉴权 |
|--------|------|------|
| DirectAdapter | 直链 | 无 |
| DouyinAdapter | 抖音 | 精简签名 |
| BilibiliAdapter | B站 | Cookie/BiliSession |
| HuyaAdapter | 虎牙 | JS 签名函数 |
| KuaishouAdapter | 快手 | - |
| DouyuAdapter | 斗鱼 | - |
| XiaohongshuAdapter | 小红书 | - |
| WeiboAdapter | 微博 | - |
| GenericPageAdapter | 兜底 | 通用 HTML `<video>` |

### 5.4 统一错误码

| 错误码 | 含义 |
|--------|------|
| `unsupported_url` | 无法识别的链接 |
| `offline` | 未开播 |
| `restricted` | 平台限制（地理围栏、禁播） |
| `parse_failed` | 解析逻辑异常 |

---

## 第六部分：录制系统设计

### 6.1 录制服务门面

**位置**：`lsc/core/services/recording_service.py`

`RecordingService` 作为统一入口，封装：流解析 → FFmpeg 启动 → 状态跟踪 → 停止校验。底层 `StreamCapture` 维护 FFmpeg 子进程生命周期。

### 6.2 StreamCapture 核心类

**位置**：`lsc/recorder/capture.py`

#### 状态机

```
IDLE → CONNECTING → RECORDING → STOPPED
                              → ERROR
```

#### FFmpeg 命令构造

```python
cmd = [ffmpeg, "-y", "-loglevel", "warning"]

# HTTP 流重连参数
if url.startswith(("http://", "https://")):
    cmd += ["-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5", "-timeout", "30000000"]

cmd += ["-i", url]

# 编码模式
if codec == "copy":
    cmd += ["-c", "copy"]
elif codec == "custom":
    pass  # 由 extra_args 提供
else:
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "23"]
    cmd += ["-c:a", "aac", "-b:a", "128k"]

# 输出格式：MP4 + 流式写入
cmd += ["-f", "mp4", "-movflags", "frag_keyframe+empty_moov+faststart", output_path]
```

#### 启动探测（5 秒超时）

`_wait_for_startup_data()`：轮询检查输出文件是否已创建且有内容，超时 5 秒。主要用于检测：
- 直播流是否已开播
- URL 是否有效
- FFmpeg 是否能正常连接

#### 三级优雅停止

```
Level 1: stdin 发送 'q' → 等 5s（让 FFmpeg 写 moov atom）
Level 2: terminate() (SIGTERM) → 等 3s
Level 3: kill() (SIGKILL) → 等 5s
Level 4: 记录孤儿 PID，标记 ERROR
```

#### 健康检查

`check_health()` 执行两项检查：
1. **进程存活**：FFmpeg 退出则清理并标记 ERROR
2. **文件增长**：连续 3 次检查文件大小无变化则判定卡住

#### 共享 stderr 线程池

所有 `StreamCapture` 实例共享一个 `ThreadPoolExecutor(max_workers=4)` 读取 stderr，避免大量并发录制时创建过多线程（每个 Python 线程在 Windows 占 ~8MB 栈）。

### 6.3 三层磁盘安全防线

| 层 | 触发时机 | 阈值 | 动作 |
|----|---------|------|------|
| 预检 | 录制前 | ≥8GB | 拒绝启动 |
| 运行时 | 每 10s 检查 | <2GB | 强制停止 |
| 目录回退 | 默认目录不可写 | - | 回退到 `~/.lsc/output` |

### 6.4 心跳分层

**位置**：`lsc/gui/multi_room/manager.py:1734-1846`

单一 `QTimer`（1s）按 tick 计数器分层：

| 频率 | 任务 |
|------|------|
| High（每秒） | elapsed time、播放位置同步 |
| Medium（每 5s） | 文件大小查询（QThreadPool 后台）、FFmpeg 看门狗、自动重连 |
| Low（每 10s） | 磁盘空间检查 |

### 6.5 自动重连（指数退避）

`_attempt_recording_reconnect`：
- 基础 2s × 2^attempts，封顶 30s
- 最多 3 次
- 错误必须 `is_recoverable_error` 才重连

**可恢复错误**：`Server returned 5xx`、`Connection timed out/refused/reset`、`Invalid data found`
**不可恢复错误**：`Permission denied`、`No space left`、`403`、`404`、`Encoder not found`

### 6.6 录制文件三层验证

**位置**：`lsc/recorder/capture.py:66-119` `validate_recording()`

1. **路径验证**：非空且文件存在
2. **大小验证**：> 0.1MB
3. **格式签名验证**（读前 12 字节）：
   - MP4：偏移 4 处为 `ftyp`
   - FLV：前 3 字节为 `FLV`
   - MKV：前 4 字节为 EBML 头 `0x1A45DFA3`

---

## 第七部分：预览系统设计

### 7.1 两套预览实现

| 方案 | 场景 | 实现 |
|------|------|------|
| libmpv 原生 | Python 原生 GUI | `python-mpv` 嵌入 PySide6 `MpvWidget` |
| MSE fMP4 | Electron 前端 | FFmpeg stdout → WebSocket → `<video>` |

### 7.2 ⭐ 核心约束：预览与录制完全独立

```
直播 CDN ──连接1──> FFmpeg A (StreamCapture) ──> 磁盘录制文件
         └──连接2──> FFmpeg B (MseStreamer) ──> stdout fMP4 ──> WebSocket ──> 前端 <video>
```

**两条独立的 FFmpeg 进程和直播流连接**：
- 互不干扰、不共享管道状态
- 支持只录不看、只看不录、同时进行
- 预览崩溃不影响录制
- 录制用 `-c copy` 直拷，预览转码降分辨率

### 7.3 MseStreamer 核心设计

**位置**：`lsc/core/services/mse_streamer.py`

#### FFmpeg 命令

```bash
ffmpeg -i <stream_url> \
  -c:v <h264_nvenc|libx264> \
  -f mp4 \
  -movflags empty_moov+default_base_moof+frag_keyframe \
  pipe:1
```

关键参数：
- `empty_moov`：文件头在结束时写入（支持流式）
- `default_base_moof`：每个 Fragment 独立可解码
- `frag_keyframe`：每个关键帧生成一个片段

#### Box 分解与边界检测

读取 stdout 字节流，定位 MP4 Box 标记：

| Box 组合 | 作用 |
|---------|------|
| `ftyp + moov` | **Init Segment**（只发一次，缓存到 `_last_init_segment`） |
| `moof + mdat` | **Media Segment**（持续推送，30fps） |

如果连续读取未检测到边界且累积超 **512KB**（`_MAX_SEGMENT_BYTES`），强制切分，防止缓冲区溢出。

#### NVENC 硬解检测

`_check_nvenc` 进程级缓存（一次检测）：
- 可用 → `h264_nvenc` + `preset p4` + `tune ll`
- 不可用 → 降级 `libx264 veryfast`

### 7.4 并发上限与动态降级

| 预览路数 | 分辨率上限 |
|---------|-----------|
| ≤4 | 原画 |
| ≥6 | ≤854×480 |
| ≥8 | ≤640×360 |

### 7.5 init 段竞态修复

`mse_init` 可能早于 `rooms_updated` 到达（前端 VideoPreview 未挂载）：
1. 前端挂载后主动发 `request_mse_init`
2. 后端 `replay_init()` 从 `_last_init_segment` 缓存补发

### 7.6 MSE 启动流程时序

1. 前端发 `enable_preview {mode: "mse"}`
2. 后端 `_handle_mse_preview` 调用 `mgr.refresh_stream_url()`（B站可达 10s+，在 `_recording_executor` 线程池执行）
3. 创建 `MseStreamer` 并 `start()`，FFmpeg 启动探测 2~8s
4. FFmpeg 输出 init 段，后端异步广播 `mse_init`
5. 后续 media 段持续以 `mse_segment` 推送，每个 Fragment Duration = 1000ms

---

## 第八部分：切片系统设计

### 8.1 三阶段流程

```
标记 (mark_in/out) → 映射 (wallclock mapping) → 导出 (FFmpeg 裁切)
```

### 8.2 标记阶段

**位置**：`python-backend/handlers/room_handler.py:894-952`

用户按 `i`/`o` 触发 `set_mark_in/out`：

```python
room.mark_in = float(time_value)            # MSE currentTime（预览时间轴）
room.mark_in_wallclock = time.monotonic()  # 单调时钟绝对时间（桥接关键）
```

- `mark_in/mark_out`：预览时间轴位置，与录制文件时间轴**不同步**
- `mark_in_wallclock/mark_out_wallclock`：`time.monotonic()` 绝对物理时间

### 8.3 时间轴对齐映射

**位置**：`python-backend/handlers/room_handler.py:1306-1325`

```
export_start = mark_in_wallclock  - recording_start_mono - content_offset
export_end   = mark_out_wallclock - recording_start_mono - content_offset
```

- `recording_start_mono`：录制启动时的 `time.monotonic()`，存于 `room.recording_start_mono`
- `content_offset`：音频互相关计算的偏移，正值表示该房间领先基准，导出时 `-ss` 增加

> ⚠️ **关键约束**：`mark_in/mark_out` **不能**直接作为 FFmpeg `-ss` 参数，必须通过墙钟映射。

**降级方案**：墙钟不可用时用固定 `preview_latency`（默认 2.0s）。

### 8.4 音频互相关对齐

**位置**：`lsc/editor/audio_aligner.py`

#### 算法流程

```
[房间 A 音频 3.0s]    [房间 B 音频 3.0s]
       16kHz mono float32 PCM
              │
              ▼
   FFT 互相关: irfft(rfft(A) * rfft(B[::-1]))
              │
              ▼
   找峰值 + 抛物线插值（亚毫秒精度）
              │
              ▼
   以最慢房间为基准，计算各房间 content_offset
```

#### 采样规范

- 采样率：**16000Hz**（Mono，float32）
- 时长：**3.0s**（`AUDIO_DURATION`）
- 缓冲：录制文件读取距直播边缘 `_SEEK_BUFFER = 2.0s`

#### 基准房间选择

以**拉流最慢**的房间为基准：
```python
slowest_id = min(valid_ids, key=lambda rid: raw_offsets[rid])
slowest_offset = raw_offsets[slowest_id]
for rid in valid_ids:
    offsets[rid] = max(0.0, raw_offsets[rid] - slowest_offset)
```

#### 置信度防线

```python
_CORRELATION_THRESHOLD = 0.1
```

> ⚠️ **设计缺陷**：`compute_offset` 不会因低置信度失败，仍返回 offset 和 score。前端在 `lsc-electron/src/pages/Workbench/index.tsx:632-681` **照样应用低置信度偏移**，可能比对齐前更糟。

#### 已知场景局限

| 场景 | 效果 |
|------|------|
| 同一直播间多路拉流 | ✅ 良好（音频相同） |
| 同一比赛不同解说 | ❌ 失效（解说音频不同源，波形不相关） |
| 无声视频 | ❌ 降级为 0 偏移 |

### 8.5 导出阶段

**位置**：`lsc/exporter/clip.py`

#### ClipExporter

```
[录制文件] ──FFmpeg -ss {start} -to {end}──> [导出文件]
```

- 通过 `ExportService` 的 `ThreadPoolExecutor`（**最大并发 2**）
- 进度通过 `asyncio.run_coroutine_threadsafe` 异步广播 `export_progress`
- 完成后广播 `clip_completed`，含 `output_path`、`thumbnail_path`、`job_id`

#### 双编码模式

| 模式 | 说明 | 回退条件 |
|------|------|---------|
| `copy` | 流拷贝，零重编码，最快 | 存在视频滤镜或 `start_sec > 0` 时降级 libx264 |
| `reencode` | libx264 或硬件编码器重编码 | - |

#### 原子写入

输出先写入 `<safe_title>.<uuid8>_tmp.mp4`，FFmpeg 成功退出后 `os.replace` 更名；失败自动清理。

#### 竖屏裁剪算法

```
crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920
```

提取画面中心区域并缩放到标准 1080×1920。

#### 看门狗

300 秒看门狗，防止 FFmpeg 卡死。

#### 进度回调

`-progress pipe:1` 读取 FFmpeg 进度，解析 `out_time_ms`，节流回调（百分比变化 ≥1% 或间隔 ≥200ms）。

### 8.6 多房间批量导出

1. 对所有目标房间调用 `audio_aligner.py` 计算 `content_offset`
2. 每个房间用各自的 `content_offset` 调整 `-ss`
3. 所有房间并行提交，受 `_DEFAULT_MAX_CONCURRENT = 2` 限制排队

---

## 第九部分：错误友好化系统

### 9.1 错误友好化映射

**位置**：`lsc/utils/error_messages.py`

#### 保留原始错误（追加 `（原始错误：{raw}）`）

| 模式 | 中文提示 |
|------|---------|
| `Permission denied\|EACCES\|WinError 5\|拒绝访问` | 文件写入权限不足 |
| `No space left\|ENOSPC\|disk full` | 磁盘空间不足 |

#### 完全替换错误

| 模式 | 中文提示 |
|------|---------|
| `403\|Forbidden` | 平台拒绝了连接（403） |
| `404\|Not Found` | 直播流地址不存在（404） |
| `Connection refused\|ECONNREFUSED` | 无法连接到直播服务器 |
| `Connection timed out\|ETIMEDOUT` | 连接超时 |
| `Invalid data found` | 无法解析直播流数据 |
| `Encoder.*not found` | 缺少视频编码器 |

#### 错误分类

| 类别 | 模式 | 处理 |
|------|------|------|
| 可恢复 | `Server returned 5xx`、`Connection timed out`、`Invalid data found` | 自动重连 |
| 不可恢复 | `Permission denied`、`No space left`、`403`、`404`、`Encoder not found` | 不重连 |

---

## 第十部分：前端设计系统

### 10.1 设计令牌

**位置**：`lsc-electron/src/styles/tokens.css`, `lsc-electron/src/styles/global.css`

#### 主色调

| 令牌 | 值 | 用途 |
|------|-----|------|
| `--brand-500` | `#007aff` | 苹果蓝（主品牌色） |
| `--brand-400` | `#2e8dff` | 高亮态 |
| `--state-success-dark` | `#30d158` | 成功（绿） |
| `--state-error-dark` | `#ff453a` | 错误（红） |
| `--state-warning-dark` | `#ff9f0a` | 警告（橙） |

#### 暗色背景分层

| 令牌 | 值 | 用途 |
|------|-----|------|
| `--bg-primary` | `#000000` | 底座（纯黑） |
| `--bg-secondary` | `#1c1c1e` | 面板卡片 |
| `--bg-tertiary` | `#2c2c2e` | 弹出层 |

#### 字体与圆角

- 字体族：`'SF Pro Display', 'PingFang SC', system-ui, -apple-system, sans-serif`
- 圆角梯级：`--radius` 14px / `--radius-sm` 10px / `--radius-lg` 18px
- 主题切换：`.theme-transition` 类，0.3s ease 平滑过渡

### 10.2 Ant Design 强样式覆盖

由于 Antd CSS-in-JS 运行时注入优先级不稳定，`global.css` 对以下组件用 `!important`：
- `.ant-card`
- `.ant-btn-primary`
- `.ant-input`
- `.ant-select-selector`

### 10.3 全局状态管理（Zustand）

**位置**：`lsc-electron/src/store/appStore.ts`

```typescript
interface AppState {
  rooms: RoomSession[]
  selectedRoomId: string | null
  clips: ClipSegment[]
  recentClips: ClipSegment[]
  settings: RecordSettings
  appSettings: AppSettings
  connectionStatus: ConnectionStatus
  diskUsage: DiskUsage | null
  dependencyStatus: DependencyStatus | null
}
```

特点：
- 按 `room_id` 去重，已存在则更新
- 删除房间时清空 `selectedRoomId`（如果是当前选中）

### 10.4 WebSocket 客户端

**位置**：`lsc-electron/src/services/websocket.ts`

#### 核心特性

- **幂等连接**：多次 `connect()` 只创建一条连接，`pendingConnect` 复用防并发竞争
- **消息队列**：断连期间缓存（上限 100 条），重连成功后 flush
- **指数退避重连**：1s → 2s → 4s → 8s → 15s 封顶，最多 20 次
- **手动关闭标志**：`disconnect()` 设置 `manualClose=true`，避免 `onclose` 误触发重连

### 10.5 MSE 播放器

**位置**：`lsc-electron/src/services/mediaSourcePlayer.ts`

- 接收 `mse_init` → `SourceBuffer.appendBuffer(initSegment)`
- 高频追加 `mse_segment`
- 缓冲超出时间阈值自动清理
- 直播流 `duration=Infinity` 时设置 live seekable range
- seek 到 `buffered.start` 触发播放

### 10.6 页面结构

| 页面 | 路径 | 功能 |
|------|------|------|
| Dashboard | `lsc-electron/src/pages/Dashboard/index.tsx` | 仪表盘，房间概览 |
| Workbench | `lsc-electron/src/pages/Workbench/index.tsx` | **核心工作台**，多房间预览、时间线、切片控制 |
| Settings | `lsc-electron/src/pages/Settings/index.tsx` | 设置页，录制参数、关于、检查更新 |

### 10.7 工作台组件

| 组件 | 路径 | 职责 |
|------|------|------|
| RoomCard | `lsc-electron/src/pages/Workbench/components/RoomCard.tsx` | 单房间卡片，含预览、录制控制 |
| ControlBar | `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` | 控制栏，时间线播放头 |
| ClipList | `lsc-electron/src/pages/Workbench/components/ClipList.tsx` | 切片列表 |
| RecordSettings | `lsc-electron/src/pages/Workbench/components/RecordSettings.tsx` | 录制设置 |

---

## 第十一部分：一键对齐功能设计

### 11.1 按钮位置

**位置**：`lsc-electron/src/pages/Workbench/index.tsx:1097-1101`

```tsx
<Button onClick={handleAlignLive}
        loading={aligning}
        disabled={aligning || selectedRoomIds.size === 0}>
  {aligning ? '对齐中' : '一键对齐'}
</Button>
```

### 11.2 三阶段流程

#### Phase 1：即时缓冲对齐

**位置**：`lsc-electron/src/pages/Workbench/index.tsx:689-706`

```ts
const targetTime = Math.max(0, minBufferEnd - 1)
selectedRoomIds.forEach(rid => {
  video.currentTime = targetTime
  video.play()
})
```

#### Phase 2：并行音频捕获

**位置**：`lsc-electron/src/pages/Workbench/index.tsx:715-732`, `lsc-electron/src/utils/previewAudioAligner.ts`

通过 Web Audio API 捕获 3 秒音频：
1. AudioWorklet 加载（模块级单例，内联 Blob）
2. 优先共享 `MediaElementSourceNode`（不受 `video.muted` 影响）
3. 回退 `captureStream`（`muted=true` 时产出全零）
4. 静音检测（RMS < 1e-6 丢弃）
5. 降采样到 16kHz（简易低通滤波抗锯齿）
6. Base64 编码发送

#### Phase 3：后端 FFT 互相关

**位置**：`python-backend/handlers/room_handler.py:1125-1200`

```python
for rid in valid_ids[1:]:
    offset, score = compute_offset(ref_audio, audio_map[rid], sample_rate)
    raw_offsets[rid] = offset
    scores[rid] = score

# 以最慢房间为基准
slowest_id = min(valid_ids, key=lambda rid: raw_offsets[rid])
```

### 11.3 结果应用

收到 `align_preview_audio_response` 后三件事并行：
1. **回传 content_offset**：`send('set_content_offset', ...)` 给导出用
2. **调整 video.currentTime**：偏移 > 0.05s 时 seek
3. **自动静音快房间**：非参考房间静音，消除回声

Toast：`已精确对齐 N 个直播间（置信度 X%），已静音 M 个快房间消除回声`

### 11.4 漂移修正

**位置**：`lsc-electron/src/utils/previewAudioAligner.ts:273-314`

`applyOffsetWithDriftCorrection`：
- seek 后测量残余差
- 若 > 50ms，用 `playbackRate ±0.05` 持续修正
- 完成后恢复 `playbackRate = 1.0`

---

## 第十二部分：高光分析功能设计

### 12.1 按钮位置

**位置**：`lsc-electron/src/pages/Workbench/index.tsx:1071-1079`

```tsx
disabled={!selectedRoomId || !rooms.find(r => r.room_id === selectedRoomId)?.record_output_path}
onClick={() => send('start_analysis', { room_id: selectedRoomId, threshold: 0.3 })}
```

### 12.2 后端处理

**位置**：`python-backend/handlers/room_handler.py:89-171` `_run_scene_analysis`

```bash
ffmpeg -i video.mp4 -vf "select='gt(scene\,0.3)',showinfo" -vsync vfr -f null -
```

解析 stderr 中 `pts_time:数字` 提取场景切换时间戳：
- 按时间间隔 >15s 切分为多段高光
- 前后各加 2s/5s padding
- 过滤 duration < 3s
- 去重重叠

### 12.3 结果展示

通过 `start_analysis_response` 回前端，弹出"高光分析结果" Modal，用户可点击"导入到切片列表"。

---

## 第十三部分：持久化与配置

### 13.1 房间列表持久化

**位置**：`python-backend/persistence.py`

- 路径：`data/rooms.json`
- 格式：`{"rooms": [...]}`
- 原子写入：`.tmp` 临时文件 + `replace` 替换
- 支持两种格式：`{"rooms": [...]}` 和 `[...]`

### 13.2 设置持久化

**位置**：`python-backend/settings.json`

| 配置键 | 默认值 | 说明 |
|--------|--------|------|
| `output_dir` | `~/LSC/recordings` | 录制产物主目录 |
| `encoder` | `h264_nvenc` | 编码器 |
| `crf` | `23` | 质量因子 (0-51) |
| `param_mode` | `"CRF 质量"` | CRF 或自定义码率 |
| `bitrate` | `"8000"` | 码率 (kbps) |
| `quality` | `"原画"` | 流抓取画质 |
| `resolution` | `""` | 分辨率缩放 |
| `framerate` | `"原画"` | 帧率 |
| `audio_codec` | `"AAC 128k"` | 音频编码 |
| `preview_quality` | `"高清"` | MSE 预览分辨率 |

---

## 第十四部分：Electron 主进程设计

### 14.1 Python 后端生命周期

**位置**：`lsc-electron/electron/main.ts`

#### Python 检测三级回退

1. 系统 PATH
2. `~/.workbuddy/binaries/python/versions/`
3. 打包内嵌 `extraResources/python/python.exe`

#### stdout 正则匹配端口

从 `WebSocket server ready at ws://localhost:PORT` 提取实际端口。

#### 退出处理

Windows：`taskkill /T /F /PID` 强杀整棵进程树，防 FFmpeg 僵尸残留。

### 14.2 版本检测（手动触发）

- API：`api.github.com/repos/Lawrence7y/LSC/releases/latest`
- Node.js 原生 `https`，`User-Agent: LSC-App/x.x.x`
- 5 分钟内存防抖缓存，防 GitHub API 限流
- 10s 超时，`req.destroy()` 中断
- 发现新版本 → `shell.openExternal(releaseUrl)` 跳浏览器，不自动下载安装

### 14.3 打包资源路径映射

```typescript
const isPackaged = process.resourcesPath !== undefined;
const backendDir = isPackaged
  ? path.join(process.resourcesPath, 'python-backend')
  : path.join(__dirname, '../../../python-backend');
```

---

## 第十五部分：安全防御设计

### 15.1 文件系统沙箱

**位置**：`_isSafePath(p)`

`open-path` 和 `show-item-in-folder` 强制校验：
- **路径白名单**：`app.getPath('userData')` 或 `~/LSC`
- **可执行黑名单**：`.exe/.bat/.ps1/.cmd/.vbs/.scr` 防范 RCE

### 15.2 子进程安全

#### 环境变量白名单

启动 Python 后端子进程时只透传：
```
PATH, USERPROFILE, APPDATA, LOCALAPPDATA, TEMP, TMP,
HOME, SYSTEMROOT, PATHEXT, PYTHONUNBUFFERED=1, PYTHONPATH
```

#### 非阻塞管道

`_set_stream_nonblocking()`：
- Windows：`msvcrt` 接口
- POSIX：`fcntl O_NONBLOCK`

防高负载读写管道死锁。

#### Windows 权限

`detached=true` 脱离父进程受限 Token，防 `WinError 5`。

### 15.3 崩溃容错

- `sys.excepthook` 捕获未处理异常写日志
- `qInstallMessageHandler` 桥接 Qt Critical/Fatal 到 Python logging
- `RotatingFileHandler` 2MB × 5 备份
- 优雅停机：Windows `taskkill /T /F`，POSIX SIGTERM → 3s → SIGKILL

---

## 第十六部分：交互辅助设计

### 16.1 全局快捷键

**位置**：`lsc-electron/src/hooks/useKeyboardShortcuts.ts`

**焦点判定**：输入焦点在 `input/textarea/select` 时自动拦截（Ctrl+1/2/3 除外）。

| 快捷键 | 动作 |
|--------|------|
| `Ctrl + 1/2/3` | 切换页面 |
| `Space` | 播放/暂停 |
| `i` / `o` | 标记入/出点 |
| `r` | 切换录制 |
| `m` | 静音切换 |
| `f` | 全屏预览 |
| `Ctrl + r` | 批量启动录制 |
| `Ctrl + Shift + r` | 批量停止录制 |
| `Ctrl + a` | 多房间全选 |
| `Ctrl + e` | 触发导出 |

---

## 第十七部分：运行与构建

### 17.1 开发启动

```bash
# Python 依赖
pip install -r requirements.txt

# 前端依赖
cd lsc-electron && npm install

# 方式1：纯 Python
python main.py

# 方式2：前端 Vite 独立
cd lsc-electron && npx vite --config vite.dev.config.ts

# 方式3：Electron 开发模式（一键拉起）
cd lsc-electron && npm run dev
```

### 17.2 打包构建

```powershell
# PowerShell 自动化（首选）
cd lsc-electron
.\build-installer.ps1

# 或三步命令
npx tsc --noEmit
npx vite build
npx electron-builder
```

### 17.3 测试

```bash
# Python 测试（需设 QT_QPA_PLATFORM=offscreen）
pytest -v
pytest -v --cov=lsc --cov-report=term

# Ruff 静态检查
ruff check lsc/

# Mypy 类型校验
mypy lsc/

# 前端类型校验
npx tsc --noEmit
```

### 17.4 日志路径

- 后端：`%APPDATA%\lsc-electron\logs\backend.log`（2MB 滚动）
- 主进程：`%APPDATA%\lsc-electron\logs\debug.log`

---

## 第十八部分：已知问题与设计缺陷

### 18.1 高优先级问题

| # | 问题 | 影响 |
|---|------|------|
| 1 | 音频对齐低置信度仍应用偏移 | "同内容不同解说"场景下错误 seek，比不对齐更糟 |
| 2 | `bridge.call` 超时后主线程可能仍在执行 | 命令乱序风险 |
| 3 | `_mse_streamers` 共享结构需线程安全访问 | 并发删除房间可能崩溃 |

### 18.2 中优先级问题

| # | 问题 | 影响 |
|---|------|------|
| 4 | 全屏预览退出后小预览区可能冻结 | 需重启恢复 |
| 5 | `rooms_updated` 合并可能丢失中间状态 | 偶发的 UI 状态不同步 |
| 6 | 预览流与录制流独立导致时间轴偏移 | 需墙钟映射，但降级方案固定 2s 不精确 |

### 18.3 架构层面的根本挑战

| 场景 | 现状 |
|------|------|
| 同一直播间多路拉流 | ✅ 音频对齐可用 |
| 同一比赛不同解说 | ❌ 无可用方案（音频失效） |
| 无声视频 | ❌ 降级为 0 偏移 |

**可行的对齐思路**（未实现，仅供参考）：
- 关键帧感知哈希 (pHash)：不受解说音频影响
- 场景切换检测对齐：复用现有"分析高光"FFmpeg 逻辑
- 多模态融合：pHash + 解说音量包络
- 手动 + 视觉辅助：拖拽时间轴对齐 UI

---

## 第十九部分：附录

### 19.1 核心并发上限

| 资源 | 上限 |
|------|------|
| 录制路数 | 12 (`MAX_CONCURRENT_RECORDINGS`) |
| 预览路数 | 4 (`MAX_CONCURRENT_PREVIEWS`) |
| 导出并发 | 2 (`_DEFAULT_MAX_CONCURRENT`) |
| 音频提取 | 6 (`_MAX_EXTRACT_WORKERS`) |
| 缩略图生成 | 4 |
| stderr 读取 | 4 (共享) |
| 启动并发 | 2 (`asyncio.Semaphore`) |

### 19.2 关键超时

| 场景 | 超时 |
|------|------|
| `bridge.call` | 10s |
| FFmpeg 启动探测 | 5s |
| FFmpeg 优雅退出 | 5s + 3s + 5s |
| 录制文件验证 | - |
| 音频提取 | 3s + 20s |
| 导出看门狗 | 300s |
| GitHub API | 10s |
| 更新检测缓存 | 5 分钟 |
| WebSocket 重连 | 1s → 15s（指数退避，20 次） |

### 19.3 关键阈值

| 配置 | 值 |
|------|-----|
| 磁盘预检 | ≥8GB |
| 磁盘运行时 | <2GB 强制停止 |
| 文件最小大小 | 0.1MB |
| 文件卡住检测 | 3 次检查无增长 |
| 互相关置信度 | 0.1 |
| 音频采样率 | 16000Hz |
| 音频时长 | 3.0s |
| MSE 分片上限 | 512KB |
| 消息队列上限 | 100 |
| 重连最大次数 | 3 (录制) / 20 (WebSocket) |

---

## 文档维护说明

本文档基于代码现状生成，反映截至 2026-06-30 的系统实现。如代码发生重大变更，请同步更新本文档对应章节。

**文档结构**：
- 第一至二部分：项目概述与架构
- 第三部分：跨线程通信
- 第四部分：数据模型
- 第五部分：平台适配
- 第六部分：录制系统
- 第七部分：预览系统
- 第八部分：切片系统
- 第九部分：错误处理
- 第十部分：前端设计
- 第十一至十二部分：核心功能（对齐、高光）
- 第十三至十四部分：持久化与 Electron
- 第十五部分：安全防御
- 第十六部分：交互辅助
- 第十七部分：运行构建
- 第十八部分：已知问题
- 第十九部分：附录参考表
