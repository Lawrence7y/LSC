# 可行性分析与实施计划：刷新按键 + 录制结束后继续分析 + 切片重名修复 + UI 布局重构

> 日期：2026-07-11
> 状态：已评审通过

---

## 一、需求概述

| # | 需求 | 可行性 |
|---|------|--------|
| 1 | 页面增加刷新按键，刷新房间状态消除错误，不阻断正在进行的进程 | ✅ 高 |
| 2 | 持续分析在录制结束后继续运行，把未分析完的部分分析完 | ✅ 高 |
| 3 | 修复切片列表中切片重名的 bug | ✅ 高 |
| 4 | 把多直播间页面的录制设置移到设置页面（右侧抽屉），空出位置给切片列表 | ✅ 高 |

---

## 二、需求 1：刷新房间状态按键

### 2.1 现状

- **无刷新按钮**：Workbench 工具栏只有"一键对齐"（SyncOutlined），没有刷新房间状态的按钮。
- **错误状态**：`RoomSession.last_error`（连接/录制错误）、`mse_error`（预览错误）会残留在房间卡片上，即使底层问题已恢复（如重连成功后旧的 `last_error` 未清除）。
- **现有清除路径**：`RoomCard.tsx:353` 的"重试"按钮只清除前端 `mse_error`，不触发后端重连或状态刷新。
- **`get_rooms`**：`room_handler.py:1854` 返回当前后端状态，但不清除错误。

### 2.2 根因

没有专门的后端 handler 来清除 `last_error` 等错误字段。前端也没有刷新按钮。

### 2.3 实施方案

#### 后端：新增 `refresh_room_status` handler

**文件**：`python-backend/handlers/room_handler.py`（在 `get_rooms` handler 附近，~line 1854）

```python
@server.on('refresh_room_status')
async def handle_refresh_room_status(data):
    """刷新房间状态：清除错误标记，不阻断正在进行的录制/预览/分析。

    只清除 last_error / mse_error / preview_error 等瞬态错误字段，
    不触碰 is_recording / is_reconnecting / is_connecting 等运行状态。
    """
    room_id = data.get('room_id')  # 可选：指定房间；不传则刷新全部
    rooms_to_refresh = []
    if room_id:
        room = manager.get_room(room_id)
        if room:
            rooms_to_refresh = [room]
    else:
        rooms_to_refresh = list(manager._rooms.values())

    refreshed = 0
    for room in rooms_to_refresh:
        # 正在重连的房间保留错误信息（用户需要看到重连进度）
        if getattr(room, 'is_reconnecting', False):
            continue
        if getattr(room, 'last_error', None):
            room.last_error = None
            refreshed += 1
        if getattr(room, 'preview_error', None):
            room.preview_error = None
            refreshed += 1

    await _broadcast_rooms()
    return {'success': True, 'refreshed': refreshed}
```

**关键安全保证**：
- ❌ 不调用 `disconnect_room`（会停止录制并重置所有状态）
- ❌ 不调用 `connect_room`（会重新解析流地址）
- ❌ 不触碰 `is_recording`、`is_reconnecting`、`is_connecting`
- ❌ 不触碰 `_continuous_tasks`（持续分析不受影响）
- ✅ 只清除 `last_error` / `preview_error` 字段
- ✅ 正在重连的房间跳过（保留错误信息供用户查看进度）

#### 前端：工具栏增加刷新按钮

**文件**：`lsc-electron/src/pages/Workbench/index.tsx`（工具栏区域，~line 1544 "一键对齐" 按钮旁边）

```tsx
<Button
  icon={<ReloadOutlined />}
  onClick={() => send('refresh_room_status', {})}
  title="刷新房间状态（清除错误标记，不影响录制/预览/分析）"
>
  刷新
</Button>
```

### 2.4 涉及文件

| 文件 | 改动 |
|------|------|
| `python-backend/handlers/room_handler.py` | 新增 `refresh_room_status` handler |
| `lsc-electron/src/pages/Workbench/index.tsx` | 工具栏增加刷新按钮 |

---

## 三、需求 2：录制结束后继续分析

### 3.1 现状

- **持续分析与录制完全解耦**：`_continuous_analysis_loop` 只检查 `cancelled` 标志，不检查 `is_recording`。停止录制不会停止分析，但也不会触发"收尾分析"。
- **滞后性**：OCR 全量扫描耗时长（19分钟视频 ~3-6分钟），录制停止时最后一个 tick 的扫描可能还没完成。
- **尾部丢失**：`_drop_open_tail_rounds` 丢弃贴着录制尾部的未闭合回合。录制停止后这些回合变为"已闭合"但不会被重新扫描，因为 `current_dur > last_analyzed + 5.0` 条件不再满足（current_dur 不再增长）。
- **无收尾机制**：录制停止后，循环继续空转，永远不会退出，也不会再触发扫描。

### 3.2 根因

1. 循环的 kick 条件 `current_dur > last_analyzed + 5.0` 依赖录制文件持续增长，录制停止后此条件永远为 False。
2. `_drop_open_tail_rounds` 在录制中丢弃尾部回合是合理的（数据不完整），但录制停止后应保留这些回合。
3. 没有检测"录制已停止但分析未完成"的状态来触发最终扫描。

### 3.3 实施方案：收尾分析（Drain 模式）

**核心思路**：在 `_continuous_analysis_loop` 中检测录制停止，进入"收尾模式"--强制再扫一次完整文件，不丢弃尾部回合，扫描完成后自动退出循环。

**文件**：`python-backend/handlers/room_handler.py`，`_continuous_analysis_loop` 函数（~line 4151）

#### 改动 1：增加收尾状态追踪

在循环初始化区域（~line 4172）增加：
```python
_finalize_pending = False        # 是否有待完成的收尾扫描
_finalize_started = False        # 收尾扫描是否已启动
_recording_was_active = False    # 录制是否曾经处于活跃状态
_recording_stop_ticks = 0        # 录制停止后经过的 tick 数（延迟确认防抖）
```

#### 改动 2：检测录制停止并触发收尾

在主循环 tick 中（~line 4299，获取 video_path/current_dur 之后）：
```python
room_obj = manager.get_room(room_id)
is_still_recording = bool(room_obj and getattr(room_obj, 'is_recording', False))

if is_still_recording:
    _recording_was_active = True
    _recording_stop_ticks = 0
elif _recording_was_active:
    _recording_stop_ticks += 1
    # 延迟 2 个 tick 确认录制真的停止了（防网络抖动误触发）
    if _recording_stop_ticks >= 2 and not _finalize_started:
        _finalize_pending = True
        _log.info("持续分析收尾: 录制已停止，触发最终完整扫描 room_id=%s", room_id)
```

#### 改动 3：收尾模式下强制扫描 + 不丢弃尾部

在消费结果区域（~line 4244）：
```python
if _valorant_incremental_rounds:
    if _finalize_pending:
        # 收尾模式：不丢弃尾部回合（录制已结束，尾部数据已完整）
        window_rounds = list(new_hl)
        for r in window_rounds:
            if r.get("phase") == "pending":
                r["phase"] = "combat"
    else:
        window_rounds = _drop_open_tail_rounds(new_hl, worker_dur)
    # ... 后续 _merge_round_windows 逻辑不变
```

#### 改动 4：收尾模式下绕过增长检查

在 kick worker 条件（~line 4328）：
```python
should_kick = False
if video_path:
    if _finalize_pending and not _finalize_started:
        should_kick = True
        _finalize_started = True
    elif current_dur > last_analyzed + 5.0:
        should_kick = True
```

#### 改动 5：收尾完成后退出循环

在消费结果完成后（~line 4284）：
```python
if _finalize_started and _finalize_pending:
    if worker_dur <= last_analyzed + 5.0:
        _finalize_pending = False
        _log.info("持续分析收尾完成: room_id=%s, 累计 %d 段", room_id, len(all_highlights))
        if room_id in _continuous_tasks:
            _continuous_tasks[room_id]['cancelled'] = True
        bridge.queue_broadcast({
            'type': 'continuous_analysis_complete',
            'data': {'room_id': room_id, 'total_highlights': len(all_highlights)},
        })
```

#### 改动 6：前端显示收尾状态

**文件**：`lsc-electron/src/pages/Workbench/index.tsx`
```tsx
unsubs.push(on('continuous_analysis_complete', (data: any) => {
  message.success(`录制结束分析完成：共 ${data.total_highlights} 个回合`)
  setContinuousAnalyzing(false)
}))
```

### 3.4 边界情况处理

| 场景 | 处理 |
|------|------|
| 用户手动停止持续分析 | `cancelled=True`，循环退出，不触发收尾 |
| 网络断开重连导致 is_recording 短暂 False | 延迟 2 个 tick 确认，防抖 |
| 收尾扫描中用户重新开始录制 | 检查 is_recording 恢复为 True 时取消收尾 |
| 收尾扫描超时 | 现有 scan_timeout 机制处理，下个 tick 重试 |

### 3.5 涉及文件

| 文件 | 改动 |
|------|------|
| `python-backend/handlers/room_handler.py` | `_continuous_analysis_loop` 增加收尾逻辑 |
| `lsc-electron/src/pages/Workbench/index.tsx` | 增加 `continuous_analysis_complete` 事件监听 |

---

## 四、需求 3：修复切片重名 bug

### 4.1 根因（4 个独立原因）

**根因 1：前端三个独立路径添加切片，label 格式不同且 clip_id 基准不同**

| 路径 | label 格式 | clip_id 时间基准 |
|------|-----------|----------------|
| `continuous_highlights`（index.tsx:1236） | `{streamer} - 高光 {i+1}` | 主房间时间 |
| `highlight_stream`（index.tsx:1297） | `{streamer} - 高光 {total}` | 主房间时间 |
| `clip_queued`（index.tsx:1322） | `{room_name}_回合{round_idx}` | 目标房间映射后时间 |

多房间时 `delta != 0`，同一回合通过 `highlight_stream` 添加的 clip_id（主房间时间）与通过 `clip_queued` 添加的 clip_id（映射后时间）不同，`addClip` 的 clip_id 去重失效。

**根因 2：`round_index` 每次全量重扫重置**

`round_index` 是单次检测运行内的序号，不是全局单调 ID。全量重扫时新检测到早期回合导致序号偏移。

**根因 3：`_exported_clip_ids` 用 1 位小数时间，边界微调导致 cid 变化**

OCR/钟声精修使边界偏移 >0.05s 时 cid 改变，同一回合被当作新回合重复导出。

**根因 4：磁盘文件重名靠 `_1.mp4` 后缀，逻辑名仍重复**

### 4.2 实施方案

#### 修复 1：前端 `highlight_stream` 不再添加切片到列表

**文件**：`lsc-electron/src/pages/Workbench/index.tsx`（~line 1297-1315）

`highlight_stream` 只用于实时通知，不添加切片（切片由 `clip_queued` 统一添加）。

#### 修复 2：前端 `continuous_highlights` 不再添加切片到列表

**文件**：`lsc-electron/src/pages/Workbench/index.tsx`（~line 1236-1289）

删除 fallback 分支的 `_newClips` 构建和 `addClip` 调用，只保留通知。

#### 修复 3：后端 `_exported_clip_ids` 用整数秒去重

**文件**：`python-backend/handlers/room_handler.py`（~line 4277）

```python
h_start_int = int(round(float(h.get('start', 0))))
h_end_int = int(round(float(h.get('end', 0))))
cid = f"{room_id}_{h_start_int}_{h_end_int}"
```

#### 修复 4：后端 label 加时间戳后缀确保文件名唯一

**文件**：`python-backend/handlers/room_handler.py`，`_auto_export_highlights`（~line 3826）

```python
label = f"{room_name}_回合{round_idx}_{int(export_start)}s"
```

### 4.3 涉及文件

| 文件 | 改动 |
|------|------|
| `lsc-electron/src/pages/Workbench/index.tsx` | `highlight_stream`/`continuous_highlights` 不再添加切片 |
| `python-backend/handlers/room_handler.py` | `_exported_clip_ids` 整数秒去重；label 加时间戳后缀 |

---

## 五、需求 4：录制设置移入设置页面

### 5.1 现状

```
Workbench 右侧面板（width: 320px）：
┌──────────────────┐
│  添加房间卡片     │  ~60px
├──────────────────┤
│  RecordSettings   │  ~280-340px  ← 要移除
├──────────────────┤
│  ClipList         │  flex:1      ← 自动扩展
└──────────────────┘
```

- 设置页面（右侧 Drawer，width: 480px）**已经有完整的"录制设置" section**（Settings/index.tsx:284-472），管理完全相同的 `settings` store 状态，且是 RecordSettings 的超集。
- RecordSettings 组件无 props、自包含，只在 `Workbench/index.tsx:1696` 渲染一次。

### 5.2 可行性：极高

这本质上是一个删除操作：
1. 设置页面已有功能完整的录制设置，不需要"合并"任何东西
2. RecordSettings 无 props、无外部消费者，只需删除 import 和一行 JSX
3. ClipList 有 `flex:1`，移除 RecordSettings 后自动向上扩展
4. 设置 Drawer 480px > Workbench 右侧 320px，录制设置反而更宽

### 5.3 实施方案

#### 改动 1：Workbench 移除 RecordSettings

**文件**：`lsc-electron/src/pages/Workbench/index.tsx`

1. 删除 import（~line 10）
2. 删除渲染（~line 1696）

#### 改动 2：增加快捷入口按钮

在添加房间卡片下方或工具栏增加"录制设置"快捷按钮，点击打开设置 Drawer：
```tsx
<Button
  size="small"
  icon={<SettingOutlined />}
  onClick={() => useAppStore.getState().setSettingsDrawerOpen(true)}
>
  录制设置
</Button>
```

### 5.4 涉及文件

| 文件 | 改动 |
|------|------|
| `lsc-electron/src/pages/Workbench/index.tsx` | 删除 RecordSettings import 和渲染；增加快捷入口 |
| `lsc-electron/src/pages/Workbench/components/RecordSettings.tsx` | 保留不删除（不再被 import） |
| `lsc-electron/src/pages/Settings/index.tsx` | 不需要改动（已有完整录制设置） |

---

## 六、实施顺序

```
需求 3（切片重名修复）→ 需求 1（刷新按键）→ 需求 4（UI 重构）→ 需求 2（收尾分析）
```

先修 bug 再加功能，UI 重构在功能修复后避免冲突。

---

## 七、测试计划

### 需求 1 测试
- 启动录制+预览，断开网络触发连接错误，点击刷新按钮，验证错误标记清除且录制不中断
- 持续分析进行中点击刷新，验证分析不受影响

### 需求 2 测试
- 启动持续分析+录制，录制 5 分钟后停止，验证分析继续运行直到全部完成
- 验证收尾扫描不丢弃尾部回合
- 验证收尾完成后循环自动退出

### 需求 3 测试
- 启动多房间持续分析，验证切片列表中同一回合只出现一次
- 验证导出的文件名不重复
- 验证 `highlight_stream` 事件不再添加切片到列表

### 需求 4 测试
- 移除 RecordSettings 后验证 ClipList 自动扩展
- 在设置 Drawer 中修改录制设置，验证保存生效
- 验证多直播间页面和设置页面的录制参数同步

---

## 八、风险与缓解

| 风险 | 缓解 |
|------|------|
| 收尾分析误触发（网络抖动） | 延迟 2 个 tick 确认录制停止 |
| 移除 highlight_stream 切片添加后用户看不到实时高光 | `clip_queued` 在导出时即时添加，延迟仅几秒 |
| `_exported_clip_ids` 整数秒去重误判不同回合 | 回合间至少间隔 25s，不会碰撞 |
| 设置页面录制设置样式与原 RecordSettings 不一致 | 设置页面已有完整字段，样式差异不影响功能 |
