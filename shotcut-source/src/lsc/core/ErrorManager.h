#ifndef ERRORMANAGER_H
#define ERRORMANAGER_H

#include <QObject>
#include <QString>
#include <QQueue>
#include <QMap>
#include <QDateTime>

namespace lsc {

enum class ErrorSeverity {
    Info,
    Warning,
    Error,
    Critical
};

enum class RecoveryAction {
    None,
    Retry,
    Reconnect,
    Fallback,
    UserAction
};

struct ErrorInfo {
    QString code;
    ErrorSeverity severity = ErrorSeverity::Error;
    QString message;
    QString technicalDetail;
    RecoveryAction recovery = RecoveryAction::None;
    QString recoveryHint;
    QDateTime timestamp;
    QString source;
};

class ErrorManager : public QObject {
    Q_OBJECT

public:
    static ErrorManager& instance();

    void reportError(const QString& code, const QString& message,
                     ErrorSeverity severity = ErrorSeverity::Error,
                     RecoveryAction recovery = RecoveryAction::None,
                     const QString& technicalDetail = {});

    QVector<ErrorInfo> recentErrors(int count = 10) const;
    ErrorInfo lastError() const;
    bool hasErrors() const;
    void clearErrors();

    QString userMessage(const QString& code) const;
    QString recoveryHint(const QString& code) const;

signals:
    void errorReported(const ErrorInfo& error);
    void errorCountChanged(int count);

private:
    ErrorManager();
    ~ErrorManager() = default;

    void initErrorDefinitions();

    QQueue<ErrorInfo> m_errors;
    int m_maxErrors = 100;
    QMap<QString, QString> m_userMessages;
    QMap<QString, QString> m_recoveryHints;
};

} // namespace lsc

#endif // ERRORMANAGER_H
