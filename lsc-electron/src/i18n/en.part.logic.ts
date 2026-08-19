/**
 * en-US 词典分片：逻辑层提示（hooks / services / utils 中的 toast 与错误文案）。
 * key 必须与代码中 t('...') 的参数逐字一致。
 */
export const enPartLogic: Record<string, string> = {
  // useWebSocket.ts
  '未连接后端，操作未发送': 'Backend not connected, action not sent',
  'MSE 流初始化超时，请重试预览': 'MSE stream initialization timed out, please retry the preview',
  '预览持续中断，请手动重新开启预览': 'The preview keeps dropping. Please manually restart the preview',
  '预览恢复中': 'Preview is recovering',

  // useRoomActions.ts
  '选择录制规格': 'Select Recording Settings',
  '开始录制': 'Start Recording',
  '取消': 'Cancel',
  '录制已停止。请稍候，持续分析正在收尾并将回合入列待确认，请勿立刻停止分析': 'Recording stopped. Please wait — continuous analysis is wrapping up and enqueuing rounds for review. Do not stop analysis yet',
  '最多 4 路同时预览，请先关闭一路': 'Up to 4 previews can run at once, please close one first',
  '多路预览已自动降画质以保证流畅': 'Preview quality was lowered automatically to keep things smooth',
  '该房间已退出持续分析映射，后续回合可能仅入列主房': 'This room exited the continuous-analysis mapping; subsequent rounds may only be queued to the main room',
  '确认断开': 'Confirm Disconnect',
  '断开将停止录制「{name}」': 'Disconnecting will stop the recording for "{name}"',
  '未知主播': 'unknown streamer',
  '确认': 'Confirm',

  // useClipDelete.tsx
  '删除切片「{label}」': 'Delete clip "{label}"',
  '已删除切片「{label}」': 'Clip "{label}" deleted',
  '已恢复切片': 'Clip restored',
  '撤销': 'Undo',

  // useAddRoom.ts
  '请输入直播间链接': 'Please enter live room link(s)',
  '一次最多添加 {count} 个直播间': 'At most {count} live rooms can be added at once',
  '直播间链接过长': 'Live room link is too long',
  '每行只能填写一个完整链接，链接中不能包含空格': 'Each line must contain one full link, and links cannot contain spaces',
  '链接格式无效：{url}': 'Invalid link format: {url}',
  '仅支持 http:// 或 https:// 直播间链接': 'Only http:// or https:// live room links are supported',
  '链接缺少有效域名或包含不安全的登录信息：{url}': 'Link is missing a valid domain or contains unsafe login info: {url}',
  '输入中存在重复链接：{url}': 'Duplicate link in the input: {url}',
  '输入内容': 'the input',
  '无法识别': 'unrecognizable',
  '直播间链接未通过验证': 'Live room link failed validation',
  '验证结果缺少有效链接，请重新输入后再试': 'No valid links were returned from validation, please re-enter and try again',
  '链接验证通过': 'Link validated',
  '{count} 个直播间链接均已验证通过，正在添加': 'All {count} live room links were validated, adding now',
  '后端未连接，已取消添加': 'Backend not connected, adding was cancelled',
  '添加房间失败': 'Failed to add room',
  '链接验证已通过，但部分房间添加失败，请根据提示重试': 'Links were validated, but some rooms failed to be added. Please retry based on the hints',
  '链接有效，直播间已添加': 'Link is valid, live room added',
  '正在验证 {count} 个直播间链接…': 'Validating {count} live room links…',
  '正在连接平台验证直播间链接…': 'Connecting to the platform to validate the live room link…',
  '后端未连接，无法验证直播间链接': 'Backend not connected, cannot validate the live room link',

  // useExportProgressListeners.ts
  '切片导出完成': 'Clip export complete',
  '导出失败：{err}': 'Export failed: {err}',
  '导出失败：未知错误。请点击切片列表中的「打开输出文件夹」排查或重试。': 'Export failed: unknown error. Please click "Open Output Folder" in the clip list to investigate or retry.',
  '导出失败': 'Export failed',
  '未知错误': 'unknown error',
  '取消导出失败：{err}': 'Failed to cancel export: {err}',
  '任务可能已结束': 'The task may have already finished',

  // useNotifications.ts
  '房间': 'room',
  '直播间': 'live room',
  '切片已就绪': 'clip is ready',
  '切片导出失败': 'Clip export failed',
  '录制已开始': 'Recording started',
  '录制启动失败': 'Failed to start recording',
  '房间连接失败': 'Room connection failed',
  '连接失败': 'connection failed',
  '后端连接断开': 'Backend connection lost',
  'WebSocket 重连失败，请检查后端状态': 'WebSocket reconnect failed, please check the backend status',
  '磁盘空间不足': 'Not enough disk space',
  '录制已停止': 'Recording stopped',
  '后端启动失败': 'Backend failed to start',

  // clipNaming.ts
  '未知': 'Unknown',
  '切片': 'clip',
}
