# LSC 前端重构实施计划

> 状态：待用户评审。本文档只定义后续实施步骤，本轮不执行生产代码修改。

**目标：** 在不改变后端协议、MSE、三轴时间模型和单轨时间线行为的前提下，将工作台整理为四个语义区域，并逐步提取低耦合业务流程。

**原则：**

- 先布局、后逻辑；
- 先复用、后新增；
- 一次只迁移一个职责；
- 每阶段可独立回滚和验证；
- 不新增依赖、状态库或坐标转换实现；
- 不覆盖当前工作区已有修改。

## 阶段 0：冻结基线

### 任务 0.1：确认工作区和测试基线

**只读检查：**

- `git status --short --branch`
- `git diff --check`
- 记录已修改和未跟踪文件，不将其当作重构产物。

**验证命令：**

```powershell
Push-Location lsc-electron
npx tsc --noEmit
Pop-Location

python -m pytest tests/test_frontend_stability_guards.py -q `
  --basetemp .tmp_frontend_refactor_baseline -p no:cacheprovider
```

**通过条件：**

- TypeScript 退出码为 0；
- 当前前端稳定性守卫全部通过；
- 如完整测试存在既有失败，单独记录，不用重构掩盖。

### 任务 0.2：建立手工回归清单

至少覆盖：

- 添加、连接、断开和删除房间；
- 单房间和批量录制/停止；
- 最多四路预览、质量、静音和全屏；
- 音频对齐成功、失败和低置信度；
- 时间轴拖拽、吸附、缩放、I/O、精修、DVR 和回直播；
- 手动切片、AI 待确认、批量确认和批量导出；
- 持续分析启动、停止、finalizing 和错误；
- 剪映草稿生成；
- 设置 Drawer、依赖检测、Cookie 和日志。

## 阶段 1：四区布局

### 任务 1.0：设计批准门

先提供本地浏览器原型或截图，至少确认：

- 会话栏主操作与辅助操作的两行层级；
- 右侧队列保持当前输入顺序，不在本阶段新增状态排序；
- Settings 在 `520px` Drawer 内的导航形式；
- `1360x800` 和 `1520x920` 下四区比例。

用户批准前不进入生产代码修改。

### 任务 1.1：先添加布局守卫

**修改文件：**

- `tests/test_frontend_stability_guards.py`

**守卫内容：**

- `Workbench` 包含会话栏、房间区、编辑区和切片队列四个语义类；
- `AnalysisProgress` 位于切片队列；
- 添加房间入口使用 Modal；
- Modal 保留现有输入、loading、回车提交和失败后保留输入行为；
- 设置 Drawer 打开时菜单选中设置项；
- `ClipList` 保留虚拟列表、`ROW_HEIGHT=88` 和 `contentVisibility`。

**验证：**

```powershell
python -m pytest tests/test_frontend_stability_guards.py `
  -k "four_zone or settings_drawer or clip_list" -q `
  --basetemp .tmp_frontend_refactor_layout -p no:cacheprovider
```

预期先失败，证明守卫能识别尚未完成的布局。

### 任务 1.2：实现四区 JSX 边界

**修改文件：**

- `lsc-electron/src/pages/Workbench/index.tsx`
- `lsc-electron/src/pages/Workbench/Workbench.css`
- `lsc-electron/src/pages/Workbench/components/ClipList.tsx`
- `lsc-electron/src/components/Layout/MainLayout.tsx`

**要求：**

- 只重排现有 JSX 和 props；
- 不复制或迁移 handler；
- 不改变 `RoomCard`、`Timeline`、`ControlBar`、`ClipList` 对外契约；
- 添加房间 Modal 复用现有 URL、loading 和提交逻辑；
- 切片列表可见名称改为“切片队列”；
- CSS 复用已有 token；
- 窄屏下队列移至下方。
- 第一阶段不改变切片输入顺序。

**验证：**

```powershell
Push-Location lsc-electron
npx tsc --noEmit
Pop-Location

python -m pytest tests/test_frontend_stability_guards.py `
  -k "four_zone or settings_drawer or clip_list or timeline" -q `
  --basetemp .tmp_frontend_refactor_layout -p no:cacheprovider
```

### 任务 1.3：手工验证布局

```powershell
Push-Location lsc-electron
npm run dev
```

检查：

- 宽屏四区层级；
- 窄屏队列可访问性；
- 添加房间 Modal 回车提交；
- 设置 Drawer 菜单选中；
- 时间轴和房间卡没有尺寸或滚动退化。

## 阶段 2：提取批量房间操作

### 任务 2.1：先添加批量操作守卫

**修改文件：**

- `tests/test_frontend_stability_guards.py`
- 必要时补充 `tests/test_cross_feature_guards.py`

**守卫内容：**

- 新 Hook 在回调执行时读取最新房间状态；
- 开始录制保持连接和录制状态过滤；
- 停止录制保留持续分析警告；
- 批量静音只作用于启用预览的房间；
- 保留乐观更新。

### 任务 2.2：新增 `useRoomBatchActions`

**新增文件：**

- `lsc-electron/src/hooks/useRoomBatchActions.ts`

**修改文件：**

- `lsc-electron/src/pages/Workbench/index.tsx`

**边界：**

- 只迁移批量录制、停止和静音；
- 不迁移 `handleAlignLive`；
- 不接管选择状态和布局；
- 不新增 store 字段；
- 不改变消息名。
- Hook 必须保留现有 `send` 布尔返回语义。

**验证：**

```powershell
Push-Location lsc-electron
npx tsc --noEmit
Pop-Location

python -m pytest tests/test_frontend_stability_guards.py `
  tests/test_cross_feature_guards.py `
  -k "batch or recording or mute or continuous" -q `
  --basetemp .tmp_frontend_refactor_room_batch -p no:cacheprovider
```

## 阶段 3：提取切片导出工作流

### 任务 3.1：先固定导出语义

**修改文件：**

- `tests/test_frontend_stability_guards.py`
- `tests/test_ux_habit_guards.py`
- `tests/test_analysis_draft_ux_guards.py`

**必须覆盖：**

- `pending/refining` 不能直接导出；
- 批量导出每个切片使用自身 `start/end`；
- 确认函数接收明确目标房间范围；
- 近似切片仍需显式确认；
- 剪映草稿不发生隐式单房 fallback；
- 空选择和失败有明确提示。

### 任务 3.2：新增 `useClipWorkflow`

**新增文件：**

- `lsc-electron/src/hooks/useClipWorkflow.ts`

**修改文件：**

- `lsc-electron/src/pages/Workbench/index.tsx`

**边界：**

- 迁移导出提交、导出确认弹窗和草稿会话；
- 通过参数注入 `send`、确认切片和通知能力；
- 不迁移精修、拖拽、播放头和时间轴转换；
- 不重复订阅导出进度事件；
- 不修改 `ClipSegment` 契约。
- 剪映草稿继续复用现有 `sendRequest`、`request_id`、120 秒超时和会话引用；
- 如果抽取会导致草稿自动重试/恢复生命周期分裂，则草稿会话继续留在 `Workbench`。

**验证：**

```powershell
Push-Location lsc-electron
npx tsc --noEmit
Pop-Location

python -m pytest `
  tests/test_frontend_stability_guards.py `
  tests/test_ux_habit_guards.py `
  tests/test_analysis_draft_ux_guards.py `
  -k "export or confirm or draft or pending or approximate" -q `
  --basetemp .tmp_frontend_refactor_clip_workflow -p no:cacheprovider
```

## 阶段 4：状态选择器和渲染边界

### 任务 4.1：先测量再调整

使用 React DevTools 或现有开发日志记录：

- `Workbench` 在 `rooms_updated` 高频广播下的渲染次数；
- 单个 `RoomCard` 在其他房间变化时是否重渲染；
- 导出进度更新是否引发整个时间轴更新；
- 持续分析状态是否造成无关区域刷新。

没有可观测问题时，不新增抽象。

### 任务 4.2：只提取有证据的 selectors/view-model

候选范围：

- 会话栏汇总状态；
- 分析进度摘要；
- 切片队列摘要；
- 房间显示排序。

**限制：**

- 不新增状态库；
- 不改变 `appStore` 持久状态结构；
- 不替换 `playheadStore`；
- 不移除现有虚拟列表、节流或 Worker；
- 每个新增 selector 必须有使用方和验证依据。

## 阶段 5：设置模块化

### 任务 5.1：固定设置契约

先验证：

- 300ms 防抖保存；
- 依赖检测；
- 抖音和 Bilibili Cookie；
- OCR 加速模式；
- shared ingest 警告；
- 导出并发；
- 输出目录与剪映目录；
- 更新和日志。

### 任务 5.2：按职责提取设置 Hook

候选文件：

- `lsc-electron/src/pages/Settings/hooks/useSettingsPersistence.ts`
- `lsc-electron/src/pages/Settings/hooks/useDependencyStatus.ts`
- `lsc-electron/src/pages/Settings/hooks/useCookieSettings.ts`
- `lsc-electron/src/pages/Settings/hooks/useUpdateStatus.ts`

只有当现有设置页面仍明显难以维护时才创建；能够继续复用当前局部组件的部分不拆。

### 任务 5.3：收敛视觉样式

- 使用已有 CSS token；
- 只迁移本阶段触及的内联样式；
- 不做全仓库换肤；
- 浅色主题问题单独立项。

## 阶段 6：最终验证

### 自动验证

```powershell
Push-Location lsc-electron
npx tsc --noEmit
Pop-Location

python -m pytest `
  tests/test_frontend_stability_guards.py `
  tests/test_ux_habit_guards.py `
  tests/test_cross_feature_guards.py `
  tests/test_analysis_draft_ux_guards.py `
  tests/test_jianying_frontend_guards.py -q `
  --basetemp .tmp_frontend_refactor_final -p no:cacheprovider

git diff --check
```

### 手工验证

- 运行 Electron 开发环境；
- 完整走通添加房间到导出的主路径；
- 分别验证未对齐、断线、MSE 错误、分析停止和导出失败；
- 检查宽屏与窄屏布局；
- 检查现有快捷键；
- 检查退出时预览、录制和分析清理。
- 在 `1360x800` 和 `1520x920` 下检查深色、浅色主题；
- 验证 12 个房间和 40 个以上切片时没有横向溢出；
- 验证 Settings 的 `520px` Drawer 内导航和宽控件仍可使用；
- 确认四路预览播放时重排布局不会销毁并重建 `VideoPreview`。

### 完成标准

- 四区信息层级清晰；
- 单轨时间线交互无回归；
- MSE/Web Audio/WS 协议无变化；
- 批量房间和导出流程从 `Workbench` 中移出且没有复制时间轴逻辑；
- 设置 Drawer 行为正确；
- 所有目标守卫和 TypeScript 检查通过；
- 完整测试若有既有失败，明确区分，不笼统宣称全仓库通过。

## 非目标与停止条件

出现以下情况应停止实施并重新评审：

- 需要修改后端消息契约才能完成布局；
- 需要复制时间轴坐标转换到第二处；
- 需要重建 MSE 或 Web Audio 生命周期；
- 需要覆盖用户未提交修改；
- Hook 接口参数数量快速膨胀，说明边界划分错误；
- 性能阶段没有测量依据却准备增加缓存或状态层。
