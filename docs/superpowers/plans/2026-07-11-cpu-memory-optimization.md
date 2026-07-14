# CPU 和内存优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降低录制、预览、分析与状态同步链路的常驻 CPU / 内存占用，同时避免任何功能退化。

**Architecture:** 先从最容易产生高频开销的前端 WebSocket/状态更新链路入手，减少重复计算和无效重渲染；再检查 Python 后端的分析、推送和缓存路径，限制并发与对象驻留；最后用同一套基线指标复测优化效果，确认功能完整性不受影响。

**Tech Stack:** Electron + React + TypeScript + Zustand + WebSocket + Python + FFmpeg

---

### Task 1: 建立资源基线和热点清单

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-11-cpu-memory-optimization.md`
- Test: `pytest -q`（只跑现有测试，确认优化前基线不破坏）

- [ ] **Step 1: 记录当前主要资源观察点**

```text
1. 录制 1 路、4 路、12 路时的 CPU / 内存峰值
2. 仅预览时的 CPU / 内存峰值
3. 启动连续分析时的 CPU / 内存峰值
4. WebSocket 重连、状态更新、MSE 预览恢复时的峰值
```

- [ ] **Step 2: 补充 README 的性能排查说明**

```markdown
## 性能排查

建议先观察以下指标：
- 后端 Python 进程 CPU / 内存
- Electron 主进程 CPU / 内存
- 渲染进程 CPU / 内存
- FFmpeg 子进程数量和峰值占用
- 预览 / 分析 / 导出触发时的资源变化
```

- [ ] **Step 3: 运行现有测试作为基线**

```bash
pytest -q
```

- [ ] **Step 4: 记录基线结果**

```text
把观测到的峰值写进本计划的“验证结果”段，作为优化前对照。
```

---

### Task 2: 减少前端 WebSocket 消息造成的重复更新

**Files:**
- Modify: `lsc-electron/src/hooks/useWebSocket.ts`
- Modify: `lsc-electron/src/store/appStore.ts`
- Test: `lsc-electron/src/hooks/useWebSocket.ts` 相关手工验证

- [ ] **Step 1: 写出消息去重和节流规则**

```ts
const SYSTEM_STATS_MIN_INTERVAL_MS = 1000
const DISK_USAGE_MIN_INTERVAL_MS = 3000
const ROOM_UPDATE_SKIP_KEYS = new Set(['updated_at'])
```

- [ ] **Step 2: 在 store 中避免重复写入相同值**

```ts
setConnectionStatus: (connectionStatus) =>
  set((state) => (state.connectionStatus === connectionStatus ? state : { connectionStatus })),

setDiskUsage: (diskUsage) =>
  set((state) => {
    const prev = state.diskUsage
    if (
      prev?.total === diskUsage?.total &&
      prev?.used === diskUsage?.used &&
      prev?.free === diskUsage?.free
    ) {
      return state
    }
    return { diskUsage }
  }),
```

- [ ] **Step 3: 给系统指标和磁盘使用量增加时间窗节流**

```ts
let lastSystemStatsAt = 0
let lastDiskUsageAt = 0

const handleSystemStats = (data: any) => {
  const now = Date.now()
  if (now - lastSystemStatsAt < SYSTEM_STATS_MIN_INTERVAL_MS) return
  lastSystemStatsAt = now
  // 原有写入逻辑
}
```

- [ ] **Step 4: 减少 room_updated 的无意义 store 更新**

```ts
const filtered = Object.fromEntries(
  Object.entries(updates).filter(([k, v]) => {
    if (ROOM_UPDATE_SKIP_KEYS.has(k)) return false
    return v !== undefined
  })
)
```

- [ ] **Step 5: 手工验证房间状态、系统状态和预览恢复仍正常**

```text
验证点：
- 连接 / 断连状态正常变化
- 房间列表能刷新
- 系统指标仍能显示
- 预览恢复和导出进度不受影响
```

---

### Task 3: 降低 MSE 缓存和定时器的长期驻留成本

**Files:**
- Modify: `lsc-electron/src/hooks/useWebSocket.ts`
- Modify: `lsc-electron/src/components/VideoPreview.tsx`
- Test: `lsc-electron/src/components/VideoPreview.tsx` 相关手工验证

- [ ] **Step 1: 给 MSE init / segment 缓存加更严格的回收条件**

```ts
const _MSE_INIT_CACHE_MAX = 12
const _MSE_SEGMENT_CACHE_TTL_MS = 30000
```

- [ ] **Step 2: 保证房间停止预览后立即清理相关缓存和重试计数**

```ts
export function clearMseRoomCache(roomId: string): void {
  delete _mseInitCache[roomId]
  delete _mseSegmentCache[roomId]
  if (_mseInitRetryTimers[roomId]) {
    clearTimeout(_mseInitRetryTimers[roomId])
    delete _mseInitRetryTimers[roomId]
  }
}
```

- [ ] **Step 3: 让 watchdog 仅在存在活跃预览时运行更少检查**

```ts
const hasActivePreview = useAppStore.getState().rooms.some(
  r => r.preview_enabled && r.is_connected && !r.preview_paused
)
if (!hasActivePreview) return
```

- [ ] **Step 4: 验证预览首帧、断线重连和缓存回放没有退化**

```text
验证点：
- 首次打开预览仍能尽快显示
- 断线重连后能恢复
- 关闭预览后缓存正确释放
```

---

### Task 4: 降低后端状态推送和分析任务的重复开销

**Files:**
- Modify: `python-backend/server.py`
- Modify: `python-backend/message_bridge.py`
- Modify: `python-backend/handlers/room_handler.py`
- Test: `tests/test_message_bridge.py`
- Test: `tests/test_ws_scheduling.py`

- [ ] **Step 1: 在消息桥接层增加同类消息合并策略**

```python
COALESCE_TYPES = {"system_stats", "disk_usage", "rooms_updated"}
```

- [ ] **Step 2: 对高频状态消息只保留最新一条待发送内容**

```python
if msg.type in COALESCE_TYPES:
    self._pending[msg.type] = msg
    return
```

- [ ] **Step 3: 限制连续分析和 AI 分析的并发度**

```python
_ai_semaphore = threading.Semaphore(1)
```

- [ ] **Step 4: 检查分析任务完成后及时释放大对象和临时结果**

```python
finally:
    del large_buffer
    gc.collect()
```

- [ ] **Step 5: 添加消息桥接合并行为测试**

```python
def test_coalesced_messages_keep_latest_only():
    ...
```

---

### Task 5: 减少 Python 侧重复计算和临时文件驻留

**Files:**
- Modify: `lsc/analyzer/audio_analyzer.py`
- Modify: `lsc/analyzer/visual_analyzer.py`
- Modify: `lsc/analyzer/fusion.py`
- Modify: `lsc/core/services/resource_monitor.py`
- Test: `tests/test_analyzer.py`
- Test: `tests/test_resource_monitor.py`

- [ ] **Step 1: 给音频和关键帧提取添加分段处理入口**

```python
def extract_audio_segments(...):
    ...
```

- [ ] **Step 2: 删除每段处理完成后立即释放临时文件**

```python
os.remove(segment_path)
```

- [ ] **Step 3: 让融合逻辑使用更少的重复遍历**

```python
indices = np.searchsorted(visual_ts, ts_grid, side="left")
```

- [ ] **Step 4: 让资源监控只采集必要指标，避免高频重采样**

```python
stats = {
  "cpu_percent": cpu_percent,
  "memory_percent": memory_percent,
}
```

- [ ] **Step 5: 验证分析结果、时间线和资源统计保持正确**

```bash
pytest tests/test_analyzer.py tests/test_resource_monitor.py -q
```

---

### Task 6: 做最终性能回归验证

**Files:**
- Modify: `docs/superpowers/plans/2026-07-11-cpu-memory-optimization.md`
- Test: `pytest -q`
- Test: `cd lsc-electron && npx tsc --noEmit`

- [ ] **Step 1: 复跑全量测试**

```bash
pytest -q
```

- [ ] **Step 2: 复跑前端类型检查**

```bash
cd lsc-electron && npx tsc --noEmit
```

- [ ] **Step 3: 复测最初记录的资源场景**

```text
对比优化前后的：
- CPU 峰值
- 内存峰值
- 预览首帧时间
- 重连恢复时间
- 分析完成时间
```

- [ ] **Step 4: 在计划末尾写入验证结论**

```text
结论：
- 哪些指标下降了
- 哪些场景保持不变
- 哪些场景仍需后续优化
```
