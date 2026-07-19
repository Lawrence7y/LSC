# 前端 UI 统一与溢出修复设计

**日期：** 2026-07-15  
**状态：** 已批准（方案 1）  
**范围：** `lsc-electron` 渲染进程 UI

## 目标

以浅色工作台 + 白底弹层（二级页蒙层压暗突出层次）为观感基准；主色 `#31B3AE`；圆角梯级 SM/默认/LG = **6 / 8 / 14**。先修溢出与遮挡，再将散落硬编码收敛到 `tokens.css` / ConfigProvider，不做页面重做。

## 非目标

- 不把设置抽屉/Modal 改成深色主题
- 不新建组件库、不换字体、不改品牌色系
- 不改录制/切片/对齐等业务逻辑

## 令牌约定

| 用途 | Token / 值 |
|---|---|
| 主色 | `--brand-500` / Config `colorPrimary: #31B3AE` |
| 控件圆角 | `--radius-md` 8px；小控件 `--radius` 链路中 SM=6 |
| 卡片/分区 | `--radius` 14 或 `--radius-sm` 10 |
| 背景/文字 | `--bg-*` / `--text-*` / `--border-default` |

`App.tsx` ConfigProvider 与 `tokens.css` 保持一致，作为 Ant Design 组件唯一主题源。

## 溢出与布局修复

1. **分析/导出 Modal「导出预设」**  
   标签与 Select 改为纵向或 `flex` + `minWidth:0`；Select `style={{ width: '100%', minWidth: 0 }}`；option 长文案缩短展示（短 label + title），禁止撑破 Modal 宽度（520）。

2. **设置抽屉右侧裁切**  
   `SettingsRow`：左侧 label 不收缩，右侧控件容器 `flex: 1; minWidth: 0; maxWidth: min(360px, 100%)`；`.settings-select { max-width: 100%; box-sizing: border-box }`。Drawer `width: 520` 保持。

3. **RoomCard 区域放大底栏被盖**  
   放大态底栏 `zIndex >= 9`（高于 VideoPreview 的 8），或放大时隐藏自定义底栏仅保留原生 controls + 缩小按钮。优先抬高 zIndex，保留静音等快捷入口。

4. **Timeline 紫标**  
   紫线贯穿更清晰（可略增高或移到 inner）；`::before` 三角不被 `overflow-y: hidden` 裁切（增加 scroll 上 padding 或降低三角伸出）。

5. **ControlBar 窄窗**  
   底栏按钮行 `flexWrap: 'wrap'`，中间时间码可 `flexShrink: 1; minWidth: 0`。

6. **ClipList 滚动**  
   右栏与 `ClipList` Card/`flex:1` 补 `minHeight: 0`。

## 风格收敛

- 内联杂色（如 `#1D9E75`、随意成功/警告色）改为语义 token
- 随意 `borderRadius: 4/10/13` 归入 6/8/10/14 档
- 卡片分区统一 `--radius`（14）或 `--radius-sm`（10）
- 不扫无关业务文件；优先 Settings、Workbench Modal、RoomCard、ControlBar、Timeline、ClipList、AnalysisProgress、MainLayout

## 验收

- [ ] 多房间同步分析导出：导出预设下拉完整落在 Modal 内
- [ ] 设置抽屉：OCR/编码器等下拉不被右缘裁切
- [ ] 房间卡片放大：底栏可点（画质/静音等）
- [ ] 预览跟播一段时间后紫标可见且三角不被裁
- [ ] 窄窗下 ControlBar 不互相覆盖
- [ ] 切片较多时列表可内部滚动
- [ ] 浅色主题下主色/圆角观感一致，无大块「另一套皮肤」感
