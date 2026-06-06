#ifndef LSCLOG_H
#define LSCLOG_H

/**
 * @file LscLog.h
 * @brief 统一日志系统
 *
 * 设计原则：
 * 1. 所有模块统一使用 LSC_LOG_* 宏，格式一致
 * 2. 日志格式：[LSC][模块名][级别] 消息
 * 3. Release 模式下可通过定义 LSC_NO_DEBUG_LOG 关闭 debug 日志
 * 4. 基于 qDebug/qWarning/qCritical，兼容 Qt 日志系统
 */

#include <QDebug>

// 日志级别宏
#ifdef LSC_NO_DEBUG_LOG
#define LSC_DEBUG(module) QDebug(QtDebugMsg).noquote().nospace() << "[LSC][" << module << "][DEBUG] "
#else
#define LSC_DEBUG(module) if (false) QDebug(QtDebugMsg).noquote().nospace()  // 编译期消除
#endif

#define LSC_INFO(module)    QDebug(QtDebugMsg).noquote().nospace()    << "[LSC][" << module << "][INFO] "
#define LSC_WARNING(module) QDebug(QtWarningMsg).noquote().nospace() << "[LSC][" << module << "][WARN] "
#define LSC_ERROR(module)   QDebug(QtCriticalMsg).noquote().nospace() << "[LSC][" << module << "][ERROR] "

// 便捷宏 — 自动使用模块名
#define LSC_LOG_DEBUG   LSC_DEBUG(MODULE_NAME)
#define LSC_LOG_INFO    LSC_INFO(MODULE_NAME)
#define LSC_LOG_WARNING LSC_WARNING(MODULE_NAME)
#define LSC_LOG_ERROR   LSC_ERROR(MODULE_NAME)

#endif // LSCLOG_H
