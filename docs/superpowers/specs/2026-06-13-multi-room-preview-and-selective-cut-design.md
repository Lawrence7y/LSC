# 多房间预览控制与可选房间同段切割设计

## 背景

多房间工作台第一期已经具备动态房间卡片、房间会话状态、平台解析接线、批量录制入口和 Dashboard 入口。下一阶段要补上两个关键能力：

- 用户可以按房间选择是否开启预览、暂停预览或恢复预览。
- 用户做同一时间段批量切割时，可以勾选本次要参与剪辑的直播间。

这两个能力的核心约束是：预览是本地观看资源，录制是后台任务，两者不能互相隐式影响。

## 目标

- 每个房间卡片都有独立预览状态。
- 用户可以对单个房间开启预览、暂停预览、恢复预览、静音预览。
- 暂停预览只释放或暂停本地播放器，不停止后台录制。
- 单房间静音只影响本地预览声音，不改变录制参数和录制文件音频。
- 移除房间时必须清理预览资源；如果该房间正在录制，也要先停止该房间录制。
- 同一时间段批量切割时，用户可以选择参与本次导出的房间。
- 被选房间逐个切割，单房失败不影响其他房间导出。

## 非目标

- 本阶段不做统一时间轴的完整 UI。
- 本阶段不做断流重连后的精确时间偏移修正。
- 本阶段不做房间布局持久化。
- 本阶段不做拖拽排序、分组和自动恢复预览。
- 本阶段不改变 FFmpeg 录制主路径。

## 预览与录制解耦规则

每个 `RoomSession` 需要区分三类状态：

- 连接状态：是否已经解析到可用直播流。
- 预览状态：是否有本地播放器正在播放或暂停该流。
- 录制状态：后台 FFmpeg 任务是否正在写文件。

状态规则：

- `pause_preview(room_id)` 只能影响该房间预览播放器。
- `pause_preview(room_id)` 不允许调用 `stop_recording()`。
- `mute_preview(room_id, muted)` 只能影响该房间播放器音量或静音属性。
- `start_recording(room_id)` 不要求预览必须开启，只要求该房间已连接并有可用流地址。
- `remove_room(room_id)` 需要按顺序执行：
  1. 如果预览存在，停止并释放预览播放器。
  2. 如果录制正在进行，停止该房间录制。
  3. 调用控制器清理逻辑。
  4. 从 manager 中删除会话。

## 会话状态扩展

建议扩展 `RoomSession`：

```python
preview_enabled: bool = False
preview_paused: bool = False
preview_error: str = ""
include_in_cut: bool = True
```

含义：

- `preview_enabled`：该房间是否已经创建并启动预览。
- `preview_paused`：该房间预览是否处于暂停状态。
- `preview_error`：该房间预览相关错误。
- `include_in_cut`：该房间是否参与下一次同段批量切割。

`preview_muted` 继续保留，默认 `True`，避免多房间同时开声。

## 预览控制接口

建议在 `MultiRoomManager` 增加：

- `start_preview(room_id) -> bool`
- `pause_preview(room_id) -> bool`
- `resume_preview(room_id) -> bool`
- `stop_preview(room_id) -> bool`
- `set_preview_muted(room_id, muted: bool) -> None`

预览实现继续沿用现有 `MpvWidget` 路径，不引入第二套播放器技术栈。

为了方便测试和后续替换，manager 不直接写死 `MpvWidget` 构造，而是使用可注入的 `preview_factory`。生产环境里 `preview_factory` 创建真实预览控件，测试里用 fake preview 验证 start/pause/resume/stop/mute 调用顺序。

## 房间卡片 UI

每张 `RoomCard` 增加这些控制：

- 预览区域：承载该房间的播放器控件或占位状态。
- 开启预览 / 暂停预览 / 恢复预览。
- 静音开关。
- 参与剪辑复选框。

按钮语义：

- 未开启预览：显示“开启预览”。
- 正在预览：显示“暂停预览”。
- 已暂停预览：显示“恢复预览”。
- 正在录制时，暂停预览按钮仍然可用，并且不会改变录制状态。

卡片状态文案应同时表达录制与预览，例如：

- 未连接
- 已连接，未预览
- 预览中，未录制
- 预览暂停，录制中
- 预览中，录制中

## 可选房间同段切割

同段切割不是默认导出所有房间，而是只处理 `include_in_cut=True` 的房间。

建议新增一个批量切割任务输入结构：

```python
@dataclass(slots=True)
class MultiRoomCutRequest:
    start_sec: float
    end_sec: float
    output_dir: str
    room_ids: list[str]
```

执行规则：

- 页面层从勾选状态收集 `room_ids`。
- manager 只对这些房间创建切割任务。
- 每个房间独立返回成功或失败。
- 不存在、未录制、无输出文件的房间要返回明确错误，不阻塞其他房间。

建议输出结构：

```python
@dataclass(slots=True)
class MultiRoomCutResult:
    room_id: str
    ok: bool
    output_path: str = ""
    error: str = ""
```

导出目录建议包含 `manifest.json`，记录：

- 全局时间段。
- 参与导出的房间列表。
- 每个房间的平台、标题、主播、原始 URL。
- 每个房间的源文件路径和导出文件路径。
- 成功或失败原因。

## 数据流

### 开启预览

```text
用户点击开启预览
-> manager.start_preview(room_id)
-> 确认房间已连接且有 stream_url
-> 创建该房间预览实例
-> 传入 stream_url 和 input_args
-> 应用 preview_muted
-> RoomSession.preview_enabled = True
```

### 暂停预览

```text
用户点击暂停预览
-> manager.pause_preview(room_id)
-> 调用该房间预览实例 pause/stop_local_playback
-> RoomSession.preview_paused = True
-> 不调用 stop_recording
```

### 移除房间

```text
用户点击移除
-> manager.stop_preview(room_id)
-> 如果 is_recording，manager.stop_recording(room_id)
-> controller.cleanup()
-> 删除 RoomSession
-> 页面移除卡片
```

### 同段切割

```text
用户选择时间段
-> 勾选参与剪辑的房间
-> 页面构造 MultiRoomCutRequest
-> manager.cut_selected_rooms(request)
-> 每房逐个执行切割
-> 写出 manifest.json
-> 返回逐房结果
```

## 错误处理

- 预览失败写入 `preview_error`，不覆盖录制错误。
- 录制失败写入 `last_error`，不覆盖预览错误。
- 切割失败写入切割结果，不改变房间连接、预览、录制状态。
- 批量操作必须失败隔离。

## 测试计划

新增或扩展测试：

- `RoomSession` 默认预览状态和 `include_in_cut` 默认值。
- `MultiRoomManager.pause_preview()` 不调用 `stop_recording()`。
- `remove_room()` 对正在录制且正在预览的房间按顺序清理。
- `RoomCard` 能发出开启、暂停、恢复预览信号。
- `RoomCard` 能切换参与剪辑状态。
- 批量切割只处理被选中的房间。
- 批量切割中单房失败不影响其他房间。

回归测试：

- 现有单房间录制参数不变。
- 现有多平台 headers / input_args 透传不变。
- 多房间批量录制不要求预览开启。

## 验收标准

- 用户可以按房间开启、暂停、恢复预览。
- 用户暂停某房间预览后，该房间后台录制仍然保持运行。
- 用户静音某房间预览后，录制文件音频不受影响。
- 移除房间时，该房间预览和录制资源都被清理。
- 用户可以勾选本次参与同段切割的房间。
- 同段切割只导出被选中的房间。
- 批量切割结果能显示每个房间成功或失败原因。
- 当前实现仍能自然承接后续统一时间轴 UI。
