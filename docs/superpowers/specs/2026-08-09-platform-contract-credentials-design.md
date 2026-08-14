# 平台契约与凭据设计规格

> 状态：提案  
> 日期：2026-08-09  
> 上游规格：[多平台直播稳定性总体架构规格](2026-08-09-multiplatform-stability-overview-design.md)  
> 主要代码范围：lsc/platforms、python-backend 的凭据与房间接口

---

## 1. 目的

定义平台适配器的统一输入、输出、能力声明、错误语义和凭据边界，使平台变化不会泄漏到录制、预览、协调器和前端业务逻辑。

---

## 2. 设计原则

1. 适配器无跨请求可变状态。
2. 适配器只解析和描述媒体，不拥有媒体进程。
3. 每次解析都显式接收凭据上下文、代理和取消信号。
4. 同一结果可包含多个协议、画质、线路和 CDN 候选。
5. 失败必须结构化，禁止上层依赖异常文本匹配 403、离线或 Cookie 失效。
6. 凭据只按最小范围提供，默认不可记录、不可广播、不可持久化到普通配置。

---

## 3. 平台能力声明

每个适配器必须暴露 PlatformCapabilities，至少包含：

| 字段 | 含义 |
|---|---|
| platform_id | 稳定平台标识 |
| supports_anonymous | 是否支持匿名解析 |
| credential_kinds | 可接受的凭据类型 |
| protocols | 可能返回的 FLV、HLS、TS 等协议 |
| qualities | 平台画质标识及统一质量等级映射 |
| multi_cdn | 是否具有多线路或多 CDN |
| signed_url | URL 是否带签名或短有效期 |
| expected_ttl_seconds | 无精确过期时间时的保守有效期 |
| max_resolve_concurrency | 单账号或单出口建议解析并发 |
| max_connect_concurrency | 单房间建议远端连接并发 |
| refresh_triggers | 哪些错误应重新解析 |
| probe_profile | 默认探测超时、字节数和协议偏好 |

能力声明是策略输入，不是保证。运行时仍必须以探测结果为准。

---

## 4. 解析请求

ResolveRequest 至少包含：

- source_url：用户输入且经过基本校验的地址。
- requested_quality：统一画质偏好，而非平台私有字符串。
- credential_context：只读凭据引用及可用性状态。
- network_context：代理、出口标识、地区提示和超时预算。
- request_id：全链路关联标识。
- force_refresh：是否跳过成功缓存。
- cancellation：调用方取消信号。

适配器不得从全局变量、模块级 Cookie 或隐藏单例读取请求状态。

---

## 5. 候选流模型

StreamCandidate 至少包含：

| 字段 | 必需 | 说明 |
|---|---|---|
| candidate_id | 是 | 单次解析内稳定标识 |
| url | 是 | 原始媒体地址，只能在受控内存中使用 |
| safe_url | 是 | 用于日志和 UI 的脱敏地址 |
| protocol | 是 | flv、hls、ts 或其他明确类型 |
| container | 否 | 预期容器 |
| video_codec | 否 | 预期视频编码 |
| audio_codec | 否 | 预期音频编码 |
| quality_id | 是 | 平台原始画质标识 |
| quality_rank | 是 | 统一质量等级 |
| line_id | 否 | 平台线路标识 |
| cdn_id | 否 | CDN 标识 |
| headers | 是 | 建连所需请求头的受控副本 |
| proxy_policy | 是 | 直连、继承或指定代理策略 |
| resolved_at | 是 | 解析时间 |
| expires_at | 否 | 可推断时的绝对过期时间 |
| source_kind | 是 | official、page_state、fallback、direct |
| confidence | 是 | 解析来源可信度 |

URL 去重不能只比较完整字符串，应去除已知易变签名参数后生成 fingerprint，同时保留不同 CDN、协议和画质的差异。

---

## 6. 解析结果

ResolveResult 包含：

- platform_id 与 canonical_room_id。
- room_title、anchor_name 和 live_status。
- capabilities_snapshot。
- candidates，允许为空。
- credential_status。
- resolved_at 和 recommended_refresh_at。
- warnings。
- error，仅在未获得可用结果时存在。

live_status 统一为 LIVE、OFFLINE、UNKNOWN、AUTH_REQUIRED、RESTRICTED。UNKNOWN 不能自动等价于 OFFLINE。

---

## 7. 结构化错误

PlatformError 至少包含：

- code：稳定机器码。
- category：AUTH、OFFLINE、RATE_LIMIT、UPSTREAM_CHANGED、NETWORK、PARSE、UNSUPPORTED、RESTRICTED、INTERNAL。
- retryable：当前上下文是否值得重试。
- retry_after_seconds：平台明确或策略计算的等待时间。
- refresh_credentials：是否需要刷新凭据。
- invalidate_cache：是否必须清除旧解析结果。
- user_message：可显示且不包含敏感信息的说明。
- diagnostic_context：只允许脱敏后的平台状态、HTTP 状态和解析阶段。

推荐稳定错误码：

- PLATFORM_OFFLINE
- AUTH_MISSING
- AUTH_EXPIRED
- AUTH_REJECTED
- RATE_LIMITED
- ROOM_NOT_FOUND
- REGION_RESTRICTED
- PAGE_SCHEMA_CHANGED
- API_SCHEMA_CHANGED
- NO_STREAM_CANDIDATE
- UNSUPPORTED_PROTOCOL
- UPSTREAM_TIMEOUT
- UPSTREAM_REJECTED

---

## 8. 凭据提供器

CredentialProvider 提供：

- get_status(platform_id, account_ref)
- get_context(platform_id, account_ref, purpose)
- refresh(platform_id, account_ref)
- invalidate(platform_id, account_ref, reason)
- redact(value)

purpose 必须区分 RESOLVE、PROBE、CONNECT，确保只提供该阶段需要的字段。

CredentialStatus 统一为：

- NOT_CONFIGURED
- AVAILABLE
- EXPIRING
- EXPIRED
- INVALID
- INTERACTION_REQUIRED

凭据安全要求：

1. 明文 Cookie 和令牌不得写入日志、WebSocket、普通 JSON 配置和诊断包。
2. 前端只接收状态、账号别名、更新时间和可操作提示。
3. 安全存储不可用时默认拒绝持久化，而不是降级为明文。
4. 凭据刷新失败不能无限循环。
5. 取消授权必须立即使关联缓存与租约失效。

---

## 9. 旧接口兼容

现有 StreamInfo 通过兼容转换器生成 ResolveResult：

- stream_url 转换为首个候选。
- quality_urls 转换为不同 quality_id 候选。
- headers 复制到候选的受控请求上下文。
- 无法确定的协议、编码和有效期保持未知，由探测器补全。
- 转换后的候选仍必须经过探测，不享有信任豁免。

兼容层只能存在于平台注册表与新解析服务之间，不允许各业务调用方自行转换。

---

## 10. 测试要求

1. 每个适配器运行同一套契约测试。
2. 验证能力声明字段完整且稳定。
3. 验证不同画质、线路和协议不会错误去重。
4. 验证匿名、有效凭据、过期凭据和缺失凭据路径。
5. 验证所有异常都映射为结构化错误。
6. 对日志、异常、事件和序列化对象执行敏感字段扫描。
7. 验证适配器并发调用时无模块级状态串扰。
8. 验证取消和超时不会遗留后台任务。

---

## 11. 验收标准

- 上层不再读取平台私有字段或匹配平台异常文本。
- 新增平台只需注册适配器、能力声明、凭据要求和测试夹具。
- 适配器代码中不存在 FFmpeg 启停、录制控制或前端事件发送。
- 所有候选都能提供可审计但不可复用的 safe_url。
- 任意平台凭据失效时，系统进入 AUTH_REQUIRED 并给出明确操作，而不是持续重试。
