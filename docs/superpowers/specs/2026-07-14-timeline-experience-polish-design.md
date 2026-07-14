# 时间线体验抛光包（方案 A）设计

> **日期：** 2026-07-14  
> **状态：** 已批准；实施计划见 [2026-07-14-timeline-experience-polish.md](../plans/2026-07-14-timeline-experience-polish.md)  
> **前置：**  
> - [2026-07-13-common-timeline-ux-design.md](./2026-07-13-common-timeline-ux-design.md)  
> - [2026-07-14-pending-clip-refine-timeline-design.md](./2026-07-14-pending-clip-refine-timeline-design.md)  
> - [2026-07-14-next-iteration-trust-platform-hygiene.md](../plans/2026-07-14-next-iteration-trust-platform-hygiene.md) Track A（精确导出契约）

## Goal

在**不扩成 NLE** 的前提下，把工作台时间线/播放控制抬到「专业播放器 + 精修手感」水位：跟手、精确、反馈清楚。只做高体验 ROI 的抛光，明确砍掉看起来专业但干扰主路径的功能。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 方向 | 播放手感 + 精修剪辑 + 视觉分层（1+2+3） |
| 落地路线 | **方案 A：体验抛光包** |
| 产品边界 | 保持单轨 scrubber；不做多轨 / 波形 / 胶片 / 完整撤销栈 |

---

## 1. 原则

1. **体验优先于功能数量**：每个改动必须让「播 / 切 / 看」更顺，而不是多一个按钮。
2. **信任优先于假精确**：MSE 预览不做帧精确；能精确的路径（公共轴 snapshot / I·O 墙钟）必须诚实可用。
3. **延续现有手势**：在现有 Space / I / O / 滚轮缩放上补齐，不发明第二套快捷键体系。

---

## 2. 范围内（本轮做）

### 2.1 播放导航

| 能力 | 行为 | 备注 |
|------|------|------|
| 小步进 | `←` / `→`：相对播放头 ±**1.0s**（公共轴就绪时对所有目标房同步 seek） | 非帧步进（MSE 无帧缓存） |
| 微调 | `,` / `.`：±**0.2s** | 精修边界用 |
| 穿梭（轻量） | `J` / `L`：−2s / +2s；`K`：暂停（若正在播）或播放（若已暂停） | 不实现加速连按变速引擎 |
| 变速 | ControlBar 增加 **0.5× / 1× / 1.5× / 2×**；快捷键可选 `Shift+<` / `Shift+>` 循环 | 写到各路 `<video>.playbackRate`；默认 1× |
| ±10s | 保留现有按钮 | 粗导航 |

### 2.2 入出点精修

| 能力 | 行为 |
|------|------|
| 微调入出点 | `[` / `]`：出点 ±**0.5s**；`Shift+[` / `Shift+]`：入点 ±**0.5s** |
| 公共轴拖拽精确化 | `getAlignStatus === 'ready'` 且 `clip_ready` 时：松手后的 mark **不得**再标为「近似」；添加切片仍走 `create_clip_snapshot`。拖拽中仍可本地预览，松手写 mark（可继续 `live:false` 给后端 mark，但**列表/导出信任**靠 snapshot，UI 不再把「公共轴已就绪」的拖拽标成近似） |
| 未对齐拖拽 | 保持现状：toast +「近似」Tag；禁止伪装精确 |
| A-B 真循环 | 用 `timeupdate` / `requestAnimationFrame` 检测播放头 ≥ out → seek 回 in；去掉 `setInterval` 粗定时。多选公共轴时对各目标房同步回跳 |

### 2.3 视觉分层（轻量）

在现有 `Timeline.css` / tokens 上强化层级，**不重做组件树**：

1. 弱化厚底板 → 悬浮轨道感  
2. 基础轨（细线）→ 已播放进度（品牌色细线）→ 选区半透明 → 入/出 marker → 播放头（最上层）  
3. 精修色带保持硬对比；确认后可选弱样式（若已有 `ocr_confirmed`/`user_confirmed` 区间，用更淡描边，不做新交互）  
4. 时间码：hover / 播放头旁可读性加强（字号/对比度），不新增独立时间码编辑框  

### 2.4 状态反馈

| 项 | 行为 |
|----|------|
| 对齐徽标 | 保持「公共轴已就绪 / 未对齐 / 失效」；文案不变 |
| 近似 Tag | **仅**在未对齐或无 snapshot 墙钟时出现；公共轴 + clip_ready 下拖拽/添加不再显示近似 |
| 缓冲条 | 若 MSE `buffered` 可映射到当前轴：显示真实 loaded 区间；映射不可靠则**保持现状**，不强行画假缓冲 |

---

## 3. 非目标（明确不做）

| 不做 | 原因 |
|------|------|
| 多轨 / ripple / roll / 转场 / 特效 | 产品定位 |
| 波形层 / 缩略图胶片 | 曾引入又砍；占 CPU 与视线，ROI 低 |
| 完整 Ctrl+Z 历史栈 | 工程大；本轮用「键微调 + 拖得准」替代 |
| 帧精确（按 fps 步进） | MSE 非帧播放器；避免假精确 |
| 可拖播放头本体 + 音频 scrub 预听 | 与 marker 拖拽冲突；复杂度高 |
| 无预览时整段录像文件 scrub | 需独立文件播放器；属另一轨 |
| 重写快捷键体系 / 可配置 keymap | 本轮固定表即可 |
| 改录制 / MSE / 共享进样架构 | 与体验抛光无关 |

---

## 4. 架构要点

```
快捷键 / ControlBar
        │
        ▼
Workbench seek/mark helpers（统一 common↔preview 换算）
        │
        ├─► 各房 MSE <video> currentTime / playbackRate
        ├─► 后端 set_mark_in/out（I/O 仍 live:true；拖拽可 live:false）
        └─► create_clip_snapshot（公共轴添加切片唯一精确路径）

Timeline 视觉：仅 CSS + 少量 class；不改三轴契约
A-B loop：监听播放头，越界 seek；不 setInterval
```

**三轴契约不变**（CLAUDE.md §8.7）：`windowStart` / 播放头只用 common 或 preview，禁止混入 `recorded_duration`。

---

## 5. 快捷键对照（增量）

| 功能 | 快捷键 | action id |
|------|--------|-----------|
| 后退 1s | `←` | `seek:back-1` |
| 前进 1s | `→` | `seek:fwd-1` |
| 后退 0.2s | `,` | `seek:back-fine` |
| 前进 0.2s | `.` | `seek:fwd-fine` |
| −2s | `J` | `seek:back-2` |
| 播放/暂停 | `K`（与 Space 同效，可并存） | `play:toggle` |
| +2s | `L` | `seek:fwd-2` |
| 出点 −0.5s / +0.5s | `[` / `]` | `mark:nudge-out` |
| 入点 −0.5s / +0.5s | `Shift+[` / `Shift+]`（实际按键常为 `{` / `}`） | `mark:nudge-in-*` |
| 循环速率 | `Shift+,` / `Shift+.`（实际按键常为 `<` / `>`） | `rate:cycle-*` |

焦点在 `input`/`textarea`/`select` 时仍全部拦截（现有规则）。

> 注：入出点 nudge 用 **0.5s** 步进（比播放微调粗一档），避免误触把选区拧碎；播放微调保持 0.2s。

---

## 6. 涉及文件（预期）

| 文件 | 职责 |
|------|------|
| `lsc-electron/src/hooks/useKeyboardShortcuts.ts` | 注册增量快捷键 |
| `lsc-electron/src/pages/Workbench/index.tsx` | seek/nudge/rate/loop；公共轴拖拽「近似」判定 |
| `lsc-electron/src/pages/Workbench/components/ControlBar.tsx` | 速率按钮；可选步进文案 |
| `lsc-electron/src/components/Timeline/Timeline.css` | 视觉分层 |
| `lsc-electron/src/components/Timeline/index.tsx` | 缓冲条映射（若可做）；class 微调 |
| `lsc-electron/src/services/mediaSourcePlayer.ts` | `setPlaybackRate` 辅助（若尚无） |
| `tests/test_frontend_stability_guards.py` | 守卫：无波形复活、快捷键 id 存在、近似 Tag 条件 |

后端原则上**不改**；若公共轴拖拽精确化只需前端 UI 判定，不新增 WS。

---

## 7. 验收标准

1. 公共轴就绪时：用 `←→` / `,.` / `J L` 可流畅挪播放头；多选房画面同步。  
2. `[` `]` 能微调出点；选区与时间码即时更新。  
3. A-B 循环在出点附近回跳，无肉眼可见的「定时器漂移」堆叠。  
4. 速率切换后各路预览同步变速；切回 1× 正常。  
5. 公共轴 + clip_ready 下拖入出点再添加切片：**不出现**「近似」Tag；未对齐时仍出现。  
6. 时间线视觉：播放头/选区/进度层级一眼可辨；暗色主题对比度不劣化。  
7. 回归：现有 I/O 墙钟精确路径、精修确认门禁、三轴 windowStart 契约测试不破。

---

## 8. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 快捷键与浏览器/输入冲突 | 沿用焦点拦截；Modal 打开时不触发 |
| 变速导致音画不同步感 | 仅改 playbackRate；不改后端时间映射 |
| 公共轴「去近似」被误解为墙钟精确 | 文案：公共轴下精确靠 snapshot；拖拽只是选区 UI |
| CSS 分层影响磁吸命中 | 不改 hit-test 几何，只改绘制层级 |

回滚：快捷键表与速率 UI 可整段关掉；CSS 可还原；loop 可退回旧实现。

---

## 9. 与既有计划关系

- **不恢复波形**（覆盖 common-timeline 里已废弃的波形项）。  
- **承接** next-iteration Track A 的「精确路径诚实」：本轮把公共轴 UI 的「近似」误报收掉。  
- **不替代** pending 精修状态机；只改善精修时的播控手感。
