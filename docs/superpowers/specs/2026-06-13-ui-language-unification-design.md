# UI 设计语言统一方案

日期：2026-06-13（原型验证后修订）

## 背景

当前 `PySide6` 界面中的选项按钮样式分散在多个自绘组件和局部样式表中，导致以下问题：

- 左侧三页导航、底部主题切换、录制页区间按钮、设置页选项按钮的视觉语言不一致。
- 某些文字只有在被选中时才更明显，未选中时识别度不足。
- 录制页控制栏按钮尺寸和间距不完全统一，存在局部遮挡和拥挤风险。
- 缺少深浅两套主题的完整 token 定义。

本次改动处理设计语言统一、布局对齐和主题 token 化，不改变录制、导出、切页、主题切换等功能逻辑。

## 目标

- 所有"选项型按钮"统一为同一套橙色设计语言。
- 所有选项文字常驻显示，不能依赖选中态才可见。
- 左侧三个页面按钮与底部黑白模式切换按钮保持相同尺寸和结构。
- 消除按钮之间、按钮与时间文本之间的遮挡问题。
- 建立完整的深浅双主题 token 体系，所有组件从 token 取色。
- 统一圆角、间距、过渡动画等基础规范。

## 范围

本次统一覆盖以下区域：

- `lsc/gui/components/sidebar.py`
  - 左侧三个页面切换按钮
  - 底部主题切换按钮
- `lsc/gui/components/widgets.py`
  - 通用 `Chip` / `ChipGroup`
- `lsc/gui/pages/record.py`
  - 录制页 `入点 / 出点 / 导出 / 全屏`
  - 与这些按钮相邻的控制栏布局
  - `InlineTimeline` 时间线视觉规范
- `lsc/gui/pages/settings.py`
  - 设置页所有使用 `ChipGroup` 的选项组
- `lsc/gui/pages/multi_room.py`（新增）
  - 多房间工作台页面的所有组件
  - 房间卡片、控制栏、状态栏
- `lsc/gui/theme.py`
  - 完整 token 定义和全局样式生成

不在本次范围内：

- 仪表盘内容布局重做
- 视频预览、时间轴、导出流程的行为重构

## 主题 Token 体系

### 色板定义

基于 `lsc/gui/theme.py` 中的 `ThemeColors` dataclass，所有组件从以下 token 取色：

#### 深色主题（DARK）

| Token | 值 | 用途 |
|-------|-----|------|
| `bg_primary` | `#0c0e12` | 页面背景 |
| `bg_secondary` | `#14161c` | 卡片、面板、顶栏背景 |
| `bg_tertiary` | `#1c1f28` | 输入框、时间线轨道背景 |
| `bg_elevated` | `#242733` | 悬浮卡片、下拉菜单、标签背景 |
| `text_primary` | `#f0f1f5` | 主要文字 |
| `text_secondary` | `#9aa0b0` | 次要文字 |
| `text_tertiary` | `#5c6270` | 辅助文字、占位符 |
| `border_subtle` | `rgba(255,255,255,0.06)` | 细微分隔线 |
| `border_default` | `rgba(255,255,255,0.10)` | 默认边框 |
| `border_strong` | `rgba(255,255,255,0.15)` | 强调边框 |
| `accent_primary` | `#ff8c42` | 主强调色（橙色） |
| `accent_primary_dim` | `rgba(255,140,66,0.15)` | 主强调色半透明背景 |
| `accent_primary_glow` | `rgba(255,140,66,0.35)` | 主强调色发光效果 |
| `accent_secondary` | `#5b8def` | 次强调色（蓝色） |
| `accent_success` | `#3dd598` | 成功/连接状态 |
| `accent_success_dim` | `rgba(61,213,152,0.12)` | 成功色半透明背景 |
| `accent_warning` | `#ffc542` | 警告状态 |
| `accent_warning_dim` | `rgba(255,197,66,0.12)` | 警告色半透明背景 |
| `accent_error` | `#ff6b6b` | 错误/录制状态 |
| `accent_error_dim` | `rgba(255,107,107,0.12)` | 错误色半透明背景 |

#### 浅色主题（LIGHT）

| Token | 值 | 用途 |
|-------|-----|------|
| `bg_primary` | `#f5f6f8` | 页面背景 |
| `bg_secondary` | `#ffffff` | 卡片、面板、顶栏背景 |
| `bg_tertiary` | `#eef0f4` | 输入框、时间线轨道背景 |
| `bg_elevated` | `#ffffff` | 悬浮卡片、下拉菜单、标签背景 |
| `text_primary` | `#1a1d26` | 主要文字 |
| `text_secondary` | `#5c6270` | 次要文字 |
| `text_tertiary` | `#9aa0b0` | 辅助文字、占位符 |
| `border_subtle` | `rgba(0,0,0,0.06)` | 细微分隔线 |
| `border_default` | `rgba(0,0,0,0.10)` | 默认边框 |
| `border_strong` | `rgba(0,0,0,0.15)` | 强调边框 |
| `accent_primary` | `#e6722f` | 主强调色（橙色） |
| `accent_primary_dim` | `rgba(230,114,47,0.12)` | 主强调色半透明背景 |
| `accent_primary_glow` | `rgba(230,114,47,0.25)` | 主强调色发光效果 |
| `accent_secondary` | `#4a7de4` | 次强调色（蓝色） |
| `accent_success` | `#2cb980` | 成功/连接状态 |
| `accent_success_dim` | `rgba(44,185,128,0.12)` | 成功色半透明背景 |
| `accent_warning` | `#e5a830` | 警告状态 |
| `accent_warning_dim` | `rgba(229,168,48,0.12)` | 警告色半透明背景 |
| `accent_error` | `#e05050` | 错误/录制状态 |
| `accent_error_dim` | `rgba(224,80,80,0.12)` | 错误色半透明背景 |

### 基础规范 Token

| Token | 值 | 说明 |
|-------|-----|------|
| `radius` | 8px | 按钮、卡片、输入框默认圆角 |
| `radius_lg` | 10px | 大卡片圆角 |
| `radius_xl` | 12px | Modal 对话框圆角 |
| `transition` | `cubic-bezier(0.16, 1, 0.3, 1)` | 全局过渡动画曲线 |
| `transition_duration` | 200ms | 默认过渡时长 |
| `shadow_sm` | `0 1px 3px rgba(0,0,0,0.3)` | 深色主题小阴影 |
| `shadow_md` | `0 4px 12px rgba(0,0,0,0.4)` | 深色主题中阴影 |
| `shadow_lg` | `0 8px 30px rgba(0,0,0,0.5)` | 深色主题大阴影 |
| `shadow_glow` | `0 0 20px accent_glow` | 橙色发光阴影 |
| `font_mono` | `JetBrains Mono, Cascadia Code, Consolas` | 等宽字体 |
| `font_sans` | `Microsoft YaHei, PingFang SC, -apple-system` | 界面字体 |

### 阴影对照（浅色主题）

| Token | 值 |
|-------|-----|
| `shadow_sm` | `0 1px 3px rgba(0,0,0,0.08)` |
| `shadow_md` | `0 4px 12px rgba(0,0,0,0.1)` |
| `shadow_lg` | `0 8px 30px rgba(0,0,0,0.12)` |
| `shadow_glow` | `0 0 20px rgba(230,114,47,0.15)` |

## 统一视觉规则

### 选中态

- 橙色边框（`accent_primary`）
- 橙色底色（`accent_primary_dim`）
- 橙色文字（`accent_primary`）
- 使用与当前"入点 / 出点"一致的圆角矩形语言
- 选中卡片增加顶部橙色发光条和 glow 阴影

### 未选中态

- 橙色边框
- 白色/透明内底
- 黑色/次要文字

### 交互约束

- 文字始终可见，不因选中状态变化而消失或明显弱化。
- 悬停态做轻微强化：`translateY(-1px)` + 阴影增强。
- 点击态做缩放反馈：`scale(0.98)`。
- 深浅主题切换时，所有组件颜色从 token 自动更新，过渡时长 400ms。

## 组件级设计

### 左侧导航按钮

- 将 `NavButton` 的自绘样式切换为与通用选项按钮一致的橙边框体系。
- 保持图标和文字并存，文字始终显示。
- 调整侧边栏宽度、按钮宽度、文字区域留白，避免中文标签被压缩。
- 按钮尺寸：`padding: 10px 14px`，`border-radius: 8px`。

### 底部主题切换按钮

- 与左侧三个导航按钮使用相同尺寸、圆角、边框、文字布局。
- 保留图标（月亮/太阳），但文字常驻显示。
- 按钮尺寸与导航按钮完全一致。

### 通用 ChipGroup

- 将 `Chip` 统一成橙边框、白底黑字的未选中态。
- 被选中时切换为橙边框、橙底、橙字。
- 为窄屏场景预留更合理的宽度和间距，降低互相顶住的概率。
- Chip 尺寸：`padding: 6px 16px`，`border-radius: 6px`。
- 悬停时 `translateY(-1px)` 浮起效果。

### 录制页控制栏

- `入点 / 出点 / 导出 / 全屏` 统一为同一套按钮尺寸和设计语言。
- 调整控制栏内按钮宽度、间距、时间文本宽度和弹性留白，避免在窗口较窄时重叠。
- 不改变按钮行为，只改视觉和布局。
- 入点按钮：绿色边框，选中态绿色填充 + 发光。
- 出点按钮：红色边框，选中态红色填充 + 发光。
- 导出按钮：橙色边框 + 橙色半透明背景，hover 时橙色填充。

### 操作按钮

| 类型 | 边框 | 文字 | hover 背景 | 示例 |
|------|------|------|-----------|------|
| primary | `accent` | `#fff` | `brightness(1.1)` + glow | 添加房间 |
| success | `success` | `success` | `success_dim` | 批量录制、入点 |
| danger | `error` | `error` | `error_dim` | 批量停止、出点 |
| default | `accent` | `accent` | `accent_dmi` | 全部静音 |
| muted | `border_default` | `text_tertiary` | `bg_tertiary` | 断开连接 |

### 图标按钮（控制栏）

- 尺寸：36x36px，圆形播放按钮 40x40px。
- 边框：`1.5px solid border_default`。
- hover：边框变 accent，背景 accent_dim，`translateY(-1px)`。
- 点击：`scale(0.95)`。

### Modal 对话框

- 圆角：12px。
- 背景遮罩：`rgba(0,0,0,0.6)` + `backdrop-filter: blur(4px)`。
- 弹出动画：`translateY(20px) scale(0.97)` → `translateY(0) scale(1)`。
- 关闭按钮 hover：`scale(1.1)`。

### Toast 通知

- 滑入动画：从右侧 `translateX(120%)` 滑入。
- 左侧色条：success/error/info 对应颜色。
- 3 秒后淡出。

## 时间线视觉规范

时间线组件 `InlineTimeline` 的视觉规范：

### 轨道

- 背景：`bg_tertiary`。
- 轨道线条：`3px` 高，`border_default` 色。
- 进度填充：从 `accent_primary` 到 `#ff5e3a` 的渐变。

### 游标

- 竖线：`2px` 宽，`text_primary` 色。
- 圆点：`10px` 直径，`text_primary` 色。
- hover 时圆点 `scale(1.3)`。
- 时间标签：`bg_elevated` 背景，`border_default` 边框，`4px` 圆角，`font_mono` 10px。

### 入点/出点

- 选区：`accent_primary_dim` 背景，`1.5px solid accent_primary` 边框，`5px` 圆角。
- 手柄：`12px x 32px`，`accent_primary` 背景，`4px` 圆角。
- 手柄 hover：`scaleY(1.15)` + `shadow_glow`。
- 手柄拖拽中：`scaleY(1.2)` + 增强 glow。
- 气泡提示：`accent_primary` 背景白字，`6px` 圆角，底部三角箭头。

### 时间刻度

- 字体：`font_mono` 8px。
- 颜色：`text_tertiary`。
- 刻度线：`1px` 宽 x `4px` 高，`border_default` 色。
- 间隔自适应：≤60s 每 5s，≤300s 每 15s，≤1200s 每 60s，>1200s 每 300s。

### 房间标签

- 位置：左上角。
- 字体：10px，`text_tertiary`，font-weight 500。

## 布局与遮挡处理

- 侧边栏增加足够宽度以承载图标和中文标签。
- 控制栏中的时间文本保留固定宽度，但缩减到更合理范围，给按钮留出稳定空间。
- 同组按钮采用统一固定高度，并减少不必要的横向占用。
- 设置页选项组延续滚动布局，不强行压缩到单行不可读状态。

## 实现策略

推荐方案：抽出统一的"选项按钮设计语言"，分别应用到自绘按钮与 `QPushButton` / `Chip`。

原因：

- 当前项目同时存在自绘控件和局部 `QSS`，纯全局样式表无法完整覆盖。
- 将通用规则下沉到组件层，可以让后续新增页面继续复用。
- 主题切换通过 `get_theme()` 获取当前 token，所有组件在 `paintEvent` 中从 token 取色。

### 主题切换实现

```python
# theme.py
def toggle_theme() -> None:
    global _current
    _current = LIGHT if _current is DARK else DARK
    _refresh_app_style()

def _refresh_app_style() -> None:
    app = QApplication.instance()
    if app:
        app.setStyleSheet(generate_stylesheet(_current, dark=(_current is DARK)))
        for w in app.allWidgets():
            w.update()
```

切换时所有组件自动从新的 token 取色，无需逐个通知。

## 验收标准

- 左侧三个页面按钮、底部主题切换按钮尺寸一致，视觉一致。
- 设置页所有选项型按钮采用相同选中/未选中样式。
- 录制页 `入点 / 出点 / 导出 / 全屏` 视觉一致。
- 所有按钮文字在未选中时依然清晰可见。
- 主窗口常见宽度下无明显按钮遮挡、文字截断或控件重叠。
- 深浅主题切换后，所有组件颜色正确过渡。
- 时间线组件的轨道、游标、手柄、刻度视觉规范统一。
- 多房间工作台页面的卡片、控制栏、状态栏遵循统一 token。
- 所有 hover/click 微交互（浮起、缩放、发光）正常工作。

## 验证计划

- 启动桌面界面，依次检查 `dashboard / workbench / settings` 三页。
- 切换黑白模式，确认按钮视觉规则不跑偏。
- 在工作台页确认控制栏按钮、时间文本、时间轴不互相遮挡。
- 在工作台页确认房间卡片 hover、选中态视觉正确。
- 在工作台页确认时间线手柄拖拽、气泡提示正常。
- 在设置页确认多个 `ChipGroup` 的选中态和未选中态一致。
- 在浅色主题下重复以上检查。
