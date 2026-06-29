# 修复录制启动失败"文件写入权限不足"错误信息被吞问题

## 摘要

用户报告"录制启动失败：文件写入权限不足。请检查输出目录权限。"，但该文本是 `humanize_error` 把任何含 `Permission denied/EACCES/WinError 5/拒绝访问` 的原始错误**统一映射**成的笼统提示，真实路径与具体原因被丢弃，导致用户无法定位问题。同时 `manager.py` 调用的 `is_recoverable_error` 函数在 `error_messages.py` 中根本不存在，录制重连逻辑每次都 ImportError 崩溃（后端日志 12:02-12:03 反复出现）。

本次修复：让错误信息透传真实路径与原因，并补齐缺失的 `is_recoverable_error`，使录制失败时用户能看到具体是哪个目录、什么原因（makedirs 失败 / FFmpeg 写文件失败 / 沙箱拦截等），从而自行判断是改目录、关沙箱还是清理权限。

## 当前状态分析

### 错误信息被吞的瓶颈点

[lsc/utils/error_messages.py](file:///d:\Project\直播切片多人\lsc\utils\error_messages.py) 第 29-30 行：

```python
(re.compile(r"Permission denied|EACCES|WinError 5|拒绝访问", re.I),
 "文件写入权限不足。请检查输出目录权限。"),
```

该模式把以下**不同根因**全部映射成同一句笼统文本：
- `preflight_recording` 中 `os.makedirs(output_dir)` 失败 → 返回 `"录制目录不可写：{path}（{strerror}）..."`（含路径，被吞）
- `manager.start_recording` 中 `os.makedirs(room_output_dir)` 失败 → 设置 `room.last_error = "录制目录不可写，请在设置中修改输出目录（{strerror}）"`（含 strerror，被吞）
- `capture.start` 中 `os.makedirs` 失败 → 抛 `RuntimeError("录制目录不可写：{path}（{strerror}）")`（含路径，被吞）
- FFmpeg 写文件 stderr 含 "Permission denied" → 被吞

### 调用链

[python-backend/handlers/room_handler.py](file:///d:\Project\直播切片多人\python-backend\handlers\room_handler.py) `handle_start_recording` 第 605-626 行有两条错误路径，都会经过 `humanize_error`：

```python
try:
    success = await ...run_in_executor(None, _start)
except Exception as exc:
    return {'success': False, 'error': humanize_error(str(exc))}   # 异常路径
...
if not success:
    error_msg = (room.last_error if room else None) or '录制启动失败...'
    return {'success': False, 'error': humanize_error(error_msg)}   # success=False 路径
```

`manager.start_recording` 内部已设置带路径的 `room.last_error`，但 humanize_error 在最后一层把它吞掉。

### is_recoverable_error 缺失

[lsc/gui/multi_room/manager.py:1312](file:///d:\Project\直播切片多人\lsc\gui\multi_room\manager.py#L1312)：

```python
from lsc.utils.error_messages import is_recoverable_error
```

但 `error_messages.py` 只有 `humanize_error` 和 `friendly_connect_error`，**没有** `is_recoverable_error`。后端日志反复出现：

```
2026-06-27 12:02:44 [ERROR] ... reconnect failed: cannot import name 'is_recoverable_error' from 'lsc.utils.error_messages'
```

导致 `_attempt_recording_reconnect` 每次都 ImportError，录制断流后无法自动重连。

### 后端日志无权限记录

最近的后端日志（`C:\Users\Administrator\AppData\Roaming\lsc-electron\logs\backend.log`）中没有任何 `permission/WinError/拒绝访问` 记录，说明用户看到的笼统文本是 humanize_error 在 handler 层映射的结果，真实根因被掩盖。可能的根因包括：Trae IDE 沙箱拦截子进程写文件（memory 硬约束明确要求在 IDE 外运行）、目录被占用、杀毒拦截等——修复信息透传后用户即可自行判断。

## 修复方案

### 改动 1：humanize_error 保留路径与原始错误（核心）

**文件**：[lsc/utils/error_messages.py](file:///d:\Project\直播切片多人\lsc\utils\error_messages.py)

**当前问题**：模式匹配命中后直接返回固定文案，丢弃原始字符串中的路径和 strerror。

**修改思路**：让 `humanize_error` 在命中权限/磁盘类模式时，把原始错误中提取的路径信息追加到友好提示后，格式为 `{友好提示}（原始错误：{raw}）`。对超长原始错误做截断（保持现有 200 字符截断逻辑）。

**具体改动**：
- `_PATTERNS` 中"权限/磁盘/连接失败"等需要保留原始信息的条目，文案改为带占位的形式，或在 `humanize_error` 函数中统一处理：命中后返回 `f"{msg}（原始错误：{raw_truncated}）"`。
- 为避免所有模式都追加（如 403/404 这类网络错误原始信息含长 URL 反而干扰），只对**权限类**与**磁盘类**两条追加原始错误，其它保持原样。
- 实现：把需要追加原始错误的模式标记出来（如改为 dict 结构或第二个列表），或在函数内对这两条单独处理。

**推荐实现**（最小改动）：将权限类与磁盘类两条从 `_PATTERNS` 抽出，在 `humanize_error` 中单独匹配并追加原始错误。其余模式保持现有行为。

伪代码：
```python
# 需要保留原始错误的模式（路径/磁盘相关，原始信息对定位问题关键）
_PRESERVE_RAW_PATTERNS = [
    (re.compile(r"Permission denied|EACCES|WinError 5|拒绝访问", re.I),
     "文件写入权限不足。请检查输出目录权限"),
    (re.compile(r"No space left|ENOSPC|disk full", re.I),
     "磁盘空间不足，无法继续录制。请清理输出目录"),
]

def humanize_error(raw: str) -> str:
    if not raw or not isinstance(raw, str):
        return "发生未知错误"
    raw_stripped = raw.strip()
    # 优先匹配需要保留原始信息的模式
    for pattern, msg in _PRESERVE_RAW_PATTERNS:
        if pattern.search(raw_stripped):
            snippet = raw_stripped[:200] + ("..." if len(raw_stripped) > 200 else "")
            return f"{msg}（原始错误：{snippet}）"
    # 其余模式保持原样
    for pattern, msg in _PATTERNS:
        if pattern.search(raw_stripped):
            return msg
    if len(raw_stripped) > 200:
        raw_stripped = raw_stripped[:200] + "..."
    return f"发生错误：{raw_stripped}"
```

注意：`_PATTERNS` 中需移除这两条避免重复匹配。

### 改动 2：补齐 is_recoverable_error 函数

**文件**：[lsc/utils/error_messages.py](file:///d:\Project\直播切片多人\lsc\utils\error_messages.py)

**当前问题**：`manager.py:1312` import 一个不存在的函数，导致录制重连 ImportError。

**修改思路**：新增 `is_recoverable_error(raw: str) -> bool`，判断错误是否值得重连（网络抖动、流暂时中断可恢复；磁盘满、权限拒绝不可恢复）。

**判断规则**：
- 可恢复：HTTP 5xx、Connection timed out、Connection reset、Stream ends prematurely、Invalid data found（流数据异常但可能恢复）、Error number -138（网络 EOF）
- 不可恢复：Permission denied、WinError 5、No space left、ENOSPC、403 Forbidden（鉴权失败重连也没用）、404 Not Found（主播下播）、Encoder not found

伪代码：
```python
_RECOVERABLE_PATTERNS = [
    re.compile(r"Server returned 5\d\d", re.I),
    re.compile(r"Connection (timed out|refused|reset)", re.I),
    re.compile(r"Stream ends prematurely", re.I),
    re.compile(r"Error number -138", re.I),
    re.compile(r"Invalid data found", re.I),
]
_NON_RECOVERABLE_PATTERNS = [
    re.compile(r"Permission denied|EACCES|WinError 5|拒绝访问", re.I),
    re.compile(r"No space left|ENOSPC|disk full", re.I),
    re.compile(r"403|Forbidden", re.I),
    re.compile(r"404|Not Found", re.I),
    re.compile(r"Encoder.*not found|cannot find encoder", re.I),
]

def is_recoverable_error(raw: str) -> bool:
    """判断录制错误是否值得自动重连。"""
    if not raw or not isinstance(raw, str):
        return False
    # 先判不可恢复（权限/磁盘/鉴权类优先）
    for pattern in _NON_RECOVERABLE_PATTERNS:
        if pattern.search(raw):
            return False
    # 再判可恢复
    for pattern in _RECOVERABLE_PATTERNS:
        if pattern.search(raw):
            return True
    # 默认不可恢复（避免对未知错误无限重连浪费资源）
    return False
```

### 改动 3（可选，验证用）：无代码改动

不改动 `room_handler.py`、`manager.py`、`capture.py` —— 它们设置的 `room.last_error` 和抛出的异常字符串已经包含路径，只是被 humanize_error 吞掉。改动 1 修复 humanize_error 后，这些路径信息会自然透传到前端。

## 假设与决策

1. **不改动调用方**：`manager.py` / `capture.py` / `room_handler.py` 中错误字符串已含路径，无需调整。只改 humanize_error 即可让信息透传。
2. **只对权限类/磁盘类追加原始错误**：网络类错误（403/404/timeout）原始信息含长 URL，追加会干扰阅读，保持原样。
3. **is_recoverable_error 默认返回 False**：对未知错误不重连，避免浪费资源。已知可恢复错误才返回 True。
4. **不修复 IDE 沙箱问题**：那是运行环境问题（memory 硬约束要求在 IDE 外运行），本次只修代码层信息透传。修复后用户能看到真实根因（如 "WinError 5" + 具体路径），自行判断是改目录还是换运行环境。

## 验证步骤

1. **单元测试**：
   - `humanize_error("录制目录不可写：C:\\LSC\\output（Permission denied）")` 应返回含 "C:\\LSC\\output" 和 "Permission denied" 的字符串
   - `humanize_error("WinError 5")` 应返回含 "WinError 5" 的字符串
   - `humanize_error("Server returned 403 Forbidden")` 保持原样（不追加原始错误）
   - `is_recoverable_error("Connection timed out")` → True
   - `is_recoverable_error("Permission denied")` → False
   - `is_recoverable_error("Server returned 404")` → False

2. **集成验证**（在 IDE 外运行）：
   - 重启程序（`npm run dev`，在独立终端而非 Trae IDE 内）
   - 添加房间、连接、开始录制
   - 若仍失败，前端错误提示应显示具体路径和原始错误（如 "文件写入权限不足。请检查输出目录权限（原始错误：录制目录不可写：C:\\Users\\Administrator\\LSC\\output（Permission denied））"），而非笼统的"文件写入权限不足"
   - 据此判断真实根因：若原始错误含 "WinError 5" 且在 IDE 内运行 → 提示用户在 IDE 外运行；若是真实目录权限 → 引导用户在设置中改 output_dir

3. **回归验证**：
   - 录制断流后，后端日志不再出现 `cannot import name 'is_recoverable_error'`
   - 可恢复错误（如网络抖动）会触发自动重连，不可恢复错误（如 403）不会无限重连
