# LSC 直播切片系统 - 全量 DOM 像素级 Figma 导出与导入指南

本指南指导如何将 LSC 项目真实前端页面（包含 100% 完整 DOM、CSS 变量、暗黑主题色、SVG 图标、多轨时间轴、模态弹窗与 4 画面监控）无损导入 Figma 空项目。

---

## 📦 导出的高保真 Figma 文件清单

所有导出文件位于：[figma-export](file:///d:/Project/直播切片多人/figma-export)

### 1. 矢量 SVG 画布文件（支持直接拖入 Figma）
- **[01_Workbench_Full_Fidelity.svg](file:///d:/Project/直播切片多人/figma-export/01_Workbench_Full_Fidelity.svg)** (53.4 KB)
  - **内容**：主工作台全量 DOM 画布。包含顶部 12.4 Mbps / 60FPS 渲染 HUD、左侧 5 个真实直播间监控卡片、中央播放器与 AI OCR 价格/说话人识别 Bounding Box、右侧 18 个高光切片队列、底部 Waveform 音频波形与时间轴 Scrubber、系统状态栏。
- **[02_Settings_Full_Fidelity.svg](file:///d:/Project/直播切片多人/figma-export/02_Settings_Full_Fidelity.svg)** (53.4 KB)
  - **内容**：系统设置全量 DOM 画布。包含通用设置（主题、自启、托盘）、预览画质、录制切片配置、NVENC 硬件加速 Toggle 切换开关、存储路径及日志诊断。
- **[03_FourZone_Workbench_Full_Fidelity.svg](file:///d:/Project/直播切片多人/figma-export/03_FourZone_Workbench_Full_Fidelity.svg)** (38.3 KB)
  - **内容**：4 画面多路监控与多轨时间轴全量 DOM 画布。

### 2. Figma JSON 节点数据文件（适用于 Figma 插件导入）
- **[01_Workbench_Full_Fidelity.figma.json](file:///d:/Project/直播切片多人/figma-export/01_Workbench_Full_Fidelity.figma.json)** (456.8 KB)
  - **内容**：包含完整 Figma `FRAME` 与 `TEXT` 节点树结构的 standard JSON 文件。

---

## 🛠️ 导入 Figma 两种方法

### 方法 A：直接拖拽 SVG 文件（最快捷）
1. 在 Figma 中打开一个空白设计文件（New Design File）。
2. 将 `01_Workbench_Full_Fidelity.svg` 或 `02_Settings_Full_Fidelity.svg` **直接拖入** Figma 画布中。
3. 选中导入的画布节点，按快捷键 `Ctrl + Shift + G` (Mac: `Cmd + Shift + G`) 解除分组，即可编辑任意文字、修改颜色变量或将其转化为 Figma 组件（Component）。

### 方法 B：使用 Figma 插件 1 键导入 JSON
1. 在 Figma 中搜索并打开插件 **Builder.io - HTML to Figma** 或 **HTML to Design**。
2. 选择 **Import JSON / File** 模式，上传 `01_Workbench_Full_Fidelity.figma.json`。
3. 插件会自动构建包含完整 Auto Layout 和样式 Token 的 Figma 图层。
