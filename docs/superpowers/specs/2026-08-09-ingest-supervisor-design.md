# 房间级进样监督器设计规格

> 状态：提案  
> 日期：2026-08-09  
> 上游规格：[解析、媒体探测与流租约设计规格](2026-08-09-resolver-probe-lease-design.md)  
> 主要代码范围：lsc/core、python-backend/handlers

---

## 1. 目的

定义同一房间单远端上游、多独立下游的媒体运行时，解决录制与预览重复建连、互相影响、重连风暴和状态不一致问题。

---

## 2. 核心模型

每个 room_session_id 对应一个 IngestSupervisor：

    StreamLease
      -> UpstreamReader
      -> MediaDistributor
          -> RecordingSink
          -> PreviewSink
          -> DiagnosticSink（可选）

监督器拥有上游生命周期和租约代次，不拥有用户任务、平台凭据明文或后处理业务。

---

## 3. 单上游约束

1. 同一房间会话默认只允许一个主远端媒体连接。
2. 录制和预览必须优先挂接同一上游。
3. 若协议或平台限制使共享不可行，必须由能力或运行时探测明确标记，并记录降级原因。
4. 降级为双上游时必须遵守平台连接并发限制，且 UI 显示 degraded。
5. 上游切换采用租约代次控制，旧连接产生的数据不能写入新代次 Sink。

---

## 4. 监督器状态机

状态：

- IDLE
- STARTING
- CONNECTING
- RUNNING
- DEGRADED
- REFRESHING
- RECONNECTING
- BACKING_OFF
- OFFLINE
- AUTH_REQUIRED
- FAILED
- STOPPING
- STOPPED

关键转换：

| 当前状态 | 事件 | 目标状态 |
|---|---|---|
| IDLE | attach 首个 Sink | STARTING |
| STARTING | 获得有效租约 | CONNECTING |
| CONNECTING | 首个可消费媒体包 | RUNNING |
| RUNNING | 单个 Sink 故障 | DEGRADED 或保持 RUNNING |
| RUNNING | 上游瞬时断开 | RECONNECTING |
| RUNNING | 租约即将过期 | REFRESHING |
| REFRESHING | 新连接首帧成功 | RUNNING |
| 任意活动态 | 凭据失效 | AUTH_REQUIRED |
| 任意活动态 | 平台确认下播 | OFFLINE |
| 任意活动态 | 所有 Sink 分离 | STOPPING |
| STOPPING | 资源全部释放 | STOPPED |

状态变化必须由监督器串行化，禁止多个协程直接写共享状态。

---

## 5. 上游读取器

UpstreamReader 负责：

- 使用租约中的 URL、headers、代理和协议参数建立连接。
- 报告首字节、首个有效媒体包、时间戳前进和吞吐。
- 将媒体数据写入有界分发缓冲。
- 发现 EOF、停滞、协议错误和进程异常。
- 响应取消并在限时内释放子进程和文件描述符。

实际实现可以使用 FFmpeg 子进程或等价媒体管线，但参数必须数组化、事件必须结构化、stderr 必须限长采集和脱敏。

---

## 6. 下游 Sink 契约

所有 Sink 统一实现：

- attach(session_context)
- consume(media_chunk)
- flush(reason)
- detach(reason)
- health()

Sink 必须有独立的：

- 有界队列。
- 消费任务。
- 超时与故障状态。
- 丢弃或降级策略。
- 指标与最后进度时间。

### 6.1 RecordingSink

- 默认不允许静默丢包。
- 队列达到高水位时优先触发监督器评估，而不是阻塞所有下游至死锁。
- 失败时标记录制不完整并关闭当前分段。
- 允许在上游恢复后开启新分段继续录制。

### 6.2 PreviewSink

- 允许丢弃过期帧以追赶实时进度。
- 慢客户端不得阻塞上游或录制。
- 前端断开只分离该 Sink。
- 重新连接预览时优先等待关键帧或重新初始化解码上下文。

---

## 7. 背压与资源限制

1. 每个 Sink 使用独立有界队列。
2. 录制队列按字节和时间双重限制。
3. 预览队列保持短实时窗口，超限时丢弃最旧可丢帧。
4. 分发器不得等待任一 Sink 无限完成。
5. 达到进程、内存或带宽上限时拒绝新增低优先级 Sink，并返回资源错误。
6. 所有队列深度、丢弃量、阻塞时长和最后消费时间可观测。

---

## 8. 重连与无缝切换

上游故障处理顺序：

1. 分类故障并判断当前租约是否仍可信。
2. 对瞬时网络故障尝试有限的同租约重连。
3. 对租约失效或重复失败请求新的已探测租约。
4. 能并行时先建立新上游并验证首个有效包，再切换 generation。
5. 关闭旧上游，通知各 Sink 出现媒体边界。
6. RecordingSink 开启新分段；PreviewSink 重置解码器或等待关键帧。

同一时刻只允许一个恢复协调器工作。后续失败事件合并进入当前恢复周期，禁止每个 Sink 自行刷新 URL。

---

## 9. 生命周期与关闭顺序

启动顺序：

1. 创建会话和监督器。
2. 附加至少一个 Sink。
3. 获取并验证租约。
4. 建立上游。
5. 确认媒体可消费。
6. 对外发布 RUNNING。

关闭顺序：

1. 拒绝新 Sink 和新恢复动作。
2. 停止上游读取。
3. 向分发器发送终止边界。
4. 各 Sink flush 并关闭当前产物。
5. 释放子进程、管道、队列和租约引用。
6. 发布最终状态并从会话注册表移除。

应用退出时必须有总 deadline；超时后强制终止资源，但仍需写入未完整关闭原因。

---

## 10. 应用层集成

- orchestrator.py 只表达用户意图和工作流，不直接解析平台 URL 或控制平台重试。
- room_handler.py 只负责 API 校验、权限、调用应用服务和事件适配。
- 现有 SharedRoomIngest、MseStreamer、RecordingController 和 capture 逐步变为监督器组件或兼容外观。
- B站 Cookie、虎牙换线等平台逻辑迁回平台策略与恢复策略。
- WebSocket 广播由状态事件驱动，不从多个后台任务拼接推测状态。

---

## 11. 测试要求

1. 录制与预览同时启动时只建立一个远端上游。
2. 预览连接反复断开不影响录制字节持续增长。
3. 录制磁盘写入故障不造成预览卡死。
4. 慢预览客户端触发丢帧但不造成录制丢包。
5. 上游断流期间只启动一个恢复周期。
6. 新租约切换后旧 generation 数据不能进入新分段。
7. 停止、取消、应用退出和异常退出路径无孤儿 FFmpeg。
8. 资源上限生效且错误可操作。
9. 并发 attach 与 detach 不造成状态机非法转换。

---

## 12. 验收标准

- 同房间录制加预览的远端连接数默认等于 1。
- 预览故障不会停止录制，录制故障不会无条件关闭预览。
- 所有恢复动作具有唯一 recovery_id，可追踪且有预算。
- 上游切换、Sink 分离和应用退出不会泄漏进程或协程。
- 上层代码不再直接管理平台流 URL 的缓存、刷新和换线。
