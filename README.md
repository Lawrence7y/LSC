# Live Stream Clipper (LSC 直播切片)

基于 Electron + React + TypeScript + Python 后端的多直播间录制切片系统，支持多平台直播流录制、MSE 实时预览和片段导出。

## 产品定位

LSC 是一款**直播录制 + 快速切片工具**，专注于：

- 多直播间同步录制（最多 12 路）
- 跨房间同步预览与选区标记
- 一键导出精彩片段
- 多视角音频对齐批量切片

## 非目标（明确不做）

- 多轨道非线性编辑（视频/音频/字幕轨道）
- 视频特效、转场、调色
- 实时直播推流

## 功能特性

- 多直播间并发录制（最多 12 路，FFmpeg 编码，支持 NVENC/QSV/AMF 硬件加速）
- MSE fMP4 实时预览（最多 4 路并发预览，独立于录制流）
- 片段导出（FFmpeg 精确裁切，支持转码/直拷贝模式、竖屏裁剪）
- 多房间音频互相关对齐（基于预览流 PCM + FFT 互相关，亚毫秒级精度）
- 平台适配（抖音、B站、虎牙、快手、斗鱼、小红书、微博、直链、通用页面）
- 暗色/亮色主题（遵循 Apple HIG 设计规范）
- 全局快捷键系统
- 版本检测与更新（GitHub API 手动触发检测）

## 技术架构

```
+----------------------------------------------+
| 1. 前端层 (Electron Render)                  |
|    React + TypeScript + Vite + Ant Design    |
|    + Zustand 状态管理 + MSE 预览播放器       |
+----------------------------------------------+
                    | WebSocket (localhost:9876)
+----------------------------------------------+
| 2. 桥接服务层 (Python Backend)               |
|    Qt 事件循环 + WebSocket 服务器             |
|    + 线程安全消息桥接器                       |
+----------------------------------------------+
                    | Qt 槽调用
+----------------------------------------------+
| 3. 核心业务层 (lsc Python 包)                |
|    平台解析 / FFmpeg 录制导出 / 音频对齐      |
+----------------------------------------------+
```

### 关键设计约束

- **预览流与录制流完全独立**：预览和录制是两条独立的 FFmpeg 进程和直播流连接，互不干扰。
- **墙钟时间轴对齐**：切片标记使用 `time.monotonic()` 单调时钟，通过墙钟差映射将预览标记精确映射到录制文件物理位置。
- **音频对齐基于预览流**：对齐算法的输入始终来自预览流（前端 Web Audio API 捕获），而非录制文件。

## 项目结构

```
├── lsc/                        # 核心 Python 包
│   ├── core/
│   │   ├── models.py           # 领域模型 (dataclass DTO)
│   │   └── services/           # 录制服务、导出服务、MSE 流分片转码
│   ├── platforms/              # 平台适配层 (Protocol + Registry 模式)
│   │   ├── base.py             # PlatformAdapter Protocol
│   │   ├── registry.py         # 适配器注册、流解析、缓存控制
│   │   ├── bilibili.py         # B站适配器
│   │   ├── douyin.py           # 抖音适配器
│   │   ├── huya.py             # 虎牙适配器
│   │   ├── kuaishou.py         # 快手适配器
│   │   ├── douyu.py            # 斗鱼适配器
│   │   ├── xiaohongshu.py      # 小红书适配器
│   │   ├── weibo.py            # 微博适配器
│   │   ├── direct.py           # 直链适配器
│   │   └── generic.py          # 通用页面兜底适配器
│   ├── recorder/               # FFmpeg 录制控制与文件验证
│   ├── exporter/               # FFmpeg 切片导出
│   ├── editor/
│   │   └── audio_aligner.py    # 音频互相关对齐算法
│   ├── utils/                  # 工具函数 (错误友好化等)
│   └── config.py               # 配置
├── python-backend/             # 桥接服务 (WebSocket + Qt)
│   ├── main.py                 # 后端入口
│   ├── server.py               # WebSocket 服务器
│   ├── message_bridge.py       # 线程安全跨线程桥接
│   └── persistence.py          # 本地配置持久化
├── lsc-electron/               # Electron 前端
│   ├── electron/
│   │   ├── main.ts             # 主进程 (窗口/托盘/Python 进程管理)
│   │   └── preload.ts          # 预加载脚本 (IPC 桥接)
│   ├── src/
│   │   ├── pages/              # 页面 (Dashboard/Workbench/Settings)
│   │   ├── components/         # 组件 (VideoPreview/Timeline/ExportQueue)
│   │   ├── store/              # Zustand 全局状态
│   │   ├── services/           # WebSocket 客户端 + MSE 播放器
│   │   ├── hooks/              # 自定义 Hooks (WebSocket/快捷键/通知)
│   │   └── styles/             # 设计令牌 + 全局样式
│   └── scripts/
│       └── prep-bundle.ps1     # 打包资源准备 (嵌入式 Python + FFmpeg)
├── scripts/                    # 辅助脚本
│   └── douyin_record.py        # 抖音录制脚本
├── tests/                      # 测试套件
├── data/                       # 运行时数据 (房间配置)
└── docs/                       # 设计文档
```

## 运行要求

- Windows 10/11 (64位)
- Python 3.10+
- FFmpeg（需在 PATH 中或通过打包内置）
- Node.js 18+（开发模式）

## 快速开始

### Electron 开发模式（一键拉起后端与前端）

```bash
cd lsc-electron
npm install
npm run dev
```

### 纯 Python 后端开发调试

```bash
pip install -r requirements.txt
cd python-backend && python main.py
```

### 前端 Vite 开发服务（纯 UI 调整）

```bash
cd lsc-electron
npx vite --config vite.dev.config.ts
```

### 运行测试

```bash
# Python 测试（需设置 QT_QPA_PLATFORM=offscreen）
set QT_QPA_PLATFORM=offscreen
pytest -v

# 前端类型检查
cd lsc-electron && npx tsc --noEmit

# 代码静态检查
ruff check lsc/
```

## 打包构建

```powershell
cd lsc-electron
.\build-installer.ps1
```

`build-installer.ps1` 会依次执行：
1. 准备打包运行时资源（嵌入式 Python + FFmpeg，由 `scripts/prep-bundle.ps1` 完成）
2. 安装 npm 依赖
3. TypeScript 编译检查
4. Vite 前端构建
5. electron-builder 打包安装程序

## 平台支持

| 平台 | 显示名 | 适配器 |
|---|---|---|
| douyin | 抖音 | DouyinAdapter |
| bilibili | B站 | BilibiliAdapter |
| huya | 虎牙 | HuyaAdapter |
| kuaishou | 快手 | KuaishouAdapter |
| douyu | 斗鱼 | DouyuAdapter |
| xiaohongshu | 小红书 | XiaohongshuAdapter |
| weibo | 微博 | WeiboAdapter |
| direct | 直链 | DirectAdapter |
| generic | 通用页面 | GenericPageAdapter |

新增平台只需实现 `PlatformAdapter` Protocol（含 `platform`、`display_name`、`can_handle`、`parse`）并注册到 `registry.py`。

## 配置

- **录制设置**：通过 GUI 设置页或工作台录制面板管理，持久化到 `settings.json`
- **房间列表**：持久化到 `data/rooms.json`（原子写入）
- **录制历史**：持久化到 `recording_history.json`
- **日志级别**：通过环境变量 `LSC_LOG_LEVEL` 控制（默认 `INFO`，开发时可设为 `DEBUG`）
- **日志路径**：`%APPDATA%\lsc-electron\logs\`

## 许可证

本项目基于 GPL v2 许可证。
