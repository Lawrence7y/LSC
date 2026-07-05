// 房间相关
export interface RoomSession {
  room_id: string
  room_url: string
  platform: string
  platform_name: string
  streamer_name: string
  stream_title: string
  is_connecting: boolean
  is_connected: boolean
  is_recording: boolean
  is_reconnecting?: boolean
  record_output_path: string
  record_started_at: string | null
  record_size_mb: number
  last_error: string
  preview_enabled: boolean
  preview_paused: boolean
  preview_muted: boolean
  stream_url: string
  mark_in: number | null
  mark_out: number | null
  mark_in_wallclock?: number | null
  mark_out_wallclock?: number | null
  recording_start_mono?: number | null
  preview_latency?: number
  /** 音频互相关偏移量（秒），表示该房间内容相对于最慢参考房间的时间差。
   *
   * 正值含义：该房间的内容比最慢房间快（直播进度领先，需 seek 回退才能与基准同步）；
   * 负值则相反。
   *
   * 导出多房间切片时，该偏移量会被用于补偿各房间录制文件的起始时间，确保
   * 多轨音画同步。
   */
  content_offset?: number
  // Electron 模式预览帧（base64 JPEG 字符串，由后端 FFmpeg 抓帧推送）
  preview_frame_data?: string
  // MSE 预览错误信息（FFmpeg 异常、编解码失败等）
  mse_error?: string
  // MSE 预览自动重连状态（后端流断开后自动重试时设置）
  mse_reconnecting?: { attempt: number; maxAttempts: number }
  // 直播分区分类
  category?: string
}

// 切片相关
export interface ClipSegment {
  start: number
  end: number
  label: string
  thumbnail_path?: string
  room_id?: string
  room_name?: string
  exported?: boolean
  outputPath?: string
  job_id?: string
}

// 流信息
export interface StreamInfo {
  platform: string
  stream_url: string
  streamer: string
  title: string
  is_live: boolean
  selected_quality: string
}

// 录制设置
export interface RecordSettings {
  output_dir: string
  encoder: string
  crf: number
  param_mode: string
  bitrate: string
  bitrate_unit: string
  quality: string
  resolution: string
  framerate: string
  audio_codec: string
  audio_bitrate: string
  preview_quality: string
  preset?: string
}

// 导出预设
export interface ExportPreset {
  id: string
  name: string
  description: string
  resolution: string
  framerate: string
  codec: string
  crf: number
  vertical_crop: boolean
  audio_bitrate: string
}

// WebSocket 消息
export interface WSMessage {
  type: string
  data: any
  id?: string
}

// API 响应
export interface ApiResponse<T = any> {
  success: boolean
  data?: T
  error?: string
}

// Electron API
export interface ElectronAPI {
  getAppVersion: () => Promise<string>
  getPlatform: () => string
  getBackendWsUrl: () => Promise<string | null>
  minimizeWindow: () => Promise<void>
  maximizeWindow: () => Promise<void>
  closeWindow: () => Promise<void>
  selectDirectory: () => Promise<string | null>
  openPath: (path: string) => Promise<{ success: boolean; error?: string }>
  // 在资源管理器中高亮定位文件（区别于 openPath 会用默认程序打开文件）
  showItemInFolder?: (path: string) => Promise<{ success: boolean; error?: string }>
  // 应用自动更新接口
  checkForUpdate: () => Promise<{ success: boolean; error?: string }>
  downloadUpdate: () => Promise<{ success: boolean; error?: string }>
  installUpdate: () => void
  onUpdateStatus: (callback: (status: any) => void) => void
  removeUpdateStatusListeners: () => void
  showNotification?: (payload: { title: string; body: string; silent?: boolean }) => Promise<void>
  setProgressBar?: (progress: number) => Promise<void>
  setTrayState?: (state: 'idle' | 'recording' | 'error') => Promise<void>
  getBackendError?: () => Promise<string | null>
  onBackendError?: (callback: (error: string) => void) => void
  readLogFile?: (opts: { file: string; lines?: number }) => Promise<{ success: boolean; content: string; path?: string; error?: string; size?: number }>
  openLogFolder?: () => Promise<{ success: boolean; error?: string }>
}

// 依赖检测状态
export interface DependencyItem {
  available: boolean
  path: string
  version: string
}

export interface DependencyStatus {
  ffmpeg: DependencyItem
  ffprobe: DependencyItem
  nvenc: { available: boolean }
  python: { version: string; path: string }
}

export interface SystemStats {
  cpu_percent: number
  memory_percent: number
  memory_total_gb: number
  memory_used_gb: number
  disk_percent: number
  disk_total_gb: number
  disk_free_gb: number
}

// 通用应用设置（主题/语言/开机自启/最小化到托盘）
export interface AppSettings {
  theme: 'dark' | 'light'
  language: 'zh-CN' | 'zh-TW' | 'en'
  autoLaunch: boolean
  minimizeToTray: boolean
}

// 主进程暴露的应用 API（与 electron/preload.ts 保持一致）
export interface AppAPI {
  setAutoLaunch(enabled: boolean): Promise<void>
  getAutoLaunch(): Promise<boolean>
  setMinimizeToTray(enabled: boolean): Promise<void>
  getMinimizeToTray(): Promise<boolean>
  onAppSettingsChange(callback: (settings: { autoLaunch: boolean; minimizeToTray: boolean }) => void): void
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
    app?: AppAPI
  }
}
