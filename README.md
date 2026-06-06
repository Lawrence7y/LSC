# Live Stream Clipper (直播切片)

基于 Shotcut 的直播切片系统，支持自动录制、高光检测和片段导出。

## 功能特性

- 🎬 直播流录制（支持抖音、B站等平台）
- 🎯 AI 高光检测（通用、游戏、舞蹈、对话策略）
- ✂️ 自动片段导出
- 📊 实时预览和分析

## 项目结构

```
├── shotcut-source/     # Shotcut 编辑器源码（含 LSC 模块）
│   └── src/lsc/        # 直播切片核心代码
├── mlt-source/         # MLT 框架源码
├── dlfcn-win32/        # Windows 动态链接库
├── docs/               # 项目文档
└── deps/               # 依赖文件（不纳入版本控制）
```

## 构建要求

- CMake 3.16+
- Qt 6.5+
- FFmpeg
- MSVC 2019+ 或 MinGW

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/<your-username>/live-stream-clipper.git
cd live-stream-clipper

# 构建
cd shotcut-source
mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release
```

## 文档

详细设计文档请参阅 `docs/` 目录。

## 许可证

本项目基于 GPL v2 许可证。
