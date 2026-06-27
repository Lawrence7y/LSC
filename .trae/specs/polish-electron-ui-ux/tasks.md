# Tasks

- [x] Task 1: 统一 Electron 前端状态管理
  - [x] SubTask 1.1: 新增 Zustand store，集中管理 rooms、clips、settings、connection 状态
  - [x] SubTask 1.2: Workbench、Dashboard、Settings 均从 store 读取数据，移除本地重复 state
  - [x] SubTask 1.3: WebSocket 消息统一写入 store，页面切换时不再丢失

- [x] Task 2: 房间配置持久化
  - [x] SubTask 2.1: 后端新增 `save_rooms` / `load_rooms` 消息处理，房间变更时自动写 JSON 文件
  - [x] SubTask 2.2: 前端在添加/删除房间时发送保存消息
  - [x] SubTask 2.3: 程序启动时后端主动推送已保存的房间列表

- [x] Task 3: 工作台房间卡片 UI 打磨
  - [x] SubTask 3.1: RoomCard 左上角增加状态 badge（录制中/已连接/未连接/失败）
  - [x] SubTask 3.2: RoomCard 底部增加 2px 录制 mini-seekbar
  - [x] SubTask 3.3: 连接失败时预览区中央显示错误原因
  - [x] SubTask 3.4: 卡片选中态改为 1px 主题色边框 + 外发光

- [x] Task 4: 录制设置面板体验优化
  - [x] SubTask 4.1: 编码器、画质预设改为横向 chip 选择器
  - [x] SubTask 4.2: CRF、编码器、码率模式增加悬浮提示
  - [x] SubTask 4.3: 保存设置后显示 `message.success` 反馈

- [x] Task 5: 仪表盘数据一致性与功能扩展
  - [x] SubTask 5.1: Dashboard 从 store 读取房间统计
  - [x] SubTask 5.2: 新增"最近录制历史"卡片，点击跳转工作台对应房间
  - [x] SubTask 5.3: 新增磁盘存储使用条与剩余空间提示

- [x] Task 6: 切片预览与最近切片栏
  - [x] SubTask 6.1: 导出切片前弹出预览弹窗，显示起止时间、时长、房间名
  - [x] SubTask 6.2: 工作台底部新增"最近切片"横向滚动区
  - [x] SubTask 6.3: 后端导出完成后推送切片信息，前端写入 store

- [x] Task 7: 连接状态与批量操作反馈
  - [x] SubTask 7.1: 顶部状态栏/房间卡片增加连接状态彩色指示点
  - [x] SubTask 7.2: 批量录制/停止按钮不可用时提示原因

- [x] Task 8: 视觉主题统一
  - [x] SubTask 8.1: 统一 Ant Design 组件圆角、阴影与 token.css 变量
  - [x] SubTask 8.2: 检查所有页面是否使用 CSS 变量而非硬编码色值

- [x] Task 9: 验证
  - [x] SubTask 9.1: 运行 `npx tsc --noEmit` 与 `npm run build`
  - [x] SubTask 9.2: 启动前后端进行用户视角的功能验证
  - [x] SubTask 9.3: 运行 Python 后端测试确保无回归

# Task Dependencies

- Task 3 依赖 Task 1（状态统一）
- Task 5 依赖 Task 1、Task 2（数据统一 + 持久化）
- Task 6 依赖 Task 1、Task 3（切片状态与卡片状态统一）
- Task 7 依赖 Task 1（状态统一）
- Task 9 依赖 Task 1-8
