# Checklist

## Python 后端自动启动
- [x] Electron 主进程在 `app.whenReady()` 后 spawn `python-backend/main.py`
- [x] 后端 stdout/stderr 写入 `%APPDATA%/LSC/logs/backend.log`
- [x] 前端 WebSocket 客户端能在 5 秒内成功连接 `ws://localhost:8765`
- [x] 退出 Electron 时 Python 后端被优雅终止（SIGTERM/taskkill）
- [x] 等待 3 秒未退出则强制终止
- [x] 不留孤儿 Python 进程
- [x] 开发环境与打包环境路径均能正确找到 `main.py`

## 房间预览由后端驱动
- [x] RoomCard 不再渲染任何 `<video>` 元素
- [x] 未启用预览的房间显示 play-overlay + "启用预览"按钮
- [x] 点击"启用预览"发送 `enable_preview` 消息（`{ room_id, enabled: true }`）
- [x] 前端 `preview_enabled` 状态随 `rooms_updated` 广播更新
- [x] 预览已启用时显示明确占位文案，不再黑屏
- [x] 再次点击预览按钮可禁用预览（`enabled: false`）

## 通用设置实际生效
- [x] `AppSettings` 类型包含 theme/language/autoLaunch/minimizeToTray
- [x] `tokens.css` 增补 light 主题 CSS 变量
- [x] MainLayout 侧边栏底部有主题切换按钮
- [x] 主题切换实时生效（dark/light），`document.documentElement` 的 `dark` class 正确切换
- [x] 开机自启开关打开后调用 `app.setLoginItemSettings({ openAtLogin: true })`
- [x] 最小化到托盘开关打开后，关闭窗口不退出程序，最小化到托盘
- [x] 托盘图标右键菜单提供"显示"/"退出"
- [x] 通用设置保存后持久化到后端 `settings.json`，重启后恢复
- [x] preload.ts 暴露 `window.app.setAutoLaunch` 等 IPC API

## 验证
- [x] `npx tsc --noEmit` 通过
- [x] `npm run build` 通过
- [ ] 用户视角全流程验证：启动 → 后端自动起 → 连接房间 → 启用预览 → 切换主题 → 最小化到托盘 → 退出无孤儿进程
