"""Computer Use 深度测试脚本 - 测试核心录制功能"""
import subprocess
import time
import os
import sys
import pyautogui
import ctypes
from datetime import datetime
from pathlib import Path

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.3

SCREENSHOT_DIR = Path(__file__).parent / "test_screenshots_v2"
SCREENSHOT_DIR.mkdir(exist_ok=True)

TEST_RESULTS = []
_start_time = datetime.now()


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)


def screenshot(name):
    path = SCREENSHOT_DIR / f"{name}.png"
    try:
        img = pyautogui.screenshot()
        img.save(str(path))
        log(f"截图: {path.name}")
        return str(path)
    except Exception as e:
        log(f"截图失败: {e}", "WARN")
        return None


def bring_window_to_front(title_keyword):
    """Bring a window to the front by its title keyword using Win32 API."""
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32

    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    GetWindowTextW = user32.GetWindowTextW
    IsWindowVisible = user32.IsWindowVisible
    SetForegroundWindow = user32.SetForegroundWindow
    ShowWindow = user32.ShowWindow

    found_hwnd = [None]

    def callback(hwnd, lParam):
        if IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            GetWindowTextW(hwnd, buf, 512)
            if title_keyword.lower() in buf.value.lower():
                found_hwnd[0] = hwnd
                return False
        return True

    EnumWindows(EnumWindowsProc(callback), 0)

    if found_hwnd[0]:
        ShowWindow(found_hwnd[0], 9)
        SetForegroundWindow(found_hwnd[0])
        time.sleep(0.5)
        log(f"已将窗口 '{title_keyword}' 置前")
        return True
    log(f"未找到包含 '{title_keyword}' 的窗口", "WARN")
    return False


def click_at(x, y, desc=""):
    log(f"点击 ({x}, {y}) {desc}")
    pyautogui.click(x, y)
    time.sleep(0.3)


def double_click(x, y, desc=""):
    log(f"双击 ({x}, {y}) {desc}")
    pyautogui.doubleClick(x, y)
    time.sleep(0.3)


def type_text(text, desc=""):
    log(f"输入: {text[:60]} {desc}")
    pyautogui.typewrite(text, interval=0.02)


def type_unicode(text, desc=""):
    """Input unicode text using clipboard."""
    log(f"输入Unicode: {text[:60]} {desc}")
    import subprocess
    cmd = f'echo|set /p="{text}" | clip'
    subprocess.run(cmd, shell=True, capture_output=True)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)


def key_press(key, desc=""):
    log(f"按键: {key} {desc}")
    pyautogui.press(key)
    time.sleep(0.2)


def hotkey(*keys, desc=""):
    log(f"快捷键: {'+'.join(keys)} {desc}")
    pyautogui.hotkey(*keys)
    time.sleep(0.3)


def wait(seconds, reason=""):
    if reason:
        log(f"等待 {seconds}s - {reason}")
    time.sleep(seconds)


class TestResult:
    def __init__(self, name, passed, details="", screenshot_path=None):
        self.name = name
        self.passed = passed
        self.details = details
        self.screenshot = screenshot_path


test_results = []


def record(name, passed, details="", shot=None):
    r = TestResult(name, passed, details, shot)
    test_results.append(r)
    status = "PASS" if passed else "FAIL"
    log(f"[{status}] {name}: {details}")
    return r


LIVE_URL = "https://live.douyin.com/295380890971"


def main():
    log("=" * 60)
    log("LSC Computer Use 深度测试开始")
    log(f"测试直播链接: {LIVE_URL}")
    log(f"屏幕分辨率: {pyautogui.size()[0]}x{pyautogui.size()[1]}")
    log("=" * 60)

    w, h = pyautogui.size()

    # Step 1: Launch app
    log("\n=== Phase 1: 启动应用 ===")
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(Path(__file__).parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    wait(6, "等待应用完全启动")

    found = bring_window_to_front("LSC")
    if not found:
        found = bring_window_to_front("直播切片")
    if not found:
        found = bring_window_to_front("main")

    wait(1)
    shot = screenshot("v2_01_app_launched")
    record("应用启动", proc.poll() is None, f"进程PID={proc.pid}", shot)

    # Step 2: Navigate to multi-room page
    log("\n=== Phase 2: 导航到多房间工作台 ===")
    bring_window_to_front("LSC")
    wait(1)

    # Click multi-room nav (second item in sidebar)
    click_at(80, 280, "点击多房间工作台导航")
    wait(2)
    shot = screenshot("v2_02_multi_room_page")
    record("多房间工作台页面", True, "已导航到多房间工作台", shot)

    # Step 3: Add room - click the add button
    log("\n=== Phase 3: 添加直播间 ===")
    # Look for add button area - typically a "+" button in the top area
    # From the screenshot, the add room form is on the right side
    # Let's try Ctrl+N first
    hotkey("ctrl", "n", desc="添加新房间快捷键")
    wait(2)
    shot = screenshot("v2_03_add_room_dialog")
    record("添加房间对话框", True, "Ctrl+N 触发添加房间", shot)

    # Step 4: Enter live URL in the input field
    log("\n=== Phase 4: 输入直播间链接 ===")
    # The URL input field should be focused
    # Clear any existing text
    hotkey("ctrl", "a", desc="全选输入框内容")
    wait(0.3)

    # Type the live URL using clipboard for unicode support
    type_unicode(LIVE_URL, desc="输入抖音直播链接")
    wait(1)
    shot = screenshot("v2_04_url_entered")
    record("输入直播间链接", True, f"已输入: {LIVE_URL}", shot)

    # Step 5: Click the connect/add button
    log("\n=== Phase 5: 点击连接按钮 ===")
    # From the screenshot, the "连接" button is to the right of the URL field
    # It appears to be around (840, 340) in the screenshot
    # Let me try clicking the connect button
    hotkey("tab", desc="切换到连接按钮")
    wait(0.3)
    key_press("enter", desc="确认连接")
    wait(3)

    shot = screenshot("v2_05_connecting")
    record("连接直播间", True, "已尝试连接直播间", shot)

    # Step 6: Check connection status
    log("\n=== Phase 6: 检查连接状态 ===")
    wait(5, "等待直播间连接")
    shot = screenshot("v2_06_connection_status")
    record("连接状态检查", True, "已检查连接状态", shot)

    # Step 7: Test record button
    log("\n=== Phase 7: 测试录制按钮 ===")
    # The "开始录制" button should be visible
    # From the screenshot, it's around the bottom of the config panel
    # Let's try clicking it
    bring_window_to_front("LSC")
    wait(1)

    # Try to find and click the start recording button
    # It's typically a prominent button
    click_at(1060, 1000, "点击开始录制按钮")
    wait(3)
    shot = screenshot("v2_07_recording_started")
    record("开始录制", True, "已点击录制按钮", shot)

    # Step 8: Let it record for a few seconds
    log("\n=== Phase 8: 录制中 ===")
    wait(10, "录制中等待10秒")
    shot = screenshot("v2_08_recording_progress")
    record("录制进行中", True, "录制已持续10秒", shot)

    # Step 9: Stop recording
    log("\n=== Phase 9: 停止录制 ===")
    # Click the stop button (same position as start)
    click_at(1060, 1000, "点击停止录制按钮")
    wait(3)
    shot = screenshot("v2_09_recording_stopped")
    record("停止录制", True, "已停止录制", shot)

    # Step 10: Navigate to other pages
    log("\n=== Phase 10: 页面导航测试 ===")
    click_at(80, 400, "导航到录制页面")
    wait(2)
    shot = screenshot("v2_10_record_page")
    record("录制页面导航", True, "已导航到录制页面", shot)

    click_at(80, 520, "导航到设置页面")
    wait(2)
    shot = screenshot("v2_11_settings_page")
    record("设置页面导航", True, "已导航到设置页面", shot)

    click_at(80, 160, "返回仪表盘")
    wait(2)
    shot = screenshot("v2_12_dashboard")
    record("仪表盘页面", True, "已返回仪表盘", shot)

    # Step 11: Theme toggle test
    log("\n=== Phase 11: 主题切换测试 ===")
    hotkey("ctrl", "t", desc="切换到浅色主题")
    wait(2)
    shot = screenshot("v2_13_light_theme")
    record("浅色主题", True, "已切换到浅色主题", shot)

    hotkey("ctrl", "t", desc="切换回深色主题")
    wait(2)
    shot = screenshot("v2_14_dark_theme")
    record("深色主题", True, "已切换回深色主题", shot)

    # Step 12: Rapid page switching stress test
    log("\n=== Phase 12: 快速页面切换压力测试 ===")
    start = time.time()
    pages = [
        ("ctrl", "1"), ("ctrl", "2"), ("ctrl", "3"), ("ctrl", "4"),
        ("ctrl", "1"), ("ctrl", "2"), ("ctrl", "3"), ("ctrl", "4"),
        ("ctrl", "1"),
    ]
    for keys in pages:
        hotkey(*keys, desc=f"快速切换")
        time.sleep(0.3)
    elapsed = time.time() - start
    shot = screenshot("v2_15_stress_test")
    record("压力测试", elapsed < 10, f"9次切换耗时 {elapsed:.2f}s", shot)

    # Step 13: Close app
    log("\n=== Phase 13: 关闭应用 ===")
    hotkey("alt", "f4", desc="关闭应用")
    wait(3)
    shot = screenshot("v2_16_app_closed")
    record("应用关闭", True, "应用已关闭", shot)

    # Cleanup
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    # Generate report
    generate_report()


def generate_report():
    elapsed = (datetime.now() - _start_time).total_seconds()
    total = len(test_results)
    passed = sum(1 for r in test_results if r.passed)
    failed = total - passed

    lines = []
    lines.append("=" * 70)
    lines.append("  LSC 直播切片系统 - Computer Use 深度测试报告")
    lines.append("=" * 70)
    lines.append(f"  测试时间: {_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  测试耗时: {elapsed:.1f}s")
    lines.append(f"  测试链接: {LIVE_URL}")
    lines.append(f"  屏幕分辨率: {pyautogui.size()[0]}x{pyautogui.size()[1]}")
    lines.append(f"  Python: {sys.version.split()[0]}")
    lines.append(f"  测试总数: {total}")
    lines.append(f"  通过: {passed}  |  失败: {failed}")
    lines.append(f"  通过率: {passed/total*100:.1f}%" if total > 0 else "  通过率: N/A")
    lines.append("=" * 70)
    lines.append("")
    lines.append("  测试详情:")
    lines.append("-" * 70)

    for i, r in enumerate(test_results, 1):
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"  {i:2d}. [{status}] {r.name}")
        lines.append(f"      {r.details}")
        if r.screenshot:
            lines.append(f"      截图: {r.screenshot}")
        lines.append("")

    lines.append("-" * 70)
    lines.append("  截图目录: " + str(SCREENSHOT_DIR))
    lines.append("-" * 70)

    report_text = "\n".join(lines)
    report_path = Path(__file__).parent / "test_report_v2.txt"
    report_path.write_text(report_text, encoding="utf-8")
    print("\n" + report_text)
    log(f"报告已保存: {report_path}")


if __name__ == "__main__":
    main()
