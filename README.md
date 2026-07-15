# Live Stream Clipper (LSC 直播切片)

基于 **Electron + React + TypeScript + Python** 的多直播间录制切片系统。面向电竞 / 直播多视角场景，提供多路同步录制、低延迟预览、墙钟精确切片、音频对齐批量导出，以及无畏契约等场景的持续高光分析。

当前版本：**v3.0.0**

仓库：[https://github.com/Lawrence7y/LSC](https://github.com/Lawrence7y/LSC)

---

## 1. 产品定位

LSC 是一款 **直播录制 + 快速切片工具**，不是 NLE 剪辑软件。

**核心目标：**

| 目标 | 说明 |
| :--- | :--- |
| 多路同步录制 | 最多 **12 路** 并发录制，适配多主播 / 多视角同场 |
| 跨房同步预览 | 最多 **4 路** MSE 预览，统一时间线标记入出点 |
| 精确切片导出 | 墙钟时间映射到录制文件，亚毫秒级对齐能力 |
| 一键多视角导出 | 预览流音频互相关对齐后，批量导出对齐片段 |
| 持续高光分析 | Valorant 回合切割 / 通用场景检测，入列后人工确认再导出 |

**明确不做：**

- 多轨道非线性编辑（视频 / 音频 / 字幕轨）
- 特效、转场、调色、字幕轨编排
- 实时直播推流

---

## 2. 核心设计理念

### 2.1 三层分层架构

```
+----------------------------------------------------------------------------------+
| 1. 前端层 (Electron Render)                                                      |
|    React + TypeScript + Vite + Ant Design + Zustand                              |
|    职责：工作台 UI、MSE 预览、快捷键、切片列表、导出队列、持续分析进度             |
+----------------------------------------------------------------------------------+
                                      │ WebSocket (localhost:9876，端口可回退)
+----------------------------------------------------------------------------------+
| 2. 桥接服务层 (Python Backend)                                                   |
|    Qt 主线程事件循环 + 工作线程 WebSocket                                        |
|    职责：线程安全消息桥接、房间/录制/导出/分析生命周期管理                         |
+----------------------------------------------------------------------------------+
                                      │ Qt 槽调用
+----------------------------------------------------------------------------------+
| 3. 核心业务层 (lsc Python 包)                                                    |
|    平台解析、FFmpeg 录制/导出、MSE 转码、音频对齐、回合/OCR 分析管线               |
+----------------------------------------------------------------------------------+
```

### 2.2 三条时间路径汇合（切片精度的根基）

切片准确性依赖三条独立路径，通过 `time.monotonic()` 单调时钟统一对齐：

1. **预览流**：独立 FFmpeg → MSE → `<video>`，用于观看与音频对齐（产出 `content_offset`）
2. **标记路径**：用户按 `i` / `o` 时记录 `mark_in/out_wallclock`（墙钟基准）
3. **录制流**：独立 FFmpeg → 磁盘文件，启动时记录 `recording_start_mono`

导出映射：

```text
export_start = mark_in_wallclock - recording_start_mono - content_offset
export_end   = mark_out_wallclock - recording_start_mono - content_offset
```

> 禁止把前端 MSE `currentTime` 直接当作 FFmpeg `-ss`。预览与录制是独立拉流，存在不定延迟。

### 2.3 预览 / 录制双模式

| 模式 | 配置 | 行为 |
| :--- | :--- | :--- |
| 独立双进程（默认） | `shared_ingest_enabled=false` | 预览与录制各一条 CDN 连接、各一个 FFmpeg，互不牵连 |
| 共享进样 | `shared_ingest_enabled=true` | 单 FFmpeg 双输出：录制 `-c copy` + 预览转码；更省连接与功耗，但故障会互相影响 |

### 2.4 时间线三坐标系契约

| 坐标系 | 典型来源 | 用途 |
| :--- | :--- | :--- |
| `preview_local` | MSE `currentTime` | 单房预览播放头 |
| `common` | 对齐后的公共轴 | 多房同步进度条 / 公共标记 |
| `recording_local` | 录制文件秒 / 墙钟差 | 导出映射、分析进度 |

进度条 `windowStart` **禁止**用 `record_started_at` 或 `recorded_duration` 直接参与，避免录制已久、预览较晚时播放头被钳到 0%。

---

## 3. 已实现功能一览

### 3.1 多房间工作台

- 房间添加 / 连接 / 批量开停录制（最多 12 路）
- 房间卡片：预览、静音、画质、放大预览、录制状态
- 多选目标房：同步分析、同步导出、对齐组
- Dashboard / 工作台 / 设置页切换（`Ctrl+1/2/3`）
- 亮色 / 暗色主题；v3 起工作台以浅色 + 品牌色 `#31B3AE` 收敛 UI 溢出与布局

### 3.2 实时预览（MSE fMP4）

- 浏览器原生 `<video>` + Media Source Extensions 播放
- 后端 FFmpeg 输出 fragmented MP4（`empty_moov+default_base_moof+frag_keyframe`）
- 最多 4 路并发预览；压力升高时自动降分辨率 / 帧率
- `mse_init` 竞态补发：`request_mse_init` + `replay_init`
- 预览源切换（直播 / 录制回看）时重建播放器，避免丢弃新 init
- 断线队列策略：可安全重放消息入队，热路径消息不堆积

### 3.3 录制引擎

- FFmpeg 录制：`copy` 直拷或 `libx264` / NVENC / QSV / AMF
- 磁盘满保护：剩余空间 < 2GB 强制安全停录
- 录制文件三层校验：路径存在、体积 > 0.1MB、格式头（MP4/FLV/MKV）
- 可恢复网络错误自动重连；权限 / 磁盘类错误不重连
- 共享进样模式下录制故障与预览统一错误广播

### 3.4 切片与导出

- `i` / `o` 标记入出点（墙钟 + 预览时间）
- 切片列表：手动切片 + AI 高光入列
- AI 回合默认 **待确认**（`confirm_status=pending`），不自动 FFmpeg 导出
- 精修：拖时间线调入出点 → 确认 / 确认并导出
- 全局导出队列：并发上限 1 或 2（`export_max_concurrent`）
- 支持转码、直拷、竖屏 9:16 裁剪、缩略图
- 导出失败友好中文提示；提交失败回滚「排队中」状态，避免按钮永久灰掉

### 3.5 多房间音频对齐

- 前端从各房预览 `<video>` 捕获 3.0s PCM（Web Audio / AudioWorklet）
- 后端 FFT 互相关计算 `content_offset`，置信度 < 0.1 降级为 0
- 以「进度最慢 / 延迟最大」房间为基准
- 低置信房间不写入对齐组，避免误对齐

### 3.6 持续分析（v3 重点）

**主房分析 → 副房映射：**

- 只分析主房录制文件
- 副房通过 `recording_start_mono` + `content_offset` 差值映射后 `clip_queued`
- 映射失败时广播 `mapping_fallback`，前端 toast 提示

**无畏契约回合切割（`valorant_round`）：**

- 音频能量 + 回合结束钟声分割战斗段
- OCR（RapidOCR）识别购买阶段 / 胜负结算，校正权威边界
- 相位调度器（buy / combat / post_combat / intermission）控制 OCR 预算，降低功耗
- 质量优先：OCR 确认回合可自动升格为可导出；仍需用户确认后再导出
- 录制结束后全文件 OCR 收尾精修

**通用模式（`generic` / `scene`）：**

- 场景切换 / 音频节奏等高光检测
- 片段去重与近邻合并

**功耗相关设计：**

- OCR 采样间隔与相位预算，避免全时段满负荷扫帧
- 预览路数压力感知降分辨率 / 帧率
- 共享进样减少重复 CDN 拉流
- OCR 加速：`ocr_accel` 支持 `auto` / `dml` / `cuda` / `cpu`（Windows 默认 DirectML）

### 3.7 平台适配

| 平台 | 适配器 | 说明 |
| :--- | :--- | :--- |
| 抖音 | DouyinAdapter | 签名拉流 |
| B站 | BilibiliAdapter | API + Cookie 鉴权 |
| 虎牙 | HuyaAdapter | JS 签名流地址 |
| 快手 / 斗鱼 / 小红书 / 微博 | 对应 Adapter | 平台 API / 页面解析 |
| 直链 | DirectAdapter | 直接媒体 URL |
| 通用页面 | GenericPageAdapter | HTML `<video>` 兜底 |

- Protocol + Registry，适配器无状态，可多线程安全并发解析
- 解析成功缓存 30s、失败 10s，防止平台 API 熔断

### 3.8 交互与运维

- 全局快捷键：播放/暂停、标记、录制、静音、全屏、批量开停录、导出等
- 设置页：编码器、码率、画质、共享进样、导出并发、OCR 加速等
- 依赖检测：FFmpeg / ffprobe / NVENC / Python
- 手动「检查更新」（GitHub Releases API，5 分钟缓存）
- 日志滚动：`%APPDATA%\lsc-electron\logs\`（单文件约 2MB × 5）

### 3.9 安全与鲁棒性

- 打开文件路径白名单 + 可执行后缀黑名单
- 子进程环境变量白名单；Windows 下 detached 启动后端
- WebSocket Origin 校验（仅 localhost / Electron）
- Handler 注册期不依赖脆弱的 `get_event_loop()`（兼容 Python 3.12+）
- 同连接消息顺序执行，避免 `set_mark_in` 与 `export_clip` 竞态
- 错误消息中英正则友好化（权限、磁盘、CDN 403/404、共享进样中断等）

---

## 4. 切片业务流程（简图）

```text
录制开始 ──► recording_start_mono
                │
预览观看 ──► 按 i/o 标记 ──► mark_*_wallclock
                │
一键对齐 ──► 预览 PCM ──► content_offset
                │
导出 / AI入列确认 ──► 墙钟映射 ──► FFmpeg -ss/-to ──► 切片文件
```

持续分析额外路径：

```text
主房录制文件 ──► 回合/场景检测 (+ OCR) ──► clip_queued(pending)
                      │
                 副房时间映射 ──► 各房切片入列
                      │
                 用户精修确认 ──► 导出队列
```

---

## 5. 技术架构细节

### 5.1 通信

- WebSocket 主端口 `9876`，占用时回退 `19877`–`19880`
- `rooms_updated` 等高频消息合并 / 日志降级
- Qt 主线程执行业务；WS 线程通过 `bridge.call` / `queue_broadcast` 跨线程

### 5.2 领域模型（节选）

- `RoomInfo`：平台流元数据
- `RecordingSession`：一次录制上下文
- `Clip`：片段定义（含墙钟、content_offset、确认状态）
- `ExportOptions`：编码 / 码率 / 竖屏等导出参数

### 5.3 项目结构

```text
├── lsc/                         # 核心 Python 包
│   ├── analyzer/                # 持续分析：回合检测、OCR、相位调度、onset
│   ├── core/models.py           # DTO
│   ├── core/services/           # 录制 / 导出 / MSE / 共享进样
│   ├── platforms/               # 平台适配器
│   ├── recorder/ · exporter/    # FFmpeg 控制
│   ├── editor/audio_aligner.py  # 音频互相关对齐
│   └── gui/multi_room/manager.py
├── python-backend/              # WebSocket 桥接服务
│   ├── main.py · server.py · message_bridge.py
│   └── handlers/                # 房间 / 时间线 / 分析 / 导出
├── lsc-electron/                # Electron 前端
│   ├── electron/                # 主进程 / preload
│   └── src/                     # 工作台 / 预览 / 时间线 / 设置
├── tests/                       # pytest 套件
├── data/                        # rooms.json 等运行时数据
└── docs/                        # 设计规格与提示词
```

---

## 6. 运行要求

- Windows 10/11（64 位）
- Python 3.10+（开发）或安装包内嵌 Python
- FFmpeg（PATH 或安装包内嵌）
- Node.js 18+（仅开发 / 打包）
- 可选：NVIDIA / Intel / AMD 硬件编码；DirectML / CUDA 加速 OCR

---

## 7. 快速开始

### 安装包（推荐）

从 [Releases](https://github.com/Lawrence7y/LSC/releases) 下载 `LSC 直播切片系统 Setup x.y.z.exe` 安装即可。

### 开发模式

```bash
# 一键拉起后端 + Electron
cd lsc-electron
npm install
npm run dev
```

```bash
# 仅 Python 后端
pip install -r requirements.txt
cd python-backend && python main.py
```

```bash
# 仅前端 Vite（纯 UI）
cd lsc-electron
npx vite --config vite.dev.config.ts
```

### 测试

```bash
set QT_QPA_PLATFORM=offscreen
pytest -v

cd lsc-electron && npx tsc --noEmit
ruff check lsc/
```

### 打包

```powershell
cd lsc-electron
.\build-installer.ps1
```

依次：嵌入式 Python + FFmpeg → npm install → `tsc --noEmit` → Vite → electron-builder。

---

## 8. 配置与数据

| 项 | 位置 | 说明 |
| :--- | :--- | :--- |
| 录制 / 编码 / 共享进样 / OCR | `settings.json` | GUI 设置页可改 |
| 房间列表 | `data/rooms.json` | 原子写入（`.tmp` + replace） |
| 录制历史 | `recording_history.json` | 会话历史 |
| 日志 | `%APPDATA%\lsc-electron\logs\` | `backend.log` / `debug.log` |
| 日志级别 | 环境变量 `LSC_LOG_LEVEL` | 默认 `INFO` |

常用设置键：`encoder`、`crf`、`bitrate`、`shared_ingest_enabled`、`export_max_concurrent`、`ocr_accel`、`preview_quality` 等。

---

## 9. v3.0.0 摘要

- **持续分析**：Valorant OCR 权威边界、相位调度、副房映射、待确认再导出
- **页面优化**：工作台 UI 统一、Modal / 设置抽屉溢出修复、分析进度与导出摘要
- **功耗优化**：OCR / 预览压力调度、共享进样可选、加速后端可选 DirectML

---

## 10. 许可证

本项目基于 **GPL v2** 许可证。
