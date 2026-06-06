# Task 9: AI 分析 Dock 面板

## 任务目标

实现 AI 分析停靠面板，展示分析进度、精彩片段表格、支持双击导出到时间线。

## 创建文件

- `src/lsc/docks/AnalysisDock.h`
- `src/lsc/docks/AnalysisDock.cpp`

## 前置条件

- Task 7 已完成 (SpeechRecognizer)
- Task 8 已完成 (HighlightDetector)

## AnalysisDock.h

```cpp
#ifndef ANALYSISDOCK_H
#define ANALYSISDOCK_H

#include <QDockWidget>
#include <QTableView>
#include <QPushButton>
#include <QProgressBar>
#include <QLabel>
#include <QStandardItemModel>
#include "analyzer/HighlightDetector.h"
#include "analyzer/SpeechRecognizer.h"

class AnalysisDock : public QDockWidget
{
    Q_OBJECT
public:
    explicit AnalysisDock(QWidget* parent = nullptr);
    ~AnalysisDock();

    QList<Highlight> highlights() const { return m_highlights; }

signals:
    void clipExportRequested(const Highlight& highlight);

private slots:
    void onAnalyzeClicked();
    void onHighlightFound(const Highlight& highlight);
    void onAnalysisCompleted(const QList<Highlight>& results);
    void onProgressChanged(int percent, const QString& status);
    void onTranscriptionReady(const QList<TranscriptionResult>& results);
    void onItemDoubleClicked(const QModelIndex& index);

private:
    void setupUI();
    void addHighlightRow(const Highlight& h);

    HighlightDetector* m_detector;
    SpeechRecognizer* m_recognizer;
    QStandardItemModel* m_model;
    QTableView* m_tableView;
    QProgressBar* m_progressBar;
    QLabel* m_statusLabel;
    QLabel* m_totalLabel;
    QPushButton* m_analyzeBtn;
    QPushButton* m_cancelBtn;
    QList<Highlight> m_highlights;
    QList<TranscriptionResult> m_transcriptions;
};

#endif
```

## AnalysisDock.cpp

```cpp
#include "AnalysisDock.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFormLayout>
#include <QGroupBox>
#include <QHeaderView>
#include <QTime>

AnalysisDock::AnalysisDock(QWidget* parent)
    : QDockWidget("AI分析", parent)
    , m_detector(new HighlightDetector(this))
    , m_recognizer(new SpeechRecognizer(this))
{
    setupUI();

    connect(m_detector, &HighlightDetector::progressChanged,
            this, &AnalysisDock::onProgressChanged);
    connect(m_detector, &HighlightDetector::highlightFound,
            this, &AnalysisDock::onHighlightFound);
    connect(m_detector, &HighlightDetector::analysisCompleted,
            this, &AnalysisDock::onAnalysisCompleted);
    connect(m_recognizer, &SpeechRecognizer::transcriptionReady,
            this, &AnalysisDock::onTranscriptionReady);
}

AnalysisDock::~AnalysisDock() {}

void AnalysisDock::setupUI()
{
    QWidget* container = new QWidget(this);
    QVBoxLayout* layout = new QVBoxLayout(container);

    // 控制按钮
    QHBoxLayout* ctrlLayout = new QHBoxLayout();
    m_analyzeBtn = new QPushButton("开始分析");
    m_analyzeBtn->setStyleSheet(
        "QPushButton { background: #89b4fa; padding: 8px; border-radius: 4px; }");
    m_cancelBtn = new QPushButton("取消");
    m_cancelBtn->setEnabled(false);
    ctrlLayout->addWidget(m_analyzeBtn);
    ctrlLayout->addWidget(m_cancelBtn);
    layout->addLayout(ctrlLayout);

    connect(m_analyzeBtn, &QPushButton::clicked,
            this, &AnalysisDock::onAnalyzeClicked);
    connect(m_cancelBtn, &QPushButton::clicked, [this]() {
        m_detector->cancel();
        m_analyzeBtn->setEnabled(true);
        m_cancelBtn->setEnabled(false);
    });

    // 进度和状态
    m_statusLabel = new QLabel("就绪");
    layout->addWidget(m_statusLabel);
    m_progressBar = new QProgressBar();
    m_progressBar->setRange(0, 100);
    layout->addWidget(m_progressBar);

    // 精彩片段表格
    QGroupBox* hlGroup = new QGroupBox("精彩片段");
    QVBoxLayout* hlLayout = new QVBoxLayout(hlGroup);

    m_model = new QStandardItemModel(0, 4, this);
    m_model->setHorizontalHeaderLabels(
        {"时间", "类型", "描述", "置信度"});

    m_tableView = new QTableView();
    m_tableView->setModel(m_model);
    m_tableView->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_tableView->setEditTriggers(QAbstractItemView::NoEditTriggers);
    m_tableView->horizontalHeader()->setStretchLastSection(true);
    m_tableView->setAlternatingRowColors(true);
    hlLayout->addWidget(m_tableView);

    connect(m_tableView, &QTableView::doubleClicked,
            this, &AnalysisDock::onItemDoubleClicked);

    layout->addWidget(hlGroup);

    // 统计
    QGroupBox* statsGroup = new QGroupBox("分析统计");
    QFormLayout* statsLayout = new QFormLayout(statsGroup);
    m_totalLabel = new QLabel("0");
    statsLayout->addRow("检测片段:", m_totalLabel);
    layout->addWidget(statsGroup);
    layout->addStretch();

    setWidget(container);
    setMinimumWidth(300);
}

void AnalysisDock::onAnalyzeClicked()
{
    // 从主窗口获取当前视频路径
    QString videoPath = "current_recording.mp4";  // 需通过信号获取

    m_highlights.clear();
    m_model->removeRows(0, m_model->rowCount());
    m_statusLabel->setText("分析中...");
    m_analyzeBtn->setEnabled(false);
    m_cancelBtn->setEnabled(true);
    m_progressBar->setValue(0);

    m_detector->analyze(videoPath, true, true);
}

void AnalysisDock::onHighlightFound(const Highlight& h)
{
    addHighlightRow(h);
}

void AnalysisDock::onAnalysisCompleted(const QList<Highlight>& results)
{
    m_highlights = results;
    m_totalLabel->setText(QString::number(results.size()));
    m_statusLabel->setText(
        QString("完成 — 共 %1 个精彩片段").arg(results.size()));
    m_analyzeBtn->setEnabled(true);
    m_cancelBtn->setEnabled(false);
    m_progressBar->setValue(100);
}

void AnalysisDock::onProgressChanged(int pct, const QString& status)
{
    m_progressBar->setValue(pct);
    m_statusLabel->setText(status);
}

void AnalysisDock::onTranscriptionReady(
    const QList<TranscriptionResult>& results)
{
    m_transcriptions = results;
    m_statusLabel->setText(
        QString("语音识别完成 — %1 条字幕").arg(results.size()));
}

void AnalysisDock::onItemDoubleClicked(const QModelIndex& index)
{
    if (index.isValid() && index.row() < m_highlights.size())
        emit clipExportRequested(m_highlights[index.row()]);
}

void AnalysisDock::addHighlightRow(const Highlight& h)
{
    int row = m_model->rowCount();
    m_model->insertRow(row);
    m_model->setItem(row, 0, new QStandardItem(
        QString("%1 - %2")
            .arg(QTime::fromMSecsSinceStartOfDay(h.startMs).toString("mm:ss"))
            .arg(QTime::fromMSecsSinceStartOfDay(h.endMs).toString("mm:ss"))));
    m_model->setItem(row, 1, new QStandardItem(h.type));
    m_model->setItem(row, 2, new QStandardItem(h.description));
    m_model->setItem(row, 3, new QStandardItem(
        QString("%1%").arg(static_cast<int>(h.confidence * 100))));
}
```

## 验证

```bash
cd shotcut-source/build
cmake --build . --config Release
```

预期：编译通过。
