"""测试抖音 title 解析。"""
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

title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
if title_match:
    print(f"<title>: {title_match.group(1)}")

og_title = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
if og_title:
    print(f"og:title: {og_title.group(1)}")

og_desc = re.search(r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']*)["\']', html, re.IGNORECASE)
if og_desc:
    print(f"og:description: {og_desc.group(1)}")

print("\n--- 直接访问 live.douyin.com ---")
url2 = "https://live.douyin.com/295380890971"
html2 = mod.fetch_page(url2)
title_match2 = re.search(r"<title[^>]*>([^<]+)</title>", html2, re.IGNORECASE)
if title_match2:
    print(f"<title>: {title_match2.group(1)}")

og_title2 = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\']', html2, re.IGNORECASE)
if og_title2:
    print(f"og:title: {og_title2.group(1)}")
