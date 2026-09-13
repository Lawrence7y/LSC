# 方案 A 实施细案：回看从「流」降级为「本地文件播放」（2026-09-11）

> 上游结论：时间线点击回看后「预览卡一次且回不到直播」的根因不是漏改某处字段，而是
> `recording_review` 用**直播流的机制**（按需新起 FFmpeg 转码 + `-re` + 会话/epoch/60s 窗口/全局 2 路名额）
> 承载**本地文件回看**。本方案删除这条通道，回看改为直接读取本地录制文件。

## 0. 已实测事实（本方案的前提）

按 `lsc/recorder/capture.py:421` 相同参数写入中的 MP4（`-f mp4 -movflags frag_keyframe+faststart`，不用 empty_moov）：

```
[t10] size=2359332 leading=[('ftyp',36),('moov',929),('mdat',239714)]
ffprobe rc=0 duration=10.000000   # 可解析，只报已写入时长
```

即：录制文件本身就是可解析的 fMP4；播放它**不需要**任何转码进程。

## 1. 目标 / 非目标

**目标**
1. 回看不再启动任何 FFmpeg，不再有 review 会话、epoch、窗口、名额。
2. 「回到直播」= 前端一次通道切换，**结构上不可能卡死**（后端无 review 状态可残留）。
3. 消除点击回看时的黑屏空窗（预热切换：新通道首帧到达前不隐藏旧通道）。
4. 录制中回看与回看已归档文件走同一套代码路径。

**非目标**：不改直播预览链路（仍为 MSE + 逐房转码）；不改导出/对齐/切片映射。

## 2. 架构

```
录制中：  FFmpeg 录制进程
            ├─ 输出1  <base>.mp4      （录制文件，OCR/导出用，保持现状）
            └─ 输出2  <base>.dvr.mp4  （回看镜像：-c copy + empty_moov fMP4，moov 一次写定）
                                            │
回看（两种来源同一路径）：               │
  已完成/离线 → <base>.mp4 ─────────────┤
                                        ▼
  渲染进程 LocalFileMseSource ──IPC 读字节──► fMP4 box 切分 ──► 现有 MsePlayer（MSE）──► review <video>
```

**为什么不用 `<video src>` 原生播放**：录制中文件 moov 会被就地重写，且 dev 模式页面来自 http 源，
需要自定义协议 + CSP 放开 `media-src/connect-src`，风险与验证成本都更高。走 IPC + 现有 MsePlayer
复用已验证的 MSE 追加逻辑，且**已完成文件与录制中文件共用同一实现**。
原生 `<video src>` 仅作为后续可选项（见 §8）。

## 3. 冻结契约（跨模块接口，改动前先对齐）

### 3.1 Electron 主进程 IPC（`lsc-electron/electron/main.ts`）

```ts
'local-media:info'      { path: string }
  -> { ok: boolean; size: number; mtimeMs: number; error?: string }

'local-media:read'      { path: string; offset: number; length: number }
  -> { ok: boolean; bytesRead: number; size: number; eof: boolean; data?: Uint8Array; error?: string }

'local-media:allow-root' { root: string } -> { ok: boolean; roots: string[] }
'local-media:roots'      {}                -> { roots: string[] }
```

校验（单一 helper，任何失败都返回 `{ok:false,error}`，绝不抛）：
- path 非空、`path.resolve` 后为绝对路径且不含 `..` 越界；
- 扩展名 ∈ `.mp4 .mkv .flv .mov .ts .m4s .m4v`（大小写不敏感）；
- 解析后路径必须位于白名单根之下（Windows 大小写不敏感 + 分隔符边界匹配）；
- 白名单来源：启动时读项目根 `settings.json` 的 `output_dir`，加上 `local-media:allow-root` 注册的根。

preload 暴露：`window.electronAPI.localMedia = { info, read, allowRoot, roots }`。
`length` 上限 8 MiB；读取用 `fs.open/read`，`eof = offset + bytesRead >= size`。

### 3.2 后端（python）

> **⚠️ 实施现状（2026-09-11 实测更正）**：下面的镜像输出**只在 legacy `lsc/recorder/capture.py` 路径落地**；
> 当前 V2 录制走 `lsc/core/services/shared_ingest.py`（`grep dvr` 为空，没有第二个输出），
> 因此 `stop_recording` 响应里 `dvr_output_path` 恒为 `''`、输出目录里没有 `.dvr.mp4`，
> 前端**永远回退到 `record_output_path`**。这不影响可用性——V2 主录制本身就是
> `frag_keyframe+empty_moov+default_base_moof`（与镜像同格式、moov 一次写定、边录边可解析），
> 但 `capture.py` 里"主输出必须 faststart 才能给 OCR 抽帧"的注释与 V2 的实际选择相反：
> 动 V2 的 `-movflags` 前必须同时验证回看与 OCR 抽帧。若要补齐镜像，需在 shared_ingest 侧
> 实现第二个输出并让 `dvr_output_path` 随归档改名同步。

- 设置项 `dvr_mirror_enabled: bool = True`（`settings.json`，缺省开）。
- 录制启动时追加第二个输出：`-c copy -f mp4 -movflags empty_moov+default_base_moof+frag_keyframe "<dvr_path>"`，
  `dvr_path = <record_path 去扩展名>.dvr.mp4`；空路径/失败不影响录制主输出。
- `RoomSession.dvr_output_path: str = ""`，与 `record_output_path` 同生命周期设置 / 清空 /
  **随归档重命名同步改名**（`_录制中.mp4` → 归档名 时 `..._录制中.dvr.mp4` → `归档名.dvr.mp4`）。
- 所有房间序列化点（`room_handler._rooms_list`、`:4498`、`recording_handlers`）新增 `dvr_output_path`。
- **删除**（整条 review 通道）：`_review_streamers`/`_review_streamers_lock`/`_MAX_CONCURRENT_REVIEWS`、
  `_start_recording_file_mse`、`start_recording_review`、`close_recording_review`、
  `_on_file_mse_error`、`_is_normal_file_playback_end`、`_offline_file_review_in_progress`、
  `_handle_mse_preview` 的 `is_review / _reset_room_live` 分支、`request_mse_init` 的 review 分支、
  `review_phase` 广播、MSE 通道 `channel='review'` 路由、
  `RoomSession` 的 `active_preview_channel / review_session_id / review_start_sec / review_window_end_sec / preview_review_start_sec`
  （以及 `preview_review_start_sec` 仍被前端读到的字段改由前端本地状态提供）。
- 离线退化（原 `_start_recording_file_mse(stop_recording_if_active=True)` 两处调用 `:4562` `:6182`）改为：
  停直播预览 sink → `preview_mode='degraded'` + `preview_error=<友好文案>` + 广播；
  **不启动任何文件流**，前端看到 `preview_mode==='degraded'` 且有 `record_output_path` 时改用本地文件回看。
- `start_recording_review`/`close_recording_review` 保留为**声明式桩**：返回
  `{success:false, error:'deprecated: use local file playback'}`（防旧前端版本误用静默失败）。

### 3.3 前端

**回看通道状态放在 `uiState`（前端本地权威，`rooms_updated` 不得覆盖）**：

```ts
// store/appStore.ts RoomUIState
preview_channel?: 'live' | 'review'   // 通道
review_path?: string                  // 回看来源文件（录制中 .dvr.mp4，否则 record_output_path）
review_seek_sec?: number              // 回看目标位置（录制轴秒）
review_offset_sec?: number            // 轴偏移（带符号）：recordingAxis = playerTime + offset
review_feeding?: boolean              // 首帧是否到达（驱动预热切换）
review_error?: string
```

- 动作：`enterReview(roomId, {path, seekSec})` / `exitReview(roomId)` /
  `setReviewFeeding` / `setReviewError` / `setReviewOffset`。
- `setRooms()` 合并规则：本地回看激活时**强制** `preview_mode='recording_review'` 并把
  `preview_review_start_sec` 取本地 `review_offset_sec`；未激活时用后端快照。
  这样既保住既有 `isNoDvrPreviewMode(room.preview_mode)` 的全部调用点，又让权威只剩一处。

**轴语义（与旧实现的关键差异）**：回看播放器喂入的是**文件原始 PTS**（不做 `-start_at_zero` 归一化），
故 `review_offset_sec` 通常为**负值**（大基座）。所有消费方必须带符号相加：

| 位置 | 处理 |
|---|---|
| `ControlBar.reviewStartSec` / `RoomCard.reviewStartSec` | 去掉 `Math.max(0, …)`，带符号 |
| `usePlayheadSampling` 回看分支 | 同上 |
| `timelineViewModel` / `resolveReviewSeekEdge` | 先把播放器时间加成录制轴秒再算跨度 |
| `handleTimelineSeek / ScrubEnd / SeekByDelta / 拖拽标记` | 经 `reviewPlayerTimeFor(rid, 录制轴秒)` 换算 |

**新增服务**
- `src/services/localMediaReader.ts`：IPC 封装 + 能力探测；`ensureLocalMediaRoot()` 用**后端下发的
  房间录制路径所在目录**向主进程注册白名单（主进程无 settings.json 时白名单为空，读取会一律失败），
  `dirnameOf()` 推导目录，白名单错误自动重试一次。
- `src/services/fmp4Box.ts`：纯函数 fMP4 顶层 box 解析 + 增量切分器（`Fmp4BoxSplitter`）、
  `moov` 内视频轨 `trackId/timescale` 解析、`moof/tfdt` 时间解析、`pickStartFragmentIndex`、
  `resetPosition(baseOffset)`（seek 后仍产出**绝对**字节偏移）。
- `src/services/localFileMseSource.ts`：顺序读（4 MiB/块）→ 切分 → 喂 `MsePlayer`；
  **有界定位**（`estimateByteOffset` 按平均码率外推 → 16 MiB 窗口内 `findFragmentBoundary` 找自洽
  `moof` 链 → 从该处继续顺序解析），模块级索引缓存跨会话复用；前瞻窗口（默认 12s）防 MSE pending 溢出；
  `follow` 按 `room.is_recording` 决定是否追增；`onFirstMedia` 供预热切换；`onIndex` 回填轴偏移。

**VideoPreview**
- 回看通道 = 独立 `MsePlayer(isFile:true, channel:'review')`，由 `LocalFileMseSource` 直喂；
  切换/再次 seek 时整体重建（MSE 时间轴从目标处单调连续）。
- WS 分片只喂直播播放器（`registry.live`），回看不再经 WebSocket。
- **预热切换**：`reviewVisible = isReviewActive && (feeding || playing/paused)`；
  首帧到达前 live 画面保持可见，只显示「正在准备回看…」轻提示 → 消除"黑屏卡一次"。
- 回看源失效（归档改名）时按房间最新 `dvr_output_path/record_output_path` 自动重进，并可选一键「回到直播」。

**Workbench**
- `mseSeek` 缓冲外 → `enterReview`（本地，无 WS 往返、无 8s 防抖 ref）；重复点击同位置直接忽略。
- `enterTimelineLive`：回看房间走 `exitReview`，`goLive()` 固定取 `registry[rid].live`
  （回看期间 `registry.player` 指向 review 播放器）；仅 `degraded`（离线）房间提示"无实时沿"。
- `handleGoLive` 删除 no-DVR 早退，统一收口到 `enterTimelineLive`。
- `ControlBar.goLiveDisabled` 只在 `preview_mode==='degraded' && !is_recording` 时为真。
- 关闭预览 / 删除房间 / 断开 / 刷新预览都会显式 `exitReview`（前端持有状态，必须收口）。

**删除**：`getMseReviewInitCache` / `drainPendingMseReviewSegments` / `_mseReviewInitCache` /
`_mseReviewSegmentCache` / `_feedMseSegment` 的 review 分支与 `streamId` 会话匹配 /
`RoomSession` 的 `active_preview_channel·review_session_id·review_start_sec·review_window_end_sec`。

## 4. 落地顺序（每步可独立验证）

1. **S1 主进程 IPC**（§3.1）——独立可测，无行为变化。
2. **S2 后端**：录制镜像输出 + 字段暴露 + 删除 review 通道（§3.2）——python 守卫测试转绿。
3. **S3 前端**：本地读源 + 通道状态 + VideoPreview/Workbench 接线（§3.3）。
4. **S4 清理与回归**：删除 `timelineCoords` 中 record-review 专用换算、更新守卫测试、跑
   `vitest` + 全量 `pytest`。

## 5. 验收用例

1. 点时间线回看 → 回看画面出现，**live 不再黑屏空窗**（切换期间旧通道继续显示）；
2. 点 LIVE → 立即回到直播沿（`goLive()`），无 toast、无 disabled；
3. 反复「回看 → LIVE → 回看」20 次，通道状态无残留、无 FFmpeg 进程增长；
4. 3 个房间同时回看（旧实现仅有 2 路名额并驱逐）——全部正常，无冻结画面；
5. 录制中回看 + 边录边追（文件增长 30s 后回看能继续追上）；
6. 回看中停止录制 → 归档改名后回看仍可用（路径按 recording_id 重新解析）；
7. 主播下线离线回看：无预览进程，直接播本地文件；
8. 全程 `Get-Process ffmpeg` 数量 = 录制 + 直播预览，**不含任何回看进程**。

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 录制中镜像文件为空/未落盘 | 首帧前显示「正在准备回看…」，`feeding=false` 时保持 live 画面可见 |
| `dvr_mirror_enabled=false` 或镜像缺失 | 回退到 `record_output_path`（已完成文件场景），再不行回退到「live 缓冲内 seek」 |
| 归档改名导致路径失效 | 用 `recording_id` 校验 + 每 500ms `info` 失败即按最新 `record_output_path` 重解析 |
| IPC 读盘带宽与录制写盘竞争 | 4 MiB 分块 + 追增间隔 500ms + 仅在 review 通道激活时读取 |
| 参数对象/字段删除破坏外部调用 | `start_recording_review`/`close_recording_review` 保留声明式桩 |

## 6.1 实施记录（2026-09-11）

**已落地（前端 + Electron，均有单测/类型检查）**
- `electron/localMedia.ts` + `main.ts` IPC 四通道 + `preload` 暴露（§3.1 全量实现，含 realpath 复核与
  扩展名白名单；6 项校验自检 + junction 逃逸用例通过）。
- `src/services/fmp4Box.ts` / `localFileMseSource.ts` / `localMediaReader.ts` 及 22 项单测
  （含分片推送、tfdt/timescale 解析、有界定位落点、追增、EOF 收尾、白名单自举）。
- store 回看通道 + VideoPreview 预热切换 + Workbench 进入/退出回看与 LIVE 收口 + 四处显式 `exitReview`。
- 前端 `tsc --noEmit` 与 `vitest run` 全绿（含 i18n 覆盖率守卫）。

**待后端落地（S2，契约见 §3.2）**：录制第二输出 `.dvr.mp4`、`dvr_output_path` 暴露与随归档改名、
删除 review 流通道与离线退化改造。在 S2 落地前，前端会自动回退到 `record_output_path`
（录制文件本身，moov 在写入中会被就地重建，可用性略低于镜像文件）。

### 6.2 首轮联调发现并修复的三个缺陷（"轴切到回看轴但画面仍是直播"）

现象：点时间线后轴/角标变成「录制回看轴」，但预览画面仍是直播。根因是**回看通道一帧都没喂进 MSE**
（`review_feeding` 恒 false，预热切换规则于是继续显示 live 画面）。三个叠加缺陷：

1. **命中索引缓存的二次进入不回看**：旧 `_locate()` 在 `_axisOffset` 未建立时（缓存命中不读文件头 ⇒ 不走
   `_consume` ⇒ 不 `_publishAxisOffset`）**直接 return**，且 `_ensureInit` 也跳过了 init 段；读游标还停在
   上一趟的扫描位置 ⇒ 队列里根本没有目标 fragment ⇒ `_canStartFeeding` 永远等不到 `t > 目标` ⇒ 一帧不喂。
   修复：`_ensureInit` **每会话都保证 init 就绪**（缓存命中用缓存 init 字节恢复解析器+MsePlayer）；`_locate` 先
   `_publishAxisOffset()` 再定位，目标被索引**连续覆盖**时**直接跳到**该 fragment（禁止回退到文件头顺序重读）。
2. **越界目标死等**：目标超出已写入范围（贴直播沿点击、或轴换算错了）时没有任何退化路径。
   修复：读到写入游标后仍找不到目标 ⇒ **退化为从最近可读位置起播**并上报 `targetClamped`。
3. **回看目标轴靠反推不稳**：`mseSeek` 用播放器轴 `t` 反推录制轴，依赖 `recording_to_preview_delta`；
   标定缺失/过期时回退 `t - content_offset` 是**错误的轴** ⇒ 越界目标（触发缺陷 2）。
   修复：调用方**显式传显示轴** `opts.axisSec`（时间线点击/拖拽/标记本来就持有该值），对齐就绪才用
   `commonToRecording`，否则显示轴即录制轴。

配套：`findCoveringFragment`（索引空洞感知的落点选择，避免多次 seek 后误跳）、索引按时间有序 + 偏移去重、
首次会话内**首帧超时（8s）→ `onError`** 带实测范围（目标/轴偏移/文件时间跨度/队列长度），
UI 显示「回看不可用 + 回到直播」而不是静默停在直播。

**后续可选优化**：① 已完成文件改用 `<video src="lsc-media://…">` 原生播放（需自定义协议 + CSP）；
② 后端持久化 fragment 索引（目前首次定位依赖头部码率外推，极端码率波动下可能多读一个窗口）；
③ `mseBinary.ts` 的 v2 通道字段可在后端确认后移除。

## 7. 与既有文档的关系

- 取代 `CLAUDE.md §8.8` 中「Live/Review 双通道 + review_session_id」的实现描述：隔离不再是"靠契约维持"，
  而是天然成立（回看根本不经后端流）。
- 不影响 `docs/plans/low-latency-preview-architecture-20260910.md`（该方案只动直播预览协议偏好）。

## 8. 后续可选优化（不在本次范围）

- 已完成文件改为 `<video src="lsc-media://...">` 原生播放（需 `protocol.registerSchemesAsPrivileged` +
  CSP `media-src lsc-media:`），换取零索引构建的原生 seek；
- 镜像文件可选 `-c copy` 之外的 `-f mpegts`（更省磁盘、追加友好）。
