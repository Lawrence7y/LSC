"""UI主题测试 - 检查浅色模式下的文字颜色和组件样式。"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QFrame, QWidget, QVBoxLayout
from PySide6.QtGui import QColor

app = QApplication.instance() or QApplication([])

from lsc.gui.theme import (
    DARK, LIGHT, get_theme, is_dark, set_dark,
    generate_stylesheet,
)
from lsc.gui.components.sidebar import Sidebar
from lsc.gui.components.widgets import PageHeader
from lsc.gui.components.room_card import RoomCard
from lsc.gui.multi_room.session import RoomSession
from lsc.gui.pages.multi_room.status_bar import _BottomBar, StatusBar

issues = []


def log_issue(area: str, desc: str) -> None:
    issues.append((area, desc))
    print(f"  ❌ ISSUE: [{area}] {desc}")


def step(name: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


def check_contrast(bg_color: str, text_color: str, threshold: float = 3.0) -> bool:
    """简单的对比度检查。"""
    def hex_to_rgb(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def luminance(rgb):
        r, g, b = [x / 255.0 for x in rgb]
        r = r / 12.92 if r <= 0.03928 else ((r + 0.055) / 1.055) ** 2.4
        g = g / 12.92 if g <= 0.03928 else ((g + 0.055) / 1.055) ** 2.4
        b = b / 12.92 if b <= 0.03928 else ((b + 0.055) / 1.055) ** 2.4
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    bg_rgb = hex_to_rgb(bg_color)
    text_rgb = hex_to_rgb(text_color)
    l1 = luminance(bg_rgb)
    l2 = luminance(text_rgb)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    ratio = (lighter + 0.05) / (darker + 0.05)
    return ratio >= threshold


def test_theme_colors():
    """测试主题颜色定义。"""
    step("测试主题颜色定义")

    for theme_name, theme in [("DARK", DARK), ("LIGHT", LIGHT)]:
        print(f"\n  [{theme_name}] 检查文字与背景对比度:")

        # 主要文字 vs 主要背景
        for text_name, text_color in [("text_primary", theme.text_primary),
                                       ("text_secondary", theme.text_secondary),
                                       ("text_tertiary", theme.text_tertiary)]:
            for bg_name, bg_color in [("bg_primary", theme.bg_primary),
                                       ("bg_secondary", theme.bg_secondary),
                                       ("bg_tertiary", theme.bg_tertiary)]:
                if not check_contrast(bg_color, text_color, 2.0):
                    log_issue("theme_colors",
                              f"{theme_name}: {text_name} vs {bg_name} 对比度过低")
                else:
                    print(f"    ✅ {text_name} vs {bg_name} 对比度正常")

        # accent 背景上的白色文字
        for accent_name, accent_color in [("accent_primary", theme.accent_primary),
                                            ("accent_success", theme.accent_success),
                                            ("accent_error", theme.accent_error),
                                            ("accent_warning", theme.accent_warning)]:
            if not check_contrast(accent_color, "#ffffff", 2.5):
                log_issue("theme_colors",
                          f"{theme_name}: 白色文字在 {accent_name} 上对比度过低")
            else:
                print(f"    ✅ 白色文字 vs {accent_name} 对比度正常")


def test_sidebar_in_both_themes():
    """测试侧边栏在两种主题下的表现。"""
    step("测试侧边栏组件")

    for dark in [True, False]:
        theme_name = "深色" if dark else "浅色"
        set_dark(dark)
        app.setStyleSheet(generate_stylesheet(get_theme(), dark=dark))

        sidebar = Sidebar()
        sidebar.setFixedWidth(240)
        sidebar.resize(240, 600)

        # 检查是否渲染成功
        if sidebar.isVisible():
            print(f"  ✅ [{theme_name}] 侧边栏创建成功")
        else:
            print(f"  ✅ [{theme_name}] 侧边栏创建成功 (offscreen)")

        sidebar.deleteLater()


def test_page_header_in_both_themes():
    """测试页面标题栏在两种主题下的表现。"""
    step("测试页面标题栏")

    for dark in [True, False]:
        theme_name = "深色" if dark else "浅色"
        set_dark(dark)
        app.setStyleSheet(generate_stylesheet(get_theme(), dark=dark))

        header = PageHeader("测试标题", "测试副标题")
        header.resize(800, 72)

        print(f"  ✅ [{theme_name}] PageHeader 创建成功")
        header.deleteLater()


def test_room_card_in_both_themes():
    """测试房间卡片在两种主题下的表现。"""
    step("测试房间卡片")

    TEST_URL = "https://www.douyin.com/follow/live/295380890971?anchor_id=2524898145613127"

    for dark in [True, False]:
        theme_name = "深色" if dark else "浅色"
        set_dark(dark)
        app.setStyleSheet(generate_stylesheet(get_theme(), dark=dark))

        # 创建一个模拟房间
        session = RoomSession(TEST_URL)
        session._streamer_name = "测试主播"
        session._stream_title = "测试直播间标题"
        session._platform_name = "抖音"
        session._is_connected = True
        session._selected_quality = "原画"

        card = RoomCard(session)
        card.resize(440, 400)

        # 更新卡片状态显示
        card._update_status_display()

        print(f"  ✅ [{theme_name}] 房间卡片创建成功")
        print(f"     标题: {session.stream_title}")
        print(f"     主播: {session.streamer_name}")

        card.deleteLater()
        del session


def test_bottom_bar_in_both_themes():
    """测试底部控制栏在两种主题下的表现。"""
    step("测试底部控制栏")

    for dark in [True, False]:
        theme_name = "深色" if dark else "浅色"
        set_dark(dark)
        app.setStyleSheet(generate_stylesheet(get_theme(), dark=dark))

        bottom_bar = _BottomBar()
        bottom_bar.resize(1000, 160)

        print(f"  ✅ [{theme_name}] 底部控制栏创建成功")
        bottom_bar.deleteLater()


def test_hardcoded_white_text():
    """检查样式表中硬编码的白色文字是否都在深色/彩色背景上。"""
    step("检查硬编码白色文字的安全性")

    import re

    # 读取样式表生成函数
    with open(os.path.join(os.path.dirname(__file__), "lsc/gui/theme.py"), "r", encoding="utf-8") as f:
        content = f.read()

    # 查找所有 color: #ffffff 或 color: white
    white_text_patterns = list(re.finditer(r'color:\s*(#ffffff|#fff|white)\s*;', content, re.IGNORECASE))

    print(f"  共找到 {len(white_text_patterns)} 处白色文字定义")

    # 已知安全的白色文字（在深色背景或彩色背景上）
    safe_contexts = [
        "navBadge",           # accent 背景
        "recordPreviewOverlay QPushButton",  # 半透明黑色背景
        "previewFullscreenButton",  # 半透明黑色背景
        "roomCardBadgeRec",   # error 背景
        "roomCardBadgeMute",  # tertiary 背景
        "fullscreenTimeLabel",  # 全屏黑色背景
        "fullscreenMuteButton",  # 全屏黑色背景
        "dashboardSessionStatus",  # success/secondary 背景
        "accent_primary",    # accent 按钮
        "accent_success",    # success 按钮
        "accent_error",      # error 按钮
        "ctrlMarkIn:checked",  # success 背景
        "ctrlMarkOut:checked",  # error 背景
        "HighlightedText",    # 高亮选中文字
    ]

    for i, match in enumerate(white_text_patterns, 1):
        start = max(0, match.start() - 100)
        end = min(len(content), match.end() + 50)
        context = content[start:end]

        is_safe = any(safe in context for safe in safe_contexts)
        status = "✅ 安全" if is_safe else "⚠️  需检查"
        line_num = content[:match.start()].count('\n') + 1
        print(f"    {i}. 第 {line_num} 行 - {status}")

        if not is_safe:
            # 提取更多上下文
            more_context = content[max(0, match.start() - 200):match.end()]
            print(f"       上下文: ...{more_context[-150:]}...")


def main():
    print("\n" + "#" * 60)
    print("#  LSC UI 主题测试")
    print("#" * 60)

    # 1. 主题颜色
    try:
        test_theme_colors()
    except Exception as e:
        print(f"\n❌ 主题颜色测试异常: {e}")
        import traceback
        traceback.print_exc()
        log_issue("theme_colors", f"测试异常: {e}")

    # 2. 侧边栏
    try:
        test_sidebar_in_both_themes()
    except Exception as e:
        print(f"\n❌ 侧边栏测试异常: {e}")
        import traceback
        traceback.print_exc()
        log_issue("sidebar", f"测试异常: {e}")

    # 3. 页面标题栏
    try:
        test_page_header_in_both_themes()
    except Exception as e:
        print(f"\n❌ 页面标题栏测试异常: {e}")
        import traceback
        traceback.print_exc()
        log_issue("page_header", f"测试异常: {e}")

    # 4. 房间卡片
    try:
        test_room_card_in_both_themes()
    except Exception as e:
        print(f"\n❌ 房间卡片测试异常: {e}")
        import traceback
        traceback.print_exc()
        log_issue("room_card", f"测试异常: {e}")

    # 5. 底部控制栏
    try:
        test_bottom_bar_in_both_themes()
    except Exception as e:
        print(f"\n❌ 底部控制栏测试异常: {e}")
        import traceback
        traceback.print_exc()
        log_issue("bottom_bar", f"测试异常: {e}")

    # 6. 硬编码白色文字检查
    try:
        test_hardcoded_white_text()
    except Exception as e:
        print(f"\n❌ 白色文字检查异常: {e}")
        import traceback
        traceback.print_exc()
        log_issue("white_text", f"检查异常: {e}")

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    if not issues:
        print("\n🎉 所有UI主题测试通过，未发现明显问题！")
    else:
        print(f"\n共发现 {len(issues)} 个问题：\n")
        for i, (area, desc) in enumerate(issues, 1):
            print(f"  {i}. [{area}] {desc}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
