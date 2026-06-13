# 多平台直播源适配器第一期设计

## 背景

当前 LSC 的录制页已经能通过抖音直播间 URL 解析出直播流，并使用现有的 `MpvWidget` 预览路径和 FFmpeg 录制链路完成预览、录制、回放和切片。第一期多平台目标是在不重写录制页主流程的前提下，把平台解析能力从录制控制器中抽出来，接入抖音、B站、虎牙和通用 m3u8/flv 直链。

本期只支持公开直播间和公开直链，不实现登录 Cookie、扫码登录、账号态解析、付费直播间或私密直播间访问。

## 目标

- 支持抖音、B站、虎牙直播间 URL 自动识别和解析。
- 支持直接输入 m3u8/flv/http/https 可播放流地址。
- 为所有平台返回统一的直播源信息结构，供现有预览、录制、清晰度选择和状态展示复用。
- 保持现有单直播间录制页的使用路径：输入 URL、连接、预览、录制、停止、回放、切片。
- 为后续多直播间预览、统一时间轴和批量同段切片预留数据结构。

## 非目标

- 不在本期实现多直播间同时预览。
- 不在本期实现统一时间轴和同段批量切割。
- 不在本期实现小红书、斗鱼和其他平台。
- 不在本期实现账号、Cookie、浏览器登录态管理。
- 不在本期更换播放器或录制引擎。

## 设计概览

新增 `lsc/platforms/` 作为平台适配器包，每个平台只负责把用户输入的房间 URL 或流 URL 解析成统一的 `StreamInfo`。录制页和录制控制器不再知道某个平台的页面结构，只依赖统一结果。

建议文件结构：

```text
lsc/platforms/
  __init__.py
  base.py
  registry.py
  direct.py
  douyin.py
  bilibili.py
  huya.py
```

核心数据结构：

```python
@dataclass
class StreamInfo:
    platform: str
    room_url: str
    stream_url: str
    title: str = ""
    streamer: str = ""
    is_live: bool = False
    quality_urls: dict[str, str] = field(default_factory=dict)
    selected_quality: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    error: str = ""
```

核心接口：

```python
class PlatformAdapter(Protocol):
    platform: str

    def can_handle(self, url: str) -> bool:
        ...

    def parse(self, url: str) -> StreamInfo:
        ...
```

`registry.py` 提供：

- `detect_platform(url)`: 根据 URL 域名和直链特征识别适配器。
- `parse_stream(url)`: 调用对应适配器并返回 `StreamInfo`。
- `select_quality(info, quality_preset)`: 在统一质量候选表中选择合适流地址。

## 平台策略

### 通用直链

`direct.py` 优先识别可直接交给 FFmpeg/mpv 的 URL：

- `.m3u8`
- `.flv`
- 明确包含直播流特征的 http/https 地址

直链适配器不抓页面，不判断主播名和标题，只返回：

- `platform="direct"`
- `is_live=True`
- `stream_url=url`
- `quality_urls={"原始": url}`

### 抖音

抖音适配器复用现有 `scripts/douyin_record.py` 中稳定的页面抓取和 SSR/RSC 提取逻辑，但通过适配器包装成 `StreamInfo`。

现有录制控制器中的 `parse_douyin_url()` 后续应退化为兼容壳，内部调用平台注册表，避免 UI 继续依赖抖音专用方法。

### B站

B站适配器识别：

- `live.bilibili.com/<room_id>`
- `b23.tv` 等短链如果能解析到直播间则支持，否则返回明确错误。

第一期建议优先使用公开 Web/API 路径获取直播状态和播放地址。若平台要求账号态或风控校验，则返回“该直播间需要登录或平台限制，本期暂不支持”的用户可读错误。

### 虎牙

虎牙适配器识别：

- `huya.com/<room>`
- `www.huya.com/<room>`

第一期优先解析公开页面中的直播流信息。若页面结构变化或需要签名参数无法公开获取，则返回明确错误，不阻塞其他平台。

## 录制页接线

`RecordingController.start_url_parse()` 从调用 `parse_douyin_url()` 改为调用 `lsc.platforms.registry.parse_stream()`。

录制页收到 `StreamInfo` 后：

- 若 `is_live=False` 或 `stream_url` 为空，显示平台适配器返回的错误。
- 若成功，更新主播、标题、平台、清晰度、分辨率、帧率。
- 预览继续调用现有 `VideoPreview.play_live(stream_url)`。
- 录制继续调用现有 `start_recording_with_crf()`，但 `input_args` 由 `StreamInfo.headers` 生成，而不是写死抖音 Referer。

为保持兼容，适配器结果可以先转成现有 dict 形态，再逐步把录制页内部改成直接使用 `StreamInfo`。

## 错误处理

平台适配器错误要分为四类：

- `unsupported_url`: 不支持的 URL 或无法识别平台。
- `offline`: 房间未开播。
- `restricted`: 需要登录、Cookie、付费或账号权限。
- `parse_failed`: 页面结构变化、接口失败、网络异常或返回格式异常。

UI 展示应包含平台名和建议动作。例如“B站直播间未开播”“虎牙页面结构已变化，未找到公开直播流”“该房间需要登录，本期暂不支持账号态解析”。

## 后续扩展预留

`StreamInfo` 的 `platform`、`room_url`、`streamer`、`title`、`headers` 和 `raw` 会作为后续多直播间会话的基础元数据。后续 `RoomSession` 可以直接持有 `StreamInfo`，并在多房间管理器中维护录制状态、预览静音、全局时间偏移和导出映射。

## 测试计划

新增平台层单元测试：

- URL 自动识别：抖音、B站、虎牙、直链、未知 URL。
- `direct` 适配器：m3u8/flv 输入返回可录制 `StreamInfo`。
- `StreamInfo` 字段默认值和错误字段稳定。
- `select_quality()` 在不同平台质量键中能返回预期地址。

新增录制控制器测试：

- `start_url_parse()` 调用平台注册表而非抖音专用方法。
- 平台 headers 能传入 FFmpeg input args。
- 平台解析失败时 UI 能收到可读错误。

保留并更新现有抖音回归测试，确认旧的抖音成功路径没有退化。

## 验收标准

- 输入公开抖音直播间 URL，能连接、预览和录制。
- 输入公开 B站直播间 URL，若可公开解析则能连接、预览和录制；若不可公开解析，显示明确原因。
- 输入公开虎牙直播间 URL，若可公开解析则能连接、预览和录制；若不可公开解析，显示明确原因。
- 输入 m3u8/flv 直链，能直接预览和录制。
- 现有单直播间录制、停止、回放和手动切片流程继续可用。
- 平台解析逻辑不再新增到录制页 UI 文件中。
