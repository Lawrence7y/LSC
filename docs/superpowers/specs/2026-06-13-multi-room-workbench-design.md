# 多直播间工作台第一期设计

## 背景

当前项目已经具备单直播间的 URL 解析、预览、录制、回放和手动切片能力，并且第一期多平台适配已经接入：

- 抖音
- B站
- 虎牙
- 通用 m3u8/flv 直链

下一步目标不是继续扩平台，而是在现有 Python GUI 基础上，把单房间录制页升级成一个可承载多个直播间的工作台，为后续统一时间轴、同段切片和批量导出打基础。

本期只做多房间工作台的第一阶段：

- 动态添加多个直播间卡片
- 多房间同时预览
- 单房间静音
- 多房间同时录制

本期不实现统一时间轴、批量同段切片、房间持久化、拖拽排序和高级布局管理。

## 目标

- 支持动态添加多个直播间卡片，而不是固定 2 宫格或 4 宫格
- 每个卡片可独立连接、预览、静音、开始录制、停止录制、移除
- 支持多个房间同时录制
- 保持现有单房间预览和录制技术路径可复用
- 为后续统一时间轴和批量切片预留会话层和管理层

## 非目标

- 不在本期实现统一时间轴
- 不在本期实现同一时间段批量切片
- 不在本期实现拖拽排序、自定义分组和房间布局持久化
- 不在本期实现多房间联动播放控制
- 不在本期替换 mpv 或 FFmpeg 技术路径

## 设计概览

本期新增一个“多直播间工作台”页面，但底层不直接把现有 `RecordPage` 粗暴复制多份，而是先抽离单房间会话模型，再由多房间页面管理这些会话。

建议新增结构：

```text
lsc/gui/multi_room/
  __init__.py
  session.py
  manager.py

lsc/gui/components/
  room_card.py

lsc/gui/pages/
  multi_room.py
```

设计原则：

- 一个房间对应一个独立 `RoomSession`
- 一个 `RoomSession` 持有自己的一套录制/预览状态
- `MultiRoomManager` 负责房间集合管理和批量操作
- 页面层只消费 manager 暴露的接口，不直接拼接零散状态

## 核心对象

### RoomSession

`RoomSession` 表示一个直播间的完整会话状态，建议持有：

```python
@dataclass
class RoomSession:
    room_id: str
    room_url: str
    platform: str = ""
    stream_info: StreamInfo | None = None
    selected_quality: str = ""
    preview_muted: bool = True
    is_connected: bool = False
    is_recording: bool = False
    record_output_path: str = ""
    record_started_at: datetime | None = None
    last_error: str = ""
    controller: RecordingController | None = None
```

说明：

- `controller` 是单房间控制器实例，不再代表整页唯一控制器
- `preview_muted` 只影响本地预览，不影响录制音频
- `stream_info` 直接复用平台适配层的统一结构

### MultiRoomManager

`MultiRoomManager` 负责会话集合和批量行为，建议提供：

- `add_room(url) -> RoomSession`
- `remove_room(room_id) -> bool`
- `get_room(room_id) -> RoomSession | None`
- `list_rooms() -> list[RoomSession]`
- `connect_room(room_id) -> bool`
- `disconnect_room(room_id) -> bool`
- `start_recording(room_id) -> bool`
- `stop_recording(room_id) -> bool`
- `mute_room(room_id, muted: bool) -> None`
- `start_recording_all() -> dict[str, bool]`
- `stop_recording_all() -> dict[str, bool]`

manager 负责：

- 房间增删改查
- 调用平台解析层
- 为每个房间创建控制器
- 执行批量录制/停止
- 汇总错误状态

manager 不负责：

- 具体 UI 布局
- 卡片绘制
- 预览控件样式

## UI 结构

新增 `MultiRoomPage`，建议布局如下：

- 顶部工具条
  - 添加房间
  - 批量开始录制
  - 批量停止录制
  - 全部静音 / 取消全部静音
- 中间主区域
  - 可滚动自由卡片网格
- 右侧详情区
  - 当前选中房间详情
- 底部状态栏
  - 房间总数
  - 正在录制数
  - 错误提示汇总

### 房间卡片

每张 `RoomCard` 至少包含：

- 平台名
- 主播名
- 房间标题
- 小型预览区域
- 连接状态
- 录制状态
- 连接 / 重连按钮
- 录制 / 停止按钮
- 静音开关
- 移除按钮

卡片行为：

- 点击卡片可切换当前选中房间
- 选中房间后，右侧详情区切换
- 卡片按添加顺序展示
- 第一版不支持拖拽排序

### 自由布局策略

本期采用响应式自由卡片布局，不做固定宫格：

- 窗口变宽时自动增加列数
- 窗口变窄时自动换行
- 卡片尺寸保持稳定，不随文本内容抖动
- 主区域可滚动

## 预览与静音规则

现有稳定预览路径是 `MpvWidget`，本期继续沿用，不引入新播放器路径。

规则明确为：

- 每个房间一个独立预览实例
- 每个房间静音只影响该房间本地预览
- 静音不改变 FFmpeg 录制参数
- 新房间默认进入时建议为静音，避免多房间同时出声

如果后续需要“聚焦某一房间自动开声”，应作为第二期交互增强，不在本期实现。

## 录制策略

本期允许多个房间同时录制。

每个房间录制沿用已有路径：

- 平台解析仍走 `lsc/platforms`
- 录制仍走 `RecordingController.start_recording_with_crf()`
- 平台请求头继续通过 `input_args` 透传

每个房间录制状态互不影响：

- 某个房间失败不应导致其他房间停止
- 批量录制应按房间逐个执行
- 批量停止也应按房间逐个执行

## 状态流转

### 添加房间

```text
输入 URL
-> manager.add_room(url)
-> 创建 RoomSession
-> 解析平台并生成基础卡片
-> 渲染到网格
```

### 连接预览

```text
点击连接
-> manager.connect_room(room_id)
-> parse_stream(url)
-> 更新 RoomSession.stream_info / stream_url
-> 启动该卡片预览
-> 更新连接状态
```

### 静音切换

```text
点击静音
-> manager.mute_room(room_id, muted)
-> 更新 RoomSession.preview_muted
-> 调整该卡片预览播放器音量/静音状态
```

### 开始录制

```text
点击录制
-> manager.start_recording(room_id)
-> 调用该房间 controller.start_recording_with_crf()
-> 更新 is_recording / output_path / started_at
```

### 停止录制

```text
点击停止
-> manager.stop_recording(room_id)
-> 更新录制状态
-> 保留输出文件路径
```

### 移除卡片

```text
点击移除
-> 若在录制先停止
-> 若在预览先释放播放器资源
-> manager.remove_room(room_id)
-> 从页面移除卡片
```

## 与现有结构的关系

当前真实的单房间控制面仍是 `lsc/gui/pages/record.py`，稳定预览路径仍是 `MpvWidget`。因此本期不建议直接把 `RecordPage` 复制多份挂在一个页面里，而是：

- 复用平台适配层
- 复用单房间控制器能力
- 新增多房间会话层和页面层

建议后续把现有 `RecordingController` 视作“单房间控制器”，而不是“整个录制页唯一控制器”。本期可以先按这个语义使用，后续若需要重命名再做单独整理。

## 错误处理

每个房间独立维护自己的错误状态：

- 平台解析失败
- 房间未开播
- 连接失败
- 录制失败
- 批量操作中的单房间失败

错误展示要求：

- 卡片内显示当前房间错误摘要
- 底部状态栏汇总错误数量
- 批量操作后保留逐房错误，不弹一个模糊总错误覆盖全部信息

## 性能与风险控制

### 1. 多播放器资源占用

多个 `MpvWidget` 同时存在会增加 CPU/GPU 和解码压力。本期控制方式：

- 卡片预览尺寸保守
- 不做复杂动画
- 不做多房间同步播放控制

### 2. 单例状态串扰

当前很多状态原本默认是单实例。本期必须把以下状态收进 `RoomSession`：

- 连接状态
- 录制状态
- 预览静音状态
- 输出路径
- 错误状态

### 3. 静音语义混乱

必须固定规则：

- 预览静音 != 录制静音
- 静音不影响原始录制音频

### 4. 批量操作副作用

批量录制和批量停止要按房间逐个执行，失败隔离，不能因单房异常中断所有任务。

## 第一版最小实现范围

本期只实现：

- `RoomSession`
- `MultiRoomManager`
- `RoomCard`
- `MultiRoomPage`
- 动态添加多个房间
- 每房独立预览
- 每房独立静音
- 每房独立录制/停止
- 批量开始录制 / 批量停止录制
- 基础错误提示和状态汇总

本期明确不实现：

- 统一时间轴
- 同段批量切片
- 拖拽排序
- 分组
- 房间持久化
- 布局恢复
- 自动重连策略增强

## 测试计划

新增测试应覆盖：

- `RoomSession` 状态初始化和状态流转
- `MultiRoomManager` 的添加、删除、查找和批量操作
- 单房间静音状态变更
- 多房间录制调用分发
- 页面交互：
  - 添加卡片
  - 移除卡片
  - 选中卡片
  - 单房静音
  - 单房开始/停止录制

回归重点：

- 单房间录制页现有能力不退化
- 平台 headers 透传不丢失
- 多房间卡片互不串状态

## 验收标准

- 可以动态添加多个直播间卡片
- 每个卡片可独立连接和显示预览
- 每个卡片可独立静音，且只影响该卡片预览声音
- 每个卡片可独立开始录制和停止录制
- 支持多个房间同时录制
- 移除卡片时能正确释放资源
- 单房间录制流程继续可用
- 本期结构可以自然承接后续统一时间轴和批量切片
