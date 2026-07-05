# Live Stream Clipper (直播切片)

基于 Python + PySide6 + FFmpeg 的多直播间切片系统，支持多平台直播流录制、实时预览和片段导出。

## 🚀 快速安装 (Windows用户)

### Electron桌面应用版本 (推荐)

**最新安装包**: [LSC 直播切片系统 Setup 1.0.0.exe](lsc-electron/release/LSC%20直播切片系统%20Setup%201.0.0.exe) (84 MB)

1. 下载安装包
2. 双击运行安装程序
3. 选择安装目录
4. 启动应用开始使用

**前置要求**:
- Windows 10/11 (64位)
- Python 3.10+ (用于后端服务)
- FFmpeg (可选,用于视频处理)

**文档**:
- [快速开始指南](README_BUILD.md)
- [更新说明](CHANGELOG.md)
- [测试指南](TESTING_GUIDE.md)

### Python原生版本 (开发者)

```bash
# 安装依赖
pip install PySide6 python-mpv

# 运行
python main.py
```

## 产品定位

LSC 是一款**直播录制 + 快速切片工具**，专注于：

- 多直播间同步录制（最多 12 路）
- 跨房间同步预览与选区标记
- 一键导出精彩片段

## 非目标（明确不做）

- 多轨道非线性编辑（视频/音频/字幕轨道）
- 视频特效、转场、调色
- 实时直播推流

## 功能特性

- 🎬 多直播间并发录制（最多 12 路）
- 📺 实时预览（基于 libmpv，最多 4 路并发预览）
- ✂️ 片段导出（FFmpeg 切片，支持转码/直拷贝模式）
- 🎯 平台适配（抖音、B站、虎牙、直链）
- 🖥️ 多房间工作台 + 单房间录制页双视图

## 项目结构

```
├── lsc/                    # 核心 Python 包
│   ├── platforms/          # 平台适配层（Protocol + Registry 模式）
│   │   ├── base.py         # StreamInfo, PlatformAdapter Protocol
│   │   ├── registry.py     # 适配器注册、流解析、画质选择
│   │   ├── bilibili.py     # B站适配器
│   │   ├── douyin.py       # 抖音适配器
│   │   ├── huya.py         # 虎牙适配器
│   │   └── direct.py       # 直链适配器
│   ├── recorder/           # 录制层
│   │   ├── capture.py      # FFmpeg 进程封装
│   │   └── session.py      # 录制会话
│   ├── exporter/           # 导出层
│   │   └── clip.py         # FFmpeg 切片导出
│   ├── gui/                # UI 层（PySide6）
│   │   ├── components/     # 可复用组件（Timeline, ControlBar, MpvWidget 等）
│   │   ├── pages/          # 页面（dashboard, multi_room, record, settings）
│   │   ├── multi_room/     # 多房间管理（manager + session）
│   │   ├── main_window.py  # 主窗口
│   │   └── theme.py        # 主题系统
│   ├── utils/              # 工具函数
│   ├── config.py           # 配置
│   └── cli.py              # CLI 入口
├── scripts/                # 辅助脚本
│   └── douyin_record.py    # 抖音录制脚本（被 douyin.py 适配器调用）
├── tests/                  # 测试套件
├── docs/                   # 设计文档与历史记录
├── main.py                 # 程序入口
└── pytest.ini              # 测试配置
```

## 运行要求

- Python 3.10+
- PySide6
- FFmpeg（需在 PATH 中或通过配置指定路径）
- libmpv（可选，用于视频预览；未安装时降级为占位符）

## 快速开始

```bash
# 安装依赖
pip install PySide6 python-mpv

# 运行
python main.py

# 运行测试
pytest
```

## 配置

- 日志级别可通过环境变量 `LSC_LOG_LEVEL` 控制（默认 `INFO`，开发时可设为 `DEBUG`）
- FFmpeg 路径、输出目录等通过 GUI 设置页或配置文件管理

## 平台支持

| 平台 | 显示名 | 适配器 |
|---|---|---|
| douyin | 抖音 | DouyinAdapter |
| bilibili | B站 | BilibiliAdapter |
| huya | 虎牙 | HuyaAdapter |
| direct | 直链 | DirectAdapter |

新增平台只需实现 `PlatformAdapter` Protocol（含 `platform`、`display_name`、`can_handle`、`parse`）并注册到 `registry.py` 的 `_DEFAULT_ADAPTERS`。

## 文档

详细设计文档请参阅 `docs/` 目录。

## 许可证

本项目基于 GPL v2 许可证。
