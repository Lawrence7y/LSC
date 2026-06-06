// shotcut-source/src/lsc/docks/FeedbackStatsDock.h
#ifndef FEEDBACKSTATSDOCK_H
#define FEEDBACKSTATSDOCK_H

#include <QDockWidget>
#include <QLabel>
#include <QPushButton>
#include <QTextEdit>

class FeedbackStore;

class FeedbackStatsDock : public QDockWidget
{
    Q_OBJECT

public:
    explicit FeedbackStatsDock(QWidget* parent = nullptr);

private slots:
    void onRefreshClicked();
    void onExportClicked();

private:
    void setupUi();
    void updateDisplay();

    QLabel* m_totalClipsLabel;
    QLabel* m_keptClipsLabel;
    QLabel* m_deletedClipsLabel;
    QLabel* m_exportedClipsLabel;
    QLabel* m_avgRatingLabel;
    QTextEdit* m_detailsText;
    QPushButton* m_refreshBtn;
    QPushButton* m_exportBtn;

    FeedbackStore* m_feedbackStore;
};

#endif // FEEDBACKSTATSDOCK_H
