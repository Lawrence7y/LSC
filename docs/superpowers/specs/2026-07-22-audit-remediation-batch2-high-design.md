# Batch2 High：正确性 / 一致性 / 取消设计

父文档：`2026-07-22-audit-remediation-overview-design.md`  
前置：Batch1 Critical 已合入（分析/导出路径稳定）。

## 目标

修复审查 High 项中的并发竞态、功能错误、前后端状态卡死、取消无响应，使终态事件与用户写操作不再静默丢失。

## H1. 并发与状态一致性

### H1a. `config.py` 锁真正使用

**现状**：定义 `_config_lock` 但 `load_config` 等路径未使用，单例存在并发竞态。

**设计**：所有读-改-写单例配置的路径在 `_config_lock` 下执行；纯只读可持锁返回拷贝或文档约定「调用方不修改返回对象」。

### H1b. `_analysis_jobs` / `_continuous_tasks`

**现状**：executor 线程与 asyncio 事件循环无锁并发读写，可能 `dictionary changed size during iteration`。

**设计**：模块级 `threading.RLock`；所有突变与迭代在锁内完成；出锁前对需广播的数据做浅/深拷贝快照。禁止在锁内做网络/FFmpeg 阻塞。

### H1c. `persistence.py` 进程内锁

**现状**：原子写（tmp + replace）存在，但无跨线程锁，并发写可 lost-update。

**设计**：`rooms.json` / `settings.json`（及同类）模块级 `Lock` 包住 read-modify-write；不引入跨进程 file lock（多进程写不在本批范围）。

**验收**：并发写 settings/rooms 的单测或线程压力脚本不丢更新；分析 jobs 迭代不再因竞态抛错。

## H2. 功能正确性

### H2a. 录制目录 hash 稳定化

**现状**：`hash(room_url)` 受 `PYTHONHASHSEED` 影响，跨进程目录名不稳定。

**设计**：`hashlib.sha1(room_url.encode("utf-8")).hexdigest()[:6]`（或 sha256 截断，固定一种）。不迁移已有目录（避免破坏用户数据）；仅新会话使用稳定算法。若需兼容旧目录，可先查旧名再回退——可选，默认不迁移。

### H2b. `round_detector` 超长回合「能量谷」分割

**现状**：注释称能量低谷分割，实现为算术中点硬切。

**设计（已定：诚实降级）**：删除假装实现；超长回合不自动对切，打 WARNING，保留整段或走已有上限策略。真实能量谷检测若后续需要，另开 spec，不在本批。

禁止保留「中点硬切但注释写能量谷」。

### H2c. OCR 分辨率探测失败

**现状**：失败硬回退 1920×1080，非该分辨率源裁剪错位 → OCR 静默全失败。

**设计**：探测失败 → 跳过 OCR / 标记 `ocr_unavailable`（或等价），由上层降级；禁止假定 1920×1080。若调用方必须有尺寸，显式报错而非静默错窗。

**验收**：非 1080p 源在探测失败时不产生错位裁剪；稳定 hash 跨解释器一致。

## H3. 前后端一致性

### H3a. 广播队列终态不丢

**现状**：`message_bridge` 队列满时丢最旧；若丢掉 `clip_completed` / `recording_stopped` 等，前端永久「进行中」。

**设计**：

- 定义**终态/关键白名单**：至少含 `clip_completed`、`clip_failed`、`recording_stopped`、`recording_started`（失败类）、`reconnect_failed`、`continuous_highlights`（含 `mapping_fallback`）
- 队列满时：优先丢弃/合并高频可丢失消息（`rooms_updated`、`mse_segment`、`mse_init` 可重要、`export_progress`）
- 若仍无法入队终态：短阻塞或临时扩容一档 + ERROR 日志；禁止静默丢终态

### H3b. 前端断线写操作

**现状**：`websocket.ts` 断线时录制/导出等写操作静默丢弃。

**设计**：写操作要么（1）返回失败 Promise + toast，要么（2）入 pending 队列，重连后重放（需幂等或用户确认）。本批最低交付：（1）；（2）为优选。

### H3c. ClipList 稳定 identity

**现状**：用数组 index 作 `key` 与删除/导出依据，异步 `setClips` 下误删/误导出。

**设计**：一律使用稳定 `clip_id`（或后端保证的唯一 id）；删除/导出/确认 API 传 id 而非 index。同步修复 `test_frontend_stability_guards.py` 所断言的源码结构，清零现有 4 失败。

**验收**：终态入队单测；断线写操作有用户可见失败；guards 全绿。

## H4. 取消机制

### H4a. CPU 回退导出

**现状**：`_export_cpu_fallback` 用 `subprocess.run(timeout=300)`，进度冻结、无法取消。

**设计**：改为 `Popen` + 与主导出一致的进度解析/cancel/watchdog；用户取消或超时可杀进程；禁止再在该路径用不可中断的 `run`。

### H4b. OCR 主循环

**现状**：逐帧无条件 OCR，无帧差预筛、无 `cancel_check`。

**设计**：

- 主循环每 N 帧或每帧调用 `cancel_check`（缺省 no-op）
- 帧差预筛为同批可选：默认轻量阈值，可配置关闭；不改变检测语义的默认开启需有对比测试或可关开关

**验收**：取消导出/分析时 CPU 回退与 OCR 在合理时间内退出（单测用假进程/假 OCR）。

## 非目标（本批）

- Critical 项（应已在 Batch1）
- mypy/CI 红线、Cookie 日志脱敏、监控线程 sleep 调参（Batch3）
- 巨型文件拆分

## 回滚

- H3a 若导致队列堆积：收紧白名单至 clip/recording 终态最小集
- H2b 若选真实能量谷实现不稳：回退到诚实降级
