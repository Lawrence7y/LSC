# 修复录制启动失败：文件写入权限不足

## 背景与现状

用户报告：录制启动失败，显示"文件写入权限不足。请检查输出目录权限。"

### 根因分析

错误消息来自 [error_messages.py#L29-L30](file:///d:/Project/直播切片多人/lsc/utils/error_messages.py#L29-L30) 的 `humanize_error`，匹配模式 `Permission denied|EACCES|WinError 5|拒绝访问`。

**完整调用链**：

1. [room_handler.py#L555](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py#L555)：从 `settings.json` 读取 `output_dir = "~/LSC/output"`（展开为 `C:\Users\Administrator\LSC\output`）
2. [room_handler.py#L573](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py#L573)：调用 `manager.start_recording(room_id, output_dir, ...)`
3. [manager.py#L1056](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py#L1056)：调用 `preflight_recording(output_dir, ...)`
4. [recording_controller.py#L301](file:///d:/Project/直播切片多人/lsc/gui/pages/recording_controller.py#L301)：`os.makedirs(output_dir, exist_ok=True)` 抛 `OSError`（权限不足）
5. [recording_controller.py#L302-303](file:///d:/Project/直播切片多人/lsc/gui/pages/recording_controller.py#L302-L303)：返回 `"录制目录不可写：C:\Users\Administrator\LSC\output（拒绝访问。）。请在设置中修改输出目录。"`
6. [manager.py#L1057-1060](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py#L1057-L1060)：设置 `room.last_error = preflight` 并 **return False**（★ 关键：此处直接返回，**走不到第 1074-1086 行的目录回退逻辑**）
7. [room_handler.py#L598-600](file:///d:/Project/直播切片多人/python-backend/handlers/room_handler.py#L598-L600)：获取 `room.last_error`，调用 `humanize_error(error_msg)`
8. [error_messages.py#L29](file:///d:/Project/直播切片多人/lsc/utils/error_messages.py#L29)：匹配到 `room.last_error` 中的"拒绝访问"（来自 `exc.strerror`），返回"文件写入权限不足。请检查输出目录权限。"

**关键缺陷**：

`manager.start_recording` 的目录回退逻辑（[manager.py#L1074-L1086](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py#L1074-L1086)）在 `preflight_recording` **之后**执行。当 `output_dir` 不可写时，`preflight_recording` 直接返回错误，`manager.start_recording` 第 1057-1060 行立即 `return False`，**永远走不到后面的回退逻辑**。

前一会话的修复（添加 `~/.lsc/output` 回退）只覆盖了 `_make_room_output_dir` 创建子目录的失败场景，没有覆盖 `preflight_recording` 的失败场景。

## 修复方案

将目录回退逻辑**提前到 preflight 检查阶段**：如果 `preflight_recording` 因目录不可写或磁盘空间不足失败，尝试回退到 `~/.lsc/output` 后重新 preflight。

### 修改 1：manager.py 的 start_recording 方法

**文件**：[d:\Project\直播切片多人\lsc\gui\multi_room\manager.py](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py)

**位置**：[manager.py#L1054-L1060](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py#L1054-L1060)

**当前代码**：
```python
# Pre-flight disk space check (2GB threshold per project memory constraint)
from lsc.gui.pages.recording_controller import RecordingController
preflight = RecordingController.preflight_recording(output_dir, concurrent_streams=1)
if preflight:
    room.last_error = preflight
    _log.warning("录制预检失败: %s", preflight)
    return False
```

**修改后**：
```python
# Pre-flight disk space check (2GB threshold per project memory constraint)
from lsc.gui.pages.recording_controller import RecordingController
preflight = RecordingController.preflight_recording(output_dir, concurrent_streams=1)
if preflight:
    # 默认目录不可写或空间不足，回退到 ~/.lsc/output（用户主目录，通常可写）
    fallback_base = os.path.join(os.path.expanduser('~'), '.lsc', 'output')
    if os.path.abspath(fallback_base) != os.path.abspath(output_dir):
        _log.warning("预检失败 %s，回退到 %s", output_dir, fallback_base)
        fallback_preflight = RecordingController.preflight_recording(fallback_base, concurrent_streams=1)
        if not fallback_preflight:
            output_dir = fallback_base
            preflight = ""
        else:
            _log.warning("回退目录预检也失败: %s", fallback_preflight)
    if preflight:
        room.last_error = preflight
        _log.warning("录制预检失败: %s", preflight)
        return False
```

**为什么**：
- 在 preflight 阶段就尝试回退，避免直接返回错误
- `os.path.abspath(fallback_base) != os.path.abspath(output_dir)` 防止 `output_dir` 本身就是回退目录时重复检查
- 回退目录通过 preflight 后，更新 `output_dir` 变量，让后续的 `_make_room_output_dir` 和 `controller.start_recording_with_crf` 使用回退目录
- 回退目录也失败时，保留原始 preflight 错误返回（此时 `room.last_error` 会是更具体的"录制目录不可写"或"磁盘空间不足"消息，而不是被 humanize 成笼统的"文件写入权限不足"）

**副作用考虑**：
- 第 1074-1086 行的 `_make_room_output_dir` + makedirs 回退逻辑仍然保留，作为子目录创建失败的二次兜底（虽然 preflight 已确保 base dir 可写，但子目录创建可能因其他原因失败）
- `room.reconnect_output_dir` 会记录回退后的目录，断线重连时使用相同目录（[manager.py#L1108](file:///d:/Project/直播切片多人/lsc/gui/multi_room/manager.py#L1108)），保持一致性

## 验证步骤

1. **单元测试**（可选）：
   ```bash
   cd "d:\Project\直播切片多人" && python -c "
   from lsc.gui.pages.recording_controller import RecordingController
   # 测试不可写目录
   print('test1:', RecordingController.preflight_recording('Z:\\nonexistent\\path', 1))
   # 测试回退目录
   print('test2:', RecordingController.preflight_recording('C:/Users/Administrator/.lsc/output', 1))
   "
   ```

2. **重启程序验证**：
   - 重启 Electron 前端（Python 后端会自动重启）
   - 添加房间 → 连接 → 点击录制
   - 预期：录制成功启动，文件保存到 `C:\Users\Administrator\.lsc\output\{platform}_{streamer}_{short_id}\` 目录
   - 不再显示"文件写入权限不足"错误

3. **日志验证**：
   - 查看后端日志，应看到 `预检失败 ... 回退到 ...` 警告
   - 确认回退目录通过 preflight

## 假设与决策

1. **不修改 `preflight_recording` 接口**：保持其为无副作用的静态检查方法，回退逻辑在调用方处理。这样 `preflight_recording` 可被其他调用方（如批量录制）复用。
2. **不修改 `settings.json` 的 `output_dir`**：用户配置保持不变，仅在运行时回退。这样用户修改输出目录后仍可生效。
3. **回退目录固定为 `~/.lsc/output`**：与前一会话的回退逻辑保持一致，用户主目录通常可写。
4. **不修复 `humanize_error` 的过度匹配**：`room.last_error` 包含"拒绝访问"时会被 humanize 成"文件写入权限不足"，虽然不够精确但用户可理解。修复后 preflight 失败会直接返回"录制目录不可写"消息（不含"拒绝访问"关键词），不会被 humanize 覆盖。
