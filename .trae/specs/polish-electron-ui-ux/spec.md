# Electron 前端 UI/UX 打磨 Spec

## Why
当前 Electron 前端已实现基础的多房间工作台、录制、切片和仪表盘功能，但从用户视角仍存在信息层级不清、状态反馈弱、数据不持久等体验问题。参考 `lsc-ui-design` 设计稿，对工作台、设置、仪表盘做一次统一的 UI/UX 打磨，提升操作效率与视觉一致性。

## What Changes
- 工作台房间卡片增加录制 mini-seekbar、状态 badge 与更明确的错误提示。
- 录制设置面板改为 chip 选择器，增加技术项提示与保存成功反馈。
- 仪表盘与工作台统一房间数据源，新增最近录制历史与存储使用概览。
- 切片系统增加导出前预览与全局最近切片栏。
- 房间配置持久化，页面切换后房间不丢失。
- 连接状态指示与批量操作不可用原因提示。
- 视觉统一：圆角、阴影、强调色向设计稿靠拢。

## Impact
- 受影响页面：`Workbench`、`Dashboard`、`Settings`。
- 受影响组件：`RoomCard`、`ControlBar`、`Timeline`、`RecordSettings`、`MainLayout`、`SettingsPage`。
- 受影响状态：房间列表、切片列表、录制设置建议改为 Zustand store + WebSocket 同步。
- 受影响后端：`python-backend/handlers/room_handler.py`、`python-backend/message_bridge.py` 可能需要新增房间持久化消息。

## ADDED Requirements

### Requirement: 房间卡片状态可视化
The system SHALL 在每个房间卡片上直观展示当前录制/连接状态与进度。

#### Scenario: 用户查看工作台
- **WHEN** 用户打开工作台页面
- **THEN** 每个房间卡片左上角显示状态 badge（录制中/已连接/未连接/失败）
- **AND** 卡片底部显示 2px 的录制 mini-seekbar，反映当前已录制时长占总时长的比例
- **AND** 连接失败时，预览区直接显示错误原因，而非仅底部小字

### Requirement: 录制设置体验优化
The system SHALL 提供更易理解的录制设置面板。

#### Scenario: 用户修改录制设置
- **WHEN** 用户打开设置页
- **THEN** 编码器、画质预设等选项以 chip 选择器展示
- **AND** CRF、编码器等术语旁提供悬浮提示，说明其对画质/文件大小/CPU 的影响
- **AND** 点击保存后，界面在 2 秒内给出明确的成功反馈

### Requirement: 仪表盘数据一致性
The system SHALL 让仪表盘与工作台使用同一套房间数据源。

#### Scenario: 用户切换页面
- **WHEN** 用户从工作台切换到仪表盘
- **THEN** 仪表盘展示的房间总数、录制中数量与工作台完全一致
- **AND** 显示最近录制历史列表，点击可跳转工作台对应房间
- **AND** 显示磁盘存储使用条，提示剩余空间

### Requirement: 切片预览与最近切片
The system SHALL 在导出切片前允许用户确认片段信息。

#### Scenario: 用户导出切片
- **WHEN** 用户标记入出点并点击导出
- **THEN** 弹出片段预览弹窗，显示起止时间、时长、房间名
- **AND** 用户可确认或取消导出
- **AND** 工作台底部新增"最近切片"横向滚动区，展示当天已导出的切片

### Requirement: 房间配置持久化
The system SHALL 在页面切换或程序重启后保留用户添加的房间。

#### Scenario: 用户添加房间后切换页面
- **WHEN** 用户在工作台添加一个房间
- **THEN** 切换到设置页再返回，房间列表仍保持不变
- **AND** 程序重启后，房间列表可自动恢复

## MODIFIED Requirements

### Requirement: 时间线交互
参考 `lsc-ui-design` 设计稿，时间线已由 canvas 改为播放器风格的 DOM/CSS 实现，保留拖拽跳转、Shift+点击设入点、Ctrl/Cmd+点击设出点的交互。

## REMOVED Requirements

无
