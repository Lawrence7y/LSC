"""截图审计脚本 — 验证所有页面的按钮设计语言统一性。"""
from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path

import win32con
import win32gui
import win32ui
from PIL import Image


def find_window(title_sub: str) -> int | None:
    handles: list[int] = []
    def callback(hwnd: int, extra: list[int]) -> None:
        text = win32gui.GetWindowText(hwnd)
        if title_sub in text:
            extra.append(hwnd)
    win32gui.EnumWindows(callback, handles)
    return handles[0] if handles else None


def capture_window(hwnd: int, out_path: str) -> None:
    """捕获窗口截图并保存。"""
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.3)

    rect = win32gui.GetWindowRect(hwnd)
    x, y, x2, y2 = rect
    w, h = x2 - x, y2 - y

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    save_bitmap = win32ui.CreateBitmap()
    save_bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(save_bitmap)

    PW_RENDERFULLCONTENT = 2
    ctypes.windll.user32.PrintWindow(ctypes.c_void_p(hwnd), save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)

    bmp_info = save_bitmap.GetInfo()
    bmp_str = save_bitmap.GetBitmapBits(True)
    im = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        bmp_str,
        "raw",
        "BGRX",
        0,
        1,
    )

    im.save(out_path)

    win32gui.DeleteObject(save_bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)


def send_key(hwnd: int, key: str, ctrl: bool = False) -> None:
    """发送按键到窗口（使用 pyautogui 更可靠）。"""
    import pyautogui
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.1)
    if ctrl:
        pyautogui.keyDown('ctrl')
        pyautogui.keyDown(key)
        pyautogui.keyUp(key)
        pyautogui.keyUp('ctrl')
    else:
        pyautogui.keyDown(key)
        pyautogui.keyUp(key)
    time.sleep(0.1)


def main() -> None:
    out_dir = Path(__file__).parent / "audit_output"
    out_dir.mkdir(exist_ok=True)

    hwnd = find_window("LSC")
    if hwnd is None:
        print("错误: 未找到 LSC 窗口")
        return

    print(f"找到窗口: hwnd={hwnd}")

    # 1. 截图仪表盘页面 (Ctrl+1)
    print("截图: 仪表盘页面...")
    send_key(hwnd, '1', ctrl=True)
    time.sleep(0.8)
    capture_window(hwnd, str(out_dir / "01_dashboard.png"))

    # 2. 切换到多房间页面 (Ctrl+2)
    print("切换到多房间页面...")
    send_key(hwnd, '2', ctrl=True)
    time.sleep(0.8)
    capture_window(hwnd, str(out_dir / "02_multiroom.png"))

    # 3. 切换到设置页面 (Ctrl+4) - 先截设置页面
    print("切换到设置页面...")
    send_key(hwnd, '4', ctrl=True)
    time.sleep(0.8)
    capture_window(hwnd, str(out_dir / "03_settings.png"))

    # 4. 切换到直播录制页面 (Ctrl+3)
    print("切换到直播录制页面...")
    send_key(hwnd, '3', ctrl=True)
    time.sleep(0.8)
    capture_window(hwnd, str(out_dir / "04_record.png"))

    print(f"截图完成，保存在: {out_dir}")


if __name__ == "__main__":
    main()
