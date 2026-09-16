## LSC v1.0.17

### 新增（放大预览：缩小为窗口播放 = 原生画中画）

- **放大态控制条新增「窗口播放」按钮**（顺序：静音 → 缩小回网格 → 窗口播放 → 全屏 → 停止预览），
  与其它覆盖层玻璃按钮同一设计语言，差异化为画中画图标 + 文字标签「窗口播放」+ 品牌青描边淡青底。
- **走播放器原生能力**（`requestPictureInPicture`）：画中画是 document 级单例，状态机在
  `src/hooks/usePictureInPicture.ts`——按 `document.pictureInPictureElement === 本房 video` 判定，
  监听 document 的 `enter/leavepictureinpicture`（绑在 video 上会在播放器重建后失效）；
  回看时跟随回看播放器；卡片卸载时若本房仍在小窗里则 `exitPictureInPicture()` 收口；
  失败给出提示而不是静默。
- **一次点击 = 缩小 + 窗口播放**：同一路画面不在卡片与小窗里各播一份；退出小窗只退小窗。

### 变更（放大预览控制条：一体化 + 自动隐藏）

- **时间线 + 走带按键合成一块玻璃面板**（原先两段各自留白与底色）。
- **默认向下隐藏，鼠标经过/移动滑出，静止 2.5s 自动收起**：状态机统一走新增的
  `src/hooks/useAutoHideControls.ts`；拖动时间线、画质下拉打开期间 `pinned` 钉住。
  隐藏态用 `translateY(100%)` + `opacity:0` + `pointer-events:none` 三重收口；补 `prefers-reduced-motion`。

### 变更（回放交互口径：能点的范围 = 真能回放的范围）

- **放大预览条可点/可拖范围 = 真实可回放范围**：`computeExpandedPreviewWindow` 左端改为
  `max(真实缓冲起点, liveEdge − 设置时长)` —— 用户设置**只作上限**，绝不把未缓冲的历史画成可点区域；
  指针拖动与方向键落点统一过 `clampSeekToRange` 收进缓冲内侧。
- **`mseSeek` 边界容差**：新增 `isWithinSeekRange`（`DVR_BUFFER_EDGE_TOLERANCE_SEC = 2s`），
  容差内落点按缓冲内处理；分片边界/浮点误差不再把一次普通点击推给本地文件回看通道。
- **删除放大预览区两个时长文案**：「回看设置 X · 实际可回放 Y」及内存降级后缀一并移除。

### 安装与发布包说明

- **Windows 安装包**：`LSC.Setup.1.0.17.exe`（或 `LSC 直播切片系统 Setup 1.0.17.exe`），
  带一键安装与自愈运行时。
- **Microsoft Store (AppX/MSIX)**：`LiveStreamClipper-1.0.17.appx`，自包含全量 Python 与 FFmpeg 运行时，
  合规 Microsoft Store 政策 10.2.5 与高分屏磁贴规范。

### 质量门禁

- 前端 vitest：26 文件 / 255 用例全绿；`tsc --noEmit` 无错误。
- 后端 pytest（本次改动相关 12 个测试文件）：533 用例全绿。
- 新增守卫：`tests/test_frontend_stability_guards.py` 三条（可点范围=可回放范围 / 原生画中画 /
  控制条一体化自动隐藏）+ 三个前端夹具（timelineWindow / usePictureInPicture / useAutoHideControls）。
