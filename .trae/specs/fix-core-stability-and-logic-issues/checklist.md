# 核心稳定性与逻辑问题修复 - Verification Checklist

## WebSocket 通信层
- [ ] 断连时发送的命令类消息（start_recording/stop_recording/enable_preview/disable_preview/remove_room等）在重连后不会被发送
- [ ] 断连时发送的查询类消息（get_settings/get_disk_usage）在重连后正常发送
- [ ] WebSocket 连接到不可达端口时 10 秒内超时并进入重连流程
- [ ] 正常连接建立后超时计时器被正确清理
- [ ] 最大重连次数（20次）后正确触发 reconnect_failed 状态

## Python 后端线程安全
- [ ] _BatchRecordWorker 不直接修改 RoomSession 属性，所有状态修改通过 Qt Signal 调度到主线程
- [ ] _rooms 字典的读写操作受 threading.Lock 保护
- [ ] 4 房间同时批量录制时快速点击开始/停止/删除无崩溃
- [ ] URL 刷新 worker 存储在独立的 _refresh_workers 字典
- [ ] 删除房间时正在进行的 refresh worker 被 requestInterruption 取消
- [ ] refresh 回调在房间已删除时安全跳过不访问空对象

## MSE 预览启动
- [ ] MseStreamer.start() 立即返回，不阻塞调用线程 2 秒
- [ ] FFmpeg 启动成功后 init 段正常发送
- [ ] FFmpeg 启动失败时 on_error 回调被正确调用，错误信息包含 stderr
- [ ] 点击预览按钮后 UI 在 100ms 内响应（按钮变 loading）
- [ ] 预览流程适配异步启动，不回归黑屏问题

## Electron 主进程
- [ ] Python 解释器检测包含依赖验证（PySide6, websockets）
- [ ] 依赖缺失时错误消息包含具体缺失的模块名和安装指引
- [ ] 前端接收到 backend-error 事件时显示友好错误提示
- [ ] openPath/showItemInFolder 白名单包含用户配置的自定义输出目录
- [ ] 自定义输出目录外的路径（如 C:\Windows\System32）仍被安全拒绝
- [ ] 程序退出时 killBackend 只执行一次，无重复 taskkill
- [ ] 程序正常退出无延迟挂起

## 录制功能
- [ ] 批量录制前预检输出目录，统一决定使用原始目录或回退目录
- [ ] 默认目录不可写时所有房间统一使用 ~/.lsc/output 回退目录
- [ ] 默认目录可写时不触发回退
- [ ] 单个房间录制仍保留内部回退逻辑作为最后防线

## 前端资源清理
- [ ] rooms_updated 事件检测到房间被删除时，清理对应 _mseInitCache 条目
- [ ] 房间删除时清理对应 _mseSegmentCache 条目
- [ ] 房间删除时清理对应 __mseInitRetryCount 条目
- [ ] 频繁添加/删除 20 个房间后内存无明显增长

## 广播队列
- [ ] QtManagerBridge 广播队列大小限制为 1000 条
- [ ] 队列满时丢弃最旧消息并记录警告日志
- [ ] WebSocket 客户端断开期间后端持续广播不会导致内存溢出

## 回归验证
- [ ] 现有单元测试全部通过
- [ ] 单房间录制/预览正常工作
- [ ] 多房间批量录制正常工作
- [ ] WebSocket 重连后房间状态正确同步
- [ ] 全屏预览功能正常
- [ ] 导出功能正常
- [ ] 设置页面修改输出目录后生效
- [ ] 程序在 IDE 外部启动正常工作
