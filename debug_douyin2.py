"""进一步调试抖音 HTML，找 title 和 nickname。"""
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
print("获取页面中...")
html = mod.fetch_page(url)
print(f"HTML长度: {len(html)}")

# 1. 看看 <title> 标签
title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
if title_match:
    print(f"\n1. <title> 标签: '{title_match.group(1).strip()}'")

# 2. 看看 meta og:title
og_title = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html, re.IGNORECASE)
if og_title:
    print(f"2. og:title: '{og_title.group(1)}'")

og_desc = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html, re.IGNORECASE)
if og_desc:
    print(f"   og:description: '{og_desc.group(1)[:100]}'")

# 3. 搜索 RENDER_DATA 或 state 之类的全局变量
print("\n3. 搜索 RENDER_DATA / _ROUTER_DATA / state 等变量...")
for pattern_name, pattern in [
    ("RENDER_DATA", r'RENDER_DATA\s*=\s*["\'](.*?)["\']'),
    ("window.__INIT_PROPS__", r'__INIT_PROPS__\s*=\s*(\{.*?\})\s*;'),
    ("window.__INIT_STATE__", r'__INIT_STATE__\s*=\s*(\{.*?\})\s*;'),
    ("window._SSR_HYDRATED_DATA", r'_SSR_HYDRATED_DATA\s*=\s*(\{.*?\})\s*;'),
]:
    match = re.search(pattern, html, re.DOTALL)
    if match:
        val = match.group(1)[:200]
        print(f"   {pattern_name}: 找到! 前200字符: {val}")
    else:
        print(f"   {pattern_name}: 未找到")

# 4. 搜索包含 nickname 的 script 标签
print("\n4. 搜索包含 nickname/title 的 script 块...")
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
print(f"   共 {len(scripts)} 个 script 标签")
for i, script in enumerate(scripts[:20]):
    if any(kw in script for kw in ["nickname", "streamerName", '"title"', 'roomName', '"name"']):
        print(f"\n   Script {i} (len={len(script)}):")
        # 找包含 nickname 的行
        lines = script.split('\n')
        for j, line in enumerate(lines):
            if any(kw in line for kw in ["nickname", "streamerName", "roomName", '"title"']):
                stripped = line.strip()[:150]
                print(f"     行{j}: {stripped}")

# 5. 搜索包含直播标题/主播名的特征字符串
print("\n5. 搜索特定关键词...")
import json
# 找所有包含 "nickname" 的位置
for kw in ["nickname", "streamerName", '"title"', 'roomName', 'anchor_name', 'owner_name']:
    count = html.count(kw)
    if count > 0:
        # 找第一次出现的位置上下文
        idx = html.find(kw)
        context = html[max(0, idx-50):idx+100]
        print(f"   '{kw}': 出现 {count} 次, 首次上下文: ...{context}...")
