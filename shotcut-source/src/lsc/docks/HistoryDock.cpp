#include "HistoryDock.h"
#include "core/LscDatabase.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QDateTime>

#define MODULE_NAME "HistoryDock"

namespace lsc {

HistoryDock::HistoryDock(QWidget* parent)
    : QDockWidget(tr("历史项目"), parent)
    , m_db(LscDatabase::instance())
{
    setupUi();

    connect(&m_db, &LscDatabase::projectAdded,
            this, &HistoryDock::onRefreshClicked);
    connect(&m_db, &LscDatabase::projectUpdated,
            this, &HistoryDock::onRefreshClicked);
    connect(&m_db, &LscDatabase::projectDeleted,
            this, &HistoryDock::onRefreshClicked);

    loadProjects();
}

void HistoryDock::setupUi() {
    auto* widget = new QWidget(this);
    auto* layout = new QVBoxLayout(widget);

    // Search bar
    auto* searchLayout = new QHBoxLayout();
    m_searchEdit = new QLineEdit(this);
    m_searchEdit->setPlaceholderText(tr("搜索项目名称或主播..."));
    m_searchEdit->setClearButtonEnabled(true);
    connect(m_searchEdit, &QLineEdit::textChanged,
            this, &HistoryDock::onSearchTextChanged);
    searchLayout->addWidget(m_searchEdit);
    layout->addLayout(searchLayout);

    // Filter bar
    auto* filterLayout = new QHBoxLayout();
    m_platformCombo = new QComboBox(this);
    m_platformCombo->addItem(tr("全部平台"), QString());
    m_platformCombo->addItem(QStringLiteral("Douyin"), QStringLiteral("Douyin"));
    m_platformCombo->addItem(QStringLiteral("Bilibili"), QStringLiteral("Bilibili"));
    m_platformCombo->addItem(QStringLiteral("YouTube"), QStringLiteral("YouTube"));
    m_platformCombo->addItem(QStringLiteral("Twitch"), QStringLiteral("Twitch"));
    m_platformCombo->addItem(QStringLiteral("Kuaishou"), QStringLiteral("Kuaishou"));
    connect(m_platformCombo, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &HistoryDock::onPlatformFilterChanged);
    filterLayout->addWidget(m_platformCombo);

    m_dateFrom = new QDateTimeEdit(this);
    m_dateFrom->setDisplayFormat(QStringLiteral("yyyy-MM-dd"));
    m_dateFrom->setCalendarPopup(true);
    m_dateFrom->setDateTime(QDateTime::currentDateTime().addMonths(-3));
    connect(m_dateFrom, &QDateTimeEdit::dateTimeChanged,
            this, &HistoryDock::onDateRangeChanged);
    filterLayout->addWidget(m_dateFrom);

    m_dateTo = new QDateTimeEdit(this);
    m_dateTo->setDisplayFormat(QStringLiteral("yyyy-MM-dd"));
    m_dateTo->setCalendarPopup(true);
    m_dateTo->setDateTime(QDateTime::currentDateTime());
    connect(m_dateTo, &QDateTimeEdit::dateTimeChanged,
            this, &HistoryDock::onDateRangeChanged);
    filterLayout->addWidget(m_dateTo);

    layout->addLayout(filterLayout);

    // Tree widget
    m_treeWidget = new QTreeWidget(this);
    m_treeWidget->setHeaderLabels({
        tr("项目名称"), tr("平台"), tr("主播"),
        tr("录制时间"), tr("时长"), tr("大小"), tr("状态")
    });
    m_treeWidget->header()->setStretchLastSection(true);
    m_treeWidget->setRootIsDecorated(false);
    m_treeWidget->setSelectionMode(QAbstractItemView::SingleSelection);
    m_treeWidget->setSortingEnabled(true);
    connect(m_treeWidget, &QTreeWidget::itemDoubleClicked,
            this, &HistoryDock::onItemDoubleClicked);
    layout->addWidget(m_treeWidget);

    // Action buttons
    auto* btnLayout = new QHBoxLayout();
    m_deleteBtn = new QPushButton(tr("删除"), this);
    m_reanalyzeBtn = new QPushButton(tr("重新分析"), this);
    m_refreshBtn = new QPushButton(tr("刷新"), this);

    connect(m_deleteBtn, &QPushButton::clicked,
            this, &HistoryDock::onDeleteClicked);
    connect(m_reanalyzeBtn, &QPushButton::clicked,
            this, &HistoryDock::onReanalyzeClicked);
    connect(m_refreshBtn, &QPushButton::clicked,
            this, &HistoryDock::onRefreshClicked);

    btnLayout->addWidget(m_deleteBtn);
    btnLayout->addWidget(m_reanalyzeBtn);
    btnLayout->addStretch();
    btnLayout->addWidget(m_refreshBtn);
    layout->addLayout(btnLayout);

    setWidget(widget);
}

void HistoryDock::loadProjects() {
    m_treeWidget->setSortingEnabled(false);
    m_treeWidget->clear();

    const auto projects = m_db.allProjects();
    for (const auto& p : projects) {
        auto* item = new QTreeWidgetItem(m_treeWidget);
        item->setData(0, Qt::UserRole, p.id);
        item->setText(0, p.name);
        item->setText(1, p.platform);
        item->setText(2, p.streamerName);
        item->setText(3, p.recordedAt.toString(QStringLiteral("yyyy-MM-dd hh:mm")));
        item->setText(4, QString("%1m").arg(p.durationSec / 60));
        item->setText(5, QString("%1MB").arg(p.fileSizeBytes / (1024 * 1024)));
        item->setText(6, p.status);
    }

    m_treeWidget->setSortingEnabled(true);
    applyFilters();
}

void HistoryDock::applyFilters() {
    const QString searchText = m_searchEdit->text().trimmed().toLower();
    const QString platform = m_platformCombo->currentData().toString();
    const QDateTime dateFrom = m_dateFrom->dateTime();
    const QDateTime dateTo = m_dateTo->dateTime();

    int visibleCount = 0;
    for (int i = 0; i < m_treeWidget->topLevelItemCount(); ++i) {
        auto* item = m_treeWidget->topLevelItem(i);
        const QString name = item->text(0).toLower();
        const QString itemPlatform = item->text(1);
        const QString streamer = item->text(2).toLower();
        const QDateTime recordedAt = QDateTime::fromString(item->text(3), QStringLiteral("yyyy-MM-dd hh:mm"));

        bool visible = true;
        if (!searchText.isEmpty() && !name.contains(searchText) && !streamer.contains(searchText)) {
            visible = false;
        }
        if (!platform.isEmpty() && itemPlatform != platform) {
            visible = false;
        }
        if (recordedAt.isValid() && (recordedAt < dateFrom || recordedAt > dateTo)) {
            visible = false;
        }

        item->setHidden(!visible);
        if (visible) ++visibleCount;
    }
}

void HistoryDock::onSearchTextChanged(const QString& /*text*/) {
    applyFilters();
}

void HistoryDock::onPlatformFilterChanged(int /*index*/) {
    applyFilters();
}

void HistoryDock::onDateRangeChanged() {
    applyFilters();
}

void HistoryDock::onItemDoubleClicked(QTreeWidgetItem* item, int /*column*/) {
    if (item) {
        const QString projectId = item->data(0, Qt::UserRole).toString();
        emit projectDoubleClicked(projectId);
    }
}

void HistoryDock::onDeleteClicked() {
    auto* item = m_treeWidget->currentItem();
    if (item) {
        const QString projectId = item->data(0, Qt::UserRole).toString();
        emit requestDeleteProject(projectId);
    }
}

void HistoryDock::onReanalyzeClicked() {
    auto* item = m_treeWidget->currentItem();
    if (item) {
        const QString projectId = item->data(0, Qt::UserRole).toString();
        emit requestReanalyze(projectId);
    }
}

void HistoryDock::onRefreshClicked() {
    loadProjects();
}

} // namespace lsc

#undef MODULE_NAME
