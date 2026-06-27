"""最小化 mpv 嵌入测试，排查 access violation 问题。"""
import os
import sys

# 确保 libmpv DLL 可被加载
os.environ["PATH"] = os.path.join(".runtime", "libmpv") + os.pathsep + os.environ["PATH"]

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt

app = QApplication(sys.argv)

# 创建一个可见的 widget
w = QWidget()
w.resize(320, 240)
w.show()
app.processEvents()

wid = int(w.winId())
print(f"winId: {wid}, valid: {wid != 0}")

import mpv

# 测试1: 不嵌入 wid，仅创建 mpv 实例
print("\n=== Test 1: 创建 mpv 实例（不嵌入 wid）===")
try:
    player = mpv.MPV(vo="gpu", hwdec="no", idle="yes")
    print("OK: mpv 实例创建成功（无 wid）")
    player.terminate()
    print("OK: terminate 成功")
except Exception as e:
    print(f"FAIL: {e}")

# 测试2: 嵌入 wid，vo=direct3d
print("\n=== Test 2: 嵌入 wid, vo=direct3d ===")
try:
    player = mpv.MPV(wid=str(wid), vo="direct3d", hwdec="no")
    print("OK: mpv 实例创建成功（wid + direct3d）")
    player.terminate()
    print("OK: terminate 成功")
except Exception as e:
    print(f"FAIL: {e}")

# 测试3: 嵌入 wid，不指定 vo
print("\n=== Test 3: 嵌入 wid, 不指定 vo ===")
try:
    player = mpv.MPV(wid=str(wid), hwdec="no")
    print("OK: mpv 实例创建成功（wid + 默认 vo）")
    player.terminate()
    print("OK: terminate 成功")
except Exception as e:
    print(f"FAIL: {e}")

# 测试4: 嵌入 wid，vo=gpu
print("\n=== Test 4: 嵌入 wid, vo=gpu ===")
try:
    player = mpv.MPV(wid=str(wid), vo="gpu", hwdec="no")
    print("OK: mpv 实例创建成功（wid + gpu）")
    player.terminate()
    print("OK: terminate 成功")
except Exception as e:
    print(f"FAIL: {e}")

# 测试5: 嵌入 wid，vo=gpu-next
print("\n=== Test 5: 嵌入 wid, vo=gpu-next ===")
try:
    player = mpv.MPV(wid=str(wid), vo="gpu-next", hwdec="no")
    print("OK: mpv 实例创建成功（wid + gpu-next）")
    player.terminate()
    print("OK: terminate 成功")
except Exception as e:
    print(f"FAIL: {e}")

print("\n=== 测试完成 ===")
w.close()
