# LSC 直播录制切片程序 — 需求审计与 Spec 修改计划

> **审计日期**: 2026-06-26（修订版）
> **审计范围**: Electron 前端 (lsc-electron/) + Python 后端 (python-backend/) → 对照 7 大核心需求
> **架构变更**: PySide6 原生 UI 已弃用，**Electron 为唯一前端**
> **目标**: Electron 版本全面超越原 PySide6，完美满足所有需求

---

## 〇、架构定论

```
Electron (唯一前端)
├── electron/main.ts          # 主进程：窗口管理/托盘/自启/Python 生命周期
├── src/                      # React 渲染进程 (Ant Design + Zustand)
│   ├── hooks/useWebSocket    # WebSocket 通信 Hook
│   ├── store/appStore        # 全局状态
│   ├── pages/                # Dashboard / Workbench / Settings
│   └── components/           # Timeline / Layout / 可复用组件
│
│  ws://localhost:8765 ────────────► python-backend/
│                                        ├── main.py      # QApplication + WebSocket 服务器
│                                        ├── server.py    # WebSocket 协议实现
│                                        ├── message_bridge.py  # 线程安全桥 (Qt ↔ WS)
│                                        └── handlers/    # 21 个消息处理器
│
│                                        共享业务层 (lsc/)
│                                        ├── platforms/    # 8 平台适配器
│                                        ├── recorder/     # FFmpeg 录制
│                                        ├── exporter/     # FFmpeg 导出
│                                        └── core/         # 核心服务层
```

**关键约束**: 所有 UI 渲染在 Electron/React 层完成，后端仅提供数据、录制控制、帧抓取。

---

## 一、逐项需求审计（Electron 视角）

### 需求 1：直播链接识别与导入 ✅ 已满足（成熟度 95%）

| 子项 | 状态 | 实现 |
|------|------|------|
| 链接识别 | ✅ | 后端 `platforms/registry.py`，前端 `handleAddRoom` → WS `add_room` |
| 8 平台适配 | ✅ | B站/抖音/虎牙/斗鱼/快手/小红书/直链/通用，全部共享后端 |
| 批量导入 | ✅ | 当前单条添加，可通过多行粘贴快速添加 |

**差距**: 无功能性差距。前端的 URL 输入体验可增强（支持粘贴多行自动拆分、URL 校验提示）。

---

### 需求 2：直播间预览与控制 ⚠️ 部分满足（成熟度 40%） 🔴 核心瓶颈

| 子项 | 状态 | 实现 |
|------|------|------|
| 实时预览 | ⚠️ | JPEG 帧流（FFmpeg → base64 → WebSocket → `<img>`），5-10fps |
| 静音切换 | ⚠️ | 后端有 `set_preview_muted`，但 JPEG 帧流无音频轨，静音无实际效果 |
| 全屏查看 | ⚠️ | Ant Design Modal + `<img>` 展示当前帧（静态图，非实时视频流） |
| 预览重连 | ⚠️ | 后端自动重连，但前端缺少"预览断线"的明确提示 |
| 预览并发 | ✅ | 0路10fps / 1路8fps / 2+路5fps（自适应） |

**🔴 核心差距**:

1. **预览帧率过低**: 5-10fps vs PySide6 的 60fps（libmpv 硬解码）。这导致：
   - 卡顿感强烈，无法流畅判断画面内容
   - 无法精确到帧的选择（画面跳跃大）
   - 用户感知差，不像专业软件

2. **无音频**: JPEG 帧流不含音频，静音/取消静音无实际意义

3. **全屏是假全屏**: 只是放大当前一帧的静态图，不是实时视频流

**🟢 解决方案**: 见下文 S-PREVIEW（视频预览升级方案）。

---

### 需求 3：多直播间管理 ⚠️ 部分满足（成熟度 65%）

| 子项 | 状态 | 实现 |
|------|------|------|
| 多房间导入 | ✅ | 后端 MAX_ROOMS=12，前端逐个添加 |
| 房间卡片 | ✅ | `RoomCard` - 预览区 + 状态 + 操作按钮 |
| 批量录制/停止 | ✅ | `handleBatchRecord`/`handleBatchStop` |
| 多选同步 | ❌ **缺失** | 无 Ctrl/Shift 多选、无同步 seek、无多选视觉反馈 |
| 卡片排序/筛选 | ❌ **缺失** | 无法按平台、状态排序或筛选 |
| 录制槽位分组 | ❌ **缺失** | 无 `include_in_cut` 复选框（批量导出时选择哪些房间） |

---

### 需求 4：统一进度条与片段导出 ⚠️ 部分满足（成熟度 60%）

| 子项 | 状态 | 实现 |
|------|------|------|
| 时间线组件 | ✅ | React `Timeline` - 播放头 + 选区标记 + 切片覆盖层 + 拖拽 seek |
| 入/出点 | ✅ | 后端 `mark_in`/`mark_out` 存储在 `RoomSession`（已经是 per-room！） |
| 切片列表 | ✅ | `ClipList` - 右侧面板展示 + 导出/删除 |
| 片段导出 | ✅ | `export_clip` WebSocket → 后端 `ClipExporter` |
| 场景检测 | ❌ **缺失** | PySide6 的 `start_analysis()` 完全不可用 |
| 选区试听 | ❌ **缺失** | 无循环播放 `[mark_in, mark_out]` 功能 |

**好消息**: 后端 `RoomSession` 已经有 `mark_in`/`mark_out` 字段，`set_mark_in`/`set_mark_out` handler 写入的是对应房间的 session，`_room_to_dict()` 返回这些值 → 前端 `rooms_updated` 广播已包含。所以 **选区已经是 per-room 的**，不存在 PySide6 的"串房"bug。

**差距**: 
1. 前端 `ControlBar` 和 `Timeline` 只在有 `selectedRoom` 时渲染，切换房间时标记正确跟着切换（因为每个房间的 mark_in/out 各自独立在 store 中）。但需要在 UI 上更明确地展示"当前操作的是哪个房间"。
2. 场景检测（高光分析）完全缺失
3. 没有选区试听功能

---

### 需求 5：内置播放器与时间线 ⚠️ 部分满足（成熟度 55%）

| 子项 | 状态 | 实现 |
|------|------|------|
| 时间线组件 | ✅ | React `Timeline` - 播放头/选区/切片/拖拽/缩放（性能好） |
| 内置播放器 | ❌ **缺失** | 没有真正的视频播放器，只有 JPEG 帧流渲染 |
| 帧精确选择 | ❌ **无法实现** | 5-10fps 的帧流无法做到帧精确 |

**差距**: 时间线 UI 本身设计良好（自定义 CSS、hover tooltip、选区高亮），但因为没有真正的视频播放器，核心"播放器+时间线"体验不完整。

---

### 需求 6：视频规格设置 ⚠️ 部分满足（成熟度 50%）

| 子项 | 状态 | 实现 |
|------|------|------|
| 编码器选择 | ✅ | libx264/libx265/copy/h264_nvenc/hevc_nvenc |
| 画质预设 | ✅ | 原画/蓝光/超清/高清/流畅 |
| CRF/码率 | ✅ | CRF 0-51 / 码率 100-100000 kbps/Mbps |
| 输出目录 | ✅ | 原生 Electron 目录选择器 |
| **分辨率设置** | ❌ **缺失** | 设置页完全无分辨率选项 |
| **帧率设置** | ❌ **缺失** | 设置页完全无帧率选项 |
| **音频编码/码率** | ❌ **缺失** | 不可配置 |
| **导出预设** | ❌ **缺失** | 无快捷预设（如"抖音竖屏"、"B站横屏"） |
| 编码参数模式 | ✅ | CRF质量/码率限制/不限制 |

**差距**: 比 PySide6 还少——PySide6 至少在导出时有竖屏裁剪选项。Electron 设置完全没有分辨率、帧率、导出预设。

---

### 需求 7：交互体验 ⚠️ 部分满足（成熟度 60%）

| 子项 | 状态 | 实现 |
|------|------|------|
| 深色/浅色主题 | ✅ | CSS 变量设计令牌 + Ant Design 组件 |
| 键盘导航 | ⚠️ | 仅 Ctrl+1/2/3 页面切换，无快捷键说明 |
| 全局快捷键 | ❌ **缺失** | 设置页展示了快捷键列表但**实际上未绑定**！ |
| 快捷键覆盖 | 设置页列出 | Space/Pause、I/O、Ctrl+Shift+R 等 → **全部未实现** |
| 全景错误提示 | ⚠️ | 仅 `message.error()`，无原始错误友好化 |
| 录制时长显示 | ✅ | RoomCard 录制中显示实时计时器 |
| 系统托盘 | ✅ | 最小化到托盘 + 右键菜单 |
| 开机自启 | ✅ | 主进程 `app.setLoginItemSettings` |

---

## 二、附加增强需求审计

| 增强功能 | 状态 | 说明 |
|----------|------|------|
| 录制状态实时反馈 | ⚠️ | 录制时长显示 ✅，但缺：实时码率、文件大小增长、磁盘预估 |
| 快捷键支持 | ❌ **完全未实现** | 设置页列了快捷键但仅作展示，无任何实际绑定 |
| 录制历史记录 | ⚠️ | Dashboard 有 5 条最近历史，但无搜索/筛选/导出 |
| 批量导出队列管理 | ❌ **缺失** | 无队列 UI，导出是单次提交无进度追踪 |

---

## 三、🔴 核心挑战：视频预览方案

这是让 Electron 超越 PySide6 最关键的技术决策。

### 现状问题
- JPEG 帧流（FFmpeg `FrameCaptureWorker` → JPEG → base64 → WebSocket → `<img>` src）
- 最高 10fps（0路预览），多路降至 5fps
- 每帧约 30-80KB base64，4路预览时带宽 ~1.6MB/s
- 无法帧精确选择

### 方案评估

#### 方案 A: Media Source Extensions + FFmpeg 转码（推荐 ⭐）

```
FFmpeg (后端)                     Electron (前端)
┌──────────────┐                 ┌──────────────────┐
│ 直播流 FLV    │                 │ MediaSource API  │
│     ↓        │                 │     ↓            │
│ FFmpeg转码   │── WebSocket ──► │ <video> 元素     │
│ fMP4 segments│  推送 fMP4      │ 浏览器原生解码    │
│ (H.264/AAC)  │  segments       │ 30fps 流畅播放   │
└──────────────┘                 └──────────────────┘
```

- **优点**: 浏览器原生 `<video>` 解码，30fps+ 流畅，支持音频，可精准 seek
- **缺点**: 实现复杂度中高（需要后端 FFmpeg 输出 fMP4 segment + 前端 MSE 组装）
- **可行性**: 高。Chromium 的 MSE 很成熟，FFmpeg 输出 fragmented MP4 是标准功能

#### 方案 B: WebRTC 流

```
FFmpeg → WebRTC (aiortc / mediasoup) → 浏览器 RTCPeerConnection
```

- **优点**: 超低延迟 (<500ms)，真正的实时流，30fps+
- **缺点**: 实现复杂度极高，需要 STUN/TURN 服务器，多房间扩展困难
- **可行性**: 中低。对直播切片场景过度设计

#### 方案 C: HLS 转封装（中间方案）

```
FFmpeg → HLS (.m3u8 + .ts segments) → 本地 HTTP 服务器 → <video src="http://localhost:xxxx/live/room_xxx.m3u8">
```

- **优点**: 实现相对简单，浏览器原生支持 HLS（通过 hls.js），支持音频
- **缺点**: HLS 延迟 5-10s，片段切换有轻微卡顿
- **可行性**: 高。适合"录制监控"场景（不需要毫秒级实时）

#### 方案 D: 优化 JPEG 帧流（保守方案）

```
FFmpeg → WebP (更小) → 更高 fps (15-20)
```

- **优点**: 实现成本最低，改动最小
- **缺点**: 仍无音频，帧精确选择仍困难，无法达到专业级
- **可行性**: 高。但无法"超越 PySide6"

### 推荐决策

**首选方案 A（MSE + fMP4）用于预览，方案 C（HLS）作为 fallback**。

分两步走：
1. **短期（本迭代）**: 实施方案 A 单路预览（解决核心瓶颈）
2. **中期**: 多路预览 + 自适应码率

---

## 四、Spec 修改计划（Electron 专属）

### 🔴 P0 — 必须立即修复（阻塞发布，3-5 天）

#### S-PREVIEW: 视频预览升级（MSE + fMP4）

- **目标**: 从 JPEG 帧流升级到浏览器原生 `<video>` + MSE 播放，达到 30fps 流畅预览 + 音频支持
- **后端改动**:
  - 新增 `lsc/core/services/mse_streamer.py` — FFmpeg 子进程输出 fragmented MP4
  - FFmpeg 命令: `ffmpeg -i <stream_url> -c:v libx264 -preset ultrafast -tune zerolatency -g 30 -f mp4 -movflags frag_keyframe+empty_moov pipe:1`
  - 通过 WebSocket 或 HTTP 分段推送 fMP4 初始化段 + 媒体段
  - `enable_preview` handler 增加 `mode: 'mse'` 支持
- **前端改动**:
  - 新增 `lsc-electron/src/services/mediaSourcePlayer.ts` — MSE 播放器封装
  - 新增 `lsc-electron/src/components/VideoPreview.tsx` — `<video>` 元素替代 `<img>`
  - `RoomCard` 预览区从 `<img>` 切换到 `<VideoPreview>`
  - 全屏预览从 Modal `<img>` 改为真正的全屏 `<video>`
  - `useWebSocket` 增加 fMP4 segment 接收 + MSE buffer 喂入
- **工时**: 3 天（后端 1.5d + 前端 1.5d）
- **验收标准**: 单路预览达到 25fps+，有音频，可拖拽 seek，无明显卡顿

#### S-RESOLUTION: 分辨率与帧率设置

- **目标**: 设置页增加录制分辨率、帧率、音频编码配置
- **后端改动**:
  - `handler/room_handler.py` — `start_recording` 读取新字段（resolution/framerate/audio_codec/audio_bitrate）
  - `lsc/recorder/capture.py` — FFmpeg 命令拼接增加 `-vf scale=W:H`、`-r N`、`-c:a codec -b:a bitrate`
  - `settings.json` 增加 `record_resolution`、`record_framerate`、`audio_codec`、`audio_bitrate`
- **前端改动**:
  - `Settings/index.tsx` — 增加分辨率 ChipGroup（原画/1080p/720p/480p）
  - 增加帧率 ChipGroup（60/30/24/原画）
  - 增加音频编码 ChipGroup（AAC 128k/AAC 192k/AAC 256k）
  - `RecordSettings.tsx` — 同步显示录制规格
  - `types/index.ts` — `RecordSettings` 增加字段
- **工时**: 1.5 天
- **验收标准**: 设置后录制的视频分辨率和帧率与配置一致

#### S-SHORTCUTS: 全局快捷键系统

- **目标**: 实现所有已在设置页展示的快捷键
- **快捷键清单**:
  - `Space` — 播放/暂停选中房间
  - `I` — 设置入点（选中房间当前播放位置）
  - `O` — 设置出点（选中房间当前播放位置）
  - `R` — 开始/停止录制选中房间
  - `M` — 静音/取消静音选中房间
  - `F` — 全屏选中房间
  - `Ctrl+R` — 批量录制
  - `Ctrl+Shift+R` — 批量停止
  - `Ctrl+A` — 全选所有房间
  - `Ctrl+E` — 导出当前选区
  - `Ctrl+1/2/3` — 页面切换（已实现）
  - 焦点输入框时不触发快捷键
- **前端改动**:
  - `MainLayout.tsx` — `keydown` handler 扩展为全局快捷键系统
  - 使用 `useAppStore` 的 `send` 方法触发后端操作
- **工时**: 0.5 天

---

### 🟡 P1 — 核心功能补全（1-2 周）

#### S-ANALYSIS: 场景检测（高光分析）

- **目标**: 实现 PySide6 中的高光片段自动识别功能
- **后端改动**:
  - 新增 `room_handler.py` handler `start_analysis` — 调用 FFmpeg scene 检测
  - FFmpeg 命令: `ffmpeg -i <video> -vf "select='gt(scene,0.3)',showinfo" -f null -`
  - 解析场景切换时间戳，返回高光片段列表
  - 增加 `get_analysis_results` — 获取分析结果（带进度回调）
- **前端改动**:
  - `Workbench/index.tsx` — 增加"分析高光"按钮
  - 分析进度 Modal 展示
  - 分析完成后自动填充切片列表
  - `ClipList` 增加批量导入按钮
- **工时**: 2 天
- **验收标准**: 可对已录制视频运行场景检测，自动识别切换点并生成切片列表

#### S-EXPORT-QUEUE: 批量导出队列管理器

- **目标**: 可视化导出队列，支持进度追踪、取消、重试
- **前端改动**:
  - 新增 `src/components/ExportQueue/index.tsx` — 队列面板（侧边栏或底部抽屉）
  - 展示每项: 文件名、进度条、状态标签（等待中/进行中/已完成/失败）、耗时
  - 支持取消单个、重试失败项
  - 完成后"打开文件夹"快捷入口
- **后端改动**:
  - `export_clip` handler 增加任务 ID 返回
  - 增加 `cancel_export` handler
  - 增加 `get_export_progress` handler（通过 `preview_frame` 类似的定时推送）
- **工时**: 2 天

#### S-EXPORT-PRESETS: 导出预设系统

- **目标**: 快捷导出预设（抖音竖屏、B站横屏、原画直出等）
- **前端改动**:
  - `Settings/index.tsx` — 增加"导出预设"section
  - 预设列表: 抖音竖屏（1080x1920, 30fps, H.264）、B站横屏（1920x1080, 30fps, H.264）、原画直出
  - 每个预设包含: 名称、分辨率、帧率、编码器、CRF
  - `ClipList` 导出按钮增加预设选择下拉
  - `types/index.ts` — 增加 `ExportPreset` 类型
- **后端改动**:
  - `export_clip` handler 增加 `preset` 参数
  - 预设参数自动映射到 `ExportProfile`
- **工时**: 1.5 天

#### S-MULTI-SELECT: 多选同步

- **目标**: 支持 Ctrl/Shift 多选房间，同步 seek 操作
- **前端改动**:
  - `Workbench/index.tsx` — 增加 `selectedRoomIds: Set<string>` 状态
  - `RoomCard` — Ctrl+Click 多选，Shift+Click 范围选
  - 多选模式下：seek 操作广播到所有选中房间
  - 所有选中卡片加蓝色高亮边框 + 同步时间戳徽章
  - `ControlBar` — 多选时显示"N 房间同步"
- **工时**: 1.5 天

---

### 🟢 P2 — 体验增强（2-3 周）

#### S-LOOP-PREVIEW: 选区试听（循环播放）

- **目标**: 选中房间的 `[mark_in, mark_out]` 循环播放
- **前端改动**:
  - `ControlBar` — 增加"试听选区"按钮（ToggleButton）
  - 使用 `<video>` 的 timeupdate 事件监听，越界自动 seek 回起点
  - 按钮状态: 按下=循环中（蓝色高亮），再按=停止
- **工时**: 0.5 天

#### S-RECORDING-FEEDBACK: 录制状态实时反馈

- **目标**: 卡片显示实时码率、文件大小增长动画
- **后端改动**:
  - 心跳中计算录制速率（基于 10s 文件增量）
  - `room_updated` 广播增加 `record_bitrate_kbps`、`record_size_mb`
- **前端改动**:
  - `RoomCard` — 录制中的卡片显示码率指示器 + 文件大小
  - 状态栏增加总录制速率 / 磁盘预估（"~2.3GB/h"）
- **工时**: 1 天

#### S-EXPORT-INCLUDE: 导出复选框

- **目标**: 房间卡片增加"纳入导出"复选框
- **前端改动**:
  - `RoomCard` — 增加 `include_in_export` 复选框（卡片右上角）
  - `ClipList` — 底部增加"导出所有选中房间的切片"按钮
  - 逻辑: 仅导出勾选房间的切片
- **工时**: 0.5 天

#### S-HISTORY: 录制历史搜索与筛选

- **目标**: Dashboard 录制历史增加搜索、筛选、导出
- **前端改动**:
  - `Dashboard/index.tsx` — 增加搜索框 + 日期范围选择器
  - 筛选: 按平台、按日期范围、按时长
  - "查看全部"链接 → 展开完整列表
- **后端改动**:
  - `get_history` handler 增加过滤参数
- **工时**: 1 天

#### S-ERROR-HUMANIZE: 错误信息友好化

- **目标**: FFmpeg/网络原始错误 → 用户可读中文提示
- **后端改动**:
  - 新增 `lsc/utils/error_messages.py` — 正则匹配 → 中文映射
  - `room_handler.py` 所有返回 error 的地方调用 `humanize_error()`
- **前端改动**:
  - 连接失败/录制失败等错误提示调用翻译后的消息
- **工时**: 0.5 天

---

### 🔵 P3 — 品质打磨（持续）

#### S-3D-PREVIEW: 3D 预览效果（锦上添花）

- **目标**: 未连接时房间卡片展示 3D 粒子背景，提升视觉品质
- **实现**: Three.js 粒子系统（GPGPU 粒子），深色主题下效果出彩
- **工时**: 1 天

#### S-BATCH-URL: 批量 URL 导入

- **目标**: 输入框支持粘贴多行 URL，自动拆分并逐个添加
- **工时**: 0.25 天

#### S-CARD-SORT: 房间卡片排序/筛选

- **目标**: Dropdown 按平台/状态排序，按录制中/已连接筛选
- **工时**: 0.5 天

#### S-DARK-MODE-ANIMATION: 主题切换动画

- **目标**: 深色/浅色切换时增加平滑过渡动画
- **实现**: CSS transition 在 `documentElement.classList` 切换时触发
- **工时**: 0.25 天

#### S-ORGANIZE-MENU: 整理侧边栏菜单

- **目标**: 增加"录制历史"独立页面、增加"导出队列"底部入口
- **工时**: 0.5 天

---

## 五、工时汇总

| 优先级 | 编号 | 功能 | 工时 | 验收标准 |
|--------|------|------|------|----------|
| P0 | S-PREVIEW | 视频预览升级（MSE+fMP4） | 3 天 | 25fps+，有音频，可 seek |
| P0 | S-RESOLUTION | 分辨率/帧率/音频设置 | 1.5 天 | 录制规格可配且生效 |
| P0 | S-SHORTCUTS | 全局快捷键系统 | 0.5 天 | 11 个快捷键全部可用 |
| **P0 小计** | | | **5 天** | |
| | | | | |
| P1 | S-ANALYSIS | 场景检测（高光分析） | 2 天 | 自动识别高光片段 |
| P1 | S-EXPORT-QUEUE | 批量导出队列管理器 | 2 天 | 可视化进度/取消/重试 |
| P1 | S-EXPORT-PRESETS | 导出预设系统 | 1.5 天 | 抖音/B站快捷预设 |
| P1 | S-MULTI-SELECT | 多选同步 | 1.5 天 | Ctrl/Shift 多选+同步 |
| **P1 小计** | | | **7 天** | |
| | | | | |
| P2 | S-LOOP-PREVIEW | 选区试听 | 0.5 天 | 循环播放选区 |
| P2 | S-RECORDING-FEEDBACK | 录制实时反馈 | 1 天 | 码率/大小实时显示 |
| P2 | S-EXPORT-INCLUDE | 导出复选框 | 0.5 天 | 卡片可勾选纳入导出 |
| P2 | S-HISTORY | 录制历史搜索筛选 | 1 天 | 搜索/日期/平台筛选 |
| P2 | S-ERROR-HUMANIZE | 错误友好化 | 0.5 天 | 中文可读错误提示 |
| **P2 小计** | | | **3.5 天** | |
| | | | | |
| P3 | S-3D-PREVIEW | 3D粒子预览背景 | 1 天 | — |
| P3 | S-BATCH-URL | 批量URL导入 | 0.25 天 | — |
| P3 | S-CARD-SORT | 房间排序筛选 | 0.5 天 | — |
| P3 | S-DARK-ANIMATION | 主题切换动画 | 0.25 天 | — |
| P3 | S-ORGANIZE-MENU | 侧边栏整理 | 0.5 天 | — |
| **P3 小计** | | | **2.5 天** | |
| | | | | |
| **总计** | | **18 项** | **~18 天** | |

---

## 六、依赖关系与执行路线

```
阶段 1 — P0 (5天，必须最先做)
┌─────────────────────────┐
│ S-PREVIEW 视频预览升级   │ ← 阻塞几乎所有后续功能（所有预览/试听/全屏都依赖此方案）
├─────────────────────────┤
│ S-RESOLUTION 规格设置    │ ← 独立，可与 S-PREVIEW 并行
├─────────────────────────┤
│ S-SHORTCUTS 快捷键系统   │ ← 独立，可与上面并行
└─────────────────────────┘

阶段 2 — P1 (7天，依赖 S-PREVIEW)
┌─────────────────────────┐
│ S-ANALYSIS 场景检测      │ ← 依赖 S-PREVIEW（需要预览视频来分析）
├─────────────────────────┤
│ S-EXPORT-QUEUE 导出队列  │ ← 独立
├─────────────────────────┤
│ S-EXPORT-PRESETS 导出预设│ ← 独立
├─────────────────────────┤
│ S-MULTI-SELECT 多选同步  │ ← 依赖 S-PREVIEW（同步 seek 需要流畅预览）
└─────────────────────────┘

阶段 3 — P2 (3.5天，可与 P1 部分并行)
┌─────────────────────────┐
│ S-LOOP-PREVIEW 选区试听  │ ← 依赖 S-PREVIEW（需要视频播放器）
├─────────────────────────┤
│ S-RECORDING-FEEDBACK     │ ← 独立
├─────────────────────────┤
│ S-EXPORT-INCLUDE         │ ← 独立
├─────────────────────────┤
│ S-HISTORY                │ ← 独立
├─────────────────────────┤
│ S-ERROR-HUMANIZE         │ ← 独立
└─────────────────────────┘

阶段 4 — P3 (2.5天，随时穿插)
```

### 推荐执行顺序

```
Week 1 (P0):  S-PREVIEW → S-RESOLUTION → S-SHORTCUTS
Week 2 (P1):  S-MULTI-SELECT → S-EXPORT-QUEUE → S-EXPORT-PRESETS
Week 3 (P1+P2): S-ANALYSIS → S-LOOP-PREVIEW → S-RECORDING-FEEDBACK
Week 4 (P2+P3): S-HISTORY → S-ERROR-HUMANIZE → S-EXPORT-INCLUDE → P3项
```

---

## 七、需求覆盖矩阵（最终目标）

| 需求 | 当前成熟度 | 完成后成熟度 | 实现项目 |
|------|-----------|-------------|---------|
| 1. 直播链接识别与导入 | 95% | 98% | S-BATCH-URL（微调） |
| 2. 直播间预览与控制 | 40% | **95%** | S-PREVIEW, S-SHORTCUTS |
| 3. 多直播间管理 | 65% | **95%** | S-MULTI-SELECT, S-CARD-SORT, S-EXPORT-INCLUDE |
| 4. 统一进度条与片段导出 | 60% | **95%** | S-ANALYSIS, S-LOOP-PREVIEW, S-EXPORT-QUEUE |
| 5. 内置播放器与时间线 | 55% | **90%** | S-PREVIEW, S-LOOP-PREVIEW |
| 6. 视频规格设置 | 50% | **95%** | S-RESOLUTION, S-EXPORT-PRESETS |
| 7. 交互体验 | 60% | **95%** | S-SHORTCUTS, S-RECORDING-FEEDBACK, S-ERROR-HUMANIZE |

---

## 八、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| **MSE 浏览器兼容性** | 低 | 中 | Chromium 原生支持 MSE，Electron 28+ 内嵌 Chromium 120+，兼容性极好 |
| **FFmpeg fMP4 输出稳定性** | 中 | 高 | `ultrafast + zerolatency` 预设确保低延迟；增加 fallback 到 JPEG 帧流 |
| **多路 MSE 预览性能** | 中 | 中 | 先实现单路 MSE，多路时降级到 JPEG 帧流（卡片缩略图用帧流，选中房间用 MSE） |
| **后端 MSE streamer 资源管理** | 中 | 中 | FFmpeg 子进程有完善的生命周期管理（已有 4 级停止降级） |
| **快捷键与输入框冲突** | 低 | 低 | `event.target` 检测，input/textarea 内不触发快捷键 |
