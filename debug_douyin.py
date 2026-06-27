"""调试抖音数据结构，看看 title 和 streamerName 为什么为空。"""
import os
import sys
import json

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

import importlib.util
spec = importlib.util.spec_from_file_location("douyin_record", "scripts/douyin_record.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

url = "https://www.douyin.com/follow/live/295380890971?anchor_id=2524898145613127"
print("获取页面中...")
html = mod.fetch_page(url)
print(f"HTML长度: {len(html)}")

print("\n提取 SSR 数据...")
data = mod.extract_ssr_data(html)
print(f"  title: '{data['title']}'")
print(f"  streamerName: '{data['streamerName']}'")
print(f"  roomId: {data['roomId']}")
print(f"  isLive: {data['isLive']}")
if data['streamUrl']:
    print(f"  streamUrl: {data['streamUrl'][:60]}...")

print("\n--- 调试: 搜索所有包含 title/nickname 字段 ---")
prefix = 'self.__pace_f.push([1,"'

count = 0
pos = 0
while True:
    start = html.find(prefix, pos)
    if start < 0:
        break
    start += len(prefix)
    end = html.find('"])', start)
    if end < 0:
        break
    json_str = html[start:end]
    json_str = json_str.replace('\\"', '"').replace("\\\\", "\x01").replace("\\/", "/").replace("\x01", "\\")
    try:
        doc = json.loads(json_str)
        count += 1
        if isinstance(doc, dict):
            keys = list(doc.keys())[:8]
            print(f"\n块 {count}: keys={keys}")
            def find_strings(obj, path="", depth=0):
                if depth > 5:
                    return
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if any(kw in k.lower() for kw in ["title", "nickname", "name", "owner", "anchor"]):
                            if isinstance(v, str) and v.strip():
                                print(f"  {path}.{k} = '{v[:80]}'")
                            elif isinstance(v, dict):
                                print(f"  {path}.{k} = <dict>")
                        find_strings(v, f"{path}.{k}", depth+1)
                elif isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], (dict, str)):
                    find_strings(obj[0], f"{path}[0]", depth+1)
            find_strings(doc)
    except Exception:
        pass
    pos = end + 3

print(f"\n共找到 {count} 个数据块")

# 同时试试用 parse_stream 函数
print("\n--- 用 parse_stream 测试 ---")
from lsc.platforms.registry import parse_stream
info = parse_stream(url)
print(f"  streamer: '{info.streamer}'")
print(f"  title: '{info.title}'")
print(f"  selected_quality: {info.selected_quality}")
print(f"  quality_urls keys: {list(info.quality_urls.keys())}")
print(f"  is_live: {info.is_live}")
print(f"  error: {info.error}")
