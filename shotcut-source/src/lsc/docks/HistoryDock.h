#ifndef HISTORYDOCK_H
#define HISTORYDOCK_H

#include <QDockWidget>
#include <QTreeWidget>
#include <QLineEdit>
#include <QComboBox>
#include <QDateTimeEdit>
#include <QPushButton>
#include <QLabel>

namespace lsc {

class LscDatabase;

class HistoryDock : public QDockWidget {
    Q_OBJECT

public:
    explicit HistoryDock(QWidget* parent = nullptr);

signals:
    void projectSelected(const QString& projectId);
    void projectDoubleClicked(const QString& projectId);
    void requestDeleteProject(const QString& projectId);
    void requestReanalyze(const QString& projectId);

private slots:
    void onSearchTextChanged(const QString& text);
    void onPlatformFilterChanged(int index);
    void onDateRangeChanged();
    void onItemDoubleClicked(QTreeWidgetItem* item, int column);
    void onDeleteClicked();
    void onReanalyzeClicked();
    void onRefreshClicked();

private:
    void setupUi();
    void loadProjects();
    void applyFilters();

    QTreeWidget* m_treeWidget;
    QLineEdit* m_searchEdit;
    QComboBox* m_platformCombo;
    QDateTimeEdit* m_dateFrom;
    QDateTimeEdit* m_dateTo;
    QPushButton* m_deleteBtn;
    QPushButton* m_reanalyzeBtn;
    QPushButton* m_refreshBtn;

    LscDatabase& m_db;
};

} // namespace lsc

#endif // HISTORYDOCK_H
