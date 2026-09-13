// 预览来源模式
export type PreviewMode = 'live_mse' | 'recording_review' | 'degraded'

export type RoomHealthStatus =
  | 'IDLE' | 'CONNECTING' | 'READY' | 'LIVE' | 'RESTRICTED' | 'UNKNOWN' | 'ERROR' | 'OFFLINE' | 'PENDING'
  | 'RUNNING' | 'DEGRADED' | 'REFRESHING' | 'RECONNECTING' | 'BACKING_OFF' | 'STOPPED' | 'FAILED' | 'AUTH_REQUIRED'
  | 'STARTING' | 'RECORDING' | 'PLAYING' | 'PAUSED'

export interface RoomPipelineHealth {
  schema_version: number
  platform_id?: string
  pipeline_mode?: 'V2' | 'LEGACY'
  platform: RoomHealthStatus
  resolver: RoomHealthStatus
  ingest: RoomHealthStatus
  recording: RoomHealthStatus
  preview: RoomHealthStatus
  error?: string
  failure_kind?: string
  support_level?: 'EXPERIMENTAL' | 'PREVIEW' | 'STABLE' | 'DEGRADED' | 'DISABLED'
  connection_policy?: string
  credential_status?: string
  credential_kinds?: string[]
  lease_id?: string
  candidate_id?: string
  quality_id?: string
  protocol?: string
  cdn_id?: string
  lease_expires_at?: number | null
  lease_refresh_at?: number | null
  manifest_path?: string
  generation?: number
  upstream_generation?: number
  recovery_attempt?: number
  max_recovery_attempts?: number
  resources?: {
    upstream_pid?: number | null
    recording_pid?: number | null
    preview_pid?: number | null
    preview_subscribers?: number
    upstream_bytes?: number
    recording_size_bytes?: number
    preview_segment_count?: number
    preview_media_bytes?: number
  }
  updated_at?: number
}

export interface RuntimeEventPayload {
  schema_version: number
  event_id?: string
  event_type: string
  room_id: string
  room_session_id?: string
  recording_session_id?: string
  platform_id?: string
  component?: string
  state: string
  state_from?: string
  state_to?: string
  severity?: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | string
  occurred_at: number
  session_id?: string
  lease_id?: string
  candidate_id?: string
  stage?: string
  failure_kind?: string
  attempt?: number
  max_attempts?: number
  next_retry_at?: number | null
  user_action?: string
  reason_code?: string
  recovery_id?: string
  generation?: number
  lease_generation?: number
  retry_after_seconds?: number | null
  context?: Record<string, unknown>
  safe_context?: Record<string, unknown>
}

// 房间相关
export interface RoomSession {
  room_id: string
  room_url: string
  platform: string
  platform_name: string
  canonical_room_id?: string
  streamer_name: string
  streamer?: string
  stream_title: string
  title?: string
  is_connecting: boolean
  is_connected: boolean
  is_recording: boolean
  /** 录制启动中（刷新流地址 / 启动 FFmpeg），用于按钮即时 loading */
  is_recording_starting?: boolean
  /** 等待录制并发槽位（Semaphore 排队中） */
  is_recording_queued?: boolean
  /** 录制排队序号（1 起），0 表示未排队 */
  recording_queue_position?: number
  is_reconnecting?: boolean
  record_output_path: string
  /**
   * 录制镜像文件（fMP4，empty_moov）：录制中回看的数据源。
   * 与录制文件同生命周期、随归档重命名同步改名；录制结束前后均可读。
   */
  dvr_output_path?: string
  record_manifest_path?: string
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
  recording_media_start_mono?: number | null
  /** 直播 MSE 预览轴相对录制轴的运行时映射：preview = recording + delta。 */
  recording_to_preview_delta?: number | null
  /** 采样对应的预览 epoch；预览重建或录制换代后必须重新采样。 */
  preview_clock_epoch_id?: string
  recording_id?: string
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
  // 预览启动阶段进度（refreshing_url=刷新流地址/probing=探测转码/streaming=拉流中/error/idle）
  preview_phase?: 'idle' | 'refreshing_url' | 'probing' | 'streaming' | 'error'
  /** 预览来源：live_mse=直播 MSE，recording_review=录制文件回看，degraded=降级；缺省视为 live_mse */
  preview_mode?: PreviewMode
  /**
   * 回看轴偏移（秒，可为负）：recordingAxis = 回看播放器 currentTime + 本值。
   * 方案 A 起由**前端本地状态**维护（本地录制文件原始 PTS 基座通常很大，
   * 故该值常为负），后端不再下发 review 会话/窗口字段。
   */
  preview_review_start_sec?: number
  /** 预览源世代 ID；切换 live/recording_review 或重建 MSE 时递增，供前端强制重建播放器 */
  preview_epoch_id?: string
  // 直播是否在线（false 表示断联）
  is_live?: boolean
  // 当前预览画质
  preview_quality?: string
  // 直播分区分类
  category?: string
  align_group_id?: string
  /** 后端只读健康投影：平台/解析/进样/录制/预览五个维度。 */
  pipeline_health?: RoomPipelineHealth
}

// 切片确认状态（与导出状态正交：确认管可信度，export 管导出队列）
export type ClipConfirmStatus =
  | 'pending'
  | 'refining'
  | 'user_confirmed'
  | 'ocr_confirmed'
  | 'vision_confirmed'
  | 'audio_pending'
  // 赛事审计拒绝终态：后端 `_prune_rejected_listed_clips` 会广播并从前端列表移除
  | 'rejected'

// 切片相关
export interface ClipSegment {
  start: number
  end: number
  label: string
  thumbnail_path?: string
  room_id?: string | null
  room_name?: string
  exported?: boolean
  export_status?: 'queued' | 'exporting' | 'completed' | 'failed' | 'pending'
  export_error?: string
  outputPath?: string
  job_id?: string
  clip_id?: string
  is_ai_highlight?: boolean
  /** 切片来源：'manual' = 手动添加, 'ai_highlight' = AI 高光 */
  source?: 'manual' | 'ai_highlight'
  /** AI 检出的确认状态（pending/refining/user_confirmed/ocr_confirmed/vision_confirmed） */
  confirm_status?: ClipConfirmStatus
  /** 混合视觉边界短证据标签（如「买枪退出 + 比分变化」） */
  boundary_evidence?: string[]
  /** 边界来源（如 valorant_hybrid_v1） */
  boundary_source?: string
  /** Valorant 输入来源策略；broadcast 切片已经通过回放/暂停审计 */
  source_profile?: 'pov' | 'broadcast' | string
  broadcast_audit?: 'passed' | string
  broadcast_audit_reason?: string
  broadcast_excluded_reason?: string
  /** 赛事审计要求人工复核（证据不完整/未通过时 True，自动草稿须拦截） */
  broadcast_review_required?: boolean
  /** 时长异常标记：拦截自动导出/自动草稿 */
  duration_anomaly?: boolean
  /** OCR 出点类型（next_prep / broadcast_exclusion / next_combat / open_tail …） */
  end_by?: string
  /** OCR 入点类型（ocr_combat / refined_combat …） */
  start_by?: string
  boundary_quality?: 'precise' | 'coarse' | 'pending' | 'invalid' | string
  boundary_quality_reason_code?: string
  boundary_review_required?: boolean
  start_quality?: 'precise' | 'coarse' | 'invalid' | string
  end_quality?: 'precise' | 'coarse' | 'invalid' | string
  start_review_required?: boolean
  end_review_required?: boolean
  broadcast_result_tail_sec?: number
  /** 稳定回合键（与持续分析 _valorant_round_key 一致），用于多房同步 */
  round_key?: string
  /** 入队时快照的墙钟入点（time.monotonic），导出时优先于房间当前 mark */
  mark_in_wallclock?: number | null
  mark_out_wallclock?: number | null
  recording_start_mono?: number | null
  recording_media_start_mono?: number | null
  /** 后端最终用于 FFmpeg 的录制文件时间轴范围（秒）。 */
  recording_start_sec?: number | null
  recording_end_sec?: number | null
  /** exact = 入队时有完整墙钟或 ClipSnapshot；approximate = 仅有 start/end（如拖拽标记） */
  mark_precision?: 'exact' | 'approximate'
  /** 入队时快照的 content_offset，导出时优先于房间当前值 */
  content_offset?: number
  /** 公共时间轴坐标（TimelineContext 模式） */
  common_start?: number
  common_end?: number
  timeline_id?: string
  /** 入列时所在 TimelineContext 的录制 ID（重对齐后用于校验旧切片可否重算公共轴） */
  recording_id?: string
  clip_snapshot_id?: string
  highlight_reason?: string
  highlight_score?: number
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
  /** 时间线直播回放缓存：0=关闭，单位秒 */
  timeline_replay_seconds: number
  preset?: string
  /** 共享进样：单 FFmpeg 同时输出录制与预览 */
  shared_ingest_enabled?: boolean
  /** 持续分析 OCR 推理加速：auto / dml / cuda / cpu */
  ocr_accel?: 'auto' | 'dml' | 'cuda' | 'cpu'
  /** 最大并发导出数（1 或 2，默认 2） */
  export_max_concurrent?: number
  /** 剪映草稿目录；空字符串表示自动探测 */
  jianying_draft_dir?: string
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

// WebSocket 消息 payload 类型映射（服务端 → 前端广播 + 响应）
export interface WSPayloadMap {
  // 生命周期
  connected: undefined
  disconnected: undefined
  reconnecting: undefined
  reconnect_failed: undefined
  // 心跳健康（客户端内部事件，非服务端消息）
  backend_crashed: undefined
  backend_revived: undefined
  // 广播
  rooms_updated: { rooms: RoomSession[] }
  rooms_loaded: { rooms: RoomSession[] }
  room_updated: { room_id: string; [key: string]: unknown }
  mse_init: { room_id: string; data: ArrayBuffer | string }
  mse_segment: { room_id: string; data: ArrayBuffer | string }
  mse_error: { room_id: string; error: string; reason?: 'offline' | 'network' | 'disk_full' | 'unknown' }
  mse_reconnecting: { room_id: string; attempt: number; max_attempts: number }
  mse_reconnected: { room_id: string; degraded?: boolean; width?: number; height?: number; fps?: number; reason?: string }
  clip_completed: { job_id: string; output_path: string; room_name?: string; thumbnail_path?: string; clip_id?: string }
  clip_failed: { error: string; room_name?: string; job_id?: string; clip_id?: string }
  export_progress: { job_id: string; percent: number; room_name?: string }
  room_connect_finished: { room_id: string; success: boolean; error: string }
  recording_started: { room_id: string; success: boolean; error: string }
  recording_stopped: { room_id: string; reason: string; message: string }
  recording_queue: { room_id?: string; position?: number; waiting?: boolean }
  system_stats: { cpu_percent: number; memory: { total: number; used: number; percent: number }; disks: unknown[] }
  preview_phase: { room_id: string; phase: string }
  runtime_event: RuntimeEventPayload
  clip_confirm_status: { room_id: string; round_key: string; confirm_status: string; start?: number; end?: number; label?: string }
  timeline_ready: { timeline: unknown }
  timeline_invalidated: { timeline_id: string; reason: string }
  continuous_analysis_status: ContinuousAnalysisStatus
  clip_queued: { clip_id: string; room_id: string; round_key?: string; start: number; end: number; duration: number; score: number; label?: string; deferred?: boolean }
  analysis_progress: { room_id: string; stage: string; progress: number; detail?: string }
  highlight_stream: { room_id: string; highlights: unknown[] }
  continuous_highlights: { room_id: string; highlights: unknown[] }
  settings_loaded: Record<string, unknown>
  get_settings_response: Record<string, unknown>
  enable_preview_response: { success?: boolean; note?: string }
  request_mse_init_response: { success?: boolean; note?: string; room_id?: string }
  set_preview_clock_response: {
    success?: boolean
    room_id?: string
    accepted?: boolean
    recording_to_preview_delta?: number
    preview_clock_epoch_id?: string
    recording_id?: string
    sample_epoch_ms?: number
    reason?: string
  }
  check_dependencies_response: { python: unknown; ffmpeg: unknown; ffprobe: unknown }
  clip_export_started: { job_id: string; room_name?: string }
  continuous_analysis_complete: {
    room_id: string
    total_highlights?: number
    highlights?: unknown[]
    listed_clip_count?: number
    listed_clips?: ClipSegment[]
    confirmed_rounds?: number
    pending_rounds?: number
    coverage_ranges?: Array<[number, number]>
    coverage_uncovered_ranges?: Array<[number, number]>
    coverage_complete?: boolean
    finalization_job_id?: string | null
    finalization_state?: 'idle' | 'checkpoint_saved' | 'finalizing' | 'completed' | 'error' | string
    audit_delivery_gap?: number
    pending_queue_depth?: number
    error?: string
    /** 收尾超时/扫描失败放弃但 pending 候选已落盘，可恢复；终端模型错误不设此标记 */
    finalization_recoverable?: boolean
  }
}

export type WSMessageType = keyof WSPayloadMap

// API 响应
export interface ApiResponse<T = any> {
  success: boolean
  data?: T
  error?: string
}

/** 自动更新状态载荷（主进程 update-status 事件） */
export interface UpdateStatusPayload {
  status?: string
  message?: string
  percent?: number
  version?: string
  [key: string]: unknown
}

// Electron API
export interface ElectronAPI {
  getAppVersion: () => Promise<string>
  getPlatform: () => string
  isStoreBuild?: () => boolean
  getBackendWsUrl: () => Promise<string | null>
  getBackendWsToken?: () => Promise<string | null>
  onBackendReady?: (callback: (payload: { url: string }) => void) => () => void
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
  /** 注册后端错误监听，返回单条注销函数（只注销本次注册，不影响其他模块） */
  onBackendError?: (callback: (error: string) => void) => (() => void) | void
  removeBackendErrorListeners?: () => void
  readLogFile?: (opts: { file: string; lines?: number }) => Promise<{ success: boolean; content: string; path?: string; error?: string; size?: number }>
  openLogFolder?: () => Promise<{ success: boolean; error?: string }>

  // 退出清理：主进程通知渲染进程清理所有房间
  onCleanupAllRooms?: (callback: () => void) => () => void
  notifyCleanupAllRoomsComplete?: (result: {
    success: boolean
    finalization_state?: 'idle' | 'checkpoint_saved' | 'finalizing' | 'completed' | 'error'
    finalization_job_id?: string | null
    stopped_recording_room_ids?: string[]
    errors?: string[]
  }) => void
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
  default_export_preset: string
  workbenchViewMode?: 'compact' | 'standard'
  autoAlignOnLive?: boolean
  defaultMuteAll?: boolean
}

export interface RoomTimeSnapshot {
  preview_epoch_id: string
  recording_id: string
  preview_to_common_delta: number
  recording_to_common_delta: number
  align_confidence: number
  media_start_mono?: number
}

export interface TimelineContext {
  timeline_id: string
  reference_room_id: string
  preview_ready: boolean
  clip_ready: boolean
  created_at: number
  room_snapshots: Record<string, RoomTimeSnapshot>
}

export interface TimelineHighlightBand {
  id: string
  start: number
  end: number
  score?: number
  reason?: string
  label?: string
  /** AI 回合确认状态：audio_pending 橙色虚线（待 OCR 复核），pending 蓝色虚线，confirmed 实色品牌色 */
  confirm_status?: string
  /** 边界精度：audio_approximate=音频粗定位，ocr_exact=OCR 精确定位 */
  boundary_precision?: 'audio_approximate' | 'ocr_exact'
  /** 该回合是在哪个扫描范围检测到的（可选） */
  scan_range?: [number, number]
  /** 来源房间（多房间时有用） */
  room_id?: string
}

export type ExportTarget = 'mp4' | 'draft' | 'both'

export interface JianyingDraftOptions {
  include_recordings?: boolean
  include_clips?: boolean
  text_labels?: boolean
  vertical?: boolean
  draft_name?: string
  non_main_volume_zero?: boolean
}

/** 草稿跳过的单条切片（后端 `_skip_reason_code` 的分类；文案可改、码不可改） */
export interface JianyingSkippedClip {
  round_key?: string
  label?: string
  start?: number | null
  end?: number | null
  confirm_status?: string | null
  broadcast_audit?: string | null
  end_by?: string | null
  end_quality?: string | null
  /** END_NOT_FINAL / NEVER_AUDITED / NO_EXCLUSION_EVIDENCE / NOT_IN_AUTHORITY / REJECTED … */
  reason_code?: string
  reason?: string
}

export interface JianyingDraftResult {
  success: boolean
  draft_name?: string
  draft_dir?: string
  tracks?: number
  segments?: number
  requested_clip_count?: number
  included_clip_count?: number
  skipped_clip_count?: number
  /** 逐条跳过明细：只有聚合告警时无法分辨是哪道门禁拦的（2026-09-11 现场） */
  skipped?: JianyingSkippedClip[]
  warnings?: string[]
  error?: string
  error_code?: string
}

/** 三轴进度展示 DTO（仅用于 UI 展示，不改变三轴换算规则） */
export interface TimelineProgressSummary {
  /** MSE 播放位置（预览轴，秒） */
  previewPosition: number
  /** 磁盘已录制时长（录制轴，秒） */
  recordedDuration: number
  /** 持续分析已扫描时长（分析轴，秒） */
  analysisScannedDuration: number
  /** 录制领先预览的秒数，max(0, recordedDuration - previewPosition) */
  previewDelay: number
  /** 当前控制栏所处轴 */
  axis: 'preview' | 'common' | 'recording_review' | 'recording'
}

export interface ContinuousAnalysisStatus {
  running: boolean
  room_id?: string | null
  target_room_ids?: string[]
  mode?: string
  analyzed_duration?: number
  recorded_duration?: number
  confirmed_rounds?: number
  pending_rounds?: number
  analysis_stage?: string
  total_highlights?: number
  phase?: 'idle' | 'running' | 'stopping' | 'finalizing' | 'checkpoint_saved' | 'completed' | 'error' | 'stalled'
  status?: string
  updated_at?: number
  scan_mode?: 'full' | 'incremental'
  scan_phase?: 'full' | 'incremental'
  scan_reason?: string
  scan_range?: [number, number]
  /** 本轮实际扫描窗口的入/出点（秒） */
  scan_in_sec?: number | null
  scan_out_sec?: number | null
  /** 最近一次扫出的回合切片入/出点（秒） */
  last_detected_in_sec?: number | null
  last_detected_out_sec?: number | null
  scan_timeout?: number
  full_rescan?: boolean
  refine_with_ocr?: boolean
  effective_interval?: number
  progress?: number
  error?: string
  // 无畏契约来源策略：pov 保持普通直播路径，broadcast 启用赛事安全审计。
  valorant_profile?: 'auto' | 'pov' | 'broadcast' | 'valorant' | string
  round_phase?: 'unknown' | 'buy' | 'pre_combat' | 'combat' | 'post_combat' | 'intermission'
  round_phase_detail?: string
  pending_round?: boolean
  /** 等待回合结束的详细解释（P2: 前端等待回合解释） */
  pending_round_info?: {
    phase?: string           // 当前等待的相位
    waiting_for?: string     // 等待什么（如 "buy_end", "combat_end"）
    since_sec?: number       // 已等待秒数
  } | null
  predicted_wake_at?: number | null
  predicted_phase?: string | null
  prediction_detail?: string
  scan_elapsed_sec?: number
  worker_job_label?: string
  scan_running?: boolean
  /** 分析滞后于录制的秒数（recorded - analyzed），后端 payload 提供 */
  analysis_lag_sec?: number
  analysis_backlog_sec?: number
  realtime_fast_mode?: boolean
  scan_cycle_sec?: number
  last_scan_wall_sec?: number
  scan_throughput_history?: number[]
  /** 实际抽帧采样频率，不是模型推理 FPS */
  sample_fps?: number
  /** 视觉模型实际处理吞吐与延迟 */
  model_infer_fps?: number
  model_infer_ms_p50?: number
  model_infer_ms_p90?: number
  pending_review_count?: number
  analysis_quality_status?: 'normal' | 'degraded' | 'review' | 'error' | string
  gross_scanned_sec?: number
  newly_covered_sec?: number
  gross_scan_throughput?: number
  net_coverage_throughput?: number
  net_coverage_throughput_history?: number[]
  lag_delta_sec?: number
  lag_slope?: number
  audit_queue_depth?: number
  audit_accepted_count?: number
  audit_rejected_count?: number
  audit_manual_review_count?: number
  audit_terminal_total?: number
  audit_delivered_total?: number
  audit_delivery_gap?: number
  pending_queue_depth?: number
  refine_result_queue_depth?: number
  finalization_pending_jobs?: number
  last_audit_rejection?: {
    start: number
    end: number
    reason: string
  } | null
  listed_clip_count?: number
  listed_clips?: ClipSegment[]
  coverage_ranges?: Array<[number, number]>
  coverage_uncovered_ranges?: Array<[number, number]>
  coverage_complete?: boolean
  finalization_job_id?: string | null
  finalization_state?: 'idle' | 'checkpoint_saved' | 'finalizing' | 'completed' | 'error' | string
  finalization_full_rescan?: boolean
  finalization_full_rescan_started?: boolean
  /** 收尾失败但可恢复（pending 候选已落盘）；前端据此渲染“恢复收尾”按钮 */
  finalization_recoverable?: boolean
  model_version?: string | null
  provider?: string | null
  provider_warning?: string | null
  last_model_inference_frames?: number
  model_inference_frames_total?: number
  last_scan_error?: string | null
  degraded_mode?: 'audio_only' | string | null
  consecutive_scan_timeouts?: number
  /** P2 卡死保护：是否处于暂停状态（连续重锚无 buy 信号） */
  stalled?: boolean
  /** 卡死原因：no_buy_signal / user_paused 等 */
  stall_reason?: string
  /** 音频待复核回合数（P3: 回合边界精度指示） */
  audio_pending_rounds?: number
  /** 多房间同步详细错误（P3: 多房间同步详细错误） */
  mapping_error?: string | null
}

// 主进程暴露的应用 API（与 electron/preload.ts 保持一致）
export interface AppAPI {
  setAutoLaunch(enabled: boolean): Promise<void>
  getAutoLaunch(): Promise<boolean>
  setMinimizeToTray(enabled: boolean): Promise<void>
  getMinimizeToTray(): Promise<boolean>
  onAppSettingsChange(callback: (settings: { autoLaunch: boolean; minimizeToTray: boolean }) => void): void
}

// MSE 播放器全局注册表条目（window.__msePlayers）
export interface MsePlayerRegistryEntry {
  feedInit: (data: ArrayBuffer) => void
  feedMedia: (data: ArrayBuffer) => void
  player: {
    stop: () => void
    pause: () => void
    goLive: () => void
    getBufferedRange: () => { start: number; end: number } | null
    getBufferedRanges?: () => Array<{ start: number; end: number }>
    setReplayBufferSeconds?: (seconds: number) => void
    state: string
    videoElement?: HTMLVideoElement
    resumePlayback?: (silent?: boolean) => void
    markSeeked?: () => void
    seek?: (time: number) => void
    sessionId?: string
  } | null
  live?: any
  review?: any
  channel?: string
  audioSource: MediaElementAudioSourceNode | null
  gainNode: GainNode | null
  /** 可选：诊断用，标识进样模式 */
  ingestMode?: string
  [key: string]: any
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
    app?: AppAPI
    __msePlayers?: Record<string, MsePlayerRegistryEntry>
    /** MSE init 段重试计数器 */
    __mseInitRetryCount?: Record<string, number>
  }
}
