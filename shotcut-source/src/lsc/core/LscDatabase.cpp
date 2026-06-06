#include "LscDatabase.h"

#include <QSqlQuery>
#include <QSqlError>
#include <QJsonDocument>
#include <QJsonObject>
#include <QCoreApplication>
#include <QDir>
#include <QStandardPaths>

#define MODULE_NAME "Database"

namespace lsc {

static const char* DB_CONNECTION_NAME = "lsc_main";

static QByteArray variantMapToJson(const QVariantMap& map)
{
    if (map.isEmpty())
        return {};
    return QJsonDocument(QJsonObject::fromVariantMap(map)).toJson(QJsonDocument::Compact);
}

static QVariantMap jsonToVariantMap(const QByteArray& json)
{
    if (json.isEmpty())
        return {};
    QJsonParseError err;
    QJsonDocument doc = QJsonDocument::fromJson(json, &err);
    if (err.error != QJsonParseError::NoError)
        return {};
    return doc.object().toVariantMap();
}

LscDatabase& LscDatabase::instance()
{
    static LscDatabase s_instance;
    return s_instance;
}

LscDatabase::LscDatabase() = default;

LscDatabase::~LscDatabase()
{
    if (m_db.isOpen()) {
        m_db.close();
    }
    QSqlDatabase::removeDatabase(DB_CONNECTION_NAME);
}

QString LscDatabase::dbPath() const
{
    const QString dataDir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(dataDir);
    return QDir(dataDir).filePath("lsc.db");
}

bool LscDatabase::initialize()
{
    if (m_db.isOpen())
        return true;

    m_db = QSqlDatabase::addDatabase("QSQLITE", DB_CONNECTION_NAME);
    m_db.setDatabaseName(dbPath());

    if (!m_db.open()) {
        qCritical() << "[LSC][Database][ERROR] Failed to open database:" << m_db.lastError().text();
        return false;
    }

    QSqlQuery query(m_db);
    query.exec("PRAGMA journal_mode=WAL");
    query.exec("PRAGMA foreign_keys=ON");

    if (!createTables()) {
        qCritical() << "[LSC][Database][ERROR] Failed to create tables";
        return false;
    }

    return true;
}

bool LscDatabase::isOpen() const
{
    return m_db.isOpen();
}

bool LscDatabase::createTables()
{
    QSqlQuery query(m_db);

    if (!query.exec(
            "CREATE TABLE IF NOT EXISTS projects ("
            "  id TEXT PRIMARY KEY,"
            "  name TEXT,"
            "  platform TEXT,"
            "  streamer_name TEXT,"
            "  source_url TEXT,"
            "  video_path TEXT,"
            "  recorded_at DATETIME,"
            "  duration_sec INTEGER,"
            "  file_size_bytes INTEGER,"
            "  analysis_profile TEXT,"
            "  status TEXT,"
            "  metadata TEXT"
            ")")) {
        qCritical() << "[LSC][Database][ERROR] Create projects table failed:" << query.lastError().text();
        return false;
    }

    if (!query.exec(
            "CREATE TABLE IF NOT EXISTS clips ("
            "  id TEXT PRIMARY KEY,"
            "  project_id TEXT,"
            "  start_sec REAL,"
            "  end_sec REAL,"
            "  score REAL,"
            "  reason TEXT,"
            "  keywords TEXT,"
            "  title TEXT,"
            "  thumbnail_path TEXT,"
            "  export_path TEXT,"
            "  status TEXT,"
            "  user_rating INTEGER,"
            "  user_note TEXT,"
            "  created_at DATETIME,"
            "  FOREIGN KEY(project_id) REFERENCES projects(id)"
            ")")) {
        qCritical() << "[LSC][Database][ERROR] Create clips table failed:" << query.lastError().text();
        return false;
    }

    if (!query.exec(
            "CREATE TABLE IF NOT EXISTS task_history ("
            "  id TEXT PRIMARY KEY,"
            "  type TEXT,"
            "  status TEXT,"
            "  title TEXT,"
            "  error TEXT,"
            "  progress INTEGER,"
            "  created_at DATETIME,"
            "  started_at DATETIME,"
            "  finished_at DATETIME,"
            "  metadata TEXT"
            ")")) {
        qCritical() << "[LSC][Database][ERROR] Create task_history table failed:" << query.lastError().text();
        return false;
    }

    query.exec("CREATE INDEX IF NOT EXISTS idx_clips_project ON clips(project_id)");
    query.exec("CREATE INDEX IF NOT EXISTS idx_projects_platform ON projects(platform)");
    query.exec("CREATE INDEX IF NOT EXISTS idx_projects_streamer ON projects(streamer_name)");

    return true;
}

// ===== Project CRUD =====

bool LscDatabase::insertProject(const ProjectRecord& p)
{
    QSqlQuery query(m_db);
    query.prepare(
        "INSERT INTO projects "
        "(id, name, platform, streamer_name, source_url, video_path, "
        " recorded_at, duration_sec, file_size_bytes, analysis_profile, status, metadata) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)");
    query.addBindValue(p.id);
    query.addBindValue(p.name);
    query.addBindValue(p.platform);
    query.addBindValue(p.streamerName);
    query.addBindValue(p.sourceUrl);
    query.addBindValue(p.videoPath);
    query.addBindValue(p.recordedAt.toString(Qt::ISODate));
    query.addBindValue(p.durationSec);
    query.addBindValue(p.fileSizeBytes);
    query.addBindValue(p.analysisProfile);
    query.addBindValue(p.status);
    query.addBindValue(variantMapToJson(p.metadata));

    if (!query.exec()) {
        qCritical() << "[LSC][Database][ERROR] insertProject failed:" << query.lastError().text();
        return false;
    }
    emit projectAdded(p.id);
    return true;
}

bool LscDatabase::updateProject(const ProjectRecord& p)
{
    QSqlQuery query(m_db);
    query.prepare(
        "UPDATE projects SET name=?, platform=?, streamer_name=?, source_url=?, "
        "video_path=?, recorded_at=?, duration_sec=?, file_size_bytes=?, "
        "analysis_profile=?, status=?, metadata=? WHERE id=?");
    query.addBindValue(p.name);
    query.addBindValue(p.platform);
    query.addBindValue(p.streamerName);
    query.addBindValue(p.sourceUrl);
    query.addBindValue(p.videoPath);
    query.addBindValue(p.recordedAt.toString(Qt::ISODate));
    query.addBindValue(p.durationSec);
    query.addBindValue(p.fileSizeBytes);
    query.addBindValue(p.analysisProfile);
    query.addBindValue(p.status);
    query.addBindValue(variantMapToJson(p.metadata));
    query.addBindValue(p.id);

    if (!query.exec()) {
        qCritical() << "[LSC][Database][ERROR] updateProject failed:" << query.lastError().text();
        return false;
    }
    emit projectUpdated(p.id);
    return true;
}

bool LscDatabase::deleteProject(const QString& projectId)
{
    QSqlQuery query(m_db);
    query.prepare("DELETE FROM projects WHERE id=?");
    query.addBindValue(projectId);

    if (!query.exec()) {
        qCritical() << "[LSC][Database][ERROR] deleteProject failed:" << query.lastError().text();
        return false;
    }
    emit projectDeleted(projectId);
    return true;
}

ProjectRecord LscDatabase::project(const QString& projectId) const
{
    ProjectRecord r;
    QSqlQuery query(m_db);
    query.prepare(
        "SELECT id, name, platform, streamer_name, source_url, video_path, "
        "recorded_at, duration_sec, file_size_bytes, analysis_profile, status, metadata "
        "FROM projects WHERE id=?");
    query.addBindValue(projectId);

    if (query.exec() && query.next()) {
        r.id = query.value(0).toString();
        r.name = query.value(1).toString();
        r.platform = query.value(2).toString();
        r.streamerName = query.value(3).toString();
        r.sourceUrl = query.value(4).toString();
        r.videoPath = query.value(5).toString();
        r.recordedAt = QDateTime::fromString(query.value(6).toString(), Qt::ISODate);
        r.durationSec = query.value(7).toLongLong();
        r.fileSizeBytes = query.value(8).toLongLong();
        r.analysisProfile = query.value(9).toString();
        r.status = query.value(10).toString();
        r.metadata = jsonToVariantMap(query.value(11).toByteArray());
    }
    return r;
}

QVector<ProjectRecord> LscDatabase::allProjects() const
{
    QVector<ProjectRecord> list;
    QSqlQuery query(m_db);
    if (!query.exec(
            "SELECT id, name, platform, streamer_name, source_url, video_path, "
            "recorded_at, duration_sec, file_size_bytes, analysis_profile, status, metadata "
            "FROM projects ORDER BY recorded_at DESC"))
        return list;

    while (query.next()) {
        ProjectRecord r;
        r.id = query.value(0).toString();
        r.name = query.value(1).toString();
        r.platform = query.value(2).toString();
        r.streamerName = query.value(3).toString();
        r.sourceUrl = query.value(4).toString();
        r.videoPath = query.value(5).toString();
        r.recordedAt = QDateTime::fromString(query.value(6).toString(), Qt::ISODate);
        r.durationSec = query.value(7).toLongLong();
        r.fileSizeBytes = query.value(8).toLongLong();
        r.analysisProfile = query.value(9).toString();
        r.status = query.value(10).toString();
        r.metadata = jsonToVariantMap(query.value(11).toByteArray());
        list.append(r);
    }
    return list;
}

QVector<ProjectRecord> LscDatabase::projectsByPlatform(const QString& platform) const
{
    QVector<ProjectRecord> list;
    QSqlQuery query(m_db);
    query.prepare(
        "SELECT id, name, platform, streamer_name, source_url, video_path, "
        "recorded_at, duration_sec, file_size_bytes, analysis_profile, status, metadata "
        "FROM projects WHERE platform=? ORDER BY recorded_at DESC");
    query.addBindValue(platform);

    if (!query.exec())
        return list;

    while (query.next()) {
        ProjectRecord r;
        r.id = query.value(0).toString();
        r.name = query.value(1).toString();
        r.platform = query.value(2).toString();
        r.streamerName = query.value(3).toString();
        r.sourceUrl = query.value(4).toString();
        r.videoPath = query.value(5).toString();
        r.recordedAt = QDateTime::fromString(query.value(6).toString(), Qt::ISODate);
        r.durationSec = query.value(7).toLongLong();
        r.fileSizeBytes = query.value(8).toLongLong();
        r.analysisProfile = query.value(9).toString();
        r.status = query.value(10).toString();
        r.metadata = jsonToVariantMap(query.value(11).toByteArray());
        list.append(r);
    }
    return list;
}

QVector<ProjectRecord> LscDatabase::projectsByStreamer(const QString& streamer) const
{
    QVector<ProjectRecord> list;
    QSqlQuery query(m_db);
    query.prepare(
        "SELECT id, name, platform, streamer_name, source_url, video_path, "
        "recorded_at, duration_sec, file_size_bytes, analysis_profile, status, metadata "
        "FROM projects WHERE streamer_name=? ORDER BY recorded_at DESC");
    query.addBindValue(streamer);

    if (!query.exec())
        return list;

    while (query.next()) {
        ProjectRecord r;
        r.id = query.value(0).toString();
        r.name = query.value(1).toString();
        r.platform = query.value(2).toString();
        r.streamerName = query.value(3).toString();
        r.sourceUrl = query.value(4).toString();
        r.videoPath = query.value(5).toString();
        r.recordedAt = QDateTime::fromString(query.value(6).toString(), Qt::ISODate);
        r.durationSec = query.value(7).toLongLong();
        r.fileSizeBytes = query.value(8).toLongLong();
        r.analysisProfile = query.value(9).toString();
        r.status = query.value(10).toString();
        r.metadata = jsonToVariantMap(query.value(11).toByteArray());
        list.append(r);
    }
    return list;
}

// ===== Clip CRUD =====

bool LscDatabase::insertClip(const ClipRecord& c)
{
    QSqlQuery query(m_db);
    query.prepare(
        "INSERT INTO clips "
        "(id, project_id, start_sec, end_sec, score, reason, keywords, title, "
        " thumbnail_path, export_path, status, user_rating, user_note, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)");
    query.addBindValue(c.id);
    query.addBindValue(c.projectId);
    query.addBindValue(c.startSec);
    query.addBindValue(c.endSec);
    query.addBindValue(c.score);
    query.addBindValue(c.reason);
    query.addBindValue(c.keywords);
    query.addBindValue(c.title);
    query.addBindValue(c.thumbnailPath);
    query.addBindValue(c.exportPath);
    query.addBindValue(c.status);
    query.addBindValue(c.userRating);
    query.addBindValue(c.userNote);
    query.addBindValue(c.createdAt.toString(Qt::ISODate));

    if (!query.exec()) {
        qCritical() << "[LSC][Database][ERROR] insertClip failed:" << query.lastError().text();
        return false;
    }
    emit clipAdded(c.id);
    return true;
}

bool LscDatabase::updateClip(const ClipRecord& c)
{
    QSqlQuery query(m_db);
    query.prepare(
        "UPDATE clips SET project_id=?, start_sec=?, end_sec=?, score=?, reason=?, "
        "keywords=?, title=?, thumbnail_path=?, export_path=?, status=?, "
        "user_rating=?, user_note=? WHERE id=?");
    query.addBindValue(c.projectId);
    query.addBindValue(c.startSec);
    query.addBindValue(c.endSec);
    query.addBindValue(c.score);
    query.addBindValue(c.reason);
    query.addBindValue(c.keywords);
    query.addBindValue(c.title);
    query.addBindValue(c.thumbnailPath);
    query.addBindValue(c.exportPath);
    query.addBindValue(c.status);
    query.addBindValue(c.userRating);
    query.addBindValue(c.userNote);
    query.addBindValue(c.id);

    if (!query.exec()) {
        qCritical() << "[LSC][Database][ERROR] updateClip failed:" << query.lastError().text();
        return false;
    }
    emit clipUpdated(c.id);
    return true;
}

bool LscDatabase::deleteClip(const QString& clipId)
{
    QSqlQuery query(m_db);
    query.prepare("DELETE FROM clips WHERE id=?");
    query.addBindValue(clipId);

    if (!query.exec()) {
        qCritical() << "[LSC][Database][ERROR] deleteClip failed:" << query.lastError().text();
        return false;
    }
    return true;
}

ClipRecord LscDatabase::clip(const QString& clipId) const
{
    ClipRecord r;
    QSqlQuery query(m_db);
    query.prepare(
        "SELECT id, project_id, start_sec, end_sec, score, reason, keywords, title, "
        "thumbnail_path, export_path, status, user_rating, user_note, created_at "
        "FROM clips WHERE id=?");
    query.addBindValue(clipId);

    if (query.exec() && query.next()) {
        r.id = query.value(0).toString();
        r.projectId = query.value(1).toString();
        r.startSec = query.value(2).toDouble();
        r.endSec = query.value(3).toDouble();
        r.score = query.value(4).toDouble();
        r.reason = query.value(5).toString();
        r.keywords = query.value(6).toString();
        r.title = query.value(7).toString();
        r.thumbnailPath = query.value(8).toString();
        r.exportPath = query.value(9).toString();
        r.status = query.value(10).toString();
        r.userRating = query.value(11).toInt();
        r.userNote = query.value(12).toString();
        r.createdAt = QDateTime::fromString(query.value(13).toString(), Qt::ISODate);
    }
    return r;
}

QVector<ClipRecord> LscDatabase::clipsByProject(const QString& projectId) const
{
    QVector<ClipRecord> list;
    QSqlQuery query(m_db);
    query.prepare(
        "SELECT id, project_id, start_sec, end_sec, score, reason, keywords, title, "
        "thumbnail_path, export_path, status, user_rating, user_note, created_at "
        "FROM clips WHERE project_id=? ORDER BY start_sec ASC");
    query.addBindValue(projectId);

    if (!query.exec())
        return list;

    while (query.next()) {
        ClipRecord r;
        r.id = query.value(0).toString();
        r.projectId = query.value(1).toString();
        r.startSec = query.value(2).toDouble();
        r.endSec = query.value(3).toDouble();
        r.score = query.value(4).toDouble();
        r.reason = query.value(5).toString();
        r.keywords = query.value(6).toString();
        r.title = query.value(7).toString();
        r.thumbnailPath = query.value(8).toString();
        r.exportPath = query.value(9).toString();
        r.status = query.value(10).toString();
        r.userRating = query.value(11).toInt();
        r.userNote = query.value(12).toString();
        r.createdAt = QDateTime::fromString(query.value(13).toString(), Qt::ISODate);
        list.append(r);
    }
    return list;
}

QVector<ClipRecord> LscDatabase::approvedClips(const QString& projectId) const
{
    QVector<ClipRecord> list;
    QSqlQuery query(m_db);
    query.prepare(
        "SELECT id, project_id, start_sec, end_sec, score, reason, keywords, title, "
        "thumbnail_path, export_path, status, user_rating, user_note, created_at "
        "FROM clips WHERE project_id=? AND status='approved' ORDER BY score DESC");
    query.addBindValue(projectId);

    if (!query.exec())
        return list;

    while (query.next()) {
        ClipRecord r;
        r.id = query.value(0).toString();
        r.projectId = query.value(1).toString();
        r.startSec = query.value(2).toDouble();
        r.endSec = query.value(3).toDouble();
        r.score = query.value(4).toDouble();
        r.reason = query.value(5).toString();
        r.keywords = query.value(6).toString();
        r.title = query.value(7).toString();
        r.thumbnailPath = query.value(8).toString();
        r.exportPath = query.value(9).toString();
        r.status = query.value(10).toString();
        r.userRating = query.value(11).toInt();
        r.userNote = query.value(12).toString();
        r.createdAt = QDateTime::fromString(query.value(13).toString(), Qt::ISODate);
        list.append(r);
    }
    return list;
}

// ===== Task history =====

bool LscDatabase::insertTask(const TaskRecord& t)
{
    QSqlQuery query(m_db);
    query.prepare(
        "INSERT INTO task_history "
        "(id, type, status, title, error, progress, created_at, started_at, finished_at, metadata) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)");
    query.addBindValue(t.id);
    query.addBindValue(t.type);
    query.addBindValue(t.status);
    query.addBindValue(t.title);
    query.addBindValue(t.error);
    query.addBindValue(t.progress);
    query.addBindValue(t.createdAt.toString(Qt::ISODate));
    query.addBindValue(t.startedAt.toString(Qt::ISODate));
    query.addBindValue(t.finishedAt.toString(Qt::ISODate));
    query.addBindValue(variantMapToJson(t.metadata));

    if (!query.exec()) {
        qCritical() << "[LSC][Database][ERROR] insertTask failed:" << query.lastError().text();
        return false;
    }
    return true;
}

bool LscDatabase::updateTask(const TaskRecord& t)
{
    QSqlQuery query(m_db);
    query.prepare(
        "UPDATE task_history SET type=?, status=?, title=?, error=?, progress=?, "
        "started_at=?, finished_at=?, metadata=? WHERE id=?");
    query.addBindValue(t.type);
    query.addBindValue(t.status);
    query.addBindValue(t.title);
    query.addBindValue(t.error);
    query.addBindValue(t.progress);
    query.addBindValue(t.startedAt.toString(Qt::ISODate));
    query.addBindValue(t.finishedAt.toString(Qt::ISODate));
    query.addBindValue(variantMapToJson(t.metadata));
    query.addBindValue(t.id);

    if (!query.exec()) {
        qCritical() << "[LSC][Database][ERROR] updateTask failed:" << query.lastError().text();
        return false;
    }
    return true;
}

QVector<TaskRecord> LscDatabase::recentTasks(int count) const
{
    QVector<TaskRecord> list;
    QSqlQuery query(m_db);
    query.prepare(
        "SELECT id, type, status, title, error, progress, "
        "created_at, started_at, finished_at, metadata "
        "FROM task_history ORDER BY created_at DESC LIMIT ?");
    query.addBindValue(count);

    if (!query.exec())
        return list;

    while (query.next()) {
        TaskRecord t;
        t.id = query.value(0).toString();
        t.type = query.value(1).toString();
        t.status = query.value(2).toString();
        t.title = query.value(3).toString();
        t.error = query.value(4).toString();
        t.progress = query.value(5).toInt();
        t.createdAt = QDateTime::fromString(query.value(6).toString(), Qt::ISODate);
        t.startedAt = QDateTime::fromString(query.value(7).toString(), Qt::ISODate);
        t.finishedAt = QDateTime::fromString(query.value(8).toString(), Qt::ISODate);
        t.metadata = jsonToVariantMap(query.value(9).toByteArray());
        list.append(t);
    }
    return list;
}

// ===== Statistics =====

int LscDatabase::totalProjects() const
{
    QSqlQuery query(m_db);
    if (query.exec("SELECT COUNT(*) FROM projects") && query.next())
        return query.value(0).toInt();
    return 0;
}

int LscDatabase::totalClips() const
{
    QSqlQuery query(m_db);
    if (query.exec("SELECT COUNT(*) FROM clips") && query.next())
        return query.value(0).toInt();
    return 0;
}

int LscDatabase::totalExportedClips() const
{
    QSqlQuery query(m_db);
    if (query.exec("SELECT COUNT(*) FROM clips WHERE status='exported'") && query.next())
        return query.value(0).toInt();
    return 0;
}

qint64 LscDatabase::totalStorageUsed() const
{
    QSqlQuery query(m_db);
    if (query.exec("SELECT COALESCE(SUM(file_size_bytes), 0) FROM projects") && query.next())
        return query.value(0).toLongLong();
    return 0;
}

} // namespace lsc
