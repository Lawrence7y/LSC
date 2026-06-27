# MSE 预览无画面问题排查与修复报告

## 排查结论

经过对预览全链路（前端 VideoPreview → WebSocket → Python Backend → MseStreamer → FFmpeg → fMP4 输出 → MediaSource API）的全面审查，**系统当前处于运行状态**（Python 后端 PID 4192 在线、WebSocket 9876 端口已连接、FFmpeg 可用），但发现 **5 个导致"点击预览无画面"的 Bug**，其中核心问题是：

> **前端缺少对后端错误响应的处理** — 当后端因任何原因返回预览启动失败时，前端静默忽略，用户看不到任何反馈。

## 修复清单

| # | Bug | 文件 | 描述 |
|---|-----|------|------|
| 1 | **前端缺失 `enable_preview_response` 错误处理** | `useWebSocket.ts` | 后端返回错误时用户无反馈，新增 handler 将错误写入房间状态 |
| 2 | **VideoPreview 无超时机制** | `VideoPreview.tsx` | 15 秒未收到帧则自动报错并通知后端关闭 |
| 3 | **request_mse_init 无重试上限** | `useWebSocket.ts` | 5 次重试上限 + 递增延迟 (1s→5s) |
| 4 | **FFmpeg 缺少连接超时** | `mse_streamer.py` | 新增 `-timeout 10s`、`-rw_timeout 15s`、`-reconnect` |
| 5 | **mse_error 未同步清除状态** | `useWebSocket.ts` | 收到 MSE 错误时同步 `preview_enabled = false` |

## 修改文件

- `lsc-electron/src/hooks/useWebSocket.ts` — 新增 3 个 handler、重试逻辑、状态同步
- `lsc-electron/src/components/VideoPreview.tsx` — 新增超时检测与数据追踪
- `lsc/core/services/mse_streamer.py` — FFmpeg 命令优化、参数位置修复
- `python-backend/handlers/room_handler.py` — 错误响应增加 `room_id`

TypeScript 编译通过 ✅
