# 虎牙单连接上游即探测设计规格

> 状态：提案  
> 日期：2026-08-13  
> 上游规格：
> - [多平台直播稳定性总体架构规格](2026-08-09-multiplatform-stability-overview-design.md)
> - [解析、媒体探测与流租约设计规格](2026-08-09-resolver-probe-lease-design.md)
> - [房间级进样监督器设计规格](2026-08-09-ingest-supervisor-design.md)
> 主要代码范围：`lsc/platforms`、`lsc/core/services`、`lsc/recorder/capture.py`、`python-backend/handlers/room_handler.py`、运行时 `lsc_config.json`

---

## 1. 目的

止住虎牙签名流被重复打开后的 FFmpeg 403/EOF 重启风暴，并让 `max_connect_concurrency=1` 的签名平台按「上游即探测」工作：签名 URL 在整个租约生命周期内只被真实共享上游打开一次。

本规格修正 2026-08-09 租约规格中「未经远端 Probe 的候选不得进主进样」对单连接签名平台的适用方式：对此类平台，共享上游的首个可消费媒体包就是探测成功，禁止再对同一签名地址发起 ffprobe/FFmpeg 预检。

---

## 2. 已确认的现场根因

1. 虎牙适配器把页面返回的 `sFlvAntiCode` 原样拼进 URL，不会为 Probe、元数据 ffprobe、录制、预览生成独立签名。`tx/al/hs` 三条 CDN 共用同一 `wsTime/wsSecret` 签名族。
2. V2 链路会对同一签名依次做媒体 Probe、元数据 ffprobe、共享上游 FFmpeg。前一次探测成功后，正式连接收到 403、EOF 或 `code=0`。
3. `SharedRoomIngest.start_preview()` 在无订阅者时返回 `ok=True`，有进程时只检查「没有立即退出」。handler 随即广播 `mse_reconnected` 并清空重试次数。进程几秒后退出又从 `attempt 1/3` 开始。
4. `recovery_policy.mark_failed_candidate()` 把虎牙 `AUTH_EXPIRED/SIGNATURE_EXPIRED` 改写成 `CDN_FORBIDDEN`，于是拿着同一失效签名在 CDN 之间轮询。预览编码器退出也会强刷虎牙地址。
5. 上游读取线程同步写录制 stdin。Windows 上非阻塞管道是 no-op，录制管道写满会堵住读取，预览一起饿死。
6. `StreamCapture.stop()` 在优雅停止结束后再次读取 `self._process` 并关闭管道，可能关掉已经启动的新 FFmpeg。
7. 运行时 `%APPDATA%/lsc-electron/lsc_config.json` 已打开 V2 且 allowlist 含 `huya`。即使 `shared_ingest_enabled=false`，`ingest_supervisor_v2` 仍强制虎牙走共享管线。虎牙真实录制/预览短测未通过，支持级别保持 `PREVIEW`。

页面 `force_refresh` 不能铸造独立签名，禁止再把「录制中刷新预览 URL」当成并发解法。

---

## 3. 范围

做：

- 阶段 0 止损：配置去掉虎牙 V2，代码硬闸禁止虎牙进 V2，关闭虎牙自动预览重连。
- 阶段 1：虎牙及后续所有 `signed_url=true` 且 `max_connect_concurrency=1` 的平台改为上游即探测。
- 阶段 1 同期：首包成功判定、重试预算、签名族作废、录制/预览解耦队列、启动停止 generation CAS。
- 阶段 2：自动化证明后解除硬闸，虎牙重新进入 V2 allowlist；支持级别仍为 `PREVIEW`，不以本规格将虎牙标为 `STABLE`。

不做：

- 把 ffprobe 的 TCP 连接交接给 FFmpeg。
- 为虎牙生成第二套 `wsSecret`。
- 改变 B 站、抖音等 `max_connect_concurrency>=2` 平台的远端 Probe。
- 把虎牙标记为 `STABLE` 或宣称 15 分钟真实稳定性已通过。

止损期间虎牙走 legacy 双进程。录制和预览仍可能抢同一签名，但不会再出现 V2 假成功把 3 次重试打穿、半小时拉起上百次 FFmpeg 的风暴。用户仍可手动开预览。

---

## 4. 能力声明

在 `PlatformCapabilities` 增加两个字段，由通用运行时读取，禁止在 orchestrator/handler 里写死 `if platform == "huya"` 做策略分支（恢复分类可继续调用适配器声明的策略函数）。

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `probe_profile` | `str` | `"default"` | `"default"` / `"ffprobe"`：现有远端 ProbeService。`"ingest"`：禁止对候选 URL 做远端媒体探测，由共享上游首包完成探测。 |
| `preview_auto_reconnect` | `bool` | `true` | 为 `false` 时预览出错只报错，不进入自动重连循环。 |

虎牙阶段 0：

- `probe_profile` 暂不生效，因为硬闸使虎牙不进 V2。
- `preview_auto_reconnect=false`
- `preview_refresh_when_recording=false`（立即关掉；即使仍走 legacy，也不允许为“独立签名”强刷）

虎牙阶段 1 解除硬闸后：

- `probe_profile="ingest"`
- `preview_auto_reconnect=true`（必须同时具备第 7 节的稳住窗口，否则不得打开）
- `preview_refresh_when_recording=false`
- `max_connect_concurrency=1`
- `signed_url=true`
- `support_level="PREVIEW"`

判定「需要上游即探测」的通用条件（单一函数，例如 `uses_ingest_probe(capabilities)`）：

```
capabilities.probe_profile == "ingest"
或（capabilities.signed_url and capabilities.max_connect_concurrency <= 1）
```

后者作为硬兜底，避免只改了并发声明却忘了改 `probe_profile`。`probe_profile` 为 `"default"` 或 `"ffprobe"` 时保持现有远端 Probe。

V2 硬闸：

```
is_platform_pipeline_v2_enabled("huya") 必须返回 false
```

即使运行时 allowlist 含 `huya`、即使 `shared_ingest_enabled=true`，虎牙也不得进入 Resolver/Probe/Lease/IngestSupervisor。硬闸放在 `is_platform_pipeline_v2_enabled`（以及 `_shared_ingest_v2_enabled` 若其绕过 allowlist 的 `shared_ingest_enabled=true` 短路径）。阶段 2 解除方式：删除该硬编码 blocklist 项。不得只把 `huya` 加回 allowlist 却留着硬闸，也不得只靠 allowlist、没有代码闸。

---

## 5. 主链路

```
页面解析（HTTP 只打虎牙房间页，不打开 CDN 媒体）
  → 规范化候选，计算 signature_family_id
  → 按历史健康 / 非隔离 CDN / 非 al 优先，选出恰好 1 条候选
  → 签发 StreamLease（probe_summary.mode = "ingest"，consumed = false）
  → IngestSupervisor 只拉起一条共享上游 FFmpeg
  → 上游 -i 该候选 URL，同时把 lease.consumed = true
  → 首个合法 MPEG-TS 包 = 探测成功，写入 probe_summary
  → RecordingSink / PreviewSink 只消费本地 TS
  → 分辨率/帧率只探测本地 TS 或跳过，直到本地媒体可用
```

禁止事项：

1. 对 `probe_profile="ingest"` 的候选调用 `ProbeService` / `probe_candidates` / `_probe_metadata_async(stream_url)`。
2. 验收 CLI、控制面探测、MSE 下播探测对同一签名 URL 再开一条媒体连接。下播判断必须走页面/API 元数据，或观察已有上游是否无数据，不得新开 CDN。
3. 预览在录制中 `refresh_stream_url(force=True)` 以获取“独立签名”。
4. 把已经 `consumed=true` 的租约 URL 再次传给任何 FFmpeg/ffprobe。
5. 用 `source` 画质别名静默换成另一条未占用过的 CDN URL（现有 `quality_urls` 兼容桥必须只暴露当前租约那一条）。

先开录再开预览、先开预览再开录，都是向同一 `IngestSupervisor` 挂 sink，不新建远端连接。

---

## 6. 签名族

`StreamCandidate` 增加 `signature_family_id: str`。日志和广播只允许出现该 id 的短哈希，不得出现 `wsSecret` 原文。

虎牙计算规则：

1. 解析媒体 URL query。
2. `family_material = wsSecret + "|" + wsTime`；两者都缺失时退回完整 anti-code query（去掉只随 host 变化的字段）。
3. `signature_family_id = sha256(family_material)[:16]`。
4. `tx/al/hs` 只要 `wsSecret/wsTime` 相同，必须得到同一个 `signature_family_id`。
5. `candidate.fingerprint` 仍可含 host，用于区分线路；恢复决策以 `signature_family_id` 为准。

`LeaseManager` 在签发时记录 family id。任一 FFmpeg 对该 URL 的 `-i` 发生后，该 family 视为已占用。

同一 family 内禁止：

- 并发 Probe 多条 CDN。
- Probe 成功后再开上游。
- 403 后用同一 family 换 host 重试。

---

## 7. 成功判定与重试预算

启动成功和可清零重试是两件不同的事。

| 阶段 | 条件 | 运行时状态 | 对 UI / 重试的效果 |
|---|---|---|---|
| 进程已拉起 | 上游 FFmpeg `poll() is None` | `CONNECTING` | 不算成功。禁止 `mse_reconnected`。禁止 `ok=True` 作为预览已接通。 |
| 媒体已接通 | 上游首个同步字节为 `0x47` 的 188 字节 TS；若请求了预览，还要收到 MSE init（ftyp+moov）和首个 media segment（moof+mdat）；若请求了录制，录制 sink 已写入媒体字节 | `RUNNING`（可标 `preview_phase=live`） | 画面可以出现。重试次数不清零。 |
| 连接已稳住 | 媒体已接通后连续 `DURABLE_SUCCESS_SEC=30` 秒上游仍存活且字节持续增加 | 保持 `RUNNING` | 此时才允许广播 `mse_reconnected`，并把该房间预览重试计数清零。 |

`SharedIngestStartResult` 区分受理和媒体接通：

- `accepted`：请求已被监督器受理（将启动或已挂接）。
- `media_ready`：已满足「媒体已接通」。
- `ok` 不得再单独表示成功。过渡期若保留 `ok`，必须等价于 `media_ready`，不得等价于「进程还在」。

硬性规则：

1. `start_preview()` 在 `_preview_subscribers` 为空时 `accepted=true, media_ready=false`。不得把这种情况报成预览已接通。
2. handler 只有 `media_ready` 才可把预览标为 LIVE；只有稳住窗口达成才清空 `_mse_reconnect_state` 的 attempts 并广播 `mse_reconnected`。
3. `_mse_reconnect_state` 在假启动、进程秒退、再次 `on_error` 之间必须保留 `attempts`。只有稳住窗口达成，或用户手动关闭预览，才删除或清零。
4. 稳住窗口内退出：消耗 1 次重试，指数退避，不清零。耗尽后停止自动重连，提示手动重开。
5. 阶段 0 虎牙 `preview_auto_reconnect=false`，出错直接 `mse_error`，不进循环。
6. 30 秒只约束重试清零，不让用户空等 30 秒才看到画面。

---

## 8. 失败分类与恢复

删除 `recovery_policy.py` 中虎牙 `AUTH_EXPIRED/SIGNATURE_EXPIRED → CDN_FORBIDDEN` 的改写。

对上游即探测平台，恢复动作由 `(capabilities, failure_kind, saw_first_ts)` 决定，而不是平台名字符串。HTTP 403 对签名单连接平台按签名族失效处理，即使通用分类器因规则顺序把它标成 `CDN_FORBIDDEN`。

| 条件 | 动作 | 是否重新解析 | 是否隔离 CDN |
|---|---|---|---|
| 403、签名失效、鉴权过期 | 作废整个 `signature_family_id`，撤销当前租约 | 是，带退避 | 否 |
| 尚无首包就 EOF / `code=0` / 立即退出 | 同上，视为签名族不可用 | 是，带退避 | 否 |
| 已有首包后的 TCP 超时、连接重置、DNS | 隔离该 `cdn_id`（房间+网络作用域，TTL 5 分钟） | 是，必须拿新签名再连 | 是 |
| `PREVIEW_ENCODER_FAILURE`、预览 15 秒无 stdout | 只重启预览 sink | 否 | 否 |
| `RECORDING_SINK_FAILURE`、录制写入超时 | 只重启录制 sink | 否 | 否 |
| 主播下播 | `OFFLINE` | 按房间轮询，不高速重试 | 否 |

任何下一次远端打开都必须使用尚未 `consumed` 的新租约。禁止把已失败 URL 原样交给新 FFmpeg。隔离 CDN 只影响下一次解析时的候选排序，不授权复用旧签名。

`refresh_triggers` 对虎牙保留 `SIGNATURE_EXPIRED`；`CDN_FORBIDDEN` 不再单独触发“换线但保留签名”。`should_force_recovery` 不得把预览编码器退出当成需要强刷页面的信号。

`_should_refresh_failed_stream` 对上游即探测平台：

- 签名族失效 / 403：刷新=重新解析新 family，不是复用缓存 URL。
- 预览编码器故障：返回 false。
- 线路故障：刷新=重新解析，且跳过被隔离 CDN。

---

## 9. 本地分发与 Windows 管道

预览已有有界队列和独立写线程。录制必须同等对待。

```
UpstreamReadThread
  只负责读上游 stdout、校验 TS 同步、按 generation 丢弃过期数据
  → recording_queue（有界）
  → preview_queue（沿用现有 drop_oldest）

RecordingWriteThread  消费 recording_queue，写录制 stdin
PreviewWriteThread    消费 preview_queue，写预览 stdin
```

规则：

1. 上游读取线程禁止调用录制/预览的 `_write_all`。
2. 录制队列默认容量与预览相同（2MiB，可配 `shared_ingest_recording_queue_bytes`）。满时丢最旧包，累计 `recording_dropped_bytes`，不得阻塞读取。
3. 连续丢包超过 5 秒或队列持续满，只把录制标为 `RECORDING_SINK_FAILURE` 并重启录制 sink；上游和预览继续。
4. 预览队列满沿用现有 `drop_oldest`，不得反向堵住录制。
5. Windows 上继续不把管道 fd 设成非阻塞读；靠有界队列和独立写线程提供超时与隔离，而不是依赖 `set_stream_nonblocking`。

---

## 10. Generation CAS

每个房间维护三套单调递增代次：`upstream_generation`、`preview_generation`、`recording_generation`。`StreamCapture` 另有 `capture_generation`。

规则：

1. `start_*` 在锁内 `generation += 1`，把新进程与该代次绑定。
2. 停止、错误回调、stderr 线程、写线程只操作自己捕获的 `(proc, generation)`。
3. `StreamCapture.stop()` 结束时禁止再次读取 `self._process` 来关闭管道。只能关闭进入 `stop()` 时快照的那个 `proc`。若当前 generation 已变化，不得把 `self._process` 置 `None`，也不得改状态为 `STOPPED`。
4. `stop_async` 后台线程必须带上发起停止时的 generation。
5. IngestSupervisor 恢复回调若 generation 已变，不得切换上游或把状态写回 `RUNNING`。
6. 旧预览/录制 sink 的 stdout 不得写入新 generation 的 MSE/文件。

---

## 11. 阶段 0 止损落地

按顺序执行，可单独部署：

1. 运行时配置 `C:/Users/Administrator/AppData/Roaming/lsc-electron/lsc_config.json`：从 `platform_pipeline_v2_allowlist` 移除 `"huya"`。仓库根目录 `lsc_config.json` 不加入虎牙。
2. 代码硬闸：`is_platform_pipeline_v2_enabled` 对 `huya` 恒为 false；`_shared_ingest_v2_enabled` 在 `shared_ingest_enabled=true` 时也不得把虎牙送进监督器。
3. `preview_auto_reconnect=false`，`preview_refresh_when_recording=false`。`room_handler` 的 shared 与 legacy 两条 MSE 重连循环都要遵守该能力。
4. 重启 Python 后端，释放旧租约、监督器和 FFmpeg。不杀用户未要求停止的非虎牙房间以外的全局状态时，以重启后端为准，因为监督器与签名租约必须丢掉。
5. 日志应出现一次明确 INFO：虎牙 V2 被硬闸拒绝，走 legacy。

阶段 0 验收：半小时内虎牙预览失败不得再出现「重连成功」后立刻再 `attempt 1/3` 的循环；预览进程启动次数应与用户手动操作同量级，而不是上百次。

---

## 12. 主要模块

| 模块 | 职责 |
|---|---|
| `lsc/platforms/models.py` | `probe_profile`、`preview_auto_reconnect`、`signature_family_id`、lease `consumed` |
| `lsc/platforms/capabilities.py` | 虎牙能力声明 |
| `lsc/config.py` | V2 硬闸；录制队列字节配置 |
| `lsc/platforms/huya.py` | 计算签名族；CDN 隔离仅用于线路故障 |
| `lsc/platforms/resolver.py` | `probe_profile="ingest"` 时跳过 `probe_candidates`，按健康选 1 条签发租约 |
| `lsc/platforms/recovery_policy.py` | 签名族作废 vs CDN 隔离；删除错误改写 |
| `lsc/platforms/failure.py` | 为签名单连接提供 403→族失效的恢复输入（分类可保留 CDN_FORBIDDEN 标签，动作必须按第 8 节） |
| `lsc/core/orchestrator.py` | 禁止元数据 ffprobe 打 CDN；ingest 选路 |
| `lsc/core/services/shared_ingest.py` | 首包/稳住窗口、录制队列与写线程、generation |
| `lsc/core/services/ingest_supervisor.py` | 单上游挂接、恢复动作分派 |
| `lsc/recorder/capture.py` | stop generation CAS |
| `python-backend/handlers/room_handler.py` | 预览成功契约、自动重连门禁、禁止虎牙录制中强刷 |
| `lsc/platforms/acceptance.py` | 虎牙验收不得远端 Probe 同一签名 |

---

## 13. 测试要求

现有「进程对象存在即成功」的测试必须改掉，不能再把 `ok=True` 当成媒体已接通。

最低自动化覆盖：

1. **签名只打开一次**：假 CDN 对同一 `wsSecret` 第一次 GET 返回 FLV/TS，第二次返回 403。`probe_profile="ingest"` 时上游能跑起来；若错误地先 Probe 再进样，进样必须 403。断言媒体 GET 次数为 1。
2. **同源签名族**：`tx` 与 `al` 仅 host 不同、query 中 `wsSecret/wsTime` 相同，`signature_family_id` 相等。403 后不得对另一 host 发起第二次媒体 GET。
3. **线路故障才换 CDN**：第一次连接 TCP 超时，允许重新解析后打开另一 CDN；第二次必须是新 `wsSecret`。
4. **假成功**：`start_preview` 无订阅者时 `media_ready` 必须为 false；进程未出 TS 不得广播重连成功；模拟 3 秒后退出，attempts 应为 2 而不是又从 1 开始。
5. **稳住窗口**：首包后 30 秒内退出不清零；30 秒后退出才允许从 1 再计。测试可把窗口注入为 0.05 秒。
6. **预览编码器退出**：不调用 `refresh_stream_url(force=True)`，不标记 CDN，不作废签名族。
7. **录制队列隔离**：录制 stdin 阻塞超过写超时，上游读取与预览队列仍前进；录制进入 sink 失败而预览不随死。
8. **Capture CAS**：`stop_async` 进行中启动新进程，旧停止不得关闭新 PID，也不得把新进程 stdin/stderr 关掉。
9. **硬闸**：allowlist 含 `huya` 且 `shared_ingest_enabled=true` 时，`is_platform_pipeline_v2_enabled("huya")` 仍为 false，`_shared_ingest_v2_enabled` 为 false。
10. **B 站回归**：`probe_profile="ffprobe"` 路径仍先 Probe 再进样；现有平台/V2 测试保持通过。

禁止用「字符串拼进 URL」或「Popen 对象非空」冒充上述场景。

---

## 14. 验收标准

阶段 0：

- 运行时 allowlist 无 `huya`。
- 代码硬闸生效。
- 虎牙预览失败不会自动重连。
- 后端重启后无旧虎牙监督器/租约。

阶段 1（自动化）：

- 第 13 节测试全部通过。
- 定向 pytest 覆盖本规格涉及模块；全量回归不得把 B 站/抖音 Probe 路径改坏。

阶段 2：

- 解除硬闸并把 `huya` 加回运行时 allowlist 之前，第 13 节必须为绿。
- 真实验收：至少一次录制-only、一次预览-only、一次并行短测不再以 `SIGNATURE_EXPIRED` / 二次 403 为主因失败。未跑真实房间前支持级别保持 `PREVIEW`，不得改为 `STABLE`。

现场对照（修复后不应再出现 2026-08-13 那种 30 分钟画像）：

- 预览进程启动次数与用户操作同量级。
- 「重连成功」次数不得接近启动次数。
- 同一 `wsSecret` 的媒体 GET 在单次租约内为 1。
- 录制文件时长与墙上时间同量级，而不是约 1 秒。

---

## 15. 对既有规格的修正

1. [resolver-probe-lease §4 / §12](2026-08-09-resolver-probe-lease-design.md)：`probe_profile="ingest"` 时，「探测成功」定义为共享上游首个可消费媒体包，而不是独立 ffprobe。禁止对已选候选再做一次等价打开。
2. [resolver-probe-lease §5](2026-08-09-resolver-probe-lease-design.md)：单连接签名平台的远端探测并发为 0；候选选择用解析元数据和历史健康，不用并行 Probe。
3. [ingest-supervisor §3](2026-08-09-ingest-supervisor-design.md)：虎牙不得降级为双上游。legacy 双进程只作为阶段 0 止损，不是阶段 1 的目标架构。
4. 虎牙 `preview_refresh_when_recording` 从 true 改为 false，与「共享一条上游」一致。
