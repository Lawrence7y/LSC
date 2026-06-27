# 修复录制功能 WinError 5 拒绝访问

## Summary

录制功能报"拒绝访问 winerror 5"的根因是：录制输出目录 `~/LSC/output`（展开为 `C:\Users\Administrator\LSC\output`）在当前进程令牌下不可写，Python `os.makedirs` 抛出 `PermissionError: [WinError 5]`，而代码中多处 `makedirs` 调用**没有 try/except 兜底**，异常直接冒泡到前端。

此外，`humanize_error` 的正则模式 `r"Permission denied|EACCES"` **匹配不到中文"拒绝访问"和"WinError 5"**，导致错误消息被原样透传为 `发生错误：[WinError 5] 拒绝访问。: 'C:\\Users\\...\\LSC\\output'`，用户难以理解。

之前有一份 spec（`.trae/specs/fix-record-perm-room-persist-preview-audio/`）声称已修复此问题，但**代码实际未落地**——`manager.py:1074` 和 `recording_controller.py:300` 的 `makedirs` 仍然没有 try/except 和目录回退逻辑。

## Current State Analysis

### 错误传播链路
```
[环境层] Trae IDE 沙箱 / 受限令牌（CodexSandboxUsers 组只有 ReadAndExecute 权限）
    ↓
[配置层] settings.json: output_dir = "~/LSC/output" → C:\Users\Administrator\LSC\output
    ↓
[代码层] recording_controller.py:300  os.makedirs(output_dir, exist_ok=True)  ★ 无 try/except
    ↓ (或 manager.py:1074  os.makedirs(room_output_dir, exist_ok=True)  ★ 无 try/except)
[异常] PermissionError: [WinError 5] 拒绝访问
    ↓
[传播] room_handler.py:580  except Exception as exc → humanize_error(str(exc))
    ↓
[缺口] error_messages.py:29  正则 r"Permission denied|EACCES" 匹配不到中文"拒绝访问"
    ↓
[兜底] error_messages.py:77  return f"发生错误：{raw_stripped}"
    ↓
[前端] 显示 "发生错误：[WinError 5] 拒绝访问。: 'C:\\Users\\...\\LSC\\output'"
```

### 关键代码位置（已通过 Read 确认）

1. **`lsc/gui/pages/recording_controller.py:300`** - `preflight_recording` 方法的 `os.makedirs(output_dir, exist_ok=True)`，无 try/except
2. **`lsc/gui/multi_room/manager.py:1074`** - `start_recording` 方法的 `os.makedirs(room_output_dir, exist_ok=True)`，无 try/except
3. **`lsc/recorder/capture.py:277`** - `StreamCapture.start` 的 `os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)`，无 try/except
4. **`lsc/utils/error_messages.py:29`** - 正则 `r"Permission denied|EACCES"` 不匹配中文错误
5. **`python-backend/handlers/room_handler.py:578-582`** - 已有 try/except 捕获并调用 `humanize_error`

### 已确认的 spec 与代码脱节
- `.trae/specs/fix-record-perm-room-persist-preview-audio/spec.md:23` 声称修改 `_start_recording_sync` 第 1589-1593 行
- 实际 `manager.py` 中**不存在** `_start_recording_sync` 方法（录制入口是 `start_recording`，第 1041 行）
- spec 描述的"回退到 `~/.lsc/output`"逻辑**完全未落地**

## Proposed Changes

### 文件 1: `d:\Project\直播切片多人\lsc\utils\error_messages.py`

#### 修改 1: 扩展权限错误正则模式

**位置**：第 29 行 `_PATTERNS` 列表

**改动**：将 `r"Permission denied|EACCES"` 改为 `r"Permission denied|EACCES|WinError 5|拒绝访问"`

**为什么**：
- 中文 Windows 抛出的 `PermissionError` 格式为 `[WinError 5] 拒绝访问。: 'path'`
- 当前正则匹配不到"拒绝访问"和"WinError 5"，走兜底分支原样透传
- 修改后能映射到友好消息"文件写入权限不足。请检查输出目录权限。"

### 文件 2: `d:\Project\直播切片多人\lsc\gui\pages\recording_controller.py`

#### 修改 2: `preflight_recording` 的 makedirs 添加 try/except

**位置**：第 300 行 `os.makedirs(output_dir, exist_ok=True)`

**改动**：用 try/except 包裹，捕获 `OSError`（含 `PermissionError`），失败时返回友好错误消息

**修改后逻辑**：
```python
try:
    os.makedirs(output_dir, exist_ok=True)
except OSError as exc:
    return f"录制目录不可写：{output_dir}（{exc.strerror or exc}）。请在设置中修改输出目录。"
```

**为什么**：
- `preflight_recording` 返回非空字符串表示预检失败，`manager.py:1057-1060` 会把错误消息设置到 `room.last_error` 并返回 False
- 这样不会抛异常，错误消息能直接传达给用户，而不是走 `humanize_error` 兜底

### 文件 3: `d:\Project\直播切片多人\lsc\gui\multi_room\manager.py`

#### 修改 3: `start_recording` 的 makedirs 添加目录回退

**位置**：第 1067-1074 行（`_make_room_output_dir` 和 `os.makedirs`）

**改动**：
- 用 try/except 包裹第 1074 行的 `os.makedirs(room_output_dir, exist_ok=True)`
- 捕获 `OSError` 后，回退到 `~/.lsc/output/{room_dir_name}`（用户主目录下的 `.lsc` 隐藏目录，沙箱可写）
- 回退目录也创建失败时，设置 `room.last_error` 并返回 False

**修改后逻辑**：
```python
room_output_dir = _make_room_output_dir(output_dir, room)
original_room_output_dir = room_output_dir
suffix = 1
while os.path.exists(room_output_dir):
    room_output_dir = f"{original_room_output_dir}_{suffix}"
    suffix += 1

try:
    os.makedirs(room_output_dir, exist_ok=True)
except OSError:
    # 默认目录不可写（如沙箱环境），回退到 ~/.lsc/output
    fallback_base = os.path.join(os.path.expanduser('~'), '.lsc', 'output')
    fallback_dir = os.path.join(fallback_base, os.path.basename(room_output_dir))
    _log.warning("录制目录不可写 %s，回退到 %s", room_output_dir, fallback_dir)
    room_output_dir = fallback_dir
    try:
        os.makedirs(room_output_dir, exist_ok=True)
    except OSError as exc:
        room.last_error = f"录制目录不可写，请在设置中修改输出目录（{exc.strerror or exc}）"
        return False
```

**为什么**：
- 沙箱环境下 `C:\Users\Administrator\LSC\output` 不可写，但 `~/.lsc/output`（即 `C:\Users\Administrator\.lsc\output`）可写
- `~/.lsc` 目录已被项目其他模块使用（如 `rooms.json` 存储在 `~/.lsc/python/`），是已知可写的位置
- 回退后 `room_output_dir` 变量指向新路径，后续 `controller.start_recording_with_crf` 会用这个路径录制

### 文件 4: `d:\Project\直播切片多人\lsc\recorder\capture.py`

#### 修改 4: `StreamCapture.start` 的 makedirs 添加 try/except

**位置**：第 277 行 `os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)`

**改动**：用 try/except 包裹，捕获 `OSError`，失败时抛出带友好消息的 `RuntimeError`

**修改后逻辑**：
```python
output_dir_path = os.path.dirname(output_path) or "."
try:
    os.makedirs(output_dir_path, exist_ok=True)
except OSError as exc:
    raise RuntimeError(f"录制目录不可写：{output_dir_path}（{exc.strerror or exc}）") from exc
```

**为什么**：
- `capture.py` 是录制链路的最底层，`manager.py` 和 `recording_controller.py` 的 makedirs 已处理，但作为防御性编程，这里也要兜底
- 抛出 `RuntimeError` 而非 `PermissionError`，让 `humanize_error` 能匹配"录制目录不可写"
- 保留 `from exc` 链以保留原始异常信息用于日志

## Assumptions & Decisions

### 假设
1. **`~/.lsc` 目录可写**：项目硬约束提到 `rooms.json` 存储在 `~/.lsc/python/`，说明该目录在沙箱下可写
2. **WinError 5 的主要触发点是 `os.makedirs`**：而非 FFmpeg 子进程（FFmpeg 子进程用 `subprocess.Popen` 传 list，不会触发 Python 层的 PermissionError）
3. **用户可能在 IDE 内启动程序**：虽然有硬约束要求在 IDE 外启动，但实际用户可能仍在 IDE 内启动，需要代码层面兜底

### 决策
1. **回退目录选择 `~/.lsc/output` 而非 `~/.lsc/python/output`**：避免与 `rooms.json` 所在的 `~/.lsc/python/` 混淆，`~/.lsc/output` 是独立的录制产物目录
2. **`preflight_recording` 返回错误字符串而非抛异常**：符合现有接口契约（返回非空字符串表示错误）
3. **`capture.py` 抛 RuntimeError 而非 PermissionError**：让 `humanize_error` 能匹配"录制目录不可写"模式，且 `RuntimeError` 携带友好消息
4. **不修改 `settings.json` 的默认 `output_dir`**：保持 `~/LSC/output` 作为默认值，仅在运行时回退；用户在非沙箱环境下仍使用正常目录
5. **不修改 Electron `main.ts` 的 `safeEnv`**：`USERPROFILE` 已在白名单中，`detached: true` 已设置，环境变量不是问题根因

## Verification steps

### 1. Python 语法验证
```powershell
cd "d:\Project\直播切片多人"; python -c "import lsc.utils.error_messages; import lsc.gui.pages.recording_controller; import lsc.gui.multi_room.manager; import lsc.recorder.capture; print('OK')"
```
预期：输出 `OK`

### 2. 单元测试验证
```powershell
cd "d:\Project\直播切片多人"; python -m pytest tests/test_recording_controller_options.py tests/test_multi_room_manager.py -v
```
预期：现有测试通过

### 3. humanize_error 验证
```powershell
cd "d:\Project\直播切片多人"; python -c "from lsc.utils.error_messages import humanize_error; print(humanize_error(\"[WinError 5] 拒绝访问。: 'C:\\\\Users\\\\Administrator\\\\LSC\\\\output'\"))"
```
预期：输出 `文件写入权限不足。请检查输出目录权限。`

### 4. 重启程序验证
- 终止现有 electron / python 进程
- 在新 PowerShell 窗口启动 `npm run dev`
- 等待 Python 后端启动完成

### 5. 录制功能验证（沙箱环境）
- 在 Electron 窗口添加房间并连接
- 点击"开始录制"按钮
- 预期结果：
  - 不再出现"拒绝访问 winerror 5"错误
  - 录制成功启动，房间卡片显示"录制中"状态
  - 检查 `C:\Users\Administrator\.lsc\output\` 目录下应出现录制文件
  - 后端日志应出现 `录制目录不可写 ...，回退到 ...` 警告日志

### 6. 录制功能验证（非沙箱环境）
- 在 IDE 外启动程序
- 点击"开始录制"按钮
- 预期结果：
  - 录制文件出现在 `C:\Users\Administrator\LSC\output\` 目录
  - 不触发目录回退逻辑

### 7. 错误消息验证
- 如果回退目录也不可写（手动设置 `~/.lsc/output` 为只读）
- 预期：前端显示"录制目录不可写，请在设置中修改输出目录"
