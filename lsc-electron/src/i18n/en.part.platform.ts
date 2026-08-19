/**
 * en-US 词典分片：平台/后端错误码与常见后端错误文案映射
 * （前端对后端返回的字符串做 t() 兜底翻译时使用）。
 * key 必须与代码中 t('...') 的参数逐字一致。
 */
export const enPartPlatform: Record<string, string> = {
  // ── lsc/utils/error_messages.py: humanize_error 输出 ──
  '文件写入权限不足。请检查输出目录权限': 'Insufficient file write permission. Please check the output directory permissions',
  '磁盘空间不足，无法继续录制。请清理输出目录': 'Not enough disk space to continue recording. Please clean up the output directory',
  '平台拒绝了连接（403）。可能主播未开播，或需要登录 Cookie。': 'The platform rejected the connection (403). The streamer may be offline, or a login Cookie is required.',
  '直播流地址失效（404）。可能是 CDN 链接过期，系统将尝试自动刷新。': 'The live stream URL is invalid (404). The CDN link may have expired; the app will try to refresh it automatically.',
  '无法连接到直播服务器。请检查网络或稍后重试。': 'Cannot connect to the live server. Check your network or try again later.',
  '连接直播服务器超时。网络不稳定或服务器无响应。': 'Connection to the live server timed out. The network may be unstable or the server unresponsive.',
  '域名解析失败。请检查网络连接。': 'DNS resolution failed. Please check your network connection.',
  '未找到直播流。主播可能已下播或流地址已过期。': 'Stream not found. The streamer may have ended the stream or the URL expired.',
  '直播平台服务器异常，请稍后重试。': 'The platform server is having issues. Please try again later.',
  '无法解析直播流数据。流格式可能不受支持。': 'Cannot parse live stream data. The stream format may be unsupported.',
  '缺少视频解码器。请确保 FFmpeg 安装完整。': 'Video decoder missing. Please make sure FFmpeg is fully installed.',
  '缺少视频编码器。请检查编码器设置。': 'Video encoder missing. Please check the encoder settings.',
  '录制引擎出错。请检查编码器设置或重启应用。': 'Recording engine error. Check the encoder settings or restart the app.',
  '需要登录凭证。请检查 Cookie 配置。': 'Login credentials required. Please check the Cookie configuration.',
  '该直播间当前未开播。': 'This live room is currently offline.',
  '无法解析直播流地址。平台可能已更新协议。': 'Cannot resolve the live stream URL. The platform may have updated its protocol.',
  '录制未能启动。直播流已接通，但录制文件还没有开始写入，请重试。': 'Recording failed to start. The stream is connected but no file is being written yet. Please retry.',
  '共享进样预览中断。录制可能仍在继续，请尝试重新开启预览。': 'Shared-ingest preview interrupted. Recording may still be running; try re-enabling preview.',
  '刷新直播流地址失败。主播可能已下播或网络异常。': 'Failed to refresh the stream URL. The streamer may be offline or the network is abnormal.',
  'OCR 引擎不可用，已回退到纯音频检测。': 'OCR engine unavailable; fell back to audio-only detection.',
  '不支持的视频格式。请尝试切换编码器。': 'Unsupported video format. Try switching the encoder.',
  '文件或路径不存在。请检查配置。': 'File or path does not exist. Please check the configuration.',
  '发生未知错误': 'An unknown error occurred',

  // ── lsc/utils/error_messages.py: get_repair_suggestion 建议 ──
  '请检查直播链接是否过期，尝试重新连接房间': 'Check whether the live link has expired, or try reconnecting the room',
  '请确认主播正在直播，或尝试刷新直播间': 'Confirm the streamer is live, or try refreshing the room',
  '连接超时，请检查网络状况或稍后重试': 'Connection timed out. Check your network or try again later.',
  '磁盘空间不足，请清理输出目录后重试': 'Not enough disk space. Clean up the output directory and retry.',
  '录制文件不完整，可尝试使用「修复录制文件」功能': 'The recording file is incomplete. Try the "Repair recording file" feature.',
  '主播当前未开播，请稍后再试': 'The streamer is not live right now. Please try again later.',

  // ── 平台适配器错误文案（lsc/platforms/*.py）──
  '无法识别 B 站直播间号。': 'Cannot recognize the Bilibili room ID.',
  '页面加载失败: {exc}': 'Page load failed: {exc}',
  '页面内容为空或过短': 'Page content is empty or too short',
  '无法识别斗鱼房间号': 'Cannot recognize the Douyu room ID',
  '斗鱼页面加载异常': 'Douyu page load error',
  '斗鱼直播间未开播': 'This Douyu room is not live',
  '获取用户直播信息失败: {exc}': 'Failed to fetch user live info: {exc}',
  '抖音直播间解析失败: {exc}': 'Douyin room parse failed: {exc}',
  '虎牙直播间解析失败: {exc}': 'Huya room parse failed: {exc}',
  '虎牙直播间未开播': 'This Huya room is not live',
  '虎牙未找到公开流': 'No public stream found on Huya',
  '快手页面解析失败: {exc}': 'Kuaishou page parse failed: {exc}',
  '快手播放列表格式异常': 'Kuaishou playlist format error',
  '微博直播页获取失败: {exc}': 'Weibo live page fetch failed: {exc}',
  '微博直播间未开播或无法获取流地址': 'This Weibo room is not live or its stream URL is unavailable',
  '微博未找到公开流': 'No public stream found on Weibo',
  '小红书短链解析失败': 'Xiaohongshu short link parse failed',
  '无法识别小红书链接': 'Cannot recognize the Xiaohongshu link',
  '小红书页面加载失败: {exc}': 'Xiaohongshu page load failed: {exc}',
  '小红书直播间未开播': 'This Xiaohongshu room is not live',
  '小红书直播流获取失败，可能需要登录': 'Failed to fetch the Xiaohongshu stream; login may be required',
  '小红书用户页面加载失败: {exc}': 'Xiaohongshu user page load failed: {exc}',
  '该用户未在直播': 'This user is not live now',
}
