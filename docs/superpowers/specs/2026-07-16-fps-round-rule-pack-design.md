# FPS 回合规则包（填空式持续分析）设计

> **日期：** 2026-07-16  
> **状态：** 待用户审阅后进入实施计划  
> **前置：**
> - [2026-07-12-valorant-round-continuous-analysis-design.md](./2026-07-12-valorant-round-continuous-analysis-design.md)
> - [2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md](./2026-07-14-valorant-phase-scheduler-continuous-analysis-design.md)
> - [2026-07-14-pending-clip-refine-timeline-design.md](./2026-07-14-pending-clip-refine-timeline-design.md)
> - [2026-07-15-continuous-analysis-quality-first-design.md](./2026-07-15-continuous-analysis-quality-first-design.md)

## Goal

把持续分析里写死的「无畏契约回合策略」抽成声明式 **FPS 回合规则包（Rule Pack）**，用同一套解释器跑：

- 官方包 `builtin:valorant`（行为与现网对等）
- 官方包 `builtin:cs2`
- 用户复制官方包后，在预览上拖 OCR 框、改关键词/时长，适配同构射击游戏

用户侧是**填空式模板**，不是全自由规则引擎。产品链路（pending → 精修确认 → 手动导出）保持不变。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 开放程度 | 用户拼规则，但 v1 收敛为填空式 `fps_round` 模板 |
| 第一场景 | 同构射击回合（Valorant / CS2 / 用户自建） |
| OCR 区域 | 预览（或录制截帧）上拖框标定 |
| 无畏契约 | 也变成内置规则包，与自定义同一引擎 |
| 落地路径 | 方案 A：声明式规则包 + 解释器；无畏先 parity 再切流 |
| 确认门 | 默认必须视觉起止才可升格（不放宽纯音频可导） |

## 非目标

- 全自由 AND/OR 条件树、自然语言生成规则
- 杂谈 / 带货等非回合模板
- 规则云分享 / 市场
- 分析进行中热切换规则包
- 自动探测 OCR 区域
- 改变「只分析主房录制文件」、预览与录制分离、导出队列架构
- v1 普通编辑页不开放关闭 `require_ocr_bounds`（避免用户误放宽确认门）

## Architecture

```
用户拖框 / 填词
        │
        ▼
┌──────────────────┐
│  Rule Pack (JSON) │  ← builtin:valorant / builtin:cs2 / 用户包
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ fps_round 解释器  │  ← 唯一策略入口
└────────┬─────────┘
         │
    ┌────┴────┬────────────┐
    ▼         ▼            ▼
  OCR信号   音频信号    相位调度参数
 (框+关键词) (RMS/onset) (pack.phase)
         │
         ▼
  回合候选 → 确认门 → clip_queued(pending)
         │
         ▼
  现有精修 / 多房映射 / 手动导出（不动）
```

### 不变

- 信号能力：OCR、RMS、onset、音效（`lsc/analyzer/*`）
- 入列与确认：`confirm_status` / `export_deferred` / 用户精修后再导出
- 多房 `_map_highlight_to_room`、全局导出队列
- 持续分析只读主房录制文件

### 会变

- `valorant_round` 不再是特殊模式分支，而是加载 `builtin:valorant`
- 启动持续分析以 `rule_pack_id` 为主键；旧 `game=valorant` 兼容映射到该内置包
- CS2 / 自定义 = 另一份包，不是另一套 worker

## 规则包 Schema

### 用户可编辑字段

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | string | 用户包唯一 id（官方包固定 `builtin:*`） |
| `name` | string | 显示名 |
| `template` | `"fps_round"` | v1 唯一模板 |
| `ocr_regions.status` | `{x,y,w,h}` | 回合状态区，相对画面 0–1，**必填** |
| `ocr_regions.killfeed` | 同上 \| null | 击杀栏，v1 可选（UI 可隐藏） |
| `keywords.buy` | string[] | 买枪/准备态 |
| `keywords.combat` | string[] | 交战态（可空：以「离开买枪」为开始） |
| `keywords.result` | string[] | 结算/胜负 |
| `duration.min_sec` | number | 最短回合 |
| `duration.max_sec` | number | 最长回合（防粘连） |
| `trim.start_pad_sec` | number | 起点后裁 |
| `trim.end_pad_sec` | number | 终点前裁 |
| `confirm.require_ocr_bounds` | bool | 默认 `true`；v1 编辑页只读展示 |

坐标约定与现有 `_GAME_CONFIGS["…"].crop_ratio` 一致：`(x, y, w, h)` 为相对宽高的比例。

### 仅内置包携带（v1 不对用户开放）

| 字段 | 含义 |
|------|------|
| `phase.*` | OCR 疏密、lookback、买枪休眠等（对标现 `ValorantProfile`） |
| `audio.*` | RMS/onset 阈值与辅助策略 |
| `confirm.start_by` / `end_by` | 确认门细节（对标 `ocr_buy_exit`、`ocr_result` / `next_buy`） |

用户可读文案（产品侧映射，不必暴露字段名）：

- 开始：离开买枪（进入交战）
- 结束：出现结算，或再次进入买枪
- 不够稳的先待确认

### 内置与用户包策略

1. **`builtin:valorant`**：从当前硬编码参数导出；必须通过行为对等测试后再切流
2. **`builtin:cs2`**：同骨架，换默认框与关键词
3. **用户包**：只能「复制官方包 → 改可编辑字段」；禁止从零创建空壳（避免不可用规则）

### 存储

| 类型 | 位置 |
|------|------|
| 官方包 | 只读资源目录 `lsc/analyzer/rule_packs/`（随应用分发） |
| 用户包 | 本地 `data/analysis_rules/*.json`（原子写：`.tmp` + replace） |

## 拖框 UX

### 入口

规则管理**主入口在持续分析启动前**（选包旁提供「管理规则」）；设置页可放同一列表的次要入口。

- 列表：官方包（只读）+ 我的包
- 操作：复制 → 编辑 → 保存；编辑页含「标定画面区域」

### 标定流程

1. 选帧来源（优先级）：当前房间 MSE 预览当前帧 → 否则该房录制文件指定秒截帧
2. 画布叠加可拖拽/缩放框：**状态区必填**；击杀栏可选
3. 「从这帧试读 OCR」：对该区域跑一次识别，展示读到的文字，便于改关键词与校验套框
4. 保存只存 0–1 比例，不存绝对像素

### 坐标与预览/录制

- 分析始终基于**录制文件**（与现持续分析一致）
- 预览帧仅用于标定；比例坐标在常见同画幅（如 16:9）下流分辨率不同仍成立
- 标定 UI 必须以 video **内容区**（排除 letterbox 黑边）为 100% 坐标系

### 防呆

- 状态区过小/过大：提示
- 试读多次为空：提示换帧或挪框
- 关键词与试读无交集：弱提示，不阻断保存
- 用户包缺少有效 `ocr_regions.status`：**禁止**启动持续分析
- 运行中不热更新规则包；更换需先停再启

## 解释器与 Worker 接入

### 启动

`start_continuous_analysis` 增加 `rule_pack_id`。

兼容：仅传旧参数 `game=valorant`（或等价 mode）时，后端映射为 `builtin:valorant`。

```
start_continuous_analysis
  → 加载 Rule Pack（缺失/损坏则失败并返回明确错误）
  → 绑定到该房 continuous task
  → worker 每轮：interpreter.scan(video, time_range) → rounds
  → 原确认门 / 入列 / 多房映射消费 rounds
```

### 解释器职责

用包参数驱动现有检测能力，不重写信号底层：

- OCR：`crop_ratio` ← `ocr_regions.*`；匹配 ← `keywords.*`
- 音频：RMS/onset，阈值 ← `audio.*`（用户不可见）
- 相位：`profile_from_pack(pack.phase)` 替代写死 `get_profile(name)`
- 入列前 trim / min-max：统一用 `trim` / `duration`

### 无畏迁移（两步）

1. **导出对等包**：现网 Valorant 参数写入 `builtin:valorant`；解释器跑该包；与旧路径做 parity
2. **切流**：parity 通过后，去掉 `valorant_round` 特殊分支；前端「无畏契约」= 选中该内置包

CS2 与用户编辑入口在步骤 2 稳定后开放，避免双路径行为漂移。

### 失败与降级

| 情况 | 行为 |
|------|------|
| 规则包缺失/损坏 | 启动失败，明确错误 |
| OCR 不可用 | 可继续音频辅助；`require_ocr_bounds=true` 时不得升格可导 |
| 用户包缺状态区 | 拒绝启动 |
| 扫描 0 回合 | 保持等待；状态可提示检查框/关键词 |

### WebSocket / 状态

- 请求：`rule_pack_id`（可选兼容旧字段）
- `continuous_analysis_status` 增加 `rule_pack_id`、`rule_pack_name`（旧客户端可忽略）

## 与现有确认/入列契约的关系

- 检出回合默认 `confirm_status=pending`、`export_deferred=true`，**不**自动 FFmpeg 导出
- 升格可确认（含 `ocr_confirmed`）仍遵守包内确认门；默认与现 `_is_auto_exportable_valorant_round` 精神一致
- 用户精修 / `confirm_highlight_clip` / 导出门禁不变
- `round_key` 去重策略保留；键 = `rule_pack_id` + 主房回合窗口（避免跨包冲突）

## Testing / 验收

1. **Valorant parity**：同一黄金样例上，`builtin:valorant` 与旧路径回合数、可确认集合一致；起止边界误差 ≤ 1.0s（目标 ≤ 0.5s）
2. **CS2 内置包**：默认框+词在样例上能切出合理回合；用户改框后试读可见字
3. **用户包**：缺状态区不能启动；框存 0–1 比例；换分辨率大致套准
4. **产品链路**：仍进 pending；确认门禁与手动导出不变
5. **兼容**：只传旧 `game=valorant` 仍能启动
6. **回归**：`phase_scheduler` / continuous guards / confirm_status / synced continuous 相关测试在切解释器后通过（或等价改写后通过）

## 成功标准（产品体感）

用户复制「CS2」→ 预览拖好状态区 → 试读看到买入/回合类文字 → 改关键词 → 启动持续分析 → 回合进入待精修列表。无畏用户无感，底层已是同一规则包引擎。

## 实施顺序（供后续计划拆解，非本轮改代码）

1. 定义 Rule Pack JSON schema + 加载/校验
2. 导出 `builtin:valorant`，解释器旁路跑通 + parity 测试
3. Worker / handler 切到解释器，保留旧参数映射
4. 前端规则列表 + 拖框标定 + OCR 试读
5. 增加 `builtin:cs2` + 「复制为用户包」
6. 清理废弃的 valorant 硬编码分支（确认无引用后）

## 开放问题（已拍板默认）

| 问题 | 默认决定 |
|------|----------|
| `round_key` 是否含 `rule_pack_id` | 含，避免跨包冲突 |
| v1 是否暴露击杀栏框 | 可选字段保留，UI 可先隐藏 |
| 用户能否关闭 OCR 确认门 | v1 否（字段只读展示） |
| 规则包热更新 | 不做，停后换包再启 |
