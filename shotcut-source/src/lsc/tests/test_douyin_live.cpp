#include <QCoreApplication>
#include <QTimer>
#include <QEventLoop>
#include <QDebug>
#include <iostream>

#include "livestream/PlatformParser.h"

/**
 * 测试抖音直播地址解析
 * 用法: test_douyin_live <url>
 */

int main(int argc, char* argv[])
{
    QCoreApplication app(argc, argv);

    QString testUrl = "https://www.douyin.com/follow/live/53682367755?anchor_id=1566126704427592";
    if (argc > 1) {
        testUrl = argv[1];
    }

    std::cout << "=== 抖音直播地址解析测试 ===" << std::endl;
    std::cout << "测试URL: " << testUrl.toStdString() << std::endl << std::endl;

    PlatformParser parser;
    QEventLoop loop;
    bool success = false;

    // 连接信号
    QObject::connect(&parser, &PlatformParser::parseComplete, [&](const PlatformInfo& info) {
        std::cout << "\n=== 解析成功 ===" << std::endl;
        std::cout << "平台: " << info.platform.toStdString() << std::endl;
        std::cout << "标题: " << info.title.toStdString() << std::endl;
        std::cout << "主播: " << info.streamerName.toStdString() << std::endl;
        std::cout << "房间ID: " << info.roomId.toStdString() << std::endl;
        std::cout << "首选画质: " << info.preferredQuality.toStdString() << std::endl;
        std::cout << "流地址有效性: " << (info.isValid ? "有效" : "无效") << std::endl;

        if (!info.streamUrl.isEmpty()) {
            // 只显示前100个字符，保护隐私
            QString shortUrl = info.streamUrl.left(100) + "...";
            std::cout << "流地址(截断): " << shortUrl.toStdString() << std::endl;
        }

        if (!info.availableQualities.isEmpty()) {
            std::cout << "可用画质: ";
            for (const QString& q : info.availableQualities) {
                std::cout << q.toStdString() << " ";
            }
            std::cout << std::endl;
        }

        success = true;
        loop.quit();
    });

    QObject::connect(&parser, &PlatformParser::parseError, [&](const QString& error) {
        std::cout << "\n=== 解析失败 ===" << std::endl;
        std::cout << "错误: " << error.toStdString() << std::endl;
        success = false;
        loop.quit();
    });

    // 设置超时
    QTimer::singleShot(30000, [&]() {
        std::cout << "\n=== 超时 ===" << std::endl;
        std::cout << "解析超时（30秒）" << std::endl;
        success = false;
        loop.quit();
    });

    // 开始解析
    std::cout << "正在解析..." << std::endl;
    parser.parseUrl(testUrl);

    // 等待完成
    loop.exec();

    std::cout << "\n=== 测试结果: " << (success ? "成功" : "失败") << " ===" << std::endl;

    return success ? 0 : 1;
}
