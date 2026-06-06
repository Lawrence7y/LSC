# Task 2: 创建直播切片模块目录结构

## 任务目标

在 Shotcut 项目下创建我们的直播切片模块 (`src/lsc/`)，包含直播流、AI分析、UI面板三个子模块。

## 前置条件

- Task 1 已完成（Shotcut 可编译）

## 执行步骤

### Step 1: 创建目录结构

```bash
cd shotcut-source
mkdir -p src/lsc/livestream
mkdir -p src/lsc/analyzer
mkdir -p src/lsc/docks
```

### Step 2: 创建模块 CMakeLists.txt

创建文件 `src/lsc/CMakeLists.txt`，内容如下：

```cmake
set(LSC_SOURCES
    livestream/StreamCapture.cpp
    livestream/PlatformParser.cpp
    livestream/RecordingSession.cpp
    analyzer/AudioAnalyzer.cpp
    analyzer/VideoAnalyzer.cpp
    analyzer/HighlightDetector.cpp
    analyzer/SpeechRecognizer.cpp
    docks/LivestreamDock.cpp
    docks/AnalysisDock.cpp
)

set(LSC_HEADERS
    livestream/StreamCapture.h
    livestream/PlatformParser.h
    livestream/RecordingSession.h
    analyzer/AudioAnalyzer.h
    analyzer/VideoAnalyzer.h
    analyzer/HighlightDetector.h
    analyzer/SpeechRecognizer.h
    docks/LivestreamDock.h
    docks/AnalysisDock.h
)

add_library(lsc STATIC ${LSC_SOURCES} ${LSC_HEADERS})
target_link_libraries(lsc PUBLIC Qt6::Widgets Qt6::Quick)
target_include_directories(lsc PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
```

### Step 3: 修改 Shotcut 的 CMakeLists.txt

在 `shotcut-source/src/CMakeLists.txt` 的 `target_link_libraries` 中添加 `lsc`：

```cmake
# 直播切片模块
add_subdirectory(lsc)

target_link_libraries(shotcut
  PRIVATE
  CuteLogger
  PkgConfig::mlt++
  PkgConfig::FFTW
  Qt6::Charts
  Qt6::GuiPrivate
  Qt6::Multimedia
  Qt6::Network
  Qt6::OpenGL
  Qt6::OpenGLWidgets
  Qt6::QuickControls2
  Qt6::QuickWidgets
  Qt6::Sql
  Qt6::WebSockets
  Qt6::Widgets
  Qt6::Xml
  lsc  # 添加这一行
)
```

### Step 4: 修改 mainwindow.h

在 `mainwindow.h` 中添加前置声明和成员变量：

```cpp
// 在 class ScreenCapture; 之后添加
class LivestreamDock;
class AnalysisDock;

// 在成员变量区域添加
LivestreamDock *m_livestreamDock;
AnalysisDock *m_analysisDock;
```

### Step 5: 修改 mainwindow.cpp

在 `mainwindow.cpp` 中添加头文件和初始化代码：

```cpp
// 在 #include "hdrpreviewwindow.h" 之后添加
#include "lsc/docks/LivestreamDock.h"
#include "lsc/docks/AnalysisDock.h"

// 在 addDockWidget(Qt::RightDockWidgetArea, m_filesDock); 之后添加
m_livestreamDock = new LivestreamDock(this);
addDockWidget(Qt::LeftDockWidgetArea, m_livestreamDock);
tabifyDockWidget(m_filesDock, m_livestreamDock);

m_analysisDock = new AnalysisDock(this);
addDockWidget(Qt::RightDockWidgetArea, m_analysisDock);
tabifyDockWidget(m_filtersDock, m_analysisDock);
```

## 预期结果

- `src/lsc/` 目录结构创建完成
- CMake 配置可正确识别新模块
- Shotcut 编译成功，新面板可见
