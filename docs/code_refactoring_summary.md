# 代码重构优化总结

## 优化概述

本次优化对现有代码进行了重构，消除重复代码，提升可维护性。

## 重构内容

### 1. 统一全屏预览组件（P0，最大改进）

**问题**: 存在两套几乎完全相同的全屏预览实现：
- `lsc/gui/components/fullscreen_preview.py` (共享组件版，443 行)
- `lsc/gui/pages/multi_room/fullscreen_preview.py` (专用版，410 行)

**重构方案**:
- 删除多房间专用版 `FullscreenPreview`
- 修改 `multi_room/page.py` 的 `_enter_fullscreen` 方法，使用共享组件的 callable 接口
- 更新 `multi_room/__init__.py` 移除专用版导出

**改进效果**: 减少约 400 行重复代码，消除两处维护点。

### 2. `_open_in_explorer` 移至公共模块（P2）

**问题**: `_open_in_explorer` 定义在 `MultiRoomPage` 类内部，无法复用。

**重构方案**:
- 将 `open_in_explorer` 函数移至 `lsc/utils/helpers.py`
- `MultiRoomPage._open_in_explorer` 改为委托调用

**改进效果**: 可在录制页等其他场景复用。

### 3. `Card.add_title()` 快捷方法（P3）

**问题**: `title = QLabel("xxx"); title.setObjectName("card_title"); card.add_widget(title)` 模式重复 7 次。

**重构方案**: 在 `Card` 类中添加 `add_title(text)` 方法。

**改进效果**: 简化标题创建代码。

## 测试验证

```
159 passed, 0 failed - 100% 通过率
```

## 修改文件清单

1. `lsc/gui/pages/multi_room/page.py` - 使用共享全屏预览组件，使用 `open_in_explorer`
2. `lsc/gui/pages/multi_room/__init__.py` - 移除专用版导出
3. `lsc/utils/helpers.py` - 添加 `open_in_explorer` 函数
4. `lsc/gui/components/widgets.py` - 添加 `Card.add_title()` 方法

## 保留的重复代码（有意保留）

| 重复项 | 原因 |
|--------|------|
| `_fmt_time` (timeline.py) | 行为不同：时间线始终显示 HH:MM:SS，helpers.py 根据时长决定 |
| QSettings 实例化 (15 处) | 改动风险高，收益低 |
| ScrollArea 配置 (6 处) | 每处可能有细微差异，提取工厂函数收益有限 |

## 本次会话完成的所有优化汇总

| 优化项 | 状态 | 效果 |
|--------|------|------|
| 全局心跳优化 | ✅ | 1.17-2.42x 性能提升 |
| multi_room.py 拆分 | ✅ | 2220行 → 5个文件 |
| record.py 拆分 | ✅ | 1817行 → 4个文件 |
| 错误恢复增强 | ✅ | 指数退避、完整性校验 |
| Bug 修复 | ✅ | 3个 bug 全部修复 |
| 性能优化 | ✅ | QSS 增量更新、文件大小缓存 |
| 类型注解完善 | ✅ | 8 处高优先级修复 |
| 代码重构优化 | ✅ | 消除 400+ 行重复代码 |
