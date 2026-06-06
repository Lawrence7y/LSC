#ifndef LSCDATABASE_H
#define LSCDATABASE_H

#include <QObject>
#include <QSqlDatabase>
#include <QString>
#include <QVariantMap>
#include <QDateTime>
#include <QVector>

namespace lsc {

struct ProjectRecord {
    QString id;
    QString name;
    QString platform;
    QString streamerName;
    QString sourceUrl;
    QString videoPath;
    QDateTime recordedAt;
    qint64 durationSec = 0;
    qint64 fileSizeBytes = 0;
    QString analysisProfile;
    QString status; // "recording", "analyzed", "exported"
    QVariantMap metadata;
};

struct ClipRecord {
    QString id;
    QString projectId;
    double startSec = 0;
    double endSec = 0;
    double score = 0;
    QString reason;
    QString keywords;
    QString title;
    QString thumbnailPath;
    QString exportPath;
    QString status; // "detected", "approved", "rejected", "exported"
    int userRating = 0;
    QString userNote;
    QDateTime createdAt;
};

struct TaskRecord {
    QString id;
    QString type;
    QString status;
    QString title;
    QString error;
    int progress = 0;
    QDateTime createdAt;
    QDateTime startedAt;
    QDateTime finishedAt;
    QVariantMap metadata;
};

class LscDatabase : public QObject {
    Q_OBJECT

public:
    static LscDatabase& instance();

    bool initialize();
    bool isOpen() const;

    // 项目操作
    bool insertProject(const ProjectRecord& project);
    bool updateProject(const ProjectRecord& project);
    bool deleteProject(const QString& projectId);
    ProjectRecord project(const QString& projectId) const;
    QVector<ProjectRecord> allProjects() const;
    QVector<ProjectRecord> projectsByPlatform(const QString& platform) const;
    QVector<ProjectRecord> projectsByStreamer(const QString& streamer) const;

    // 片段操作
    bool insertClip(const ClipRecord& clip);
    bool updateClip(const ClipRecord& clip);
    bool deleteClip(const QString& clipId);
    ClipRecord clip(const QString& clipId) const;
    QVector<ClipRecord> clipsByProject(const QString& projectId) const;
    QVector<ClipRecord> approvedClips(const QString& projectId) const;

    // 任务记录
    bool insertTask(const TaskRecord& task);
    bool updateTask(const TaskRecord& task);
    QVector<TaskRecord> recentTasks(int count = 50) const;

    // 统计
    int totalProjects() const;
    int totalClips() const;
    int totalExportedClips() const;
    qint64 totalStorageUsed() const;

signals:
    void projectAdded(const QString& projectId);
    void projectUpdated(const QString& projectId);
    void projectDeleted(const QString& projectId);
    void clipAdded(const QString& clipId);
    void clipUpdated(const QString& clipId);

private:
    LscDatabase();
    ~LscDatabase();

    bool createTables();
    QString dbPath() const;

    QSqlDatabase m_db;
};

} // namespace lsc

#endif // LSCDATABASE_H
