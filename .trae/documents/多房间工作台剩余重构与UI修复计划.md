# 多房间工作台剩余重构与 UI 修复计划

## 概述

本计划承接前一会话的部分完成工作，聚焦于以下剩余任务：
1. 完成 RecordPage 删除（main.py 残留清理 + sidebar.py + 文件删除）
2. 修复静音按钮同步 Bug（`_sync_mute_all_button` 调用缺失）
3. 修复预览无画面 Bug（`_ensure_mpv` 失败后无重试机制）
4. 实现单房间降级（N=1 时隐藏批量操作 UI）
5. 更新测试套件（移除 RecordPage 引用）
6. 位置节流优化（0.5s→0.2s）

---

## 当前状态分析

### 已完成 ✅
- range_loop 代码从 manager.py 完全移除
- 回直播浮窗按钮在 PreviewSurface、RoomCard、MultiRoomPage、FullscreenPreview 中实现
- 全屏控制栏半透明背景（`rgba(0, 0, 0, 180)`）+ 2400ms 自动隐藏
- 分析功能迁移到 MultiRoomManager（AnalysisWorker/BatchExportWorker）
- 磁盘预检统一使用 8GB/路标准
- main.py 中 RecordPage 的 import、实例化、PAGE_MAP、快捷键、_refresh_dashboard 已清理
- FlowLayout 已设置 `max_per_row=2`
- 底部状态栏已包裹 QScrollArea（横向滚动条按需显示）
- ControlBar 中"试听选区"和"回直播"按钮已删除

### 待完成 ❌
- main.py 的 `closeEvent` 仍残留 `self._record` 引用（第 316-328 行，死代码）
- sidebar.py 仍保留 "record" 导航项（第 29 行）和 Ctrl+3 快捷键
- record 页面文件未删除（page.py、__init__.py、record.py、record_page.py 等）
- 静音按钮同步 Bug：`_sync_mute_all_button` 未在关键路径调用
- 预览无画面 Bug：`_ensure_mpv` 失败后无重试机制
- 单房间降级逻辑缺失
- 测试套件仍引用 RecordPage（3 个测试文件，50+ 处引用）
- 位置节流阈值 0.5s 偏大

---

## 提议变更

### 阶段 1：修复静音按钮同步 Bug（高优先级）

**文件**：`d:\Project\直播切片多人\lsc\gui\pages\multi_room\page.py`

**问题根因**：
`_sync_mute_all_button` 仅在 `_on_mute_toggled`（单卡切换）中被调用。在以下场景中按钮状态与实际房间状态不同步：
- 启动加载 `load_rooms()` 后
- `_refresh()` / `_refresh_all()` 后
- `_on_add_room()` / `_add_card()` 后
- 通过非 `_on_mute_toggled` 路径修改 `preview_muted` 时

更严重的场景：当按钮显示"取消静音"但实际并非全部静音时，用户点击期望取消静音，但 `all_muted = all([False, True, True]) = False`，`new_muted = True`（静音全部），已静音的房间无可见变化 → 用户报告的"部分房间的静音指示器未改变"。

**修复方案**：
1. 在 `_refresh()` 方法末尾添加 `self._sync_mute_all_button()` 调用
2. 在 `_refresh_all()` 方法末尾添加 `self._sync_mute_all_button()` 调用
3. 在 `_add_card()` 方法末尾添加 `self._sync_mute_all_button()` 调用
4. 在 `__init__()` 中 `load_rooms()` 循环后添加 `self._sync_mute_all_button()` 调用

**验证**：
- 启动程序，添加 2 个房间，点击"全部静音"，再手动取消一个房间的静音，观察"全部静音"按钮文字是否变为"全部静音"
- 点击"全部静音"按钮，观察所有房间的静音指示器是否同步变化

---

### 阶段 2：修复预览无画面 Bug（高优先级）

**文件**：`d:\Project\直播切片多人\lsc\gui\components\mpv_widget.py`

**问题根因**：
`_play_stream` 在 widget 尚未获得有效尺寸时被调用（150ms 延迟可能不足）。`_ensure_mpv` 检查 `isVisible() and width >= 10 and height >= 10`，失败后直接返回。`_play` 设置 `_current_path` 后返回，依赖 `showEvent` 重试，但 `widget.show()` 已在 t=0 调用过，`showEvent` 不会再次触发。`MpvWidget` 没有重写 `resizeEvent` 来在获得有效尺寸时触发 mpv 初始化。

**修复方案**：
重写 `resizeEvent`，在 widget 获得有效尺寸且 `_current_path` 已设置但 `_mpv` 为 None 时，触发延迟初始化和播放。

```python
def resizeEvent(self, event) -> None:
    super().resizeEvent(event)
    # 如果已设置播放地址但 mpv 尚未初始化，且现在有有效尺寸，触发延迟初始化
    if self._mpv is None and self._current_path and self.isVisible():
        if self.width() >= 10 and self.height() >= 10:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, self._deferred_init_and_play)
```

**验证**：
- 启动程序，添加房间，点击预览，观察是否立即显示画面（无需切换页面）
- 快速连续点击多个房间的预览按钮，观察所有房间是否都有画面

---

### 阶段 3：完成 RecordPage 删除

#### 3.1 清理 main.py 的 closeEvent

**文件**：`d:\Project\直播切片多人\main.py`

**变更**：删除 `closeEvent` 中第 315-328 行的 "# 2. 清理录制页" 块（`self._record._ctrl` 和 `self._record._preview` 引用）。重新编号注释（"# 3. 清理 Toast" → "# 2. 清理 Toast"）。

#### 3.2 更新 sidebar.py

**文件**：`d:\Project\直播切片多人\lsc\gui\components\sidebar.py`

**变更**：
1. 从 `_NAV_ITEMS` 列表中删除第 29 行的 `("record", "直播录制", ...)` 条目
2. 将 "settings" 条目的快捷键从 "Ctrl+4" 改为 "Ctrl+3"

#### 3.3 删除 record 页面文件

**删除以下文件**：
- `d:\Project\直播切片多人\lsc\gui\pages\record\page.py`
- `d:\Project\直播切片多人\lsc\gui\pages\record\__init__.py`
- `d:\Project\直播切片多人\lsc\gui\pages\record\config_panel.py`
- `d:\Project\直播切片多人\lsc\gui\pages\record\video_preview.py`
- `d:\Project\直播切片多人\lsc\gui\pages\record\icon_widgets.py`
- `d:\Project\直播切片多人\lsc\gui\pages\record.py`
- `d:\Project\直播切片多人\lsc\gui\pages\record_page.py`
- 删除空目录 `d:\Project\直播切片多人\lsc\gui\pages\record\`

**保留**：`d:\Project\直播切片多人\lsc\gui\pages\recording_controller.py`（包含 AnalysisWorker、BatchExportWorker、RecordingController，被 MultiRoomManager 使用）

**验证**：
- 启动程序，确认侧边栏只有 3 个导航项（仪表盘、工作台、设置）
- 确认 Ctrl+1/2/3 快捷键正确切换页面
- 确认关闭窗口时不报错

---

### 阶段 4：单房间降级

**文件**：`d:\Project\直播切片多人\lsc\gui\pages\multi_room\page.py`

**变更**：在 `_refresh()` 方法中添加单房间降级逻辑，当 `len(self._cards) <= 1` 时隐藏批量操作按钮。

```python
def _refresh(self) -> None:
    has_cards = bool(self._cards)
    single_room = len(self._cards) <= 1
    self._empty_label.setVisible(not has_cards)
    if not has_cards:
        self._update_empty_label_geometry()
    self._card_container.setVisible(True)
    # 单房间降级：隐藏批量操作 UI
    self._batch_record_btn.setVisible(not single_room)
    self._batch_stop_btn.setVisible(not single_room)
    self._align_live_btn.setVisible(not single_room)
    # "全部静音"在单房间时仍保留（仍有意义）
    self._update_room_limit_label()
    self._update_statusbar()
    self._sync_mute_all_button()  # 修复静音按钮同步
```

**设计决策**：
- 隐藏"批量录制"、"批量停止"、"对齐直播"（这些在单房间时无意义）
- 保留"全部静音"（单房间时仍有意义，等同于该房间的静音切换）
- 保留房间数量徽章（始终显示当前状态）
- 保留详情面板、控制栏、切片列表（这些是核心功能）

**验证**：
- 添加 1 个房间，确认"批量录制"、"批量停止"、"对齐直播"按钮不可见
- 添加第 2 个房间，确认上述按钮重新出现
- 删除房间直到只剩 1 个，确认按钮再次隐藏

---

### 阶段 5：更新测试套件

#### 5.1 删除 RecordPage 专用测试

**删除**：
- `d:\Project\直播切片多人\tests\gui\test_record_interactions.py`（整个文件针对 RecordPage 交互测试）

#### 5.2 更新 test_gui_page_audit.py

**文件**：`d:\Project\直播切片多人\tests\test_gui_page_audit.py`

**变更**：删除所有引用 RecordPage 的测试函数（28 处引用）：
- 删除 `from lsc.gui.pages.record import ...` 导入
- 删除所有 `test_record_page_*` 测试函数
- 保留与 MultiRoomPage、DashboardPage、SettingsPage 相关的测试

#### 5.3 更新 test_gui_page_reexports.py

**文件**：`d:\Project\直播切片多人\tests\test_gui_page_reexports.py`

**变更**：删除 `record_page` re-export 测试（10 处引用）：
- 删除 `_load_record_page_with_stubbed_record` 函数
- 删除 `test_record_page_module_reexports_*` 测试函数

#### 5.4 保留 test_recording_controller_options.py

**文件**：`d:\Project\直播切片多人\tests\test_recording_controller_options.py`

**不变**：该文件测试 `RecordingController`（位于 `recording_controller.py`，被保留），不直接引用 RecordPage。

**验证**：
- 运行 `python -m pytest tests/ -x` 确认所有测试通过
- 确认无 import 错误

---

### 阶段 6：位置节流优化

**文件**：`d:\Project\直播切片多人\lsc\gui\pages\multi_room\page.py`

**变更**：将 `_POSITION_THRESHOLD` 从 `0.5` 改为 `0.2`，提升时间轴拖动时的响应感。

```python
self._POSITION_THRESHOLD = 0.2  # seconds
```

**验证**：
- 启动预览，拖动时间轴，观察时间标签更新是否更流畅

---

## 假设与决策

1. **保留 recording_controller.py**：虽然 RecordPage 被删除，但 `RecordingController`、`AnalysisWorker`、`BatchExportWorker` 被 MultiRoomManager 使用，必须保留。
2. **单房间降级策略**：隐藏批量操作按钮，保留"全部静音"。这是最小化 UI 变更的方案，避免布局重构。
3. **预览无画面修复方案**：选择重写 `resizeEvent` 而非增加更多延迟计时器，因为 `resizeEvent` 是 Qt 布局完成的可靠信号。
4. **静音按钮同步修复**：在 `_refresh`、`_refresh_all`、`_add_card`、`__init__` 中添加 `_sync_mute_all_button()` 调用，覆盖所有状态变更路径。
5. **测试文件处理**：删除 RecordPage 专用测试，不尝试迁移到 MultiRoomPage（因为 MultiRoomPage 已有独立测试覆盖）。

---

## 验证步骤

1. **启动程序**：`python main.py`，确认无 import 错误，侧边栏 3 个导航项
2. **静音同步**：添加 2 房间，点击"全部静音"→"取消静音"→手动取消一个房间静音，观察按钮文字同步
3. **预览画面**：添加房间，点击预览，确认立即显示画面
4. **单房间降级**：添加 1 房间，确认批量按钮隐藏；添加第 2 房间，确认按钮出现
5. **快捷键**：Ctrl+1/2/3 切换页面，Ctrl+T 切换主题
6. **关闭窗口**：确认无报错，无 FFmpeg 孤儿进程
7. **测试套件**：`python -m pytest tests/ -x` 全部通过
