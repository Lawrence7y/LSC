"""深入调试抖音 HTML 的 title。"""
import os
import re
import importlib.util

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

spec = importlib.util.spec_from_file_location("douyin_record", "scripts/douyin_record.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

url = "https://www.douyin.com/follow/live/295380890971?anchor_id=2524898145613127"
html = mod.fetch_page(url)

# 找所有 title 出现的位置
print("搜索 'title' 出现的位置...")
for m in re.finditer(r"title", html, re.IGNORECASE):
    start = max(0, m.start() - 20)
    end = min(len(html), m.end() + 50)
    context = html[start:end].replace("\n", " ")
    print(f"  位置 {m.start()}: ...{context}...")

print("\n--- 找 og:title ---")
for m in re.finditer(r"og:title", html):
    start = max(0, m.start() - 10)
    end = min(len(html), m.end() + 100)
    context = html[start:end].replace("\n", " ")
    print(f"  {context}")

print("\n--- 找 <title ---")
for m in re.finditer(r"<title", html, re.IGNORECASE):
    start = max(0, m.start() - 5)
    end = min(len(html), m.end() + 200)
    context = html[start:end].replace("\n", " ")
    print(f"  {context[:200]}")
