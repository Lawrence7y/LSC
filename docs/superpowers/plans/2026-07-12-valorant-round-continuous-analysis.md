# 无畏契约回合持续分析实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 仅在用户选择“无畏契约回合切割”时，识别屏障解除至结算/下一回合买枪的完整回合；每个确认回合立刻为所有已选、已同步房间入列并导出；前端展示真实进度。

**架构：** 复用 `round_detector`、连续分析 worker、同步映射、`queue_export()`、`clip_queued` 和现有 WebSocket。新增的只是 OCR 起止元数据、严格确认过滤和前端状态字段，不增加依赖或新队列。

**技术栈：** Python、pytest、NumPy、OCR/FFmpeg、React、TypeScript、Ant Design、Zustand。

---

## 修改文件

- `lsc/analyzer/round_detector.py` 和 `tests/test_round_detector.py`：OCR 回合边界。
- `python-backend/handlers/room_handler.py`、`tests/test_continuous_analysis_guards.py`、`tests/test_synced_continuous_analysis.py`：连续扫描、确认、同步导出、状态事件。
- `lsc-electron/src/types/index.ts`、`lsc-electron/src/components/AnalysisProgress.tsx`、`lsc-electron/src/pages/Workbench/components/ClipList.tsx`、`lsc-electron/src/pages/Workbench/index.tsx`、`tests/test_frontend_stability_guards.py`：显式模式、切片状态、进度展示。

### Task 1: 先修正 OCR 回合边界

**文件：**
- 修改：`tests/test_round_detector.py`
- 修改：`lsc/analyzer/round_detector.py`

- [ ] **步骤 1：写失败测试。**

```python
def test_ocr_round_uses_barrier_exit_and_explicit_end() -> None:
    cfg = ValorantRoundConfig(phase_sample_interval=2.0)
    rounds = _build_round_segments_from_phase_markers(
        [
            {"timestamp": 100.0, "type": "round_start"},
            {"timestamp": 154.0, "type": "round_end"},
            {"timestamp": 200.0, "type": "round_start"},
        ],
        240.0,
        cfg,
    )
    assert rounds[0]["start"] == 102.0
    assert rounds[0]["end"] == 154.0
    assert rounds[0]["start_by"] == "ocr_buy_exit"
    assert rounds[0]["end_by"] == "ocr_result"
    assert rounds[0]["ocr_confirmed"] is True


def test_ocr_round_uses_next_buy_as_confirmed_end() -> None:
    rounds = _build_round_segments_from_phase_markers(
        [{"timestamp": 100.0, "type": "round_start"}, {"timestamp": 200.0, "type": "round_start"}],
        240.0,
        ValorantRoundConfig(phase_sample_interval=2.0),
    )
    assert rounds[0]["start"] == 102.0
    assert rounds[0]["end"] == 200.0
    assert rounds[0]["end_by"] == "next_buy"
    assert rounds[0]["ocr_confirmed"] is True


def test_ocr_open_tail_is_not_confirmed() -> None:
    rounds = _build_round_segments_from_phase_markers(
        [{"timestamp": 100.0, "type": "round_start"}], 180.0, ValorantRoundConfig()
    )
    assert rounds[0]["end_by"] == "open_tail"
    assert rounds[0]["ocr_confirmed"] is False
```

- [ ] **步骤 2：确认红灯。**

运行：`pytest tests/test_round_detector.py -q`

预期：当前输出没有 `start_by/end_by`，且会以买枪采样点或推断尾部作为边界。

- [ ] **步骤 3：写最小实现。**

在 `_build_round_segments_from_phase_markers()` 中，使用最后一个买枪 OCR 采样点后的一个采样间隔作为起点；优先使用 `round_end`，没有结算标记则以下一次买枪为终点，二者都不存在才写 `open_tail`。输出必须含以下字段：

```python
start = min(duration, round(buy_marker + cfg.phase_sample_interval, 3))
end_by = "ocr_result" if explicit_ends else "next_buy" if next_buy else "open_tail"
ocr_confirmed = end_by in {"ocr_result", "next_buy"}
result.update({
    "start_by": "ocr_buy_exit",
    "end_by": end_by,
    "ocr_confirmed": ocr_confirmed,
})
```

保留音频钟声的校验作用，不能覆盖这些 OCR 边界。加入注释：`# ponytail: 复用既有 2 秒 OCR 采样；需更细精度时仅加密转场附近采样。`

- [ ] **步骤 4：确认绿灯。**

运行：`pytest tests/test_round_detector.py -q`

预期：完整回合从屏障解除后开始，有结算/下一买枪才能确认，尾部保持未确认。

- [ ] **步骤 5：提交。**

运行：`git add lsc/analyzer/round_detector.py tests/test_round_detector.py; git commit -m "feat: confirm valorant round OCR boundaries"`

### Task 2: 固定回看窗口并只确认完整 OCR 回合

**文件：**
- 修改：`tests/test_continuous_analysis_guards.py`
- 修改：`python-backend/handlers/room_handler.py`

- [ ] **步骤 1：写失败测试。**

```python
def test_valorant_round_scan_uses_trailing_window_after_first_scan() -> None:
    scan_range, use_ocr, _, full_rescan = room_handler._continuous_valorant_scan_budget(
        "valorant_round", 600.0, 720.0, {"level": "normal", "analysis_window_sec": 180}
    )
    assert (scan_range, use_ocr, full_rescan) == ((540.0, 720.0), True, False)


def test_only_complete_ocr_rounds_are_auto_exportable() -> None:
    assert room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "start_by": "ocr_buy_exit", "end_by": "ocr_result",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "pending",
        "start_by": "ocr_buy_exit", "end_by": "open_tail",
    })
    assert not room_handler._is_auto_exportable_valorant_round({
        "start": 102.0, "end": 154.0, "phase": "combat",
        "start_by": "audio", "end_by": "audio",
    })
```

- [ ] **步骤 2：确认红灯。**

运行：`pytest tests/test_continuous_analysis_guards.py -q`

预期：确认谓词不存在，旧连续分析仍使用 `fast` 与 `tail_by` 筛选。

- [ ] **步骤 3：实现最小状态规则。**

在 `_drop_open_tail_rounds()` 前新增：

```python
def _is_auto_exportable_valorant_round(round_data: dict[str, Any]) -> bool:
    try:
        valid_span = float(round_data.get("end", 0.0)) > float(round_data.get("start", 0.0))
    except (TypeError, ValueError):
        return False
    return (
        valid_span
        and round_data.get("phase") != "pending"
        and round_data.get("start_by") == "ocr_buy_exit"
        and round_data.get("end_by") in {"ocr_result", "next_buy"}
    )
```

将 `_continuous_valorant_refine_with_ocr()` 限定为 `mode == "valorant_round"`。将 `_continuous_valorant_scan_budget()` 改为首次扫描 `(0.0, current_dur)`、之后扫描 `(max(0.0, current_dur - lookback), current_dur)`；非 critical 压力时每次 `valorant_round` 扫描都启用 OCR。移除按总直播长度扩大扫描间隔和周期性全量重扫的分支。

在 `_continuous_analysis_loop()` 使用：

```python
_valorant_incremental_rounds = mode == "valorant_round" and game == "valorant"
```

删除 `window_rounds` 为空时回退发布 `new_hl` 的分支，未闭合片段只能以 `pending` 等待下一次回看。

- [ ] **步骤 4：确认绿灯。**

运行：`pytest tests/test_continuous_analysis_guards.py -q`

预期：每次工作量受回看窗口限制，只有完整 OCR 起止回合可导出。

- [ ] **步骤 5：提交。**

运行：`git add python-backend/handlers/room_handler.py tests/test_continuous_analysis_guards.py; git commit -m "feat: stabilize valorant round continuous scans"`

### Task 3: 复用现有队列导出新确认回合

**文件：**
- 修改：`tests/test_synced_continuous_analysis.py`
- 修改：`python-backend/handlers/room_handler.py`

- [ ] **步骤 1：写失败契约测试。**

```python
def test_continuous_loop_exports_only_new_complete_ocr_rounds() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    loop_body = source.split("async def _continuous_analysis_loop(", 1)[1].split(
        "async def _export_and_broadcast(", 1
    )[0]
    assert "for h in new_hl" in loop_body
    assert "_is_auto_exportable_valorant_round(h)" in loop_body
    assert "tail_by") in _CONFIRMED_TAIL" not in loop_body


def test_auto_export_queues_each_synced_room_and_emits_clip() -> None:
    source = (ROOT / "python-backend/handlers/room_handler.py").read_text(encoding="utf-8")
    export_body = source.split("async def _auto_export_highlights(", 1)[1].split("def _build_export_profile(", 1)[0]
    assert "for target_room in target_rooms:" in export_body
    assert "await queue_export(" in export_body
    assert "'type': 'clip_queued'" in export_body
```

- [ ] **步骤 2：确认红灯。**

运行：`pytest tests/test_synced_continuous_analysis.py -q`

预期：当前循环基于累计 `all_highlights` 和 `tail_by`，会让边界微调影响重复导出判断。

- [ ] **步骤 3：实现稳定去重和新回合过滤。**

以 `_exported_round_keys: set[str]` 替换起止秒数集合。在 `_merge_round_windows()` 中，让重叠新回合继承旧 `round_key`，新回合默认使用 OCR 起点：

```python
new.setdefault("round_key", old.get("round_key", f"{float(old['start']):.1f}"))
item.setdefault("round_key", f"{float(item.get('start', 0.0)):.1f}")
```

消费 worker 结果后，使用：

```python
confirmed_hl = [
    h for h in new_hl
    if _is_auto_exportable_valorant_round(h)
    and h.get("round_key") not in _exported_round_keys
]
for highlight in confirmed_hl:
    _exported_round_keys.add(str(highlight["round_key"]))
await _auto_export_highlights(main_room_for_map, target_rooms_for_map, confirmed_hl, job_prefix)
```

保留 `_auto_export_highlights()` 的 `for target_room in target_rooms`、`queue_export(..., source="ai_highlight")` 和 `clip_queued` 广播；任务 ID 改为 `auto-{job_prefix}-{round_key}-{room_id}`。

- [ ] **步骤 4：确认绿灯。**

运行：`pytest tests/test_synced_continuous_analysis.py -q`

预期：每个确认回合只提交一次，并为每个已选同步房间创建一个自动导出任务和一条列表事件。

- [ ] **步骤 5：提交。**

运行：`git add python-backend/handlers/room_handler.py tests/test_synced_continuous_analysis.py; git commit -m "feat: auto export confirmed valorant rounds"`

### Task 4: 让前端显式选择回合切割

**文件：**
- 修改：`tests/test_frontend_stability_guards.py`
- 修改：`lsc-electron/src/pages/Workbench/index.tsx`

- [ ] **步骤 1：写失败测试。**

```python
def test_workbench_requires_explicit_valorant_round_mode() -> None:
    source = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    body = source.split("const handleConfirmAnalysisExport = () => {", 1)[1].split("// 监听分析结果与进度", 1)[0]
    assert "analysisGameType === 'valorant_round'" in body
    assert "mode: isValorantRoundCutting ? 'valorant_round' : 'scene'" in body
    assert "game: isValorantRoundCutting ? 'valorant' : 'generic'" in body
    assert "setAnalysisGameType('valorant')" not in source
```

- [ ] **步骤 2：确认红灯。**

运行：`pytest tests/test_frontend_stability_guards.py::test_workbench_requires_explicit_valorant_round_mode -q`

预期：现有连续分析开关会自动选择无畏契约并发送旧的 `fast` 模式。

- [ ] **步骤 3：实现显式请求。**

```tsx
const [analysisGameType, setAnalysisGameType] = useState<'valorant_round' | 'generic'>('generic')
const isValorantRoundCutting = analysisGameType === 'valorant_round'

send('start_continuous_analysis', {
  main_room_id: continuousMainRoom,
  target_room_ids: targetRoomIds,
  mode: isValorantRoundCutting ? 'valorant_round' : 'scene',
  interval: 120,
  threshold: 0.3,
  game: isValorantRoundCutting ? 'valorant' : 'generic',
})
```

将单选项改为 `value="valorant_round"` 的“无畏契约回合切割”和 `value="generic"` 的“通用直播”；删除连续分析开关中自动设置游戏类型的代码。

- [ ] **步骤 4：验证。**

运行：`pytest tests/test_frontend_stability_guards.py::test_workbench_requires_explicit_valorant_round_mode -q; npm --prefix lsc-electron exec tsc -- --noEmit`

预期：只有主动选择回合切割才发送专用模式，TypeScript 无错误。

- [ ] **步骤 5：提交。**

运行：`git add lsc-electron/src/pages/Workbench/index.tsx tests/test_frontend_stability_guards.py; git commit -m "feat: require explicit valorant round mode"`

### Task 5: 显示进度和导出状态

**文件：**
- 修改：`lsc-electron/src/types/index.ts`
- 修改：`lsc-electron/src/components/AnalysisProgress.tsx`
- 修改：`lsc-electron/src/pages/Workbench/components/ClipList.tsx`
- 修改：`lsc-electron/src/pages/Workbench/index.tsx`
- 修改：`python-backend/handlers/room_handler.py`
- 修改：`tests/test_frontend_stability_guards.py`

- [ ] **步骤 1：写失败测试。**

```python
def test_continuous_progress_exposes_round_and_export_states() -> None:
    types = (ROOT / "lsc-electron/src/types/index.ts").read_text(encoding="utf-8")
    progress = (ROOT / "lsc-electron/src/components/AnalysisProgress.tsx").read_text(encoding="utf-8")
    workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
    for field in ("recorded_duration", "confirmed_rounds", "pending_rounds", "export_status"):
        assert field in types
    assert "exportSummary" in progress
    assert "export_status: 'queued'" in workbench
    assert "export_status: 'exporting'" in workbench
    assert "export_status: 'failed'" in workbench
```

- [ ] **步骤 2：确认红灯。**

运行：`pytest tests/test_frontend_stability_guards.py::test_continuous_progress_exposes_round_and_export_states -q`

预期：当前类型和 UI 未保留队列/失败状态，也没有确认/待确认回合数。

- [ ] **步骤 3：补充后端状态字段。**

在连续循环中计算并加入所有 `continuous_analysis_status` 广播：

```python
confirmed_rounds = sum(_is_auto_exportable_valorant_round(h) for h in all_highlights)
pending_rounds = sum(h.get("phase") == "pending" or h.get("end_by") == "open_tail" for h in all_highlights)
status_data.update({
    "recorded_duration": current_dur,
    "confirmed_rounds": confirmed_rounds,
    "pending_rounds": pending_rounds,
    "analysis_stage": "waiting_for_round_end" if pending_rounds else "detecting_round",
})
```

保持已有 `phase` 的 `running/finalizing/completed/error` 生命周期。

- [ ] **步骤 4：写回切片状态并渲染摘要。**

在 `types/index.ts` 添加：

```ts
export type ClipExportStatus = 'queued' | 'exporting' | 'completed' | 'failed'
export interface ClipSegment { export_status?: ClipExportStatus; export_error?: string }
export interface ContinuousAnalysisStatus {
  recorded_duration?: number
  confirmed_rounds?: number
  pending_rounds?: number
  analysis_stage?: 'waiting_for_recording' | 'detecting_round' | 'waiting_for_round_end' | 'exporting'
}
```

在 `clip_queued`、`export_progress`、`clip_completed`、`clip_failed` 中按 `job_id` 写入 `queued`、`exporting`、`completed`、`failed`。从 `clips` 聚合 `exportSummary`，传给 `AnalysisProgress`。该组件显示已录制/已分析时长、当前阶段、确认/待确认回合和导出数；持续直播仅表示追赶当前录制时长，不能显示全局 100%。`ClipList` 按 `export_status` 显示状态，排队/导出中禁用重复导出，失败可重试。

- [ ] **步骤 5：执行完整定向验证并提交。**

运行：`pytest tests/test_round_detector.py tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py tests/test_frontend_stability_guards.py -q; ruff check lsc/analyzer/round_detector.py python-backend/handlers/room_handler.py tests/test_round_detector.py tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py tests/test_frontend_stability_guards.py; npm --prefix lsc-electron exec tsc -- --noEmit`

预期：pytest、Ruff、TypeScript 均通过。

运行：`git add lsc/analyzer/round_detector.py python-backend/handlers/room_handler.py lsc-electron/src/types/index.ts lsc-electron/src/components/AnalysisProgress.tsx lsc-electron/src/pages/Workbench/components/ClipList.tsx lsc-electron/src/pages/Workbench/index.tsx tests/test_round_detector.py tests/test_continuous_analysis_guards.py tests/test_synced_continuous_analysis.py tests/test_frontend_stability_guards.py; git commit -m "feat: show valorant continuous export progress"`

预期：提交只包含本功能相关文件。
