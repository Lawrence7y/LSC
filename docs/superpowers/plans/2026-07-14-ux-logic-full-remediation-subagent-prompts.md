# 子代理提示词（UX / 逻辑全量修复）

> 主计划：`docs/superpowers/plans/2026-07-14-ux-logic-full-remediation.md`  
> 用法：每个 Workstream 开一个**全新**子代理，粘贴对应「完整提示词」整段。完成后由主代理做 diff review，再开下一个。  
> 仓库根目录：`D:\Project\直播切片多人`  
> 约束：遵守 `CLAUDE.md`；不扩范围；不提交 unless 用户要求；改完跑计划中的验证命令。

---

## 调度顺序（推荐）

1. **W1**（阻塞正确性）→ 2. **W2** → 3. **W3** → 4. **W6** → 5. **W7** → 6. **W8** → 7. **W4** → 8. **W5**  
可并行（无冲突时）：W4 ∥ W7；W5 尽量最后。

---

## W1 — AI 精修坐标系（P0）

### 完整提示词

```
你是 LSC 代码库的实现子代理。只做 Workstream W1，不要做其他 WS。

仓库：D:\Project\直播切片多人
主计划：docs/superpowers/plans/2026-07-14-ux-logic-full-remediation.md（先读 W1 与「时间轴契约」节）

## 背景（必须理解）
- AI 持续分析入列时 clip.start/end 是「录制文件内秒数」recording_local（见 room_handler._auto_export_highlights）。
- 预览 seek / set_mark_in|out 的 time 必须是「预览本地秒」preview_local。
- 当前 bug：
  1) applySelectClip 在无 common 轴时用 recording 秒直接 mseSeek / set_mark（Workbench/index.tsx ~1491-1531）
  2) handleConfirmClip 在 common 模式下用 commonToPreview 得到 preview 秒写回 confirm_highlight_clip，随后列表 start/end 变成 preview 秒；导出 source=ai_highlight 时 _resolve_export_range 原样当 recording 秒（room_handler ~1876-1877）→ 切错段。

## 目标
1. 进入精修时：recording_local → preview_local（优先 TimelineContext：recording↔common↔preview；无 context 时安全降级或拒绝并提示）。
2. 确认精修时：写回列表与 confirm_highlight_clip 的 start/end 必须是 recording_local。
3. isApproximateClip：is_ai_highlight 为 true 时不要显示「近似」（除非 mark_precision==='approximate'）。
4. 精修横幅文案按是否 commonMode 区分，禁止写死「当前为录制时间轴」。

## 关键文件
- lsc-electron/src/pages/Workbench/index.tsx（applySelectClip, handleConfirmClip, isApproximateClip, 横幅）
- lsc-electron/src/pages/Workbench/components/ClipList.tsx（若徽标逻辑在此）
- python-backend/handlers/room_handler.py（仅当后端也需校验/转换时；优先前端传对坐标）
- tests/test_clip_refine_handlers.py
- tests/test_frontend_stability_guards.py

## 实现要求
- TDD：先补/改测试锁定「confirm 后导出仍用 recording 秒」，再改代码。
- 复用现有 previewToCommon / commonToPreview / commonToRecording（查 timeline 工具函数，勿发明第二套公式）。
- 不实现手动切片精修。
- 不改命名、品牌色、导出并发。

## 验证
pytest tests/test_clip_refine_handlers.py tests/test_frontend_stability_guards.py -v
cd lsc-electron && npx tsc --noEmit

## 交付
简要说明改了哪些函数、坐标转换路径、测试结果。不要 git commit（除非提示要求）。
```

---

## W2 — 命名统一 + 手动切片模型

### 完整提示词

```
你是 LSC 实现子代理。只做 Workstream W2。

仓库：D:\Project\直播切片多人
主计划：docs/superpowers/plans/2026-07-14-ux-logic-full-remediation.md（W2 + 决策 D1/D5）

## 决策锁定
- 文件名/label：
  - 手动：{主播}_手动_{NNN}（NNN 三位，按 room_id 独立计数）
  - AI 回合：{主播}_AI_回合{RR}_{NNN}
- 手动切片：统一模型字段（必须有 clip_id），命名统一；不进入 refine UI。
- 点手动切片时：不要 begin_refine；可 toast「手动切片可直接导出」。

## 目标
1. 新建 lsc-electron/src/utils/clipNaming.ts（sanitize + 两个 format 函数）。
2. Python 侧在 room_handler 增加等价 helper，替换：
   - _auto_export_highlights 的 label（现：{room}_回合{idx}_{start}s）
   - start_analysis_export 的「高光」label
3. 前端手动 handleAddClip / handleControlAddClip / clip_queued 展示 label 全部走统一命名。
4. 禁止再用全局 clips.length 当跨房间序号。
5. 旧格式字符串守卫写入 test_frontend_stability_guards.py。

## 不要做
W1 坐标系、导出并发、品牌色、设置页删除阈值。

## 验证
pytest tests/test_frontend_stability_guards.py -v
cd lsc-electron && npx tsc --noEmit

## 交付
列出所有 label 生成调用点替换清单 + 测试结果。不要 commit。
```

---

## W3 — 导出队列 / Ctrl+E / 分析入列一致

### 完整提示词

```
你是 LSC 实现子代理。只做 Workstream W3。

仓库：D:\Project\直播切片多人
主计划：docs/superpowers/plans/2026-07-14-ux-logic-full-remediation.md（W3 + 决策 D2）

## 目标
1. settings 增加 export_max_concurrent: 1|2，默认 2；Settings UI 可选；save_settings 后后端生效。
2. room_handler 中 _EXPORT_MAX_CONCURRENT 改为可读设置；worker 池可收缩/扩张（降并发不杀进行中任务）。
3. Ctrl+E（Workbench export:clip）：优先导出 ClipList 勾选项中 canExport 的；否则 refining 条；否则第一条可导；无则 toast。
4. handleExportMany：统计无 record_output_path 跳过数，toast「已排队 N，跳过 M（无录制文件）」。
5. 同步分析 start_analysis_export 与持续分析一致：默认 list_only / clip_queued pending，不自动 queue_export。

## 关键文件
- lsc-electron/src/pages/Settings/index.tsx
- lsc-electron/src/types/index.ts
- lsc-electron/src/store/appStore.ts
- lsc-electron/src/pages/Workbench/index.tsx（handleExportMany, shortcuts）
- lsc-electron/src/pages/Workbench/components/ClipList.tsx（selectedIndices 需可被快捷键读取——必要时把选择提升到 Workbench/store）
- python-backend/handlers/room_handler.py（导出池 + start_analysis_export）
- python-backend/persistence.py 或 settings 默认

## 注意
ClipList 的 selectedIndices 若是组件 local state，Ctrl+E 读不到——请提升状态或通过回调/store 暴露，这是本任务一部分。

## 验证
pytest 相关 tests（含 test_frontend_stability_guards 若加守卫）
npx tsc --noEmit

## 交付
说明并发如何热更新、Ctrl+E 选择来源、同步分析是否仍自动导出。不要 commit。
```

---

## W4 — 设置清理

### 完整提示词

```
你是 LSC 实现子代理。只做 Workstream W4。

仓库：D:\Project\直播切片多人
主计划：docs/superpowers/plans/2026-07-14-ux-logic-full-remediation.md（W4 + 决策 D3）

## 目标
1. 删除设置页「绝对分数下限」UI；从 AnalysisSettings 类型、appStore 默认、Workbench 请求体移除 absolute_threshold。
2. 不要去「接线」分析管线；就是删掉误导配置。
3. 共享进样开关变更后立即 save_settings（与主题即时保存一致），toast 提示新预览/录制生效。
4. 文案：「码率限制」改为「自定义码率」；预览画质选项与后端/文档对齐（处理「标清」：映射到现有档或删除选项）。

## 文件
- lsc-electron/src/pages/Settings/index.tsx
- lsc-electron/src/types/index.ts
- lsc-electron/src/store/appStore.ts
- lsc-electron/src/pages/Workbench/index.tsx（传参清理）
- 若 persistence 有默认 analysis_settings，一并清理

## 不要做
品牌色 token（W5）、导出并发 UI（若 W3 未做且你会撞 Settings，可预留 export_max_concurrent 控件位置但不实现逻辑——优先与 W3 协调：若 export 控件已存在则勿重复）。

## 验证
npx tsc --noEmit
grep -r absolute_threshold lsc-electron/src 应无业务引用（测试除外）

## 交付
变更文件列表。不要 commit。
```

---

## W5 — 品牌色 #31B3AE

### 完整提示词

```
你是 LSC 实现子代理。只做 Workstream W5。

仓库：D:\Project\直播切片多人
主计划：docs/superpowers/plans/2026-07-14-ux-logic-full-remediation.md（W5 + 决策 D4）

## 目标
将主色从苹果蓝统一为图标青 #31B3AE。

1. lsc-electron/src/styles/tokens.css：--brand-500: #31B3AE；设置合理的 --brand-400 / --brand-600（亮/暗各一档）。
2. App.tsx ConfigProvider colorPrimary: '#31B3AE'。
3. 全前端业务代码 grep 替换硬编码 #007aff / #1677ff / #2e8dff（含 ClipList fallback、RoomCard、RefreshButton、ErrorBoundary fallback）。
4. 保持暗色背景体系不变；成功/错误/警告色不动。
5. 不要改 lsc-ui-design 历史 JSON（除非必要）；以运行时 tokens 为准。

## 验证
npx tsc --noEmit
在 tokens.css 确认 --brand-500 为 #31B3AE
grep 业务 src 无 #007aff（允许注释说明迁移）

## 交付
色板取值表（500/400/600）+ 替换文件列表。不要 commit。
```

---

## W6 — 对齐静音 / 公共轴软失效 / 分析门槛

### 完整提示词

```
你是 LSC 实现子代理。只做 Workstream W6。

仓库：D:\Project\直播切片多人
主计划：docs/superpowers/plans/2026-07-14-ux-logic-full-remediation.md（W6）

## 目标
1. 一键对齐成功后：禁止自动 set_preview_muted=true。改为 toast 操作「静音其他房间」或设置项「对齐后静音快房」默认 false。
2. disconnect_room / 房间掉线：不要无条件 _invalidate_room_timeline 整组。
   - 非参考房断开：从 group 移除该房，保留其余 TimelineContext（剩余≥2）。
   - 参考房断开或剩余<2：才全组 invalidate，并 toast 说明。
3. 多房开始持续分析：未对齐时按钮 disabled + Tooltip「请先一键对齐」，不要等提交后才 error。
4. 未对齐拖拽导出：保持 approximate 警告；不要假装 exact。

## 关键文件
- lsc-electron/src/pages/Workbench/index.tsx（对齐成功分支 ~1246、分析入口 ~1875）
- python-backend/handlers/room_handler.py（_invalidate_room_timeline 调用点 ~2348、移除房等）
- 可能涉及 timeline state 的前端 store

## 验证
pytest tests/test_timeline_delta_consistency.py tests/test_frontend_stability_guards.py -q
手动推理：断非参考房后 timelineContext 仍在的路径

## 交付
说明新的 invalidate 策略与对齐静音交互。不要 commit。
```

---

## W7 — 快捷键边界

### 完整提示词

```
你是 LSC 实现子代理。只做 Workstream W7。

仓库：D:\Project\直播切片多人
主计划：docs/superpowers/plans/2026-07-14-ux-logic-full-remediation.md（W7）

## 目标
1. useKeyboardShortcuts.ts：除 input/textarea/select/contentEditable 外，当存在可见 Ant Design Modal（.ant-modal-wrap 且未 hidden）或 role=dialog 时，拦截业务快捷键（Ctrl+1/2、F5 导航除外，与现逻辑一致）。
2. Workbench：无选中房间时 I/O/R/M/Space 等不要静默 return；message.info('请先选择房间')。
3. Ctrl+E 若 W3 已修好则不要回退；若尚未做，实现最小版：导出 clips 中第一条 canExport，并加 TODO 指向 W3——优先检查 W3 是否已合入。

## 文件
- lsc-electron/src/hooks/useKeyboardShortcuts.ts
- lsc-electron/src/pages/Workbench/index.tsx（handleWorkbenchShortcut）

## 验证
npx tsc --noEmit
可加 frontend_stability_guards 断言 isInputFocused 或 modal 检测字符串存在

## 交付
焦点门控规则说明。不要 commit。
```

---

## W8 — 删除联动 / 磁盘满 / 预览信任

### 完整提示词

```
你是 LSC 实现子代理。只做 Workstream W8。

仓库：D:\Project\直播切片多人
主计划：docs/superpowers/plans/2026-07-14-ux-logic-full-remediation.md（W8）

## 目标（全部要做）
1. handleDeleteClip：删除前若 queued/exporting → send cancel_export；若 refiningClipId 匹配 → cancel_refine_clip；再更新 clips。
2. 磁盘满停录：manager 已有检测（_MIN_FREE_BYTES_WHILE_RECORDING）。增加广播 type 如 recording_stopped / room_error，reason=disk_full；前端 useNotifications/Workbench 强制 toast.error（聚焦窗口也要显示），房间卡显示持久错误。
3. 多路预览降画质：在预览数≥阈值时显示持久横幅，不仅 3 秒 toast。
4. preview_phase：确保 refreshing_url / probing / transcoding 等阶段会变化；VideoPreview overlay 跟阶段文案（不要求假百分比）。
5. MSE watchdog（useWebSocket.ts）：stall 先 request_mse_init；连续失败才 enable_preview 重拉；用户可见「预览恢复中」。
6. 连接 offline：解析 offline 时尽快结束「连接中」loading，卡片明确「未开播」。

## 关键文件
- Workbench/index.tsx（delete、横幅）
- useNotifications.ts / useWebSocket.ts
- VideoPreview.tsx / RoomCard.tsx
- lsc/gui/multi_room/manager.py
- python-backend/handlers/room_handler.py 或 message 广播路径

## 验证
pytest 中若有 resource/recording 相关测试则跑；tsc --noEmit
grep disk_full 或等价 reason 确认前后端贯通

## 交付
事件名字与前端处理路径。不要 commit。
```

---

## 主代理用：派发检查清单

每完成一个 WS，主代理检查：

- [ ] diff 是否越界到其他 WS
- [ ] 是否破坏时间轴契约（尤其 W1 之后）
- [ ] 测试/tsc 是否通过
- [ ] 是否引入新的硬编码蓝 `#007aff`（W5 前可暂存，W5 后为零）
- [ ] 是否误给手动切片接上 refine（禁止）

全部 WS 完成后跑：

```bash
pytest tests/test_clip_refine_handlers.py tests/test_frontend_stability_guards.py tests/test_continuous_analysis_guards.py tests/test_timeline_delta_consistency.py -v
cd lsc-electron && npx tsc --noEmit
```
