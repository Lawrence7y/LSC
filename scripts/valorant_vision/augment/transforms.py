"""多语言 Valorant 直播流 UI 区域增强变换。

模拟不同语言直播流的视觉差异：
- 繁体中文：笔画更多、字符更密集
- 日语/韩语：不同字符体系、字体渲染差异
- 英语/西语：字符宽度、间距不同
- 各语言直播调色风格差异

设计原则：
- 仅增强 UI 区域（顶部 15% + 底部 25%），保留画面中心不变
- 随机组合 1-2 种变换，避免过度增强
- 兼容 PIL Image（torchvision transforms 中间步骤）
"""
from __future__ import annotations

import random

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


# UI 区域配置：顶部（计时器/比分）+ 底部（买枪界面）
TOP_RATIO = 0.15
BOTTOM_RATIO = 0.25


def _pil_to_cv2(img: Image.Image) -> np.ndarray:
    """PIL Image (RGB) -> OpenCV ndarray (BGR)."""
    return np.array(img)[:, :, ::-1].copy()


def _cv2_to_pil(arr: np.ndarray) -> Image.Image:
    """OpenCV ndarray (BGR) -> PIL Image (RGB)."""
    return Image.fromarray(arr[:, :, ::-1])


def dilate_ui_regions(
    frame: Image.Image,
    kernel_size: int = 2,
    iterations: int = 1,
    top_ratio: float = TOP_RATIO,
    bottom_ratio: float = BOTTOM_RATIO,
) -> Image.Image:
    """对 UI 区域应用形态学膨胀，模拟高密度文字（繁体中文、日文等）。

    繁体中文（如 買槍 vs 买枪）和日文的笔画更多、字符更密集。
    此操作通过膨胀加粗 UI 区域的笔画，让模型适应这种视觉差异。

    Args:
        frame: PIL Image (RGB)
        kernel_size: 膨胀核大小（2 或 3）
        iterations: 膨胀次数（1 或 2）
        top_ratio: UI 顶部区域占比
        bottom_ratio: UI 底部区域占比

    Returns:
        增强后的 PIL Image
    """
    if not _HAS_CV2:
        return frame

    img = _pil_to_cv2(frame)
    h = img.shape[0]

    # 提取 UI 区域
    top_end = int(h * top_ratio)
    bottom_start = int(h * (1 - bottom_ratio))

    # 顶部区域（计时器、比分）
    if top_end > 0:
        top_region = img[:top_end, :]
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        top_region = cv2.dilate(top_region, kernel, iterations=iterations)
        img[:top_end, :] = top_region

    # 底部区域（买枪界面、技能图标）
    if bottom_start < h:
        bottom_region = img[bottom_start:, :]
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        bottom_region = cv2.dilate(bottom_region, kernel, iterations=iterations)
        img[bottom_start:, :] = bottom_region

    return _cv2_to_pil(img)


def enhance_ui_texture(
    frame: Image.Image,
    strength: float = 0.3,
    top_ratio: float = TOP_RATIO,
    bottom_ratio: float = BOTTOM_RATIO,
) -> Image.Image:
    """对 UI 区域应用纹理增强，模拟不同字体的填充率差异。

    使用 unsharp masking 增强边缘对比度 + 轻微噪声，
    模拟不同语言字体的渲染差异。

    Args:
        frame: PIL Image (RGB)
        strength: 增强强度（0.1~0.5）
        top_ratio: UI 顶部区域占比
        bottom_ratio: UI 底部区域占比

    Returns:
        增强后的 PIL Image
    """
    if not _HAS_CV2:
        return frame

    img = _pil_to_cv2(frame).astype(np.float32)
    h = img.shape[0]

    top_end = int(h * top_ratio)
    bottom_start = int(h * (1 - bottom_ratio))

    def _enhance_region(region: np.ndarray, strength: float) -> np.ndarray:
        # Unsharp masking: 增强边缘
        blurred = cv2.GaussianBlur(region, (0, 0), 2.0)
        enhanced = cv2.addWeighted(region, 1.0 + strength, blurred, -strength, 0)
        # 轻微高斯噪声，模拟不同压缩/渲染质量
        noise = np.random.normal(0, 2, region.shape).astype(np.float32)
        enhanced = np.clip(enhanced + noise, 0, 255)
        return enhanced

    if top_end > 0:
        img[:top_end, :] = _enhance_region(img[:top_end, :], strength)

    if bottom_start < h:
        img[bottom_start:, :] = _enhance_region(img[bottom_start:, :], strength)

    return _cv2_to_pil(img.astype(np.uint8))


def random_ui_erasing(
    frame: Image.Image,
    n_boxes: int = 2,
    box_size_ratio: float = 0.05,
    top_ratio: float = TOP_RATIO,
    bottom_ratio: float = BOTTOM_RATIO,
) -> Image.Image:
    """在 UI 区域随机遮挡小块，模拟不同语言 UI 元素差异。

    不同语言的 UI 元素（按钮文字、图标）位置和大小略有不同。
    此操作在 UI 区域随机放置灰色遮挡块，模拟这种差异。

    Args:
        frame: PIL Image (RGB)
        n_boxes: 遮挡块数量
        box_size_ratio: 遮挡块大小相对于帧宽的比例
        top_ratio: UI 顶部区域占比
        bottom_ratio: UI 底部区域占比

    Returns:
        增强后的 PIL Image
    """
    if not _HAS_CV2:
        return frame

    img = _pil_to_cv2(frame).copy()
    h, w = img.shape[:2]

    top_end = int(h * top_ratio)
    bottom_start = int(h * (1 - bottom_ratio))
    box_size = int(w * box_size_ratio)

    for _ in range(n_boxes):
        # 随机选择顶部或底部区域
        if random.random() < 0.5 and top_end > box_size:
            # 顶部区域
            y = random.randint(0, top_end - box_size)
        elif bottom_start < h - box_size:
            # 底部区域
            y = random.randint(bottom_start, h - box_size)
        else:
            continue

        x = random.randint(0, w - box_size)
        # 灰色填充（模拟 UI 元素）
        color = random.randint(40, 180)
        cv2.rectangle(img, (x, y), (x + box_size, y + box_size), (color, color, color), -1)

    return _cv2_to_pil(img)


class MultiLanguageUIAugment:
    """多语言 UI 区域增强组合。

    随机选择 1-2 种变换应用，模拟不同语言直播流的视觉差异。

    使用方式（在 torchvision transforms.Compose 中）：
        train_tf = transforms.Compose([
            transforms.Resize((240, 240)),
            transforms.RandomCrop((224, 224)),
            MultiLanguageUIAugment(),  # 在此处插入
            transforms.ColorJitter(...),
            transforms.ToTensor(),
            ...
        ])
    """

    def __init__(
        self,
        prob_dilate: float = 0.5,
        prob_texture: float = 0.3,
        prob_erasing: float = 0.2,
        top_ratio: float = TOP_RATIO,
        bottom_ratio: float = BOTTOM_RATIO,
    ) -> None:
        """
        Args:
            prob_dilate: 应用笔画增厚的概率
            prob_texture: 应用纹理增强的概率
            prob_erasing: 应用 UI 遮挡的概率
            top_ratio: UI 顶部区域占比
            bottom_ratio: UI 底部区域占比
        """
        self.prob_dilate = prob_dilate
        self.prob_texture = prob_texture
        self.prob_erasing = prob_erasing
        self.top_ratio = top_ratio
        self.bottom_ratio = bottom_ratio

    def __call__(self, frame: Image.Image) -> Image.Image:
        """应用随机增强组合。"""
        if not _HAS_CV2:
            return frame

        # 随机选择 1-2 种变换
        transforms_to_apply = []

        if random.random() < self.prob_dilate:
            kernel_size = random.choice([2, 3])
            iterations = random.choice([1, 2])
            transforms_to_apply.append(
                lambda f, ks=kernel_size, it=iterations: dilate_ui_regions(
                    f, ks, it, self.top_ratio, self.bottom_ratio
                )
            )

        if random.random() < self.prob_texture:
            strength = random.uniform(0.15, 0.4)
            transforms_to_apply.append(
                lambda f, s=strength: enhance_ui_texture(
                    f, s, self.top_ratio, self.bottom_ratio
                )
            )

        if random.random() < self.prob_erasing:
            n_boxes = random.randint(1, 3)
            transforms_to_apply.append(
                lambda f, n=n_boxes: random_ui_erasing(
                    f, n, 0.05, self.top_ratio, self.bottom_ratio
                )
            )

        # 应用选中的变换
        for t in transforms_to_apply:
            frame = t(frame)

        return frame

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"prob_dilate={self.prob_dilate}, "
            f"prob_texture={self.prob_texture}, "
            f"prob_erasing={self.prob_erasing})"
        )
