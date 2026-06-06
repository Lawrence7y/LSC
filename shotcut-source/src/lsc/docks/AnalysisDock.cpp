#include "AnalysisDock.h"

#include "analyzer/ClipExporter.h"
#include "analyzer/HighlightUtils.h"

#include <QCheckBox>
#include <QComboBox>
#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QFrame>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QListWidgetItem>
#include <QPixmap>
#include <QProgressBar>
#include <QPushButton>
#include <QScrollArea>
#include <QSignalBlocker>
#include <QSlider>
#include <QTime>
#include <QVBoxLayout>

namespace {
constexpr int kThumbnailRole = Qt::UserRole + 1;

double cardOverlapRatio(const HighlightCardData& a, const HighlightCardData& b)
{
    const double overlapStart = qMax(a.startSec, b.startSec);
    const double overlapEnd = qMin(a.endSec, b.endSec);
    const double overlap = overlapEnd - overlapStart;
    if (overlap <= 0.0) {
        return 0.0;
    }

    const double minLength = qMax(0.1, qMin(a.endSec - a.startSec, b.endSec - b.startSec));
    return overlap / minLength;
}

QStringList mergeKeywords(const QStringList& left, const QStringList& right)
{
    return HighlightUtils::mergeKeywords(left, right);
}

QString dockScrollBarStyle()
{
    return QStringLiteral(
        "QScrollBar:vertical {"
        " background: #f5f6f8;"
        " width: 12px;"
        " margin: 6px 2px 6px 2px;"
        " border-radius: 6px;"
        "}"
        "QScrollBar::handle:vertical {"
        " background: #c8ccd3;"
        " min-height: 48px;"
        " border-radius: 6px;"
        "}"
        "QScrollBar::handle:vertical:hover {"
        " background: #b5bac3;"
        "}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
        " height: 0px;"
        "}"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
        " background: transparent;"
        "}");
}
}

AnalysisDock::AnalysisDock(QWidget* parent)
    : QDockWidget(QString::fromUtf8("AI 分析"), parent)
    , m_thumbnailGen(new ThumbnailGenerator(this))
    , m_clipExporter(new ClipExporter(this))
{
    setupUi();

    setHighlightEngine(new HighlightEngine(this));
    m_ownsEngine = true;

    connect(m_thumbnailGen, &ThumbnailGenerator::thumbnailReady,
            this, &AnalysisDock::onThumbnailReady);
    connect(m_thumbnailGen, &ThumbnailGenerator::allFinished,
            this, &AnalysisDock::onAllThumbnailsReady);
    connect(m_thumbnailGen, &ThumbnailGenerator::errorOccurred,
            this, &AnalysisDock::onError);

    connect(m_clipExporter, &ClipExporter::clipExported, this,
            [this](const QString& filePath, const QString&) {
                --m_pendingExports;
                if (m_pendingExports < 0) {
                    m_pendingExports = 0;
                }
                m_exportStatusLabel->setText(
                    QString::fromUtf8("已导出: %1").arg(QFileInfo(filePath).fileName()));
                emit clipExported(filePath);
                updateSelectionState();
            });
    connect(m_clipExporter, &ClipExporter::exportError, this,
            [this](const QString& filePath, const QString& error) {
                --m_pendingExports;
                if (m_pendingExports < 0) {
                    m_pendingExports = 0;
                }
                m_exportStatusLabel->setText(
                    QString::fromUtf8("导出失败: %1").arg(QFileInfo(filePath).fileName()));
                if (!error.isEmpty()) {
                    m_statusLabel->setText(QString::fromUtf8("导出错误: %1").arg(error));
                }
                updateSelectionState();
            });
    connect(m_clipExporter, &ClipExporter::allFinished, this, [this]() {
        if (m_pendingExports == 0) {
            m_statusLabel->setText(QString::fromUtf8("片段导出完成"));
        }
        updateSelectionState();
    });
}

void AnalysisDock::setHighlightEngine(HighlightEngine* engine)
{
    if (m_engine == engine) {
        reconnectEngineSignals();
        return;
    }

    if (m_engine) {
        disconnect(m_engine, nullptr, this, nullptr);
        if (m_ownsEngine && m_engine->parent() == this) {
            m_engine->deleteLater();
        }
    }

    m_engine = engine;
    m_ownsEngine = m_engine && m_engine->parent() == this;

    if (m_engine) {
        if (!m_engine->parent()) {
            m_engine->setParent(this);
            m_ownsEngine = true;
        }
        reconnectEngineSignals();
        syncStrategyComboToProfile(m_engine->analysisProfile());
        if (!m_engine->currentStrategy() && m_strategyCombo && m_sensitivitySlider && m_keywordEdit) {
            applyStrategySelection();
        }
    }
    updateAnalyzeButtonState();
}

void AnalysisDock::setVideoPath(const QString& videoPath)
{
    m_videoPath = videoPath;
    if (m_videoPathEdit) {
        m_videoPathEdit->setText(videoPath);
    }

    const bool exists = QFileInfo::exists(videoPath);
    m_statusLabel->setText(exists ? QString::fromUtf8("视频已就绪，可以开始分析")
                                  : QString::fromUtf8("等待有效的视频文件"));
    updateAnalyzeButtonState();
}

void AnalysisDock::onRecordingComplete(const QString& videoPath)
{
    setVideoPath(videoPath);
    if (m_engine) {
        syncStrategyComboToProfile(m_engine->analysisProfile());
    }
    if (m_autoAnalyzeCheck && m_autoAnalyzeCheck->isChecked()) {
        onAnalyzeClicked();
    }
}

void AnalysisDock::requestPreviewExport(double startSec, double endSec)
{
    onPreviewExportRequested(startSec, endSec);
}

void AnalysisDock::ingestRealtimeSegment(const HighlightSegment& segment, const QString& videoPath)
{
    if (m_videoPath != videoPath) {
        setVideoPath(videoPath);
    }

    HighlightCardData card;
    card.startSec = segment.startSec;
    card.endSec = segment.endSec;
    card.score = segment.score;
    card.reason = segment.reason;
    card.keywords = segment.keywords;
    card.sourceTag = QStringLiteral("实时高光");
    card.realtime = true;

    const int index = upsertCard(card);
    if (m_listWidget->count() == 1 || m_listWidget->currentRow() < 0) {
        m_listWidget->setCurrentRow(index);
    }

    updateSelectionState();
}

void AnalysisDock::setupUi()
{
    QScrollArea* scrollArea = new QScrollArea(this);
    scrollArea->setWidgetResizable(true);
    scrollArea->setFrameShape(QFrame::NoFrame);
    scrollArea->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    scrollArea->setStyleSheet(dockScrollBarStyle());

    QWidget* mainWidget = new QWidget(scrollArea);
    QVBoxLayout* mainLayout = new QVBoxLayout(mainWidget);

    QGroupBox* inputGroup = new QGroupBox(QString::fromUtf8("分析设置"), mainWidget);
    QVBoxLayout* inputLayout = new QVBoxLayout(inputGroup);

    QHBoxLayout* pathLayout = new QHBoxLayout();
    pathLayout->addWidget(new QLabel(QString::fromUtf8("视频:")));
    m_videoPathEdit = new QLineEdit(inputGroup);
    m_videoPathEdit->setReadOnly(true);
    m_videoPathEdit->setPlaceholderText(QString::fromUtf8("录制完成后会自动填入"));
    pathLayout->addWidget(m_videoPathEdit, 1);
    m_analyzeBtn = new QPushButton(QString::fromUtf8("开始分析"), inputGroup);
    connect(m_analyzeBtn, &QPushButton::clicked, this, &AnalysisDock::onAnalyzeClicked);
    pathLayout->addWidget(m_analyzeBtn);
    inputLayout->addLayout(pathLayout);

    QHBoxLayout* strategyLayout = new QHBoxLayout();
    strategyLayout->addWidget(new QLabel(QString::fromUtf8("策略:")));
    m_strategyCombo = new QComboBox(inputGroup);
    m_strategyCombo->addItem(QString::fromUtf8("通用高光"), QStringLiteral("generic"));
    m_strategyCombo->addItem(QString::fromUtf8("游戏高光"), QStringLiteral("game"));
    m_strategyCombo->addItem(QString::fromUtf8("舞蹈卡点"), QStringLiteral("dance"));
    m_strategyCombo->addItem(QString::fromUtf8("对话切片"), QStringLiteral("dialog"));
    connect(m_strategyCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &AnalysisDock::onStrategyChanged);
    strategyLayout->addWidget(m_strategyCombo, 1);
    inputLayout->addLayout(strategyLayout);

    QHBoxLayout* gameModeLayout = new QHBoxLayout();
    m_gameModeLabel = new QLabel(QString::fromUtf8("游戏模式:"), inputGroup);
    gameModeLayout->addWidget(m_gameModeLabel);
    m_gameModeCombo = new QComboBox(inputGroup);
    m_gameModeCombo->addItem(QString::fromUtf8("按回合"), QStringLiteral("round"));
    m_gameModeCombo->addItem(QString::fromUtf8("按击杀/团战"), QStringLiteral("kill"));
    m_gameModeCombo->addItem(QString::fromUtf8("智能混合"), QStringLiteral("smart"));
    gameModeLayout->addWidget(m_gameModeCombo, 1);
    inputLayout->addLayout(gameModeLayout);

    QHBoxLayout* sensitivityLayout = new QHBoxLayout();
    sensitivityLayout->addWidget(new QLabel(QString::fromUtf8("灵敏度:")));
    m_sensitivitySlider = new QSlider(Qt::Horizontal, inputGroup);
    m_sensitivitySlider->setRange(1, 10);
    m_sensitivitySlider->setValue(5);
    connect(m_sensitivitySlider, &QSlider::valueChanged,
            this, &AnalysisDock::onSensitivityChanged);
    sensitivityLayout->addWidget(m_sensitivitySlider, 1);
    m_sensitivityLabel = new QLabel(QStringLiteral("5"), inputGroup);
    sensitivityLayout->addWidget(m_sensitivityLabel);
    inputLayout->addLayout(sensitivityLayout);

    QHBoxLayout* keywordLayout = new QHBoxLayout();
    keywordLayout->addWidget(new QLabel(QString::fromUtf8("关键词:")));
    m_keywordEdit = new QLineEdit(inputGroup);
    m_keywordEdit->setPlaceholderText(QString::fromUtf8("精彩, 666, 击杀, 获胜"));
    keywordLayout->addWidget(m_keywordEdit, 1);
    inputLayout->addLayout(keywordLayout);

    m_autoAnalyzeCheck = new QCheckBox(QString::fromUtf8("录制完成后自动开始分析"), inputGroup);
    m_autoAnalyzeCheck->setChecked(true);
    inputLayout->addWidget(m_autoAnalyzeCheck);

    mainLayout->addWidget(inputGroup);

    m_progressBar = new QProgressBar(mainWidget);
    m_progressBar->setVisible(false);
    mainLayout->addWidget(m_progressBar);

    m_statusLabel = new QLabel(QString::fromUtf8("等待视频输入"), mainWidget);
    mainLayout->addWidget(m_statusLabel);

    m_summaryLabel = new QLabel(mainWidget);
    m_summaryLabel->setVisible(false);
    mainLayout->addWidget(m_summaryLabel);

    QGroupBox* listGroup = new QGroupBox(QString::fromUtf8("高光列表"), mainWidget);
    QVBoxLayout* listLayout = new QVBoxLayout(listGroup);
    m_listWidget = new QListWidget(listGroup);
    m_listWidget->setSelectionMode(QAbstractItemView::SingleSelection);
    connect(m_listWidget, &QListWidget::currentRowChanged, this, &AnalysisDock::onItemClicked);
    connect(m_listWidget, &QListWidget::itemChanged, this, [this](QListWidgetItem*) {
        updateSelectionState();
    });
    connect(m_listWidget, &QListWidget::itemDoubleClicked, this,
            [this](QListWidgetItem* item) {
                if (item) {
                    onItemDoubleClicked(m_listWidget->row(item));
                }
            });
    listLayout->addWidget(m_listWidget);

    QHBoxLayout* actionLayout = new QHBoxLayout();
    m_selectAllBtn = new QPushButton(QString::fromUtf8("全选"), listGroup);
    connect(m_selectAllBtn, &QPushButton::clicked, this, &AnalysisDock::onSelectAllClicked);
    actionLayout->addWidget(m_selectAllBtn);

    m_invertBtn = new QPushButton(QString::fromUtf8("反选"), listGroup);
    connect(m_invertBtn, &QPushButton::clicked, this, &AnalysisDock::onInvertSelectionClicked);
    actionLayout->addWidget(m_invertBtn);

    m_batchExportBtn = new QPushButton(QString::fromUtf8("批量导出"), listGroup);
    connect(m_batchExportBtn, &QPushButton::clicked, this, &AnalysisDock::onBatchExportClicked);
    actionLayout->addWidget(m_batchExportBtn);
    listLayout->addLayout(actionLayout);

    m_exportStatusLabel = new QLabel(QString::fromUtf8("尚未导出片段"), listGroup);
    listLayout->addWidget(m_exportStatusLabel);

    // Valorant pilot: ranked clips tree view
    QGroupBox* rankedGroup = new QGroupBox(QString::fromUtf8("排序结果"), mainWidget);
    QVBoxLayout* rankedLayout = new QVBoxLayout(rankedGroup);
    m_treeWidget = new QTreeWidget(rankedGroup);
    m_treeWidget->setColumnCount(4);
    m_treeWidget->setHeaderLabels({
        QString::fromUtf8("片段"),
        QString::fromUtf8("分数"),
        QString::fromUtf8("类型"),
        QString::fromUtf8("说明"),
    });
    m_treeWidget->setAlternatingRowColors(true);
    m_treeWidget->setSelectionMode(QAbstractItemView::SingleSelection);
    rankedLayout->addWidget(m_treeWidget);
    mainLayout->addWidget(rankedGroup, 1);

    // Valorant pilot: annotation panel
    QGroupBox* annotationGroup = new QGroupBox(QString::fromUtf8("标注"), mainWidget);
    QHBoxLayout* annotationLayout = new QHBoxLayout(annotationGroup);

    m_annotationKeepBtn = new QPushButton(QString::fromUtf8("保留"), annotationGroup);
    m_annotationDeleteBtn = new QPushButton(QString::fromUtf8("删除"), annotationGroup);
    m_annotationAdjustBtn = new QPushButton(QString::fromUtf8("调整边界"), annotationGroup);
    annotationLayout->addWidget(m_annotationKeepBtn);
    annotationLayout->addWidget(m_annotationDeleteBtn);
    annotationLayout->addWidget(m_annotationAdjustBtn);

    annotationLayout->addWidget(new QLabel(QString::fromUtf8("类型:")));
    m_annotationTypeCombo = new QComboBox(annotationGroup);
    m_annotationTypeCombo->addItems({
        QString::fromUtf8(""),
        QString::fromUtf8("多杀"),
        QString::fromUtf8("残局"),
        QString::fromUtf8("翻盘"),
        QString::fromUtf8("解说高能"),
        QString::fromUtf8("情绪反应"),
    });
    annotationLayout->addWidget(m_annotationTypeCombo);

    annotationLayout->addWidget(new QLabel(QString::fromUtf8("重要度:")));
    m_annotationImportanceSlider = new QSlider(Qt::Horizontal, annotationGroup);
    m_annotationImportanceSlider->setRange(0, 5);
    m_annotationImportanceSlider->setValue(0);
    m_annotationImportanceLabel = new QLabel(QStringLiteral("0"), annotationGroup);
    annotationLayout->addWidget(m_annotationImportanceSlider);
    annotationLayout->addWidget(m_annotationImportanceLabel);

    m_annotationStatusLabel = new QLabel(QString::fromUtf8(""), annotationGroup);
    annotationLayout->addWidget(m_annotationStatusLabel);

    mainLayout->addWidget(annotationGroup);

    // Connect annotation signals
    connect(m_annotationKeepBtn, &QPushButton::clicked, this, &AnalysisDock::onAnnotationKeep);
    connect(m_annotationDeleteBtn, &QPushButton::clicked, this, &AnalysisDock::onAnnotationDelete);
    connect(m_annotationAdjustBtn, &QPushButton::clicked, this, &AnalysisDock::onAnnotationAdjustBoundary);
    connect(m_annotationTypeCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &AnalysisDock::onAnnotationTypeChanged);
    connect(m_annotationImportanceSlider, &QSlider::valueChanged,
            this, &AnalysisDock::onAnnotationImportanceChanged);

    mainLayout->addWidget(listGroup, 1);
    scrollArea->setWidget(mainWidget);
    setWidget(scrollArea);

    onSensitivityChanged(m_sensitivitySlider->value());
    onStrategyChanged(m_strategyCombo->currentIndex());
    updateAnalyzeButtonState();
    updateSelectionState();
}

void AnalysisDock::reconnectEngineSignals()
{
    if (!m_engine) {
        return;
    }

    disconnect(m_engine, nullptr, this, nullptr);
    connect(m_engine, &HighlightEngine::segmentFound, this, &AnalysisDock::onSegmentFound);
    connect(m_engine, &HighlightEngine::finished, this, &AnalysisDock::onAnalysisFinished);
    connect(m_engine, &HighlightEngine::progressChanged, this, &AnalysisDock::onProgressChanged);
    connect(m_engine, &HighlightEngine::errorOccurred, this, &AnalysisDock::onError);
    connect(m_engine, &HighlightEngine::clipExported, this,
            [this](const QString& filePath, const QString&) { emit clipExported(filePath); });
}

void AnalysisDock::applyStrategySelection()
{
    if (!m_engine || !m_strategyCombo || !m_sensitivitySlider || !m_keywordEdit) {
        return;
    }

    const QString key = m_strategyCombo->currentData().toString();
    AnalysisProfile profile = AnalysisProfile::generic();
    if (key == QStringLiteral("game")) {
        profile = AnalysisProfile::valorant();
    } else if (key == QStringLiteral("dance")) {
        profile = AnalysisProfile::dance();
    } else if (key == QStringLiteral("dialog")) {
        profile = AnalysisProfile::commentary();
    }
    m_engine->setAnalysisProfile(profile);

    const double sensitivity = m_sensitivitySlider->value() / 10.0;

    QJsonObject params;
    params.insert(QStringLiteral("sensitivity"), sensitivity);
    params.insert(QStringLiteral("threshold"), qBound(0.08, 0.38 - sensitivity * 0.22, 0.38));
    params.insert(QStringLiteral("minBeats"), 4);
    if (profile.id == QStringLiteral("valorant")) {
        params.insert(QStringLiteral("gameHint"),
                      profile.gameKey.isEmpty() ? profile.id : profile.gameKey);
        if (m_gameModeCombo) {
            params.insert(QStringLiteral("segmentMode"), m_gameModeCombo->currentData().toString());
        }
    }

    const QStringList rawKeywords = m_keywordEdit->text().split(',', Qt::SkipEmptyParts);
    QJsonArray keywords;
    for (const QString& rawKeyword : rawKeywords) {
        const QString keyword = rawKeyword.trimmed();
        if (!keyword.isEmpty()) {
            keywords.append(keyword);
        }
    }
    params.insert(QStringLiteral("keywords"), keywords);
    if (m_engine->currentStrategy()) {
        m_engine->currentStrategy()->configure(params);
    }
}

void AnalysisDock::syncStrategyComboToProfile(const AnalysisProfile& profile)
{
    if (!m_strategyCombo) {
        return;
    }

    QString comboKey = profile.id;
    if (comboKey == QStringLiteral("valorant")) {
        comboKey = QStringLiteral("game");
    } else if (comboKey == QStringLiteral("commentary")) {
        comboKey = QStringLiteral("dialog");
    }

    const int index = m_strategyCombo->findData(comboKey);
    if (index < 0 || index == m_strategyCombo->currentIndex()) {
        return;
    }

    const QSignalBlocker blocker(m_strategyCombo);
    m_strategyCombo->setCurrentIndex(index);
}

void AnalysisDock::updateAnalyzeButtonState()
{
    const bool ready = !m_videoPath.isEmpty() && QFileInfo::exists(m_videoPath)
                       && m_engine != nullptr && !m_engine->isRunning();
    if (m_analyzeBtn) {
        m_analyzeBtn->setEnabled(ready);
    }
}

void AnalysisDock::onAnalyzeClicked()
{
    if (!m_engine || m_videoPath.isEmpty() || !QFileInfo::exists(m_videoPath)) {
        onError(QString::fromUtf8("请先提供可访问的视频文件"));
        return;
    }

    m_cards.clear();
    m_listWidget->clear();
    m_summaryLabel->clear();
    m_summaryLabel->setVisible(false);
    m_exportStatusLabel->setText(QString::fromUtf8("尚未导出片段"));

    if (m_thumbnailGen->isRunning()) {
        m_thumbnailGen->cancel();
    }

    applyStrategySelection();

    m_progressBar->setVisible(true);
    m_progressBar->setRange(0, 0);
    m_statusLabel->setText(QString::fromUtf8("分析中..."));
    m_analyzeBtn->setEnabled(false);
    m_engine->analyze(m_videoPath);
}

void AnalysisDock::onSegmentFound(const HighlightSegment& segment)
{
    HighlightCardData card;
    card.startSec = segment.startSec;
    card.endSec = segment.endSec;
    card.score = segment.score;
    card.reason = segment.reason;
    card.keywords = segment.keywords;
    const int index = upsertCard(card);
    if (m_listWidget->count() == 1 || m_listWidget->currentRow() < 0) {
        m_listWidget->setCurrentRow(index);
    }

    updateSelectionState();
}

void AnalysisDock::onAnalysisFinished()
{
    m_progressBar->setVisible(false);
    m_summaryLabel->setVisible(true);
    m_summaryLabel->setText(
        QString::fromUtf8("分析完成，共发现 %1 个高光片段。").arg(m_cards.size()));
    m_statusLabel->setText(QString::fromUtf8("双击片段可同步到 Shotcut 选区"));

    if (!m_cards.isEmpty() && QFileInfo::exists(m_videoPath)) {
        QVector<double> timestamps;
        timestamps.reserve(m_cards.size());
        for (int i = 0; i < m_cards.size(); ++i) {
            timestamps.append(m_cards.at(i).startSec);
        }
        m_thumbnailGen->generate(m_videoPath, timestamps);
    }

    updateAnalyzeButtonState();
    emit analysisCompleted();
}

void AnalysisDock::onProgressChanged(int percent)
{
    m_progressBar->setRange(0, 100);
    m_progressBar->setValue(percent);
    m_statusLabel->setText(QString::fromUtf8("分析中... %1%").arg(percent));
}

void AnalysisDock::onError(const QString& error)
{
    m_progressBar->setVisible(false);
    m_statusLabel->setText(QString::fromUtf8("错误: %1").arg(error));
    updateAnalyzeButtonState();
}

void AnalysisDock::onThumbnailReady(double timestamp, const QImage& thumbnail)
{
    for (int i = 0; i < m_cards.size(); ++i) {
        if (qAbs(m_cards[i].startSec - timestamp) < 0.01) {
            m_cards[i].thumbnail = thumbnail;
            updateCardWidget(i);
            break;
        }
    }
}

void AnalysisDock::onAllThumbnailsReady()
{
    if (!m_cards.isEmpty()) {
        m_statusLabel->setText(QString::fromUtf8("缩略图已生成，可预览或导出"));
    }
}

void AnalysisDock::onItemClicked(int index)
{
    if (index < 0 || index >= m_cards.size()) {
        return;
    }

    const HighlightCardData& card = m_cards.at(index);
    emit highlightSelected(card.startSec, card.endSec);
}

void AnalysisDock::onItemDoubleClicked(int index)
{
    if (index < 0 || index >= m_cards.size()) {
        return;
    }

    const HighlightCardData& card = m_cards.at(index);
    emit highlightSelected(card.startSec, card.endSec);
}

void AnalysisDock::onSelectAllClicked()
{
    for (int i = 0; i < m_cards.size(); ++i) {
        m_cards[i].selected = true;
        if (QListWidgetItem* item = m_listWidget->item(i)) {
            item->setCheckState(Qt::Checked);
        }
    }
    updateSelectionState();
}

void AnalysisDock::onInvertSelectionClicked()
{
    for (int i = 0; i < m_cards.size(); ++i) {
        m_cards[i].selected = !m_cards[i].selected;
        if (QListWidgetItem* item = m_listWidget->item(i)) {
            item->setCheckState(m_cards[i].selected ? Qt::Checked : Qt::Unchecked);
        }
    }
    updateSelectionState();
}

void AnalysisDock::onBatchExportClicked()
{
    const QVector<int> indices = selectedCardIndices();
    if (indices.isEmpty()) {
        m_exportStatusLabel->setText(QString::fromUtf8("请先勾选要导出的片段"));
        return;
    }
    exportSegments(indices);
}

void AnalysisDock::onStrategyChanged(int)
{
    const bool keywordsEnabled = m_strategyCombo->currentData().toString() != QStringLiteral("dance");
    const bool gameModeEnabled = m_strategyCombo->currentData().toString() == QStringLiteral("game");
    m_keywordEdit->setEnabled(keywordsEnabled);
    if (m_gameModeLabel) {
        m_gameModeLabel->setVisible(gameModeEnabled);
    }
    if (m_gameModeCombo) {
        m_gameModeCombo->setVisible(gameModeEnabled);
    }
}

void AnalysisDock::onSensitivityChanged(int value)
{
    m_sensitivityLabel->setText(QString::number(value));
}

void AnalysisDock::onPreviewExportRequested(double startSec, double endSec)
{
    QVector<int> indices;
    for (int i = 0; i < m_cards.size(); ++i) {
        const HighlightCardData& card = m_cards.at(i);
        if (qAbs(card.startSec - startSec) < 0.01 && qAbs(card.endSec - endSec) < 0.01) {
            indices.append(i);
            break;
        }
    }

    if (indices.isEmpty()) {
        m_exportStatusLabel->setText(QString::fromUtf8("当前预览片段不在列表中"));
        return;
    }

    exportSegments(indices);
}

int AnalysisDock::upsertCard(const HighlightCardData& card)
{
    for (int i = 0; i < m_cards.size(); ++i) {
        HighlightCardData& existing = m_cards[i];
        const bool overlaps = cardOverlapRatio(existing, card) >= 0.35;
        const double gapSec =
            qMax(existing.startSec, card.startSec) - qMin(existing.endSec, card.endSec);
        const bool nearAdjacent = gapSec > 0.0 && gapSec <= 0.8;
        if (!overlaps && !nearAdjacent) {
            continue;
        }

        mergeCardInto(existing, card);
        updateCardWidget(i);
        return i;
    }

    m_cards.append(card);
    QListWidgetItem* item = new QListWidgetItem();
    item->setFlags(item->flags() | Qt::ItemIsUserCheckable);
    item->setCheckState(Qt::Unchecked);
    m_listWidget->addItem(item);
    updateCardWidget(m_cards.size() - 1);
    return m_cards.size() - 1;
}

void AnalysisDock::mergeCardInto(HighlightCardData& target, const HighlightCardData& incoming)
{
    const bool incomingPreferred = incoming.score >= target.score;
    target.startSec = qMin(target.startSec, incoming.startSec);
    target.endSec = qMax(target.endSec, incoming.endSec);
    target.score = qMax(target.score, incoming.score);
    target.reason = incomingPreferred ? incoming.reason : target.reason;
    target.keywords = mergeKeywords(target.keywords, incoming.keywords);
    target.realtime = target.realtime || incoming.realtime;
    if (target.sourceTag.isEmpty()) {
        target.sourceTag = incoming.sourceTag;
    }
    if (target.thumbnail.isNull() && !incoming.thumbnail.isNull()) {
        target.thumbnail = incoming.thumbnail;
    }
}

void AnalysisDock::updateCardWidget(int index)
{
    if (index < 0 || index >= m_cards.size()) {
        return;
    }

    QListWidgetItem* item = m_listWidget->item(index);
    if (!item) {
        return;
    }

    const HighlightCardData& card = m_cards.at(index);
    if (card.realtime) {
        item->setText(QStringLiteral("[%1][%2 - %3] %4%%  %5")
                          .arg(card.sourceTag)
                          .arg(formatSegmentTime(card.startSec))
                          .arg(formatSegmentTime(card.endSec))
                          .arg(static_cast<int>(card.score * 100.0))
                          .arg(card.reason));
    } else {
        item->setText(QStringLiteral("[%1 - %2] %3%%  %4")
                          .arg(formatSegmentTime(card.startSec))
                          .arg(formatSegmentTime(card.endSec))
                          .arg(static_cast<int>(card.score * 100.0))
                          .arg(card.reason));
    }
    item->setToolTip(card.keywords.isEmpty()
                         ? card.reason
                         : QStringLiteral("%1\n%2")
                               .arg(card.reason, card.keywords.join(QStringLiteral(", "))));
    item->setCheckState(card.selected ? Qt::Checked : Qt::Unchecked);
    if (!card.thumbnail.isNull()) {
        item->setData(kThumbnailRole, true);
        item->setIcon(QPixmap::fromImage(card.thumbnail));
    } else {
        item->setData(kThumbnailRole, false);
    }
}

void AnalysisDock::updateSelectionState()
{
    int selectedCount = 0;
    for (int i = 0; i < m_cards.size(); ++i) {
        if (QListWidgetItem* item = m_listWidget->item(i)) {
            m_cards[i].selected = item->checkState() == Qt::Checked;
        }
        if (m_cards[i].selected) {
            ++selectedCount;
        }
    }

    const bool canExport = selectedCount > 0 && !m_videoPath.isEmpty() && m_pendingExports == 0;
    m_batchExportBtn->setEnabled(canExport);
    m_selectAllBtn->setEnabled(!m_cards.isEmpty());
    m_invertBtn->setEnabled(!m_cards.isEmpty());
}

QVector<int> AnalysisDock::selectedCardIndices() const
{
    QVector<int> indices;
    for (int i = 0; i < m_cards.size(); ++i) {
        if (QListWidgetItem* item = m_listWidget->item(i)) {
            if (item->checkState() == Qt::Checked) {
                indices.append(i);
            }
        } else if (m_cards.at(i).selected) {
            indices.append(i);
        }
    }
    return indices;
}

void AnalysisDock::exportSegments(const QVector<int>& indices)
{
    if (m_videoPath.isEmpty() || !QFileInfo::exists(m_videoPath)) {
        m_exportStatusLabel->setText(QString::fromUtf8("缺少可导出的源视频"));
        return;
    }

    const QFileInfo sourceInfo(m_videoPath);
    const QString outputDir = ClipExporter::defaultHighlightDirForSource(m_videoPath);
    m_clipExporter->setOutputDir(outputDir);

    const QString exportStamp =
        QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd_HHmmss"));
    for (int index : indices) {
        if (index < 0 || index >= m_cards.size()) {
            continue;
        }

        const HighlightCardData& card = m_cards.at(index);
        ClipJob job;
        job.sourcePath = m_videoPath;
        job.startSec = card.startSec;
        job.endSec = card.endSec;
        job.title = card.reason;
        job.outputPath = QDir(outputDir).filePath(
            QStringLiteral("%1_highlight_%2_%3_%4.mp4")
                .arg(sourceInfo.completeBaseName())
                .arg(exportStamp)
                .arg(index + 1)
                .arg(formatSegmentTime(card.startSec).replace(':', '-')));
        job.useCopy = true;
        m_clipExporter->exportClip(job);
        ++m_pendingExports;
    }

    m_exportStatusLabel->setText(QString::fromUtf8("正在导出 %1 个片段...").arg(indices.size()));
    updateSelectionState();
}

QString AnalysisDock::formatSegmentTime(double seconds) const
{
    return QTime::fromMSecsSinceStartOfDay(static_cast<int>(seconds * 1000.0)).toString("mm:ss");
}

// ===== Valorant pilot: ranked clips tree and annotation =====

void AnalysisDock::setRankedClips(const QVector<RankedClip>& clips)
{
    m_rankedClips = clips;
    if (!m_treeWidget) return;
    m_treeWidget->clear();

    QHash<QString, QTreeWidgetItem*> mothers;
    for (const RankedClip& clip : clips) {
        if (clip.parentClipId.isEmpty()) {
            auto* item = new QTreeWidgetItem(m_treeWidget);
            item->setText(0, clip.clipId);
            item->setText(1, QString::number(clip.rankScore, 'f', 2));
            item->setText(2, clip.isPrimary ? QStringLiteral("★主推") : QString());
            item->setText(3, clip.explanation);
            item->setData(0, Qt::UserRole, clip.clipId);
            mothers.insert(clip.clipId, item);
            continue;
        }
        if (mothers.contains(clip.parentClipId)) {
            auto* child = new QTreeWidgetItem(mothers.value(clip.parentClipId));
            child->setText(0, clip.clipId);
            child->setText(1, QString::number(clip.rankScore, 'f', 2));
            child->setText(2, clip.isPrimary ? QStringLiteral("★主推") : QStringLiteral("备选"));
            child->setText(3, clip.explanation);
            child->setData(0, Qt::UserRole, clip.clipId);
        }
    }
}

void AnalysisDock::onAnnotationKeep()
{
    if (!m_treeWidget) return;
    QTreeWidgetItem* item = m_treeWidget->currentItem();
    if (!item) return;
    const QString clipId = item->data(0, Qt::UserRole).toString();
    simulateAnnotation(clipId, QStringLiteral("keep"),
                       m_annotationImportanceSlider ? m_annotationImportanceSlider->value() : 0,
                       m_annotationTypeCombo ? m_annotationTypeCombo->currentText() : QString());
}

void AnalysisDock::onAnnotationDelete()
{
    if (!m_treeWidget) return;
    QTreeWidgetItem* item = m_treeWidget->currentItem();
    if (!item) return;
    const QString clipId = item->data(0, Qt::UserRole).toString();
    simulateAnnotation(clipId, QStringLiteral("delete"), 0, QString());
}

void AnalysisDock::onAnnotationAdjustBoundary()
{
    if (!m_treeWidget) return;
    QTreeWidgetItem* item = m_treeWidget->currentItem();
    if (!item) return;
    const QString clipId = item->data(0, Qt::UserRole).toString();
    for (const RankedClip& clip : m_rankedClips) {
        if (clip.clipId == clipId) {
            ClipFeedback fb;
            fb.clipId = clipId;
            fb.action = QStringLiteral("adjust_boundary");
            fb.adjustedStartSec = clip.startSec;
            fb.adjustedEndSec = clip.endSec;
            fb.importance = m_annotationImportanceSlider ? m_annotationImportanceSlider->value() : 0;
            fb.highlightType = m_annotationTypeCombo ? m_annotationTypeCombo->currentText() : QString();
            m_pendingFeedback.append(fb);
            break;
        }
    }
    writePendingFeedback();
}

void AnalysisDock::onAnnotationTypeChanged(int)
{
    // Auto-save when type changes if a clip is selected.
    if (!m_treeWidget) return;
    QTreeWidgetItem* item = m_treeWidget->currentItem();
    if (item) onAnnotationKeep();
}

void AnalysisDock::onAnnotationImportanceChanged(int value)
{
    if (m_annotationImportanceLabel) {
        m_annotationImportanceLabel->setText(QString::number(value));
    }
}

void AnalysisDock::simulateAnnotation(const QString& clipId, const QString& action,
                                       int importance, const QString& highlightType)
{
    ClipFeedback fb;
    fb.clipId = clipId;
    fb.action = action;
    fb.importance = importance;
    fb.highlightType = highlightType;
    m_pendingFeedback.append(fb);
    writePendingFeedback();
    if (m_annotationStatusLabel) {
        m_annotationStatusLabel->setText(
            QString::fromUtf8("已标注: %1 → %2").arg(clipId, action));
    }
}

void AnalysisDock::writePendingFeedback()
{
    if (m_feedbackFilePath.isEmpty()) {
        m_feedbackFilePath = m_videoPath + QStringLiteral(".feedback.json");
    }
    m_feedbackStore.save(m_feedbackFilePath, m_pendingFeedback);
}
