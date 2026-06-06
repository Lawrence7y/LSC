// shotcut-source/src/lsc/docks/FeedbackStatsDock.cpp
#include "FeedbackStatsDock.h"
#include "analyzer/FeedbackStore.h"

#include <QFileDialog>
#include <QGridLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QVBoxLayout>

FeedbackStatsDock::FeedbackStatsDock(QWidget* parent)
    : QDockWidget(QString::fromUtf8("反馈统计"), parent)
    , m_feedbackStore(new FeedbackStore(this))
{
    setupUi();
    updateDisplay();

    connect(m_feedbackStore, &FeedbackStore::statsUpdated,
            this, &FeedbackStatsDock::updateDisplay);
}

void FeedbackStatsDock::setupUi()
{
    auto* central = new QWidget;
    auto* mainLayout = new QVBoxLayout(central);

    auto* summaryGroup = new QGroupBox(QString::fromUtf8("概览"));
    auto* summaryLayout = new QGridLayout;
    summaryLayout->addWidget(new QLabel(QString::fromUtf8("总切片:")), 0, 0);
    m_totalClipsLabel = new QLabel("0");
    summaryLayout->addWidget(m_totalClipsLabel, 0, 1);

    summaryLayout->addWidget(new QLabel(QString::fromUtf8("保留:")), 0, 2);
    m_keptClipsLabel = new QLabel("0");
    summaryLayout->addWidget(m_keptClipsLabel, 0, 3);

    summaryLayout->addWidget(new QLabel(QString::fromUtf8("删除:")), 1, 0);
    m_deletedClipsLabel = new QLabel("0");
    summaryLayout->addWidget(m_deletedClipsLabel, 1, 1);

    summaryLayout->addWidget(new QLabel(QString::fromUtf8("导出:")), 1, 2);
    m_exportedClipsLabel = new QLabel("0");
    summaryLayout->addWidget(m_exportedClipsLabel, 1, 3);

    summaryLayout->addWidget(new QLabel(QString::fromUtf8("平均评分:")), 2, 0);
    m_avgRatingLabel = new QLabel("0.0");
    summaryLayout->addWidget(m_avgRatingLabel, 2, 1);

    summaryGroup->setLayout(summaryLayout);
    mainLayout->addWidget(summaryGroup);

    auto* detailsGroup = new QGroupBox(QString::fromUtf8("详细信息"));
    auto* detailsLayout = new QVBoxLayout;
    m_detailsText = new QTextEdit;
    m_detailsText->setReadOnly(true);
    m_detailsText->setMaximumHeight(200);
    detailsLayout->addWidget(m_detailsText);
    detailsGroup->setLayout(detailsLayout);
    mainLayout->addWidget(detailsGroup);

    auto* btnLayout = new QHBoxLayout;
    m_refreshBtn = new QPushButton(QString::fromUtf8("刷新"));
    m_exportBtn = new QPushButton(QString::fromUtf8("导出报告"));
    btnLayout->addWidget(m_refreshBtn);
    btnLayout->addWidget(m_exportBtn);
    mainLayout->addLayout(btnLayout);

    connect(m_refreshBtn, &QPushButton::clicked, this, &FeedbackStatsDock::onRefreshClicked);
    connect(m_exportBtn, &QPushButton::clicked, this, &FeedbackStatsDock::onExportClicked);

    setWidget(central);
}

void FeedbackStatsDock::updateDisplay()
{
    const FeedbackStats stats = m_feedbackStore->globalStats();

    m_totalClipsLabel->setText(QString::number(stats.totalClips));
    m_keptClipsLabel->setText(QString::number(stats.keptClips));
    m_deletedClipsLabel->setText(QString::number(stats.deletedClips));
    m_exportedClipsLabel->setText(QString::number(stats.exportedClips));
    m_avgRatingLabel->setText(QString::number(stats.avgUserRating, 'f', 1));

    QString details;
    details += QString::fromUtf8("高光类型分布:\n");
    for (auto it = stats.highlightTypeCounts.constBegin(); it != stats.highlightTypeCounts.constEnd(); ++it)
        details += QString("  %1: %2\n").arg(it.key()).arg(it.value());

    details += QString::fromUtf8("\n操作类型分布:\n");
    for (auto it = stats.actionCounts.constBegin(); it != stats.actionCounts.constEnd(); ++it)
        details += QString("  %1: %2\n").arg(it.key()).arg(it.value());

    details += QString::fromUtf8("\n平均边界调整时长: %1 秒")
                   .arg(stats.avgBoundaryAdjustment, 0, 'f', 2);

    m_detailsText->setText(details);
}

void FeedbackStatsDock::onRefreshClicked()
{
    updateDisplay();
}

void FeedbackStatsDock::onExportClicked()
{
    const QString path = QFileDialog::getSaveFileName(
        this, QString::fromUtf8("导出统计报告"),
        QString(), "Text Files (*.txt)");
    if (!path.isEmpty())
        m_feedbackStore->exportStatsReport(path);
}
