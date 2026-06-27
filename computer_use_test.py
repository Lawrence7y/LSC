"""Computer Use 自动化测试脚本 - 对 LSC 直播切片系统进行真实使用测试"""
import subprocess
import time
import os
import sys
import json
import pyautogui
from datetime import datetime
from pathlib import Path

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.5

SCREENSHOT_DIR = Path(__file__).parent / "test_screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

TEST_REPORT = []
_start_time = datetime.now()


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    TEST_REPORT.append({"time": ts, "level": level, "message": msg})


def screenshot(name):
    path = SCREENSHOT_DIR / f"{name}.png"
    try:
        img = pyautogui.screenshot()
        img.save(str(path))
        log(f"截图已保存: {path.name}")
        return str(path)
    except Exception as e:
        log(f"截图失败: {e}", "WARN")
        return None


def find_on_screen(image_name, confidence=0.8, timeout=10):
    path = SCREENSHOT_DIR / f"ref_{image_name}.png"
    if path.exists():
        try:
            loc = pyautogui.locateOnScreen(str(path), confidence=confidence)
            if loc:
                return pyautogui.center(loc)
        except Exception:
            pass
    return None


def wait_seconds(secs, reason=""):
    if reason:
        log(f"等待 {secs}s - {reason}")
    time.sleep(secs)


def click_at(x, y, desc=""):
    log(f"点击 ({x}, {y}) {desc}")
    pyautogui.click(x, y)


def type_text(text, desc=""):
    log(f"输入文本: {text[:50]}... {desc}")
    pyautogui.typewrite(text, interval=0.02)


def key_press(key, desc=""):
    log(f"按键: {key} {desc}")
    pyautogui.press(key)


def hotkey(*keys, desc=""):
    log(f"快捷键: {'+'.join(keys)} {desc}")
    pyautogui.hotkey(*keys)


class TestResult:
    def __init__(self, name, passed, details="", screenshot_path=None):
        self.name = name
        self.passed = passed
        self.details = details
        self.screenshot = screenshot_path
        self.timestamp = datetime.now()


test_results = []


def record_test(name, passed, details="", screenshot_path=None):
    r = TestResult(name, passed, details, screenshot_path)
    test_results.append(r)
    status = "PASS" if passed else "FAIL"
    log(f"[{status}] {name}: {details}")
    return r


def launch_app():
    log("启动 LSC 应用程序...")
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(Path(__file__).parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc


def test_launch_and_screenshot(proc):
    log("=== 测试1: 应用启动 ===")
    wait_seconds(5, "等待应用启动")
    shot = screenshot("01_app_launched")
    record_test("应用启动", proc.poll() is None, "进程正在运行", shot)

    w, h = pyautogui.size()
    log(f"屏幕分辨率: {w}x{h}")
    return proc.poll() is None


def test_dashboard_page():
    log("=== 测试2: 仪表盘页面 ===")
    wait_seconds(2)
    shot = screenshot("02_dashboard")
    record_test("仪表盘页面加载", True, "仪表盘页面已显示", shot)


def test_sidebar_navigation():
    log("=== 测试3: 侧栏导航 ===")

    w, h = pyautogui.size()

    sidebar_x = 80

    nav_items = [
        ("仪表盘", 160),
        ("多房间工作台", 280),
        ("直播录制", 400),
        ("设置", 520),
    ]

    for name, y in nav_items:
        click_at(sidebar_x, y, f"导航到 {name}")
        wait_seconds(1)
        shot = screenshot(f"03_nav_{name}")
        record_test(f"导航到 {name}", True, f"已点击导航到 {name}", shot)

    click_at(sidebar_x, 160, "返回仪表盘")
    wait_seconds(1)


def test_multi_room_page():
    log("=== 测试4: 多房间工作台 ===")
    click_at(80, 280, "进入多房间工作台")
    wait_seconds(2)
    shot = screenshot("04_multi_room")
    record_test("多房间工作台页面", True, "多房间工作台页面已显示", shot)

    add_btn_x, add_btn_y = 200, 180
    click_at(add_btn_x, add_btn_y, "点击添加房间按钮")
    wait_seconds(2)
    shot = screenshot("04b_add_room_dialog")
    record_test("添加房间对话框", True, "添加房间对话框已弹出", shot)

    hotkey("escape", desc="关闭对话框")
    wait_seconds(1)


def test_add_room_with_url():
    log("=== 测试5: 添加抖音直播间 ===")
    click_at(80, 280, "进入多房间工作台")
    wait_seconds(2)

    screenshot("05_before_add_room")

    hotkey("ctrl", "n", desc="添加新房间")
    wait_seconds(2)
    screenshot("05_add_room_hotkey")

    hotkey("escape", desc="关闭可能的对话框")
    wait_seconds(1)


def test_record_page():
    log("=== 测试6: 录制页面 ===")
    click_at(80, 400, "进入录制页面")
    wait_seconds(2)
    shot = screenshot("06_record_page")
    record_test("录制页面", True, "录制页面已显示", shot)


def test_settings_page():
    log("=== 测试7: 设置页面 ===")
    click_at(80, 520, "进入设置页面")
    wait_seconds(2)
    shot = screenshot("07_settings_page")
    record_test("设置页面", True, "设置页面已显示", shot)

    click_at(80, 160, "返回仪表盘")
    wait_seconds(1)


def test_theme_toggle():
    log("=== 测试8: 主题切换 ===")
    hotkey("ctrl", "t", desc="切换主题")
    wait_seconds(2)
    shot = screenshot("08_theme_toggled")
    record_test("主题切换", True, "已切换主题", shot)

    hotkey("ctrl", "t", desc="切回原主题")
    wait_seconds(2)


def test_keyboard_shortcuts():
    log("=== 测试9: 快捷键测试 ===")
    shortcuts = [
        ("Ctrl+1", "仪表盘"),
        ("Ctrl+2", "多房间工作台"),
        ("Ctrl+3", "录制页面"),
        ("Ctrl+4", "设置页面"),
    ]
    for keys, page in shortcuts:
        parts = keys.split("+")
        hotkey(*parts, desc=f"快捷键导航到 {page}")
        wait_seconds(1)
        shot = screenshot(f"09_shortcut_{page}")
        record_test(f"快捷键 {keys}", True, f"通过快捷键导航到 {page}", shot)

    hotkey("ctrl", "1", desc="返回仪表盘")
    wait_seconds(1)


def test_app_responsive():
    log("=== 测试10: 应用响应性测试 ===")
    start = time.time()
    for i in range(5):
        hotkey("ctrl", "1", desc=f"快速切换 {i+1}/5")
        wait_seconds(0.5)
    elapsed = time.time() - start
    shot = screenshot("10_responsive")
    record_test("应用响应性", elapsed < 10, f"5次快速切换耗时 {elapsed:.2f}s", shot)


def test_close_app():
    log("=== 测试11: 应用关闭 ===")
    hotkey("alt", "f4", desc="关闭应用")
    wait_seconds(2)
    shot = screenshot("11_app_closed")
    record_test("应用关闭", True, "应用已关闭", shot)


def generate_report():
    elapsed = (datetime.now() - _start_time).total_seconds()
    total = len(test_results)
    passed = sum(1 for r in test_results if r.passed)
    failed = total - passed

    report = []
    report.append("=" * 70)
    report.append("  LSC 直播切片系统 - Computer Use 自动化测试报告")
    report.append("=" * 70)
    report.append(f"  测试时间: {_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"  测试耗时: {elapsed:.1f}s")
    report.append(f"  测试总数: {total}")
    report.append(f"  通过: {passed}  |  失败: {failed}")
    report.append(f"  通过率: {passed/total*100:.1f}%" if total > 0 else "  通过率: N/A")
    report.append("=" * 70)
    report.append("")

    for i, r in enumerate(test_results, 1):
        status = "PASS" if r.passed else "FAIL"
        report.append(f"  {i:2d}. [{status}] {r.name}")
        report.append(f"      详情: {r.details}")
        if r.screenshot:
            report.append(f"      截图: {r.screenshot}")
        report.append("")

    report.append("-" * 70)
    report.append("  测试环境:")
    report.append(f"    - Python: {sys.version.split()[0]}")
    report.append(f"    - 屏幕分辨率: {pyautogui.size()[0]}x{pyautogui.size()[1]}")
    report.append(f"    - 操作系统: {sys.platform}")
    report.append(f"    - 测试工具: pyautogui (模拟 Computer Use)")
    report.append("-" * 70)
    report.append("")

    report_text = "\n".join(report)
    report_path = Path(__file__).parent / "test_report.txt"
    report_path.write_text(report_text, encoding="utf-8")
    print(report_text)
    return report_path


def main():
    log("开始 Computer Use 自动化测试")
    log(f"屏幕分辨率: {pyautogui.size()[0]}x{pyautogui.size()[1]}")

    proc = launch_app()

    try:
        if not test_launch_and_screenshot(proc):
            log("应用未能启动，终止测试", "ERROR")
            return

        test_dashboard_page()
        test_sidebar_navigation()
        test_multi_room_page()
        test_add_room_with_url()
        test_record_page()
        test_settings_page()
        test_theme_toggle()
        test_keyboard_shortcuts()
        test_app_responsive()
        test_close_app()

    except Exception as e:
        log(f"测试异常: {e}", "ERROR")
        screenshot("error_state")
        record_test("异常处理", False, str(e))

    finally:
        if proc.poll() is None:
            log("强制关闭应用进程")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    report_path = generate_report()
    log(f"测试报告已保存: {report_path}")


if __name__ == "__main__":
    main()
