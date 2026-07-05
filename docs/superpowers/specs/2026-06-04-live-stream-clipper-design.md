# 直播切片大师 - 设计文档

## 1. 项目概述

### 1.1 项目目标
基于 Shotcut 开源视频编辑器，开发一款专注于直播切片的桌面应用程序。在 Shotcut 成熟的视频编辑框架上，添加直播流录制、AI智能分析等差异化功能。

### 1.2 目标用户
- 短视频创作者
- 直播切片UP主
- 内容运营人员

### 1.3 核心价值
- 基于成熟的 Shotcut 框架，无需重新开发视频编辑功能
- 自动识别直播中的精彩时刻，提高切片效率
- 支持多平台直播源，统一工作流

---

## 2. 技术栈

| 类别 | 技术选型 | 说明 |
|------|----------|------|
| 基础框架 | **Shotcut** | 开源视频编辑器，基于MLT/Qt6 |
| 编程语言 | C++17 + QML | Shotcut原生技术栈 |
| UI框架 | Qt 6 + QML | Shotcut使用Qt Widgets + QML |
| 视频引擎 | MLT Framework | Shotcut底层引擎 |
| 视频处理 | FFmpeg | MLT内置支持 |
| 画面分析 | OpenCV | 计算机视觉库 |
| 语音识别 | Whisper | OpenAI开源模型 |
| 构建系统 | CMake | Shotcut使用CMake |

---

## 3. 基于 Shotcut 的架构

### 3.1 Shotcut 原有架构

```
Shotcut 源码结构:
src/
├── main.cpp                    # 程序入口
├── mainwindow.h/cpp            # 主窗口
├── mltcontroller.h/cpp         # MLT控制器（核心）
├── player.h/cpp                # 播放器
├── settings.h/cpp              # 设置管理
├── commands/                   # 撤销/重做命令
├── controllers/                # 控制器
├── dialogs/                    # 对话框
├── docks/                      # 停靠面板
│   ├── filtersdock.*           # 滤镜面板
│   ├── playlistdock.*          # 播放列表
│   ├── timeline.*              # 时间线
│   ├── encodedock.*            # 导出面板
│   └── ...
├── models/                     # 数据模型
├── qml/                        # QML界面
├── widgets/                    # 自定义控件
├── jobs/                       # 后台任务
└── screencapture/              # 屏幕捕获（已有！）
```

### 3.2 我们的扩展架构

```
我们新增的模块:
src/
├── lsc/                        # 直播切片特有模块
│   ├── livestream/             # 直播流模块
│   │   ├── StreamCapture.*     # 直播流抓取
│   │   ├── PlatformParser.*    # 平台URL解析
│   │   └── RecordingSession.*  # 录制会话
│   ├── analyzer/               # AI分析模块
│   │   ├── AudioAnalyzer.*     # 音频分析
│   │   ├── VideoAnalyzer.*     # 视频分析
│   │   ├── HighlightDetector.* # 高能时刻检测
│   │   └── SpeechRecognizer.*  # 语音识别
│   └── docks/                  # 新增面板
│       ├── livestreamdock.*    # 直播源面板
│       └── analysisdock.*      # AI分析面板
└── ...
```

---

## 4. 模块设计

### 4.1 直播流录制模块

**功能特性**:
- 直播流URL输入和解析
- 支持抖音、快手、B站、YouTube等平台
- RTMP/RTSP/HLS协议支持
- 自动重连机制
- 流质量选择

**集成方式**:
- 扩展现有的 `ScreenCapture` 模块
- 使用FFmpeg的网络流处理能力
- 录制文件自动导入媒体库

### 4.2 AI分析模块

**功能特性**:
- 高能时刻识别（画面变化、音频能量）
- 语音转文字（Whisper模型）
- 弹幕/互动热点分析
- 精彩片段自动标记

**集成方式**:
- 分析结果作为时间线标记
- 可一键将精彩片段添加到时间线
- 后台异步处理，不阻塞UI

### 4.3 复用Shotcut的功能

| Shotcut功能 | 我们的用途 |
|-------------|-----------|
| Timeline | 时间线编辑 |
| FiltersDock | 特效/滤镜 |
| EncodeDock | 视频导出 |
| Player | 视频预览 |
| PlaylistDock | 片段管理 |
| MLTController | 视频引擎 |

---

## 5. 开发计划

### 阶段一：环境搭建（1周）
- 克隆Shotcut源码
- 配置开发环境
- 成功编译运行Shotcut

### 阶段二：直播流模块（3周）
- 直播流URL解析器
- 直播流录制功能
- 直播源UI面板

### 阶段三：AI分析模块（3周）
- Whisper语音识别集成
- 高能时刻检测
- 分析结果展示面板

### 阶段四：集成和优化（2周）
- 模块间集成测试
- UI调整和美化
- 性能优化

**总工期：约9周**

---

## 6. 依赖安装

### Shotcut 依赖
参考 Shotcut 官方文档: https://github.com/mltframework/shotcut

### AI模块依赖
```bash
# OpenCV
vcpkg install opencv4

# Whisper.cpp
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp && mkdir build && cd build
cmake .. && cmake --build .
```

---

## 7. 风险和挑战

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| Shotcut代码复杂 | 学习曲线陡峭 | 专注核心模块，参考现有实现 |
| 直播平台反爬 | 无法获取直播流 | 优先支持屏幕录制 |
| AI模型性能 | 分析速度慢 | 使用轻量级模型，后台处理 |
