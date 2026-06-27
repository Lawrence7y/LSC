import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from lsc.platforms.registry import parse_stream

url = "https://www.douyin.com/follow/live/295380890971?anchor_id=2524898145613127"
info = parse_stream(url)
print(f"主播: '{info.streamer}'")
print(f"标题: '{info.title}'")
print(f"是否直播: {info.is_live}")
print(f"画质数量: {len(info.quality_urls)}")
print(f"画质列表: {list(info.quality_urls.keys())}")
print(f"选中画质: {info.selected_quality}")
print(f"流地址: {info.stream_url[:60]}..." if info.stream_url else "流地址: 无")
