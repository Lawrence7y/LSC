"""平台品牌图标 SVG path 定义与渲染。

SVG path 从 docs/superpowers/prototypes/ui-prototype.html:427 迁移，
统一 viewBox 24x24，fill 由调用方注入。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 平台 SVG path（viewBox 24x24）
_PLATFORM_SVG_PATHS: dict[str, str] = {
    "douyin": (
        "M16.6 5.82s.51.5 0 0A4.28 4.28 0 0 1 15.54 3h-3.09v12.4"
        "a2.59 2.59 0 0 1-2.59 2.5c-1.42 0-2.6-1.16-2.6-2.6"
        " 0-1.72 1.66-3.01 3.37-2.48V9.66c-3.45-.46-6.43 2.22-6.43 5.64"
        " 0 3.33 2.76 5.7 5.69 5.7 3.14 0 5.69-2.55 5.69-5.7V9.01"
        "a7.35 7.35 0 0 0 4.3 1.38V7.3s-1.88.09-3.24-1.48z"
    ),
    "bilibili": (
        "M17.813 4.653h.854c1.51.054 2.769.578 3.773 1.574"
        " 1.004.995 1.524 2.249 1.56 3.76v7.36c-.036 1.51-.556 2.769-1.56 3.773"
        "s-2.262 1.524-3.773 1.56H5.333c-1.51-.036-2.769-.556-3.773-1.56"
        "S.036 18.858 0 17.347v-7.36c.036-1.511.556-2.765 1.56-3.76"
        " 1.004-.996 2.262-1.52 3.773-1.574h.774l-1.174-1.12"
        "a1.234 1.234 0 0 1-.373-.906c0-.356.124-.658.373-.907l.027-.027"
        "c.267-.249.573-.373.92-.373.347 0 .653.124.92.373L9.653 4.44"
        "c.071.071.134.142.187.213h4.267a.836.836 0 0 1 .16-.213"
        "l2.853-2.747c.267-.249.573-.373.92-.373.347 0 .662.151.929.4"
        ".267.249.391.551.391.907 0 .355-.124.657-.373.906z"
        "M5.333 7.24c-.746.018-1.373.276-1.88.773-.506.498-.769 1.13-.786 1.894"
        "v7.52c.017.764.28 1.395.786 1.893.507.498 1.134.756 1.88.773"
        "h13.334c.746-.017 1.373-.275 1.88-.773.506-.498.769-1.129.786-1.893"
        "v-7.52c-.017-.765-.28-1.396-.786-1.894-.507-.497-1.134-.755-1.88-.773"
        "H5.333zm4 5.427a1.334 1.334 0 1 1 0-2.668 1.334 1.334 0 0 1 0 2.668z"
        "m5.334 0a1.334 1.334 0 1 1 0-2.668 1.334 1.334 0 0 1 0 2.668z"
    ),
    "huya": (
        "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10"
        " 10-4.48 10-10S17.52 2 12 2zm-1 5h2v6h-2zm0 8h2v2h-2z"
    ),
    "custom": (
        "M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5"
        "s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1z"
        "M8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1"
        "s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z"
    ),
}


def render_platform_icon(
    key: str, color: str = "#ffffff", size: int = 14
) -> QPixmap:
    """渲染平台图标为单色 QPixmap，供 _PlatformTag 使用。

    Args:
        key: 平台标识（douyin/bilibili/huya/custom）
        color: 填充颜色（CSS 格式）
        size: 图标尺寸（像素）

    Returns:
        渲染好的 QPixmap，若 SVG 无效则返回空 QPixmap。
    """
    path = _PLATFORM_SVG_PATHS.get(key, _PLATFORM_SVG_PATHS["custom"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="{color}"><path d="{path}"/></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return pm
