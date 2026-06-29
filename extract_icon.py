#!/usr/bin/env python3
"""提取图片中心图标的脚本"""

from PIL import Image
import numpy as np

# 读取原始图片
input_path = r'C:\Users\Administrator\AppData\Roaming\QoderCN\SharedClientCache\cache\images\task-7cb\cd2a0eea-d9f3-4dcd-a0b6-a9340ec5893a-71d17cf8.jpg'
output_path = r'd:\Project\直播切片多人\extracted_icon.png'

img = Image.open(input_path)
width, height = img.size

print(f"原始图片尺寸: {width}x{height}")

# 转换为RGBA模式(如果需要)
if img.mode != 'RGBA':
    img = img.convert('RGBA')

# 获取像素数据
pixels = img.load()

# 创建一个新的RGBA图像用于输出
output_img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
output_pixels = output_img.load()

# 定义背景色范围(白色/浅灰色)
# 根据图片观察,背景是接近白色的
def is_background(r, g, b):
    """判断是否为背景色(白色或浅灰色)"""
    # 如果RGB值都较高且接近,认为是背景
    return r > 200 and g > 200 and b > 200

# 遍历所有像素,保留非背景色的像素
for y in range(height):
    for x in range(width):
        r, g, b, a = pixels[x, y]
        
        if not is_background(r, g, b):
            # 保留图标部分
            output_pixels[x, y] = (r, g, b, 255)
        else:
            # 背景设为透明
            output_pixels[x, y] = (0, 0, 0, 0)

# 裁剪到图标实际区域(去除四周的透明区域)
bbox = output_img.getbbox()
if bbox:
    cropped_img = output_img.crop(bbox)
    print(f"裁剪后尺寸: {cropped_img.size[0]}x{cropped_img.size[1]}")
    cropped_img.save(output_path)
    print(f"已保存到: {output_path}")
else:
    print("未找到图标内容!")
    output_img.save(output_path)
    print(f"已保存到: {output_path}")

print("完成!")
