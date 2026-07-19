# 六模块稳定性 / 延迟全量修复 — 设计说明

> 日期：2026-07-16  
> 来源：六路只读扫描（MSE / 录制 / 导出 / 对齐 / 持续分析 / WS·性能）  
> 计划：`docs/superpowers/plans/2026-07-16-stability-latency-six-module-fixes.md`

## 目标

消除扫描列出的全部稳定性与延迟热点：假成功、永久卡住、预览无法恢复、多路周期性卡顿、分析窗跳过、导出/取消竞态等；不改墙钟映射与「预览/录制独立」核心契约。

## 原则

1. **正确性先于延迟**：先修永久卡住 / 假成功 / 门禁错误，再做节流与吞吐。
2. **单一恢复编排**：MSE stall 收敛为「先轻量、再全链 + epoch」一条路径。
3. **最小改动**：不重做分析管线、不换 MSE 传输协议（base64→二进制列为明确非目标）。
4. **可测**：每项有 pytest 源码守卫或行为测试；前端关键路径补字符串守卫。

## 非目标

- 不把 WebSocket 分片改为二进制/ArrayBuffer。
- 不拆分 `Workbench/index.tsx` 大重构。
- 不默认开启 `shared_ingest_enabled`。
- 不缩短对齐 PCM 窗口到破坏置信度的程度（可优化线程卸载，不强制改 8s）。
- 不扩大 `_analysis_semaphore` 到无上限（最多可选 1→2，需守卫）。

## 模块决策摘要

### A. MSE 预览

| ID | 问题 | 决策 |
|----|------|------|
| A1 | watchdog 只 replay init，player 忽略二次 init | stall：第 1 次 `request_mse_init`；连续失败 ≥2 次 → `enable_preview` + 后端轮换 `preview_epoch_id`；toast「预览恢复中」 |
| A2 | shared 重连无 epoch | shared `mse_reconnected` 与 legacy 一样 `_rotate_epoch_on_reconnect` |
| A3 | shared 无 stdout 挂死检测 | 复用 MseStreamer 15s 无数据 → 杀进程 / 触发 on_error |
| A4 | 三层 stall 打架 | watchdog 触发时不假装「已收到 segment」掩盖故障；仅在真正收到 segment 时刷新时间戳；player error 态时强制走全链 |
| A5 | `-re` + 1s frag 延迟 | 独立 MSE **去掉输入 `-re`**（直播源已实时）；`frag_duration` 保持 1s（稳定性优先，本轮不改 GOP） |
| A6 | base64 无背压 | **本轮不做协议改**；仅确保 drop 统计可 debug；文档标明非目标 |

### B. 录制 / 重连 / 磁盘

| ID | 问题 | 决策 |
|----|------|------|
| B1 | shared + controller 存在时健康检查跳过 | `_on_global_tick`：shared 录制中即使 controller 存在也跑 ingest 健康/停滞检测 |
| B2 | 重连走 8GB 预检 | 重连路径用 `_MIN_FREE_BYTES_WHILE_RECORDING`（2GB）或专用 `preflight_reconnect`，开录仍用 8GB/路 |
| B3 | controller=None 跳过 disk_full | shared 分支也执行 2GB 磁盘检测 + `disk_full` emit |
| B4 | stop 走 bridge 易超时 | `handle_stop_recording` timeout 提到 15s（或与 start 同 executor 短路径）；不改开录架构 |
| B5 | 测试缺口 | 补 shared tick 健康检查 + 重连预检阈值守卫 |

### C. 导出队列

| ID | 问题 | 决策 |
|----|------|------|
| C1 | 启动失败无 `clip_failed` | `_process_export_job` 在 `result['error']` 且未跑 FFmpeg 时 broadcast `clip_failed` |
| C2 | cancel / export_jobs 竞态 | `start_export` 成功后**立即**注册 `export_jobs`；cancel 时若 job 在处理中也查 controller workers |
| C3 | 队列满阻塞 WS | `put` 改为 `put_nowait`，满则返回 `success:false` 友好错误 |
| C4 | semaphore 热更新延迟 | 保持现有 empty 才换；日志已够，补注释 |
| C5 | `export_clip_by_id` offset 非快照 | 入队时传入创建时 `content_offset` 快照（与 `queue_export` 一致） |

### D. 对齐 / 时间轴

| ID | 问题 | 决策 |
|----|------|------|
| D1 | create_timeline=None 仍 success | 返回 `success:false` 或 `partial`；前端无 timeline 禁止「已精确对齐」、清 align_group |
| D2 | 单房 epoch 全组失效 | 本轮：**保留全组失效**（安全）；补 toast 已有路径即可。增量失效列为后续 |
| D3 | 8s 采集 | 本轮不改时长；loading 文案已说明 |
| D4 | 互相关堵 WS | `align_audio_map` 丢 `_recording_executor` / `run_in_executor` |
| D5 | Track A 文档漂移 | 更新 plan checkbox + split-brain 守卫测试 |

### E. 持续分析

| ID | 问题 | 决策 |
|----|------|------|
| E1 | OCR 超时后 skip-kick | 上一轮 `worker_error` / `scan_result.error` 时 `_should_skip_continuous_scan_kick` 返回 False |
| E2 | 串行 semaphore(1) | 保持 1（质量优先）；仅在注释标明吞吐天花板；不做无产品确认的扩容 |
| E3 | pause_analysis 冻主循环 | Valorant OCR 模式：`pause_analysis` 只拉长 interval，**不 skip tick** |
| E4 | 双管线无短路 | 小优化：若 phase markers 已覆盖且无音频需求可跳过——**本轮仅当有明确安全短路**；否则跳过 |
| E5 | pending 依赖 lookback | 设计权衡，本轮不改；依赖 E1 修复后可升格 |

### F. WS / 前端性能

| ID | 问题 | 决策 |
|----|------|------|
| F1 | setRooms 无浅比较 | `setRooms`：按 room_id 字段浅比较，全同则 return state |
| F2 | 5s 全量绕过 300ms 节流 | `_queue_rooms_update` 走与 `_broadcast_rooms` 同一 coalesce/节流 |
| F3 | systemStats 无时间窗节流 | `handleSystemStats` 1s、`handleDiskUsage` 3s 节流 |
| F4 | watchdog（与 A1 合并） | 见 A1 |
| F5 | Workbench 大重构 | **不做**；仅依赖 F1–F3 降低重渲染 |

## 验收

- 新建/扩展：`tests/test_stability_latency_guards.py`（或按模块分散到既有 guard 文件）
- `pytest` 相关文件全绿
- `lsc-electron`：`npx tsc --noEmit`
- **不要 git commit**（除非用户另行要求）
