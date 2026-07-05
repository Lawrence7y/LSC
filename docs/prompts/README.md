# 直播切片大师 — AI 可执行任务提示词

## 使用方法

将每个 `.md` 文件的内容直接发给 AI（如 Claude、ChatGPT、Gemini 等），即可执行对应任务。

## 任务列表

| # | 文件 | 任务 | 依赖 |
|---|------|------|------|
| 1 | `01-shotcut-setup.md` | 编译 Shotcut | 无 |
| 2 | `02-module-structure.md` | 创建模块目录结构 | Task 1 |
| 3 | `03-platform-parser.md` | 直播平台 URL 解析器 | Task 2 |
| 4 | `04-stream-capture.md` | FFmpeg 直播流录制器 | Task 2 |
| 5 | `05-recording-session.md` | 录制会话管理 | Tasks 3, 4 |
| 6 | `06-livestream-dock.md` | 直播源 UI 面板 | Task 5 |
| 7 | `07-speech-recognizer.md` | Whisper 语音识别器 | Task 2 |
| 8 | `08-highlight-detector.md` | 高能时刻检测器 | Task 2 |
| 9 | `09-analysis-dock.md` | AI 分析 UI 面板 | Tasks 7, 8 |
| 10 | `10-mainwindow-integration.md` | 集成到 Shotcut 主窗口 | Tasks 6, 9 |
| 11 | `11-build-verify.md` | 编译验证 | Tasks 1-10 |

## 执行顺序

按任务编号顺序执行。每个任务完成后确认编译通过再继续。

## 目录结构参考

```
D:\Project\直播切片\
├── shotcut-source/          # Shotcut 源码 (已克隆)
│   └── src/
│       ├── lsc/             # 我们的模块 (任务2-9创建)
│       │   ├── livestream/
│       │   │   ├── PlatformParser.h/.cpp
│       │   │   ├── StreamCapture.h/.cpp
│       │   │   └── RecordingSession.h/.cpp
│       │   ├── analyzer/
│       │   │   ├── SpeechRecognizer.h/.cpp
│       │   │   ├── HighlightDetector.h/.cpp
│       │   │   └── AudioAnalyzer.h/.cpp
│       │   └── docks/
│       │       ├── LivestreamDock.h/.cpp
│       │       └── AnalysisDock.h/.cpp
│       └── CMakeLists.txt   # 修改：添加 lsc 子目录
├── third_party/
│   └── whisper/
│       └── CMakeLists.txt   # Whisper.cpp 依赖配置
├── docs/
│   ├── specs/
│   │   └── 2026-06-04-live-stream-clipper-design.md
│   ├── plans/
│   │   └── 2026-06-04-live-stream-clipper-implementation.md
│   ├── prompts/             # 本目录
│   └── ui-prototype/
│       └── index.html       # UI 原型
└── README.md
```
