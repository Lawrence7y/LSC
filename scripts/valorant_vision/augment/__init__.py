"""多语言 Valorant 直播流数据增强包。

提供 UI 区域增强变换，模拟不同语言（简体/繁体中文、日语、韩语、英语、西语）
直播流的视觉差异，提升模型泛化能力。

主要组件：
- transforms.py: 增强变换函数和 MultiLanguageUIAugment 类
- aug_offline.py: 离线增强脚本（可选，用于预览和调试）
"""

from .transforms import (
    MultiLanguageUIAugment,
    dilate_ui_regions,
    enhance_ui_texture,
    random_ui_erasing,
)

__all__ = [
    "MultiLanguageUIAugment",
    "dilate_ui_regions",
    "enhance_ui_texture",
    "random_ui_erasing",
]
