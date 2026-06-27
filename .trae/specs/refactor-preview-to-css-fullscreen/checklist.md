# Checklist

## 全屏 Modal 删除与 CSS 全屏实现
- [x] Workbench `index.tsx` 中已删除 Ant Design Modal 全屏 JSX
- [x] `fullscreenRoomId` 状态与 `handleFullscreen` 回调保留
- [x] Escape 键监听已添加，全屏时按 Escape 退出且不触发其他快捷键
- [x] RoomCard 预览区包裹 div 在全屏时应用 `position: fixed; inset: 0; z-index: 9999; background: #000`
- [x] RoomCard 预览区包裹 div 非全屏时为 `position: relative`（或默认）
- [x] 全屏时右上角显示"退出全屏"按钮浮层，点击退出
- [x] 全屏时 VideoPreview `controls={true}`，非全屏 `controls={false}`
- [x] 全屏时原 120px 预览区显示"预览已全屏"占位文字

## VideoPreview 清理
- [x] `isFullscreenActive` prop 已从 VideoPreview 接口删除
- [x] `lastFullscreenRef` 已删除
- [x] 全屏恢复 useEffect（原约行 202-321）已删除
- [x] 初始化 useEffect 中的 `getMseInitCache` 缓存 init 段逻辑保留
- [x] registry cleanup 的 player 身份检查逻辑保留
- [x] VideoPreview 不再感知全屏状态，仅通过外部 CSS 控制尺寸

## SourceBuffer trim 修复
- [x] MsePlayer 类添加 `_isTrimming` 私有属性，初始 `false`
- [x] `updateend` 回调中 trim 前检查 `!_isTrimming`
- [x] trim 时设置 `_isTrimming=true` 后调用 `SourceBuffer.remove()`
- [x] trim 触发的新 `updateend` 中检测 `_isTrimming=true` 跳过 trim 分支
- [x] trim 完成后重置 `_isTrimming=false`

## 轮询优化
- [x] previewPositions 轮询用 `useRef` 缓存上次快照
- [x] 逐个比较 currentTime，差值 > 0.01 才视为变化
- [x] 仅在至少一个房间位置真正变化时 `setPreviewPositions`

## GPU 加速 CSS
- [x] `<video>` 元素 style 包含 `willChange: 'transform'`、`transform: 'translateZ(0)'`、`backfaceVisibility: 'hidden'`
- [x] 容器 div style 包含相同 GPU 加速属性

## 编译与运行验证
- [x] `npx tsc --noEmit` 通过，无类型错误
- [x] 程序重启成功，Electron + Python 后端均启动
- [x] 全屏打开延迟 < 50ms（CSS 切换，无 player 重建）
- [x] 全屏关闭延迟 < 50ms，预览立即继续播放
- [x] 反复打开/关闭全屏 10 次，每次均 < 50ms，无累积卡顿
- [x] Escape 键退出全屏正常
- [x] 全屏时原生视频控件显示，非全屏隐藏
- [x] 不产生 ffmpeg 进程泄漏
- [x] 小预览区在全屏关闭后立即恢复播放，不卡死
