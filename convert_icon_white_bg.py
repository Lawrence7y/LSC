#!/usr/bin/env python3
"""将PNG图标转换为ICO格式,使用白色背景替代透明"""

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
print(f"Original mode: {img.mode}")

# 如果图片有透明通道(RGBA),需要转换为白色背景
if img.mode == 'RGBA':
    # 创建白色背景
    white_background = Image.new('RGB', img.size, (255, 255, 255))
    # 将原图粘贴到白色背景上,使用alpha通道作为mask
    white_background.paste(img, mask=img.split()[3])  # 第4个通道是alpha
    img = white_background
    print("Converted transparent background to white")
elif img.mode != 'RGB':
    img = img.convert('RGB')
    print("Converted to RGB mode")

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

# 同时保存PNG版本(白色背景)
img.save(output_png, format='PNG')
print(f"Generated PNG file: {output_png}")

print("\nDone!")
