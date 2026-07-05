# Task 10: 集成到 Shotcut 主窗口

## 任务目标

在 Shotcut 的 `MainWindow` 中注册新的 LivestreamDock 和 AnalysisDock 面板，并连接录制完成→导入Media、分析完成→标记时间线等信号。

## 前置条件

- Task 6 已完成 (LivestreamDock)
- Task 9 已完成 (AnalysisDock)

## 修改文件

- `shotcut-source/src/mainwindow.h` — 添加前置声明和成员变量
- `shotcut-source/src/mainwindow.cpp` — 添加 dock 初始化代码

## Step 1: 修改 mainwindow.h

在文件顶部的 Class 前置声明区添加：

```cpp
class LivestreamDock;
class AnalysisDock;
```

在 `MainWindow` 类中添加两个公共成员访问器：

```cpp
    LivestreamDock* livestreamDock() const { return m_livestreamDock; }
    AnalysisDock* analysisDock() const { return m_analysisDock; }
```

在 private 区域添加成员变量：

```cpp
    LivestreamDock* m_livestreamDock;
    AnalysisDock* m_analysisDock;
```

## Step 2: 修改 mainwindow.cpp

### 2.1 包含头文件

在文件顶部的 include 区添加：

```cpp
#include "lsc/docks/LivestreamDock.h"
#include "lsc/docks/AnalysisDock.h"
```

### 2.2 添加 dock 初始化函数

在 MainWindow 的构造函数或 `setupUi()` 后的 dock 初始化区添加：

```cpp
void MainWindow::setupDocks()
{
    // ========== 现有 Shotcut dock 代码保持不变 ==========

    // --- 直播源面板 ---
    m_livestreamDock = new LivestreamDock(this);
    addDockWidget(Qt::LeftDockWidgetArea, m_livestreamDock);

    // 连接到录制完成信号
    connect(m_livestreamDock, &LivestreamDock::recordingStopped,
            this, [this](const QString& path) {
        Mlt::Producer* producer = new Mlt::Producer(
            MLT.profile(), path.toUtf8().constData());
        if (producer && producer->is_valid()) {
            m_playlistDock->append(producer);
            statusBar()->showMessage(
                "录制完成，已导入: " + path, 5000);
        }
    });

    // --- AI分析面板 ---
    m_analysisDock = new AnalysisDock(this);
    addDockWidget(Qt::RightDockWidgetArea, m_analysisDock);

    // 连接到分析片段导出信号
    connect(m_analysisDock, &AnalysisDock::clipExportRequested,
            this, [this](const Highlight& h) {
        MLT.setIn(h.startMs);
        MLT.setOut(h.endMs);
        statusBar()->showMessage(
            QString("已标记精彩片段: %1 - %2")
                .arg(QTime::fromMSecsSinceStartOfDay(h.startMs).toString("mm:ss"))
                .arg(QTime::fromMSecsSinceStartOfDay(h.endMs).toString("mm:ss")),
            3000);
    });

    // 录制开始时关联分析面板
    connect(m_livestreamDock, &LivestreamDock::recordingStarted,
            this, [this](const QString& path) {
        m_analysisDock->setProperty("currentVideo", path);
    });
}
```

### 2.3 确保 setupDocks() 被调用

检查 MainWindow 构造函数中是否调用了 `setupDocks()`。如果没有，在构造函数末尾添加：

```cpp
setupDocks();
```

## Step 3: 添加菜单项（可选）

如果需要菜单入口，在创建视图菜单时添加：

```cpp
QAction* showLivestreamAction = viewMenu->addAction("直播源面板");
showLivestreamAction->setCheckable(true);
showLivestreamAction->setChecked(true);
connect(showLivestreamAction, &QAction::toggled,
        m_livestreamDock, &QDockWidget::setVisible);

QAction* showAnalysisAction = viewMenu->addAction("AI分析面板");
showAnalysisAction->setCheckable(true);
showAnalysisAction->setChecked(true);
connect(showAnalysisAction, &QAction::toggled,
        m_analysisDock, &QDockWidget::setVisible);
```

## 验证

```bash
cd shotcut-source/build
cmake --build . --config Release
```

预期：编译通过，Shotcut 启动后可看到新面板。

## 注意事项

- 不要删除或修改 Shotcut 原有的 dock 代码
- 如果 Shotcut 的 dock 创建位置不易找到，搜索 `m_filesDock` 或 `addDockWidget` 定位
- `Highlight` 类型定义在 `analyzer/HighlightDetector.h` 中
