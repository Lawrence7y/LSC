#ifndef ANALYSISPROFILE_H
#define ANALYSISPROFILE_H

#include <QString>

struct AnalysisProfile
{
    QString id;
    QString displayName;
    bool enableRealtimePreview = true;
    bool enableRealtimeHighlight = true;
    bool enableRoundSegmentation = false;
    bool enableCommentarySegmentation = false;
    bool enableDanceSegmentation = false;
    QString gameKey;

    static AnalysisProfile generic()
    {
        return {QStringLiteral("generic"),
                QStringLiteral("通用直播"),
                true,
                true,
                false,
                false,
                false,
                QString()};
    }

    static AnalysisProfile dance()
    {
        return {QStringLiteral("dance"),
                QStringLiteral("舞蹈直播"),
                true,
                true,
                false,
                false,
                true,
                QString()};
    }

    static AnalysisProfile valorant()
    {
        return {QStringLiteral("valorant"),
                QStringLiteral("无畏契约"),
                true,   // enableRealtimePreview
                true,   // enableRealtimeHighlight — controlled by RealtimeStrategy now
                true,   // enableRoundSegmentation
                true,   // enableCommentarySegmentation
                false,  // enableDanceSegmentation
                QStringLiteral("valorant")};
        // Note: valorant() is the user-facing unified entry.
        // Internal routing (streamer_pov / commentary_watchparty / uncertain dual-run)
        // is handled by MaterialClassifier → HighlightEngine at final analysis time.
    }

    static AnalysisProfile commentary()
    {
        return {QStringLiteral("commentary"),
                QStringLiteral("解说切片"),
                true,
                true,
                false,
                true,
                false,
                QString()};
    }
};

#endif // ANALYSISPROFILE_H
