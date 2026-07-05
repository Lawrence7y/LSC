# LSC Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `shotcut-source/src/lsc` 与 Shotcut 主程序集成重新回到可编译、可运行、可验证的状态，并把录制到分析导出的现有主链路做完整。

**Architecture:** 先统一 `AnalysisDock` 的单一实现与公共接口，消除 `lsc` 独立工程和 Shotcut 集成工程之间的接口漂移；再把 `RecordingSession`、`HighlightEngine`、`LivestreamDock`、`AnalysisDock` 接成稳定闭环；最后补齐测试注册、预览/缩略图依赖和一批未完成功能的最小可用实现。

**Tech Stack:** C++17, Qt6 Widgets/Network/Multimedia, FFmpeg, Shotcut/MLT, CMake, MSBuild/CTest

---

### Task 1: 收口构建入口与 Dock 接口

**Files:**
- Modify: `shotcut-source/src/lsc/docks/AnalysisDock.h`
- Modify: `shotcut-source/src/lsc/docks/AnalysisDock.cpp`
- Modify: `shotcut-source/src/mainwindow.cpp`
- Modify: `shotcut-source/src/mainwindow.h`

- [ ] **Step 1: 统一 `AnalysisDock` 公开接口**
- [ ] **Step 2: 让 `AnalysisDock.cpp` 与头文件保持一致，只保留一套实现**
- [ ] **Step 3: 修复 Shotcut 主窗口对 `AnalysisDock` 的信号槽调用**
- [ ] **Step 4: 编译 `lsc` 库，确认不再因 `AnalysisDock` 接口漂移失败**

### Task 2: 修复 `lsc` CMake 依赖与测试注册

**Files:**
- Modify: `shotcut-source/src/lsc/CMakeLists.txt`
- Modify: `shotcut-source/src/CMakeLists.txt`

- [ ] **Step 1: 把 `HighlightPreviewWidget`、`ThumbnailGenerator` 所需源码和 Qt Multimedia 依赖补进 `lsc`**
- [ ] **Step 2: 为现有测试目标补 `add_test(...)` 注册**
- [ ] **Step 3: 清理明显多余或错误的 Shotcut 集成 CMake 配置**
- [ ] **Step 4: 运行 CMake 配置与 Release 构建，记录真实剩余报错**

### Task 3: 打通录制到分析闭环

**Files:**
- Modify: `shotcut-source/src/lsc/livestream/RecordingSession.h`
- Modify: `shotcut-source/src/lsc/livestream/RecordingSession.cpp`
- Modify: `shotcut-source/src/lsc/docks/LivestreamDock.cpp`
- Modify: `shotcut-source/src/mainwindow.cpp`

- [ ] **Step 1: 在 Shotcut 主程序集成时创建并注入共享 `HighlightEngine`**
- [ ] **Step 2: 修复 `RecordingSession` 停止录制后的元数据更新顺序**
- [ ] **Step 3: 明确当前“实时分析”能力边界，避免空逻辑误导**
- [ ] **Step 4: 验证录制完成后自动导入、自动分析、自动导出信号链**

### Task 4: 修复分析基础数据质量

**Files:**
- Modify: `shotcut-source/src/lsc/analyzer/AudioAnalyzer.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/VideoAnalyzer.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/HighlightDetector.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/DialogStrategy.cpp`

- [ ] **Step 1: 让 `AudioAnalyzer` 产出真实可用的段级响度/能量数据**
- [ ] **Step 2: 去掉硬编码 999 秒等伪数据**
- [ ] **Step 3: 修正 `VideoAnalyzer` 的运动段生成逻辑**
- [ ] **Step 4: 让 `DialogStrategy` 正确使用静音/字幕边界，而不是名实不符的变量**

### Task 5: 完善预览、缩略图与批量导出链路

**Files:**
- Modify: `shotcut-source/src/lsc/docks/AnalysisDock.h`
- Modify: `shotcut-source/src/lsc/docks/AnalysisDock.cpp`
- Modify: `shotcut-source/src/lsc/docks/HighlightPreviewWidget.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/ThumbnailGenerator.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/ClipExporter.cpp`

- [ ] **Step 1: 让分析结果列表与预览控件真正接起来**
- [ ] **Step 2: 生成并展示高光缩略图**
- [ ] **Step 3: 支持单个/批量导出并把状态回传 UI**
- [ ] **Step 4: 修复现有日志格式与明显 UI 状态问题**

### Task 6: 提升测试可验证性

**Files:**
- Modify: `shotcut-source/src/lsc/tests/test_highlight_detector.cpp`
- Modify: `shotcut-source/src/lsc/tests/test_highlight_engine.cpp`
- Modify: `shotcut-source/src/lsc/tests/test_video_analyzer.cpp`
- Modify: `shotcut-source/src/lsc/tests/test_recording_session.cpp`
- Modify: `shotcut-source/src/lsc/tests/test_stream_capture.cpp`
- Create: `shotcut-source/src/lsc/tests/test_analysis_dock.cpp` (如有必要)

- [ ] **Step 1: 去掉“跳过即通过”或恒真断言的测试漏洞**
- [ ] **Step 2: 区分单元测试和依赖外网的集成测试**
- [ ] **Step 3: 给关键回归点补最小失败用例**
- [ ] **Step 4: 运行 `ctest -C Release --output-on-failure` 并记录结果**

### Task 7: 补完第一批缺失功能

**Files:**
- Modify: `shotcut-source/src/lsc/livestream/PlatformParser.h`
- Modify: `shotcut-source/src/lsc/livestream/PlatformParser.cpp`
- Modify: `shotcut-source/src/lsc/docks/LivestreamDock.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/DialogStrategy.cpp`
- Modify: `shotcut-source/src/lsc/analyzer/GameStrategy.cpp`

- [ ] **Step 1: 补直播源画质选择的最小可用实现（主流/备用流）**
- [ ] **Step 2: 补平台基础元数据回填（标题/房间号/主播名能取多少取多少）**
- [ ] **Step 3: 让 `GameStrategy` 真正走 FPS 特化路径**
- [ ] **Step 4: 为后续弹幕热点/说话人分离预留稳定接口，但不引入空壳 UI**
