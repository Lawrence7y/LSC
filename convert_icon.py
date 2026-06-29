#!/usr/bin/env python3
"""将PNG图标转换为ICO格式用于Electron应用(包含256x256尺寸)"""

from PIL import Image
import os

# 输入输出路径
input_icon = r'd:\Project\直播切片多人\extracted_icon.png'
output_dir = r'd:\Project\直播切片多人\lsc-electron\assets'
output_ico = os.path.join(output_dir, 'icon.ico')
output_png = os.path.join(output_dir, 'logo.png')

print(f"Reading icon: {input_icon}")

# 读取提取的图标
img = Image.open(input_icon)
print(f"Original size: {img.size}")

# ICO需要多种尺寸以适应不同场景,包括256x256
sizes = [16, 32, 48, 64, 128, 256]

# 创建多个尺寸的图像列表
icon_images = []
for size in sizes:
    # 使用高质量缩放算法
    resized = img.resize((size, size), Image.LANCZOS)
    icon_images.append(resized)
    print(f"  Added size: {size}x{size}")

# 保存为ICO格式(包含所有尺寸,包括256x256)
img.save(output_ico, format='ICO', sizes=[(s, s) for s in sizes])
print(f"\nGenerated ICO file: {output_ico}")

# 同时保存PNG版本作为备用
img.save(output_png, format='PNG')
print(f"Generated PNG file: {output_png}")

print("\nDone!")
