# 多平台运行状态、可观测性与前端设计规格

> 状态：提案  
> 日期：2026-08-09  
> 上游规格：[多平台直播稳定性总体架构规格](2026-08-09-multiplatform-stability-overview-design.md)  
> 主要代码范围：lsc 结构化事件、python-backend API 与 WebSocket、lsc-electron 前端

---

## 1. 目的

建立统一、可诊断、默认脱敏的运行状态，使用户能区分“平台没开播、凭据失效、正在换线、预览故障、录制故障”等情况，研发也能从同一事件链定位问题。

---

## 2. 健康模型

房间健康状态拆分为五个维度：

| 维度 | 示例状态 |
|---|---|
| platform | LIVE、OFFLINE、AUTH_REQUIRED、RESTRICTED、UNKNOWN |
| resolver | IDLE、RESOLVING、PROBING、READY、BACKING_OFF、FAILED |
| ingest | CONNECTING、RUNNING、REFRESHING、RECONNECTING、DEGRADED、FAILED |
| recording | NOT_REQUESTED、STARTING、RECORDING、RECOVERING、PARTIAL、FAILED、STOPPED |
| preview | NOT_ATTACHED、ATTACHING、PLAYING、BUFFERING、DEGRADED、FAILED |

聚合状态只用于列表概览，详情页必须保留各维度状态，不能用一个 recording 布尔值覆盖全部事实。

---

## 3. 结构化事件

统一 RuntimeEvent 至少包含：

- schema_version。
- event_id。
- event_type。
- occurred_at。
- room_id 与 room_session_id。
- recording_session_id，可空。
- platform_id。
- component。
- state_from 与 state_to，可空。
- reason_code。
- severity。
- recovery_id，可空。
- lease_generation，可空。
- attempt 与 retry_after_seconds，可空。
- safe_context。

事件类别至少包括：

- PLATFORM_STATUS_CHANGED
- RESOLVE_STARTED、RESOLVE_COMPLETED、RESOLVE_FAILED
- PROBE_COMPLETED、CANDIDATE_REJECTED
- LEASE_ISSUED、LEASE_REFRESHED、LEASE_INVALIDATED
- INGEST_STATE_CHANGED、UPSTREAM_SWITCHED
- SINK_ATTACHED、SINK_DETACHED、SINK_FAILED
- SEGMENT_OPENED、SEGMENT_COMPLETED、SEGMENT_RECOVERED
- CREDENTIAL_STATUS_CHANGED
- RESOURCE_PRESSURE

event_type 和 reason_code 是稳定机器字段；message 只用于人类阅读，不能作为业务判断依据。

---

## 4. 日志关联

所有相关日志必须携带：

- request_id 或 command_id。
- room_session_id。
- component。
- platform_id。
- recovery_id 或 lease_generation。
- candidate 的安全 fingerprint，而非完整 URL。

一次用户操作应能从 HTTP 请求追踪到解析、探测、租约、进样、Sink 和分段事件。

stderr 和平台响应摘要需要：

- 限制长度。
- 去除 Cookie、Authorization、签名参数和个人标识。
- 保留 HTTP 状态、协议阶段、错误码和必要的服务器脱敏标识。

---

## 5. 指标

核心指标：

- resolve_success_rate。
- probe_success_rate。
- connect_success_rate。
- time_to_first_media_ms。
- reconnect_count。
- lease_refresh_success_rate。
- upstream_switch_count。
- preview_buffering_ratio。
- recording_gap_seconds。
- segment_validation_failure_rate。
- orphan_process_count。
- auth_required_count。

指标至少按 platform_id、protocol、support_level 和应用版本聚合。账号、完整房间 URL 和签名参数不得作为指标标签。

---

## 6. 后端状态接口

房间详情接口返回：

- 当前五维健康状态。
- 当前会话与录制会话标识。
- 平台支持等级和能力摘要。
- 凭据状态与可执行操作。
- 当前实际画质、协议和脱敏线路。
- 最近一次媒体进度时间。
- 最近一次恢复原因、尝试次数和下一次重试时间。
- 录制分段数、缺口和产物状态。

诊断接口默认只返回脱敏摘要。导出更完整诊断包必须由用户明确操作，并再次运行敏感信息扫描。

---

## 7. WebSocket 兼容

- 保留现有事件名和必要字段，新增 schema_version、health、reason_code 与 session_id。
- 新字段先以向后兼容方式添加。
- 前端按 schema_version 解析，未知字段忽略，未知状态显示为“未知状态”。
- 同一状态代次的旧事件不得覆盖新事件；前端使用 occurred_at 和 generation 防止乱序。
- 断线重连后先请求当前快照，再消费增量事件。

---

## 8. 前端交互

房间卡片显示：

- 平台与支持等级。
- 聚合健康状态。
- 录制和预览独立标识。
- 实际画质或降级提示。
- 下一次自动恢复倒计时。

详情面板显示：

- 解析、进样、录制、预览五维状态。
- 最近状态变化时间线。
- 当前线路的脱敏名称和租约剩余时间。
- 当前分段进度与已检测缺口。
- 可执行操作。

可执行操作按状态限制：

- AUTH_REQUIRED：配置或刷新凭据。
- BACKING_OFF：查看原因；允许在安全间隔后手动重试。
- DEGRADED：查看降级详情或切换画质偏好。
- FAILED：重新解析、重新启动会话或导出诊断。
- OFFLINE：保持监控或停止监控。

UI 不提供“无限重试”或展示完整流地址的入口。

---

## 9. 凭据 UI

前端只显示：

- 是否已配置。
- 账号别名或脱敏标识。
- 最近验证时间。
- 预计过期或已失效状态。
- 重新授权、替换和撤销按钮。

凭据输入通过专用受控接口提交，不回显完整值。提交成功后立即清空本地输入和临时状态。

---

## 10. 告警与降噪

- 同一 recovery_id 的重复错误在短窗口内聚合。
- 状态未变化的轮询事件不重复弹出用户通知。
- OFFLINE 属于业务状态，除非用户要求，不按系统错误告警。
- AUTH_REQUIRED、持续录制缺口、磁盘压力和进程泄漏属于高价值告警。
- 平台整体错误率超过门槛触发平台级降级或熔断提示。

---

## 11. 测试要求

1. 验证旧前端可忽略新字段，新前端可兼容旧载荷。
2. 验证乱序、重复和断线重连事件不会回退状态。
3. 验证录制与预览独立状态正确呈现。
4. 验证所有日志、事件、接口和诊断包脱敏。
5. 验证各种 reason_code 映射到明确且可操作的中文提示。
6. 验证高频重连不会形成通知风暴。
7. 验证指标标签无高基数敏感字段。
8. 验证真实恢复过程中时间线、尝试次数和倒计时一致。

---

## 12. 验收标准

- 用户可以在一个页面内判断失败发生在平台、凭据、解析、进样、录制还是预览。
- UI 展示的是后端权威状态，不通过本地计时器猜测。
- 研发可以用 room_session_id 和 recovery_id 关联完整故障链。
- 自动扫描确认所有用户可见和可导出的数据不包含可复用凭据或签名 URL。
- 旧版消费者在兼容窗口内不因新增事件字段中断。
