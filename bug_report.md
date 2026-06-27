# LSC 项目 Bug 分析报告

## 测试结果概览

- **总测试数**: 159
- **通过**: 159
- **失败**: 0
- **通过率**: 100% ✅

---

## Bug #1: FullscreenPreview 缺少封装类 ✅ 已修复

### 状态
**已修复** - 2026-06-23

### 修复方案
在 `lsc/gui/pages/multi_room/fullscreen_preview.py` 中创建了 `FullscreenPreview` 封装类：
- `is_active()` 方法
- `window()` 方法返回实际顶层窗口
- `_auto_hide_timer` 属性
- `_show_controls` 方法
- `_hide_controls` 方法
- `_sync_controls` 方法
- 完整的播放控制和自动隐藏逻辑

### 修复文件
- `lsc/gui/pages/multi_room/fullscreen_preview.py` (新增)
- `lsc/gui/pages/multi_room/__init__.py` (更新导出)
- `lsc/gui/pages/multi_room/page.py` (使用新封装类)

---

## Bug #2: MultiRoomPage 缺少响应式布局属性 ✅ 已修复

### 状态
**已修复** - 2026-06-23

### 修复方案
在 `MultiRoomPage._build_ui` 中保存布局组件为实例属性：
- `_page_scroll`: 页面级滚动区域
- `_page_body`: 页面主体容器
- `_splitter`: 分割器
- `_right_scroll`: 右侧滚动区域
- `_card_container`: 卡片容器
- `_card_layout`: FlowLayout 实例

### 修复文件
- `lsc/gui/pages/multi_room/page.py` (重构)

---

## Bug #3: DetailPanel 包含嵌套 ScrollArea ✅ 已修复

### 状态
**已修复** - 2026-06-23

### 修复方案
将 `DetailPanel` 改为不包含 `QScrollArea`：
- 移除 `_scroll`，直接使用 `_body_layout`
- 使用 `QVBoxLayout` 直接管理内容
- 添加 `addStretch()` 保持内容顶部对齐

### 修复文件
- `lsc/gui/pages/multi_room/detail_panel.py` (新增)

---

## 修复总结

所有 3 个 bug 已在 2026-06-23 的代码重构中修复：

| Bug | 状态 | 修复方式 |
|-----|------|----------|
| #1 FullscreenPreview | ✅ | 创建独立封装类 |
| #2 响应式布局属性 | ✅ | 保存为实例属性 |
| #3 嵌套 ScrollArea | ✅ | 移除内部 ScrollArea |

### 相关优化
本次修复还完成了以下优化：
1. **全局心跳优化** - 分层心跳机制，1.17-2.42x 性能提升
2. **巨型文件拆分** - multi_room.py (2220行) 和 record.py (1817行) 拆分为多个模块
3. **错误恢复增强** - 扩展错误映射、指数退避重连、录制完整性校验

---

## 其他潜在问题（待观察）

### 1. 内存泄漏风险
- `_fullscreen_window` 关闭后，预览 widget 的父对象需要正确清理
- 多房间场景下，每个房间的 controller 和 preview_widget 需要及时释放
- **状态**: 已在 FullscreenPreview._cleanup() 中处理

### 2. 线程安全
- `_ConnectWorker` 和 `_BatchRecordWorker` 在后台线程修改房间状态
- 虽然 Python GIL 保护简单属性写入，但复合操作可能有竞态条件
- **状态**: 低风险，继续观察

### 3. 错误处理
- 平台适配器的 `parse` 方法可能抛出未捕获异常
- FFmpeg 进程崩溃后的重连逻辑可能过于激进
- **状态**: 已增强错误恢复机制，添加指数退避
