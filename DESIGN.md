---
name: LSC Design System
description: Muted Cyan / Slate System for Desktop Multi-Stream Live Clipper. Apple/Linear inspired. Restraint in chrome, precision in tabular data, zero fluorescent clutter.

colors:
  # Brand anchors
  brand-primary: "#3ea8a4"
  brand-hover: "#4dc4bf"
  brand-light: "#2d8b87"
  
  # Dark Surfaces
  dark-ground: "#0d1013"
  dark-card: "#161a1f"
  dark-surface: "#20262e"
  dark-border: "rgba(255, 255, 255, 0.08)"
  
  # Light Surfaces
  light-ground: "#f4f5f8"
  light-card: "#ffffff"
  light-surface: "#f0f2f5"
  light-border: "rgba(0, 0, 0, 0.08)"
  
  # Typography
  dark-text-primary: "#ededed"
  dark-text-secondary: "#9ba1a6"
  dark-text-tertiary: "#636b75"
  
  light-text-primary: "#1a1d23"
  light-text-secondary: "#5f656b"
  light-text-tertiary: "#8c9299"

typography:
  sans: "'SF Pro Display', 'PingFang SC', system-ui, -apple-system, sans-serif"
  mono: "'SF Mono', 'JetBrains Mono', 'Menlo', monospace"

radii:
  sm: 4px
  md: 6px
  lg: 10px
  full: 9999px
---

# LSC Design System

## 1. Core Visual Direction
- **Muted Mineral Cyan & Slate (素雅矿物青与深邃石板灰)**:
  - 摒弃任何廉价高饱和荧光色（如纯青 #00ffff、荧光绿、大面积刺眼亮黄）；
  - 主强调色采用素雅冷矿物青（#3ea8a4 / #2d8b87）；
  - 界面信息层级主要依靠排版字重（700 vs 400）、字号（14px vs 11px）与明度灰阶对比建立，而非彩色文字堆砌。

## 2. Component Guidelines
- **持续分析状态栏 (Continuous Analysis HUD)**:
  - 进度条必须定宽紧凑（190px - 210px），高度 22px；
  - 粒子槽内纯净展示六边形能量粒子逐个充能前沿，槽内不叠加任何文字；
  - 状态、回合战果、实时跟进时间、百分比一律外置，按四段式线性排列，各区间用 1px 细线隔离。
- **工作台工具栏 (Workbench Toolbar)**:
  - 功能分段排布：选择组、播放同步组、核心业务组（批量录制/停止）、视图组；
  - 静音切换使用柔和微弱状态，不使用 Primary 抢占业务核心视觉。
- **房间监控卡片 (Room Card)**:
  - 主播与房间标题在卡片头部合并同组呈现；
  - 未开录卡片采用素雅微描边 Ghost 按钮，禁止全宽大满铺刺眼纯色块；
  - 异常状态卡片提供微型直接重试与编辑行动。
- **网格密度 (Grid Density)**:
  - 支持标准模式与紧凑模式（Dense Mode）；
  - 紧凑模式下控制收拢至悬浮层，6 路画面 100% 满屏无纵向滚动条。
- **时间轴控制栏 (Timeline Dock)**:
  - 核心剪辑动作附带键盘快捷键微标（入点 [I]、出点 [O]、添加切片 [K]）；
  - 实时反馈选区时长与推荐状态（选区: 00:45s (推荐切片)）。
