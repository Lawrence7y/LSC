# Spec: 持续分析剪映草稿导出（多机位同步工程）

## 概述

为持续分析/切片链路新增**剪映草稿导出**能力：一场直播生成一个剪映草稿，内含「每房一条全程录制轨 + 每房一条切片轨 + 可选回合标签文本轨」，所有内容按公共对齐时间轴落位（保留真实时间间隔），实现"同一时间段的切片落在不同轨道的相同时间位置"的多机位同步工程。

**导出目标三选一**：仅生成 MP4 切片 / 仅生成剪映草稿 / 两者同时生成。

**底层依赖**：`pyJianYingDraft`（Apache-2.0，pip 安装，已在本机 Python 3.12 烟测通过）。只做草稿生成，**不做** uiautomation 自动导出（剪映 ≤6 限定、模拟键鼠脆弱，用户在剪映里手动导出）。

**明确不做**：
- 不做剪映自动导出、不控制剪映 UI
- 不做 AI 配音/字幕/特效（那是 jianying-editor-skill 的领域，用户可在剪映/AI 编辑器里二次创作）
- v1 不支持跨重启的历史录制会话生成草稿（recording_history.json 当前不持久化 output_path，见 §边界情况 H12）
- 不支持 macOS / CapCut 国际版（项目本来就是 Windows 工具）

---

## 已验证事实（烟测结论，2026-07-23）

在本机 Python 3.12.10 上 `pip install pyJianYingDraft==0.3.0` 并实跑通过：

- `DraftFolder(root).create_draft(name, 1920, 1080)` 创建草稿，生成 `draft_content.json` + `draft_meta_info.json`
- `append_tracks` 创建 **4 视频轨 + 1 文本轨**（含中文轨道名「房间A·切片」），轨道后 append 者在上层（前景）
- `VideoSegment(path, trange("2s","5s"), source_timerange=trange("5.8s","5s"))` 非破坏引用源文件任意区间、落在草稿任意绝对位置 ✅
- `TextSegment("R5", trange(...))` 文本标签 ✅
- 中文轨道名/中文标签正常序列化 ✅

版本兼容（官方支持表）：草稿生成（轨道/片段/文本）剪映 **5.9 ✅ 与新版 10.8 ✅**；模板模式新版需 fallback_loader（本 spec 不用模板模式）；自动导出仅 ≤6（本 spec 不用）。

**现有时间映射基础设施（直接复用，不重造）**：

- `TimelineContext.room_snapshots[rid]`：`recording_to_common_delta` / `preview_to_common_delta`（锁定约定：`common = recording + recording_to_common_delta`）
- `ClipSegment.common_start/common_end`：前端切片已携带公共轴坐标
- `clip.mark_in_wallclock / recording_start_mono / content_offset`：墙钟快照（exact 精度）
- `build_room_snapshots_from_align`：仅含置信度 ≥0.3 的房间（草稿成员资格与其一致）

---

## 核心设计 A：时间轴映射算法

### 坐标系

草稿时间轴 = 公共对齐轴（common）。**草稿原点**（draft t=0）= 所有参与房间的最早公共时刻：

```
draft_origin = min( recording_to_common_delta[rid] for rid in 参与房间 )
```

（录制文件 t=0 在公共轴上的位置就是 `recording_to_common_delta`，取最小值使最早内容落在草稿 t=0。）

### 全程录制轨（每房一条）

房间 `rid` 的录制文件 `[0, dur_rid)` 映射为草稿段：

```
target = trange(recording_to_common_delta[rid] - draft_origin, dur_rid)
source = trange(0, dur_rid)          # 整段引用
```

其中 `dur_rid` 由 `VideoMaterial(path).duration` 实测（不以房间状态为准，防止录制中文件时长虚报）。

### 切片轨（每房一条，相同时间段 = 相同草稿位置）

切片 `c`（公共轴坐标 `[cs, ce]`，来自 `common_start/common_end` 或墙钟换算）在房间 `rid` 的切片轨上：

```
target = trange(cs - draft_origin, ce - cs)
source = trange(cs - recording_to_common_delta[rid], ce - cs)
```

**这就是"同一时间段的切片放到不同轨道的相同时间位置"**：所有房间的 target 相同，各自 source 按本房 delta 换算，对齐精度 = 一键对齐精度。

### 坐标来源优先级（与导出口径一致）

1. `clip.common_start/common_end` 存在（TimelineContext 模式）→ 直接用
2. 否则 `clip.mark_in_wallclock` + `recording_start_mono`（exact）→ `common = wallclock - min(media_start_mono)` 推算
3. 都没有（approximate，如拖拽标记）→ 该切片不进草稿，进 warnings 列表（与导出"近似定位"警告口径一致）

### 越界裁剪

- `source.start < 0`（副房开播晚，早期回合不在其录制内）→ **跳过该房此段**，记 warning「房间 X 无此时段素材」
- `source.end > dur_rid`（录制中生成，回合尾部超出当前文件）→ clamp 到 `dur_rid` 并记 warning
- `source.duration <= 0.2s` → 丢弃该段

---

## 核心设计 B：轨道布局与命名

### 轨道顺序（剪映上层 = 前景）

`append_tracks` 后到者在上，故按以下顺序 append（列表首 = 最底层）：

```
[ 房间N·录制, …, 房间2·录制, 主房·录制,      # 录制轨组（主房在其最上）
  房间N·切片, …, 房间2·切片, 主房·切片,      # 切片轨组（主房在其最上）
  回合标签 ]                                  # 文本轨最顶层
```

效果（剪映里从上往下看）：回合标签 → 主房切片 → 副房切片… → 主房录制 → 副房录制…。主房轨在各自组内最上 = 默认视野就是主视角，编辑者逐段挑选是否换副房角度。

### 命名规范

- 视频轨：`{streamer_name or 平台+房间短id}·录制` / `…·切片`（用户要求：轨道名对应房间）
- 文本轨：`回合标签`
- 草稿名：`LSC_{主房名}_{yyyyMMdd_HHmm}`（非法字符替换为 `_`；同名处理见 §边界情况 H7）

### 片段内容

- 录制轨段：`VideoSegment(record_output_path, target, source_timerange=整段)`
- 切片轨段：同上但 source 为回合区间；**音频默认全部保留**（用户决定在剪映里自调；`non_main_volume_zero` 选项预留，v1 不暴露 UI）
- 文本轨段（可选，`text_labels: bool` 默认开）：每个确认回合一段 `TextSegment("{label}", target)`，label 取切片标题（如 `R7 三杀残局`）

### 草稿分辨率

- 默认 1920×1080；`vertical: true` 时 1080×1920 且每个视频段带 `ClipSettings` 居中裁剪（裁剪数学与现有 `vertical_crop` 导出一致：`crop=ih*9/16:ih` 中心区域）

---

## 数据模型与配置

### `lsc/core/models.py` 新增

```python
@dataclass(slots=True)
class JianyingDraftOptions:
    include_recordings: bool = True      # 含全程录制轨
    include_clips: bool = True           # 含切片轨
    text_labels: bool = True             # 回合标签文本轨
    vertical: bool = False               # 9:16 竖屏草稿
    draft_name: str = ""                 # 空 = 自动生成
    non_main_volume_zero: bool = False   # 预留，v1 不暴露

@dataclass(slots=True)
class JianyingDraftResult:
    success: bool
    draft_name: str
    draft_dir: str                # 草稿文件夹完整路径
    tracks: int
    segments: int
    warnings: list[str]           # 跳过/裁剪/降级的人类可读原因
    error: str = ""
```

### 配置（settings.json + Settings 页）

新增 `jianying_draft_dir`，默认空 = 自动探测：

```
%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft
```

探测失败（剪映未安装/目录被用户改迁）→ 生成动作报友好错误并引导到设置页手动配置。

---

## 后端实现

### 新模块 `lsc/exporter/jianying_draft.py`

纯函数式 builder（不依赖 Qt、不依赖 WS，可独立单测）：

```python
def build_session_draft(
    rooms: list[RoomDraftSource],     # room_id, name, record_output_path, recording_to_common_delta
    clips: list[ClipDraftSource],     # common_start, common_end, label, source(exact/approx)
    options: JianyingDraftOptions,
    draft_root: str,
) -> JianyingDraftResult: ...

def detect_jianying_draft_dir() -> str | None: ...   # %LOCALAPPDATA% 探测
def validate_draft_dir(path: str) -> bool: ...
```

职责：坐标映射（§核心设计A）、轨道布局（§核心设计B）、越界裁剪、warnings 汇总、同名处理、异常友好化（缺文件/库未安装/目录不可写）。

### WebSocket 消息（python-backend/handlers/room_handler.py 或新 export 域 handler）

**`generate_jianying_draft`**（请求）：

```json
{
  "room_ids": ["rid1", "rid2"],           // 空 = 当前对齐组全部
  "clip_ids": ["cid1", "cid2"],           // 空 = 切片列表中全部符合口径的
  "options": { "include_recordings": true, "include_clips": true,
               "text_labels": true, "vertical": false, "draft_name": "" }
}
```

**`generate_jianying_draft_response`**（同步响应，生成是秒级、不走导出队列）：

```json
{ "success": true, "draft_name": "LSC_nobody_20260723_2105",
  "draft_dir": "C:\\...\\com.lveditor.draft\\LSC_nobody_20260723_2105",
  "tracks": 5,
  "segments": 23,
  "warnings": ["房间 whzy 无 21:03 前时段素材，已跳过 2 段"],
  "error": "" }
```

失败：`{ "success": false, "error": "剪映草稿目录未找到，请到设置页配置", "error_code": "draft_dir_missing" }`

error_code 枚举：`draft_dir_missing` / `no_rooms` / `no_aligned_context` / `library_missing`（pyJianYingDraft 未安装）/ `write_failed` / `invalid_state`

**`get_jianying_draft_dir`**（请求）→ 响应 `{ "draft_dir": "...", "auto_detected": true, "exists": true }`，供设置页展示当前生效目录。

### handler 装配逻辑

1. 解析 room_ids → 收集 `record_output_path`（缺文件的房间跳过 + warning）
2. 房间成员资格与 delta 取值：优先当前 `TimelineService` 的活动 TimelineContext（`room_snapshots[rid].recording_to_common_delta`）；context 不存在或房间不在其中 → 按 §边界情况 H2 降级
3. 解析 clip_ids → 从切片持久化/clip snapshot 取 `common_start/common_end` 或墙钟四元组 + `label` + `confirm_status`（pending/refining 不进草稿，与导出口径一致）
4. 调 `build_session_draft(...)`，在线程池执行（VideoMaterial 探测有 I/O）
5. 广播响应；`warnings` 前端以 notification 逐条折叠展示

### Electron 主进程（lsc-electron/electron/main.ts）

`_isSafePath` 白名单扩展：允许 `open-path` / `show-item-in-folder` 打开**已配置的剪映草稿目录**（从 settings 读取，加入白名单候选；未配置则仍拒绝）。这是"生成成功后 → 打开草稿目录"按钮的前置。

---

## 前端实现（lsc-electron）

### 1. 导出弹窗三选一（单条切片，`Workbench/index.tsx` 现有"导出切片预览"Modal）

footer 上方加 `Radio.Group`（受控 state `exportTarget: 'mp4' | 'draft' | 'both'`，默认 `'mp4'`，选择持久化到 localStorage）：

```
导出方式： ( ) 仅 MP4 切片   ( ) 仅剪映草稿   (•) 两者都要
```

- `仅 MP4`：走现有 `export_clip` 路径，行为不变
- `仅草稿`：发 `generate_jianying_draft`（clip_ids=[本条]，含录制轨与文本轨）
- `两者`：先 `export_clip` 入队，同时发 `generate_jianying_draft`；成功反馈合并展示（MP4 走 export_progress，草稿同步返回）
- 确认按钮文案随选择变化：「确认导出 / 生成草稿 / 导出并生成草稿」

### 2. 批量导出入口（ClipList 底部「导出所选 / 导出全部」）

按钮旁加同款 `Segmented`（MP4 / 草稿 / 两者，与单条弹窗共享同一持久化偏好）：

- 选 `草稿` 或 `两者` 时，批量不再逐条发 `export_clip`，而是收集 prepared 切片的 clip_ids 发**一次** `generate_jianying_draft`（一个会话草稿包含全部切片轨，符合"一场直播一个草稿"）
- `pending` 切片处理口径与现有 `handleExportMany` 完全一致（boundsOnly 确认后才进 clip_ids）

### 3. 会话级入口（整场直播一个草稿）

头部"更多"菜单（⋯）新增「生成剪映草稿」项：

- 无切片时也可点（`include_clips` 自动为 false，生成纯录制同步工程——对应"录制的文件也可以生成剪映草稿"）
- 未对齐多房点击 → 引导提示先一键对齐（§H2）
- 生成中 loading；成功后 Modal 展示：草稿名、轨道数、片段数、warnings 列表、「打开草稿目录」按钮 + 提示"剪映中需重启或进出一次草稿刷新列表"

### 4. 设置页（Settings/index.tsx）

新增「剪映草稿目录」项：显示当前生效目录（自动探测值或手动值）+「更改」+「恢复自动探测」。探测失败显示警示图标与说明。

### 5. store/types

- `types/index.ts`：`JianyingDraftOptions` / `JianyingDraftResult` / WS payload 映射
- `appStore`：`jianyingDraftDir`、`exportTarget` 偏好
- `useWebSocket`：注册 `generate_jianying_draft_response` / `get_jianying_draft_dir_response` 处理

---

## 边界情况全集

| # | 场景 | 行为 |
|---|------|------|
| H1 | 剪映未安装/草稿目录探测失败且未手动配置 | 报 `draft_dir_missing`，引导设置页；**不阻塞** MP4 导出 |
| H2 | 多房但未一键对齐（无 TimelineContext） | 草稿动作提示「多房草稿需先一键对齐」；用户可选降级为**主房单房草稿**（warning 记录） |
| H3 | 房间对齐置信度 <0.3 不在 context 中 | 该房不进草稿，warning 列出 |
| H4 | 房间无 record_output_path（未录制/文件被删） | 跳过该房（其切片轨也不生成），warning；全部缺失 → `no_rooms` 错误 |
| H5 | 录制进行中生成 | 允许；duration 以 VideoMaterial 实测为准，越界段 clamp（§越界裁剪），warning 提示"基于当前进度，可停录后重新生成" |
| H6 | 切片 confirm_status=pending/refining | 不进草稿（与导出口径一致）；UI 提示先确认 |
| H7 | 同名草稿已存在 | 生成前删除旧草稿文件夹再创建（草稿=最新快照语义）；warning 提示"已覆盖同名草稿，若剪映中已打开请先关闭" |
| H8 | approximate 精度切片（拖拽标记、无墙钟快照） | 不进草稿，warning（近似定位可能偏差数秒） |
| H9 | 副房开播晚，早期回合 source.start<0 | 跳过该房该段，warning |
| H10 | 切片 duration ≤0.2s 或越界 clamp 后为空 | 丢弃该段 |
| H11 | 中文/空格/特殊字符房间名与路径 | 轨道名与草稿名做非法字符替换（`<>:"/\|?*` → `_`）；烟测已验证中文正常 |
| H12 | 跨重启历史录制（recording_history 无 output_path） | v1 不支持；会话内房间的 record_output_path 可用即可生成 |
| H13 | 剪映打开着同名草稿时覆盖 | 覆写前提示；剪映不会实时刷新列表（toast 固定提示重启剪映或进出一次草稿） |
| H14 | pyJianYingDraft 未安装（打包漏依赖） | `library_missing`，friendly 错误；CI 加依赖 guard 防回归 |
| H15 | 草稿目录不可写 | `write_failed`，友好化错误（权限/磁盘满映射现有 error_messages 模式） |
| H16 | 9:16 竖屏 + 多机位 | 草稿 1080×1920，所有视频段统一居中裁剪；文本轨字号相应缩小 |

---

## 测试计划

### 单元测试（tests/test_jianying_draft.py）

- 坐标映射：构造 2 房（delta 0 / +1.5s）+ 2 切片，断言各轨 target 一致、source 按 delta 换算正确（微秒级）
- 原点计算：3 房不同 delta，draft_origin 取最小值，最早内容落 t=0
- 越界：source.start<0 跳过、end>dur clamp、≤0.2s 丢弃、warnings 文案
- 轨道布局：append 顺序断言（录制组→切片组→文本，主房在组内最上）；轨道命名与房间名对应
- 同名覆盖：旧草稿文件夹被替换
- approximate/pending 切片被排除
- 用 ffmpeg 造 2 个真实小视频（如烟测），端到端生成并回读 draft_content.json 校验轨道数/片段数/时间值（集成冒烟，放入 tests/ 常驻）

### Guard 测试

- WS payload 形状（请求/响应/error_code 枚举）快照
- 批量"草稿"目标只发一次 generate_jianying_draft（不逐条）
- pending 口径与 handleExportMany 一致
- `_isSafePath` 白名单：已配置草稿目录可打开、未配置拒绝
- CI 依赖 guard：`import pyJianYingDraft` 可用

### 手动验收

- 2 房录制 + 一键对齐 + 持续分析出回合 → 生成草稿 → 剪映打开验证：多轨同步、切片同时段同位置、文本标签、音频可独立调
- 无分析直接对录制中会话生成纯录制草稿
- 三选一的三种组合各跑一遍

---

## 里程碑

| 里程碑 | 内容 | 验收 |
|--------|------|------|
| J1 | 依赖引入（requirements.txt + CI guard）+ `lsc/exporter/jianying_draft.py` builder + 单测 | 单测全绿；烟测脚本转常驻测试 |
| J2 | WS handler + settings 配置 + TimelineContext 取值装配 + 边界处理 | guard 测试绿；后端手动生成验证 |
| J3 | 前端三选一（弹窗 + 批量）+ 会话级入口 + 设置页 + 白名单扩展 | tsc 绿；三种导出组合手动验收；剪映端目视验收 |

**实现顺序注意**：J1 纯新增零风险；J2 只加新消息不改旧消息；J3 前端三选一默认 `'mp4'` 保持现状行为，不改变任何冻结交互。

完成后更新 `docs/PROJECT_DESIGN.md` 第八部分（切片系统）新增剪映草稿小节。