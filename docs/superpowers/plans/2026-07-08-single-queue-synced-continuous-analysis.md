# Single-Queue Synced Continuous Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将持续分析改为全局单并发队列，支持多房间先音频对齐、选择主直播间分析、再按 `content_offset` 把同一批高光片段同步落到所有选中直播间。

**Architecture:** 复用项目已有的“一键对齐”和“一次性同步分析导出”语义：只分析主直播间，其他直播间只做时间映射。后端在 `room_handler.py` 中提取同步校验与高光映射 helper，持续分析启动时只允许一个任务存在；前端在 Workbench 里新增持续分析主直播间选择 Modal，并让 `continuous_highlights` 支持 `mapped_highlights_by_room` 批量写入 clips。

**Tech Stack:** Python 3 / PySide6 / asyncio / pytest / TypeScript / React / Zustand / antd / Electron

---

## 项目现状确认

### 已存在能力

| 能力 | 当前位置 | 现状 |
|---|---|---|
| 多房间音频对齐 | `python-backend/handlers/room_handler.py:1978`、`lsc/gui/multi_room/manager.py:1139` | 前端触发 `align_audio`，后端调用 `manager.start_audio_align(room_ids)` |
| 对齐结果落房间状态 | `lsc/gui/multi_room/manager.py:1205` | 成功后为每个房间写入 `room.content_offset` 与 `room.align_group_id` |
| 一次性同步分析导出 | `python-backend/handlers/room_handler.py:3108` | 已支持 `main_room_id`、`target_room_ids`，分析主房间后用 `offset_main - target_offset` 映射并批量导出 |
| 前端同步分析导出 Modal | `lsc-electron/src/pages/Workbench/index.tsx:1476` | 已有主直播间 Radio、分析模式、导出预设 |
| 持续分析 | `python-backend/handlers/room_handler.py:3589` | 当前只收 `room_id`，按房间创建任务，未实现全局单并发和多房间同步映射 |
| 前端持续分析按钮 | `lsc-electron/src/pages/Workbench/index.tsx:1226` | 当前依赖 `selectedRoomId`，多选时不可用，点击后直接启动单房间分析 |
| 前端持续分析结果导入 | `lsc-electron/src/pages/Workbench/index.tsx:1038` | 当前只读取 `data.room_id` 与 `data.highlights`，只写入一个房间的 clips |

### 目标行为

1. 用户先多选直播间并点击“一键对齐”。
2. 用户点击“持续分析”。
3. 系统弹出主直播间选择 Modal。
4. 后端只对主直播间运行持续分析。
5. 后端每轮把主直播间高光按 `content_offset` 映射到所有选中并已对齐的目标直播间。
6. 前端把每个目标直播间的映射高光写入各自 clips。
7. 用户导出时，选中的直播间都会按相同内容时间点导出。
8. 系统同一时间只允许一个持续分析任务，避免多房间同时跑分析导致 CPU 和内存压力飙升。

---

## 文件结构总览

### 新增文件

| 文件 | 职责 |
|---|---|
| `tests/test_synced_continuous_analysis.py` | 验证同步目标校验、`content_offset` 高光映射、持续分析单并发入口、前端源码关键接入点 |

### 修改文件

| 文件 | 改动内容 |
|---|---|
| `python-backend/handlers/room_handler.py` | 提取同步目标校验与高光映射 helper；让一次性同步导出复用 helper；持续分析扩展 `main_room_id` / `target_room_ids`；限制全局单任务；广播 `mapped_highlights_by_room` |
| `lsc-electron/src/pages/Workbench/index.tsx` | 新增持续分析 Modal 状态和确认函数；多选时允许启动持续分析；处理 `mapped_highlights_by_room` 写入多房间 clips |

---

## Task 1: 后端同步映射 helper 的失败测试

**Files:**
- Create: `tests/test_synced_continuous_analysis.py`

- [ ] **Step 1: 写入失败测试**

创建 `tests/test_synced_continuous_analysis.py`，内容如下：

```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from handlers import room_handler


ROOT = Path(__file__).resolve().parents[1]


class _FakeManager:
    def __init__(self, rooms):
        self._rooms = {room.room_id: room for room in rooms}

    def get_room(self, room_id: str):
        return self._rooms.get(room_id)


def _room(tmp_path, room_id: str, offset: float, group: str = "group-a"):
    video = tmp_path / f"{room_id}.mp4"
    video.write_bytes(b"fake-video")
    return SimpleNamespace(
        room_id=room_id,
        streamer_name=room_id,
        record_output_path=str(video),
        content_offset=offset,
        align_group_id=group,
    )


def test_validate_synced_targets_accepts_same_align_group(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=10.0)
    side = _room(tmp_path, "side", offset=3.0)
    manager = _FakeManager([main, side])

    ok, error, resolved_main, target_rooms = room_handler._validate_synced_analysis_targets(
        manager,
        "main",
        ["main", "side"],
    )

    assert ok is True
    assert error == ""
    assert resolved_main is main
    assert target_rooms == [main, side]


def test_validate_synced_targets_rejects_different_align_group(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=10.0, group="group-a")
    side = _room(tmp_path, "side", offset=3.0, group="group-b")
    manager = _FakeManager([main, side])

    ok, error, resolved_main, target_rooms = room_handler._validate_synced_analysis_targets(
        manager,
        "main",
        ["main", "side"],
    )

    assert ok is False
    assert "不在同一对齐组" in error
    assert resolved_main is None
    assert target_rooms == []


def test_map_highlight_to_room_uses_content_offset_delta(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=10.0)
    side = _room(tmp_path, "side", offset=3.0)
    highlight = {
        "start": 30.0,
        "end": 45.0,
        "score": 0.9,
        "reason": "round",
        "speech_score": 0.0,
        "visual_score": 0.0,
        "transcript": "",
    }

    mapped = room_handler._map_highlight_to_room(highlight, main, side)

    assert mapped["start"] == 37.0
    assert mapped["end"] == 52.0
    assert mapped["source_start"] == 30.0
    assert mapped["source_end"] == 45.0
    assert mapped["source_room_id"] == "main"
    assert mapped["room_id"] == "side"
    assert mapped["offset_delta"] == 7.0


def test_map_highlights_by_room_returns_room_keyed_payload(tmp_path) -> None:
    main = _room(tmp_path, "main", offset=10.0)
    side = _room(tmp_path, "side", offset=3.0)
    highlights = [
        {"start": 30.0, "end": 45.0, "score": 0.9},
        {"start": 60.0, "end": 70.0, "score": 0.8},
    ]

    mapped = room_handler._map_highlights_by_room(highlights, main, [main, side])

    assert set(mapped) == {"main", "side"}
    assert mapped["main"][0]["start"] == 30.0
    assert mapped["main"][0]["end"] == 45.0
    assert mapped["side"][0]["start"] == 37.0
    assert mapped["side"][0]["end"] == 52.0
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py -q
```

Expected:

```text
FAILED tests/test_synced_continuous_analysis.py::test_validate_synced_targets_accepts_same_align_group
FAILED tests/test_synced_continuous_analysis.py::test_map_highlight_to_room_uses_content_offset_delta
```

失败原因应为 `room_handler` 还没有 `_validate_synced_analysis_targets`、`_map_highlight_to_room`、`_map_highlights_by_room`。

- [ ] **Step 3: Commit**

```powershell
git add tests/test_synced_continuous_analysis.py
git commit -m "test: cover synced continuous analysis mapping helpers"
```

---

## Task 2: 实现并复用后端同步目标校验与高光映射 helper

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Test: `tests/test_synced_continuous_analysis.py`

- [ ] **Step 1: 在 `room_handler.py` 顶层新增 helper**

在 `_build_export_profile` 后、`register_handlers` 前加入以下代码：

```python
def _validate_synced_analysis_targets(
    manager: Any,
    main_room_id: str,
    target_room_ids: list[str],
) -> tuple[bool, str, Any | None, list[Any]]:
    """校验同步分析目标，返回主房间和按入参顺序排列的目标房间。"""
    main_room = manager.get_room(main_room_id)
    if main_room is None:
        return False, "主房间不存在", None, []
    if not getattr(main_room, "record_output_path", None) or not os.path.isfile(main_room.record_output_path):
        return False, "主房间录制文件不存在", None, []

    normalized_target_ids: list[str] = []
    for rid in target_room_ids or [main_room_id]:
        if rid and rid not in normalized_target_ids:
            normalized_target_ids.append(rid)
    if main_room_id not in normalized_target_ids:
        normalized_target_ids.insert(0, main_room_id)

    target_rooms: list[Any] = []
    if len(normalized_target_ids) > 1:
        main_group = getattr(main_room, "align_group_id", "")
        if not main_group:
            return False, "主房间未对齐，请先一键对齐", None, []
        for rid in normalized_target_ids:
            room = manager.get_room(rid)
            if room is None:
                return False, f"房间不存在: {rid}", None, []
            if not getattr(room, "record_output_path", None) or not os.path.isfile(room.record_output_path):
                return False, f"房间录制文件不存在: {rid}", None, []
            if getattr(room, "align_group_id", "") != main_group:
                return False, f"房间 {rid} 与主房间不在同一对齐组，请重新一键对齐", None, []
            target_rooms.append(room)
    else:
        target_rooms = [main_room]

    return True, "", main_room, target_rooms


def _map_highlight_to_room(highlight: dict[str, Any], main_room: Any, target_room: Any) -> dict[str, Any]:
    """把主房间高光按 content_offset 映射到目标房间时间轴。"""
    start = float(highlight.get("start", 0.0))
    end = float(highlight.get("end", 0.0))
    offset_main = float(getattr(main_room, "content_offset", 0.0) or 0.0)
    offset_target = float(getattr(target_room, "content_offset", 0.0) or 0.0)
    delta = offset_main - offset_target

    mapped = dict(highlight)
    mapped["start"] = max(0.0, start + delta)
    mapped["end"] = max(0.0, end + delta)
    mapped["room_id"] = getattr(target_room, "room_id", "")
    mapped["source_room_id"] = getattr(main_room, "room_id", "")
    mapped["source_start"] = start
    mapped["source_end"] = end
    mapped["offset_delta"] = delta
    return mapped


def _map_highlights_by_room(
    highlights: list[dict[str, Any]],
    main_room: Any,
    target_rooms: list[Any],
) -> dict[str, list[dict[str, Any]]]:
    """按房间 ID 返回映射后的高光列表。"""
    mapped: dict[str, list[dict[str, Any]]] = {}
    for room in target_rooms:
        room_id = getattr(room, "room_id", "")
        if not room_id:
            continue
        mapped[room_id] = [
            _map_highlight_to_room(highlight, main_room, room)
            for highlight in highlights
            if float(highlight.get("start", 0.0)) < float(highlight.get("end", 0.0))
        ]
    return mapped
```

- [ ] **Step 2: 让 `start_analysis_export` 复用 helper**

在 `handle_start_analysis_export` 的 `_do_analysis_and_export()` 中，将当前“1. 校验主房间”和“2. 校验目标房间 + 对齐组”的手写逻辑替换为：

```python
            ok, error, main_room, target_rooms = _validate_synced_analysis_targets(
                manager,
                main_room_id,
                target_room_ids,
            )
            if not ok:
                return {"success": False, "error": error}
```

在批量导出循环中，将每个目标房间的时间映射改为调用 `_map_highlight_to_room`。把原先依赖 `offset_main`、`delta`、`mapped_start`、`mapped_end` 的片段替换为：

```python
                    for r in target_rooms:
                        mapped_hl = _map_highlight_to_room(hl, main_room, r)
                        mapped_start = float(mapped_hl.get("start", 0.0))
                        mapped_end = float(mapped_hl.get("end", 0.0))
                        if mapped_start >= mapped_end:
                            continue
                        room_label = getattr(r, "streamer_name", None) or getattr(r, "room_id", "room")
                        job_id = f"{job_prefix}-{getattr(r, 'room_id', 'room')}-{i}"
```

保留原有 `ExportWorker`、`export_progress`、`clip_completed`、`clip_failed` 提交流程，只替换时间计算来源。

- [ ] **Step 3: 运行 helper 测试**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 4: 运行现有持续分析保护测试**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_continuous_analysis_guards.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit**

```powershell
git add python-backend/handlers/room_handler.py tests/test_synced_continuous_analysis.py
git commit -m "refactor: share synced highlight mapping helpers"
```

---

## Task 3: 后端持续分析改为全局单并发并支持同步目标

**Files:**
- Modify: `tests/test_synced_continuous_analysis.py`
- Modify: `python-backend/handlers/room_handler.py`

- [ ] **Step 1: 补充源码级失败测试**

在 `tests/test_synced_continuous_analysis.py` 末尾追加：

```python
def test_continuous_analysis_start_accepts_synced_payload_and_rejects_global_concurrency() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    start_body = source.split("@server.on('start_continuous_analysis')", 1)[1].split("@server.on('stop_continuous_analysis')", 1)[0]

    assert "main_room_id = data.get('main_room_id') or data.get('room_id')" in start_body
    assert "target_room_ids = data.get('target_room_ids') or [main_room_id]" in start_body
    assert "if _continuous_tasks:" in start_body
    assert "已有持续分析任务正在运行" in start_body
    assert "_validate_synced_analysis_targets(" in start_body
    assert "_continuous_analysis_loop(main_room_id, target_room_ids," in start_body


def test_continuous_analysis_broadcasts_mapped_highlights_by_room() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split("@server.on('start_continuous_analysis')", 1)[0]

    assert "mapped_highlights_by_room = _map_highlights_by_room(" in loop_body
    assert "'main_room_id': main_room_id" in loop_body
    assert "'target_room_ids': target_room_ids" in loop_body
    assert "'mapped_highlights_by_room': mapped_highlights_by_room" in loop_body
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py::test_continuous_analysis_start_accepts_synced_payload_and_rejects_global_concurrency tests/test_synced_continuous_analysis.py::test_continuous_analysis_broadcasts_mapped_highlights_by_room -q
```

Expected:

```text
2 failed
```

- [ ] **Step 3: 扩展 `_continuous_analysis_loop` 签名**

将函数签名从：

```python
    async def _continuous_analysis_loop(
        room_id: str, interval: int, threshold: float,
        mode: str = 'scene', game: str = 'valorant',
    ) -> None:
```

替换为：

```python
    async def _continuous_analysis_loop(
        main_room_id: str,
        target_room_ids: list[str],
        interval: int,
        threshold: float,
        mode: str = 'scene',
        game: str = 'valorant',
    ) -> None:
        room_id = main_room_id
```

该 `room_id` 变量保留给现有循环内部代码使用，降低改动面。

- [ ] **Step 4: 在每轮广播前生成多房间映射结果**

在 `_continuous_analysis_loop` 中 `bridge.queue_broadcast({ 'type': 'continuous_highlights', ... })` 前加入：

```python
                    mapped_highlights_by_room: dict[str, list[dict[str, Any]]] = {}
                    ok, error, main_room_for_map, target_rooms_for_map = _validate_synced_analysis_targets(
                        manager,
                        main_room_id,
                        target_room_ids,
                    )
                    if ok and main_room_for_map is not None:
                        mapped_highlights_by_room = _map_highlights_by_room(
                            all_highlights,
                            main_room_for_map,
                            target_rooms_for_map,
                        )
                    else:
                        _log.warning(
                            "持续分析同步映射跳过: main_room_id=%s, targets=%s, error=%s",
                            main_room_id,
                            target_room_ids,
                            error,
                        )
```

然后把广播 data 从：

```python
                            'data': {
                                'room_id': room_id,
                                'highlights': all_highlights,
                                'new_count': len(new_hl),
                                'total': len(all_highlights),
                            },
```

替换为：

```python
                            'data': {
                                'room_id': main_room_id,
                                'main_room_id': main_room_id,
                                'target_room_ids': target_room_ids,
                                'highlights': all_highlights,
                                'mapped_highlights_by_room': mapped_highlights_by_room,
                                'new_count': len(new_hl),
                                'total': len(all_highlights),
                            },
```

- [ ] **Step 5: 修改 `start_continuous_analysis` handler**

将 handler 开头到创建任务的逻辑替换为：

```python
        main_room_id = data.get('main_room_id') or data.get('room_id')
        target_room_ids = data.get('target_room_ids') or [main_room_id]
        mode = data.get('mode', 'scene')
        interval = int(data.get('interval', 60))
        threshold = _safe_float(data.get('threshold', 0.3), 0.3)
        game = data.get('game', 'valorant')  # 'valorant' | 'generic'
        if not main_room_id:
            return {'error': 'main_room_id is required'}
        if _continuous_tasks:
            active_room_id = next(iter(_continuous_tasks.keys()))
            return {
                'success': False,
                'error': f'已有持续分析任务正在运行，请先停止: {active_room_id}',
                'active_room_id': active_room_id,
            }
        if interval < 10:
            interval = 10

        ok, error, main_room, target_rooms = _validate_synced_analysis_targets(
            manager,
            main_room_id,
            target_room_ids,
        )
        if not ok:
            return {'success': False, 'error': error}
        resolved_target_room_ids = [getattr(room, 'room_id', '') for room in target_rooms if getattr(room, 'room_id', '')]

        task = asyncio.create_task(_continuous_analysis_loop(main_room_id, resolved_target_room_ids, interval, threshold, mode, game))
        _continuous_tasks[main_room_id] = {
            'task': task,
            'last_analyzed': 0.0,
            'highlights': [],
            'cancelled': False,
            'mode': mode,
            'main_room_id': main_room_id,
            'target_room_ids': resolved_target_room_ids,
        }
        _log.info(
            "持续分析已启动: main_room_id=%s, targets=%s, mode=%s, interval=%ds, threshold=%.2f",
            main_room_id,
            resolved_target_room_ids,
            mode,
            interval,
            threshold,
        )
        return {
            'success': True,
            'message': f'持续分析已启动（{mode} 模式，间隔 {interval}s）',
            'main_room_id': main_room_id,
            'target_room_ids': resolved_target_room_ids,
        }
```

- [ ] **Step 6: 让 `stop_continuous_analysis` 支持省略房间 ID 时停止当前全局任务**

将 stop handler 的取房间 ID 和校验逻辑替换为：

```python
        room_id = data.get('main_room_id') or data.get('room_id')
        if not room_id and len(_continuous_tasks) == 1:
            room_id = next(iter(_continuous_tasks.keys()))
        if not room_id:
            return {'error': 'room_id is required'}
        state = _continuous_tasks.get(room_id)
        if not state:
            return {'success': False, 'error': '该房间没有持续分析任务'}
```

保留后面的 `state['cancelled'] = True`、`state['task'].cancel()` 和日志。

- [ ] **Step 7: 运行后端测试**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py tests/test_continuous_analysis_guards.py tests/test_room_handler_lifecycle.py -q
```

Expected:

```text
passed
```

- [ ] **Step 8: Commit**

```powershell
git add python-backend/handlers/room_handler.py tests/test_synced_continuous_analysis.py
git commit -m "feat: make continuous analysis single queue and synced"
```

---

## Task 4: 前端持续分析启动 Modal 与主直播间选择

**Files:**
- Modify: `tests/test_synced_continuous_analysis.py`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1: 添加前端源码失败测试**

在 `tests/test_synced_continuous_analysis.py` 末尾追加：

```python
def test_workbench_has_continuous_analysis_modal_and_synced_start_payload() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")

    assert "continuousModalOpen" in source
    assert "continuousMainRoom" in source
    assert "handleConfirmContinuousAnalysis" in source
    assert "send('start_continuous_analysis'" in source
    assert "main_room_id: continuousMainRoom" in source
    assert "target_room_ids: continuousTargetRoomIds" in source
    assert "选择持续分析主直播间" in source
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py::test_workbench_has_continuous_analysis_modal_and_synced_start_payload -q
```

Expected:

```text
1 failed
```

- [ ] **Step 3: 增加 Workbench 状态**

在同步导出 Modal 状态之后、持续分析状态之前加入：

```tsx
  // 同步持续分析 Modal 状态
  const [continuousModalOpen, setContinuousModalOpen] = useState(false)
  const [continuousMainRoom, setContinuousMainRoom] = useState<string | null>(null)
  const [continuousTargetRoomIds, setContinuousTargetRoomIds] = useState<string[]>([])
```

- [ ] **Step 4: 增加持续分析启用条件与提示**

在 `syncExportTooltip` 后加入：

```tsx
  const singleContinuousRoom = selectedRoomId
    ? rooms.find(r => r.room_id === selectedRoomId)
    : null
  const continuousSelectedRoomList = selectedRoomIds.size >= 2
    ? selectedRoomList
    : singleContinuousRoom
      ? [singleContinuousRoom]
      : []
  const continuousEnabled = continuousSelectedRoomList.length >= 1
    && continuousSelectedRoomList.every(r => r.record_output_path)
    && (
      continuousSelectedRoomList.length === 1
      || (() => {
        const groups = new Set(continuousSelectedRoomList.map(r => r.align_group_id || ''))
        return groups.size === 1 && !groups.has('')
      })()
    )
  const continuousTooltip = continuousAnalyzing
    ? '停止当前持续分析任务'
    : continuousSelectedRoomList.length === 0
      ? '请选择一个或多个直播间'
      : !continuousSelectedRoomList.every(r => r.record_output_path)
        ? '部分直播间缺少录制文件'
        : continuousSelectedRoomList.length > 1 && !continuousEnabled
          ? '多房间持续分析需要先一键对齐'
          : '只分析主直播间，并按对齐偏移同步到选中直播间'
```

- [ ] **Step 5: 新增确认启动函数**

在 `handleConfirmSyncExport` 后加入：

```tsx
  const handleConfirmContinuousAnalysis = () => {
    if (!continuousMainRoom) {
      message.warning('请选择持续分析主直播间')
      return
    }
    const targetRoomIds = continuousTargetRoomIds.length > 0
      ? continuousTargetRoomIds
      : [continuousMainRoom]
    const selected = rooms.filter(r => targetRoomIds.includes(r.room_id))
    if (selected.length !== targetRoomIds.length) {
      message.error('选中的直播间不存在，请刷新后重试')
      return
    }
    if (!selected.every(r => r.record_output_path)) {
      message.error('选中的直播间缺少录制文件')
      return
    }
    if (targetRoomIds.length > 1) {
      const groupSet = new Set(selected.map(r => r.align_group_id || ''))
      if (groupSet.size !== 1 || groupSet.has('')) {
        message.error('多房间持续分析需要先一键对齐')
        return
      }
    }

    send('start_continuous_analysis', {
      main_room_id: continuousMainRoom,
      target_room_ids: targetRoomIds,
      mode: 'fast',
      interval: 120,
      threshold: 0.3,
      game: 'valorant',
    })
    setContinuousAnalyzing(true)
    setContinuousRoomId(continuousMainRoom)
    setContinuousModalOpen(false)
    message.info('持续分析已启动：只分析主直播间，并同步到选中直播间')
  }
```

- [ ] **Step 6: 替换持续分析按钮行为**

把当前持续分析按钮的 `disabled`、`onClick`、`title` 改为：

```tsx
            disabled={!continuousEnabled && !continuousAnalyzing}
            title={continuousTooltip}
            onClick={() => {
              if (continuousAnalyzing) {
                send('stop_continuous_analysis', { room_id: continuousRoomId || undefined })
                setContinuousAnalyzing(false)
                setContinuousRoomId(null)
                message.info('持续分析已停止')
                return
              }
              if (!continuousEnabled) {
                message.warning(continuousTooltip)
                return
              }
              const targetRoomIds = continuousSelectedRoomList.map(r => r.room_id)
              setContinuousTargetRoomIds(targetRoomIds)
              setContinuousMainRoom(targetRoomIds[0] || null)
              setContinuousModalOpen(true)
            }}
```

按钮文案保留：

```tsx
            {continuousAnalyzing ? '停止持续分析' : '持续分析(快速回合)'}
```

- [ ] **Step 7: 在同步导出 Modal 前新增持续分析 Modal**

在 `{/* 多房间同步分析导出 Modal */}` 前加入：

```tsx
      {/* 同步持续分析 Modal */}
      <Modal
        title="选择持续分析主直播间"
        open={continuousModalOpen}
        onCancel={() => setContinuousModalOpen(false)}
        width={520}
        footer={[
          <Button key="cancel" onClick={() => setContinuousModalOpen(false)}>取消</Button>,
          <Button
            key="confirm"
            type="primary"
            disabled={!continuousMainRoom}
            onClick={handleConfirmContinuousAnalysis}
          >
            开始持续分析
          </Button>,
        ]}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{
            padding: '8px 12px',
            borderRadius: 6,
            background: 'var(--bg-tertiary)',
            fontSize: 12,
            color: 'var(--text-secondary)',
          }}>
            系统只分析主直播间，按 content_offset 同步映射到 {continuousTargetRoomIds.length} 个选中直播间。多房间模式需要先完成一键对齐。
          </div>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>选择主直播间</div>
            <Radio.Group
              value={continuousMainRoom}
              onChange={(e) => setContinuousMainRoom(e.target.value)}
              style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
            >
              {rooms.filter(r => continuousTargetRoomIds.includes(r.room_id)).map(r => (
                <Radio key={r.room_id} value={r.room_id}>
                  {r.streamer_name || r.room_id}
                  {!r.record_output_path && <span style={{ color: 'var(--state-error)', marginLeft: 8 }}>（无录制文件）</span>}
                </Radio>
              ))}
            </Radio.Group>
          </div>
        </div>
      </Modal>
```

- [ ] **Step 8: 运行前端源码测试**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py::test_workbench_has_continuous_analysis_modal_and_synced_start_payload -q
```

Expected:

```text
1 passed
```

- [ ] **Step 9: Commit**

```powershell
git add lsc-electron/src/pages/Workbench/index.tsx tests/test_synced_continuous_analysis.py
git commit -m "feat: choose main room for continuous analysis"
```

---

## Task 5: 前端接收多房间持续分析高光并写入 clips

**Files:**
- Modify: `tests/test_synced_continuous_analysis.py`
- Modify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1: 添加前端多房间结果失败测试**

在 `tests/test_synced_continuous_analysis.py` 末尾追加：

```python
def test_workbench_imports_mapped_continuous_highlights_by_room() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    handler_body = source.split("on('continuous_highlights'", 1)[1].split("}))", 1)[0]

    assert "mapped_highlights_by_room" in handler_body
    assert "Object.entries(data.mapped_highlights_by_room)" in handler_body
    assert "mappedRoomIds.includes(c.room_id)" in handler_body
    assert "source='ai_highlight'" not in handler_body
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py::test_workbench_imports_mapped_continuous_highlights_by_room -q
```

Expected:

```text
1 failed
```

- [ ] **Step 3: 替换 `continuous_highlights` 监听逻辑**

将 `on('continuous_highlights', (data: any) => { ... })` 内部替换为：

```tsx
      const st = useAppStore.getState()
      const currentClips = st.clips

      if (data?.mapped_highlights_by_room && typeof data.mapped_highlights_by_room === 'object') {
        const mappedEntries = Object.entries(data.mapped_highlights_by_room) as [string, Highlight[]][]
        const mappedRoomIds = mappedEntries.map(([roomId]) => roomId)
        const mappedClips = mappedEntries.flatMap(([roomId, highlights]) => {
          const room = st.rooms.find(r => r.room_id === roomId)
          return (Array.isArray(highlights) ? highlights : []).map((h, i) => ({
            start: h.start,
            end: h.end,
            label: `${room?.streamer_name || roomId} - 高光 ${i + 1}`,
            room_id: roomId,
            is_ai_highlight: true,
          }))
        })
        const preservedClips = currentClips.filter(c => !(mappedRoomIds.includes(c.room_id) && c.is_ai_highlight))
        st.setClips([...preservedClips, ...mappedClips])
        if (data.new_count > 0) {
          message.success(`自动同步 ${data.new_count} 个新高光到 ${mappedRoomIds.length} 个直播间`)
        }
        return
      }

      if (data?.highlights && Array.isArray(data.highlights)) {
        const roomId = data.room_id || continuousRoomId || selectedRoomId
        if (roomId) {
          const room = st.rooms.find(r => r.room_id === roomId)
          const continuousClips = (data.highlights as Highlight[]).map((h, i) => ({
            start: h.start,
            end: h.end,
            label: `${room?.streamer_name || '房间'} - 高光 ${i + 1}`,
            room_id: roomId,
            is_ai_highlight: true,
          }))
          const preservedClips = currentClips.filter(c => !(c.room_id === roomId && c.is_ai_highlight))
          st.setClips([...preservedClips, ...continuousClips])
          if (data.new_count > 0) {
            message.success(`自动导入 ${data.new_count} 个新高光到切片列表`)
          }
        }
      }
```

保留单房间旧 payload 分支，确保旧后端或单房间持续分析仍可工作。

- [ ] **Step 4: 运行前端源码测试**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py::test_workbench_imports_mapped_continuous_highlights_by_room -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: 运行 TypeScript 类型检查**

Run:

```powershell
cd lsc-electron; npm exec tsc -- --noEmit
```

Expected:

```text
无 TypeScript 编译错误
```

- [ ] **Step 6: Commit**

```powershell
git add lsc-electron/src/pages/Workbench/index.tsx tests/test_synced_continuous_analysis.py
git commit -m "feat: import synced continuous highlights by room"
```

---

## Task 6: 导出路径确认与全量回归

**Files:**
- Modify: `tests/test_synced_continuous_analysis.py`
- Verify: `python-backend/handlers/room_handler.py`
- Verify: `lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **Step 1: 添加导出路径源码保护测试**

在 `tests/test_synced_continuous_analysis.py` 末尾追加：

```python
def test_ai_highlight_export_uses_clip_room_timestamps_without_extra_offset() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    export_body = source.split("@server.on('export_clip')", 1)[1].split("@server.on('cancel_export')", 1)[0]

    assert "source == 'ai_highlight'" in export_body
    assert "start_sec" in export_body
    assert "end_sec" in export_body
    ai_branch = export_body.split("source == 'ai_highlight'", 1)[1].split("else", 1)[0]
    assert "content_offset" not in ai_branch
```

- [ ] **Step 2: 运行导出保护测试**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py::test_ai_highlight_export_uses_clip_room_timestamps_without_extra_offset -q
```

Expected:

```text
1 passed
```

如果该测试失败，调整 `export_clip` 中 `source == 'ai_highlight'` 分支，使它直接使用 clip 的 `start_sec` / `end_sec`，不要再套用 `content_offset`。因为持续分析写入 clips 时已经把主房间高光映射到了各目标房间时间轴。

- [ ] **Step 3: 运行同步持续分析测试集**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_synced_continuous_analysis.py -q
```

Expected:

```text
全部通过
```

- [ ] **Step 4: 运行稳定性相关后端测试**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_continuous_analysis_guards.py tests/test_room_handler_lifecycle.py tests/test_message_bridge.py tests/test_multi_room_manager.py tests/test_resource_monitor.py -q
```

Expected:

```text
全部通过
```

- [ ] **Step 5: 运行前端稳定性源码测试**

Run:

```powershell
$env:PYTHONPATH='.;python-backend'; pytest tests/test_frontend_stability_guards.py -q
```

Expected:

```text
全部通过
```

- [ ] **Step 6: 运行 TypeScript 类型检查**

Run:

```powershell
cd lsc-electron; npm exec tsc -- --noEmit
```

Expected:

```text
无 TypeScript 编译错误
```

- [ ] **Step 7: 手动验收流程**

按以下流程验收：

```text
1. 启动 2-3 个直播间录制。
2. 多选这些直播间。
3. 点击“一键对齐”，等待对齐成功。
4. 点击“持续分析(快速回合)”。
5. 在 Modal 中选择一个主直播间。
6. 确认后观察后端日志：只出现一个持续分析任务。
7. 等待至少一轮持续分析广播。
8. 检查前端切片列表：每个选中房间都出现同批次高光，片段数量一致。
9. 对任意房间高光执行导出，确认导出使用该房间录制文件和映射后的 start/end。
10. 再次点击“持续分析(快速回合)”停止任务。
11. 重新选择另一组直播间启动持续分析，确认不会残留上一轮结果。
```

- [ ] **Step 8: Commit**

```powershell
git add tests/test_synced_continuous_analysis.py
git commit -m "test: guard synced continuous analysis export path"
```

---

## 风险与降级策略

| 风险 | 处理方式 |
|---|---|
| 旧前端仍发送 `{ room_id }` | 后端 `main_room_id = data.get('main_room_id') or data.get('room_id')` 保持兼容 |
| 多房间未对齐就启动持续分析 | 前端按钮提示并阻止；后端 `_validate_synced_analysis_targets` 再拦截 |
| 任务运行中用户再次启动 | 后端检查 `_continuous_tasks` 非空直接拒绝，返回 `active_room_id` |
| 单房间持续分析被新逻辑影响 | `target_room_ids` 为空时回落为 `[main_room_id]`，映射结果仍只有主房间 |
| 导出二次偏移 | 加源码测试保护 `source == 'ai_highlight'` 分支不再使用 `content_offset` |
| 对齐后房间录制文件变化 | 每轮广播前重新校验目标房间，失败时跳过同步映射并记录 warning |

---

## 完成标准

1. `tests/test_synced_continuous_analysis.py` 全部通过。
2. `tests/test_continuous_analysis_guards.py`、`tests/test_room_handler_lifecycle.py`、`tests/test_frontend_stability_guards.py` 保持通过。
3. `cd lsc-electron; npm exec tsc -- --noEmit` 无错误。
4. 多选已对齐房间后，持续分析会弹出主直播间选择。
5. 后端同一时间只允许一个持续分析任务。
6. 持续分析结果能同步写入所有选中房间 clips。
7. 导出映射后的 clips 时不会再次套用 `content_offset`。

---

## Self-Review

- Spec coverage: 计划覆盖“单并发队列”“多房间先对齐”“选择主直播间”“只分析主直播间”“导出选中直播间相同时间点片段”五个核心要求。
- Placeholder scan: 本计划没有使用待补占位，所有代码步骤都给出具体文件、具体代码和验证命令。
- Type consistency: 后端统一使用 `main_room_id`、`target_room_ids`、`mapped_highlights_by_room`；前端发送和接收字段与后端一致。
