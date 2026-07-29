import { create } from 'zustand'
import { RoomSession, ClipSegment, RecordSettings, AppSettings, DependencyStatus, SystemStats, TimelineContext, ContinuousAnalysisStatus } from '@/types'

export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected' | 'reconnect_failed'

export interface PreviewDegradationInfo {
  width: number
  height: number
  fps?: number
  reason?: string
}

export interface PreviewDegradationBanner {
  width: number
  height: number
  fps?: number
  reason?: string
}

// 前端 UI 状态（后端不感知，rooms_updated 不会覆盖）
interface RoomUIState {
  preview_phase?: string
  mse_error?: string
  mse_reconnecting?: { attempt: number; maxAttempts: number }
  preview_frame_data?: string
}

interface AppState {
  rooms: RoomSession[]
  selectedRoomId: string | null
  clips: ClipSegment[]
  settings: RecordSettings
  appSettings: AppSettings
  connectionStatus: ConnectionStatus
  systemStats: SystemStats | null
  dependencyStatus: DependencyStatus | null
  timelineContext: TimelineContext | null
  timelineInvalidated: boolean
  continuousAnalysisStatus: ContinuousAnalysisStatus | null
  settingsDrawerOpen: boolean
  previewDegradationBanner: PreviewDegradationBanner | null
  uiState: Record<string, RoomUIState>  // 前端 UI 状态，按 room_id 索引
}

interface AppActions {
  setRooms: (rooms: RoomSession[]) => void
  addRoom: (room: RoomSession) => void
  removeRoom: (roomId: string) => void
  updateRoom: (roomId: string, updates: Partial<RoomSession>) => void
  setSelectedRoomId: (roomId: string | null) => void
  setClips: (clips: ClipSegment[]) => void
  addClip: (clip: ClipSegment) => void
  setSettings: (settings: Partial<RecordSettings>) => void
  setAppSettings: (s: Partial<AppSettings>) => void
  setConnectionStatus: (status: ConnectionStatus) => void
  setSystemStats: (stats: SystemStats | null) => void
  setDependencyStatus: (status: DependencyStatus | null) => void
  setTimelineContext: (ctx: TimelineContext | null) => void
  setTimelineInvalidated: (invalidated: boolean) => void
  setContinuousAnalysisStatus: (status: ContinuousAnalysisStatus | null) => void
  setSettingsDrawerOpen: (open: boolean) => void
  setPreviewDegradationBanner: (info: PreviewDegradationInfo | null) => void
  dismissPreviewDegradationBanner: () => void
}

const defaultSettings: RecordSettings = {
  output_dir: '~/LSC/output',
  encoder: 'h264_nvenc',
  crf: 23,
  param_mode: 'CRF 质量',
  bitrate: '8000',
  bitrate_unit: 'kbps',
  quality: '原画',
  resolution: '原画',
  framerate: '原画',
  audio_codec: 'AAC 128k',
  audio_bitrate: '128k',
  preview_quality: '高清',
  preset: 'medium',
  ocr_accel: 'dml',
  export_max_concurrent: 2,
  jianying_draft_dir: '',
}

const defaultAppSettings: AppSettings = {
  theme: 'dark',
  language: 'zh-CN',
  autoLaunch: false,
  minimizeToTray: false,
  default_export_preset: 'douyin_vertical',
}

/** rooms_updated 浅比较：字段全同则跳过 set，避免无意义整树替换。 */
function roomsShallowEqual(a: RoomSession[], b: RoomSession[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    const left = a[i] as unknown as Record<string, unknown>
    const right = b[i] as unknown as Record<string, unknown>
    const keys = new Set([...Object.keys(left), ...Object.keys(right)])
    for (const key of keys) {
      if (left[key] !== right[key]) return false
    }
  }
  return true
}

export const useAppStore = create<AppState & AppActions>((set) => ({
  rooms: [],
  selectedRoomId: null,
  clips: [],
  settings: defaultSettings,
  appSettings: defaultAppSettings,
  connectionStatus: 'disconnected',
  systemStats: null,
  dependencyStatus: null,
  timelineContext: null,
  uiState: {},
  timelineInvalidated: false,
  continuousAnalysisStatus: null,
  settingsDrawerOpen: false,
  previewDegradationBanner: null,

  setRooms: (rooms) => set((state) => {
    if (state.rooms === rooms) return state
    // 前端 UI 状态存储在 uiState 中，rooms 整表替换不会影响
    if (roomsShallowEqual(state.rooms, rooms)) return state
    return { rooms }
  }),

  addRoom: (room) =>
    set((state) => ({
      // 按 room_id 去重，已存在则更新
      rooms: state.rooms.some((r) => r.room_id === room.room_id)
        ? state.rooms.map((r) => (r.room_id === room.room_id ? room : r))
        : [...state.rooms, room],
    })),

  removeRoom: (roomId) =>
    set((state) => ({
      rooms: state.rooms.filter((r) => r.room_id !== roomId),
      selectedRoomId:
        state.selectedRoomId === roomId ? null : state.selectedRoomId,
    })),

  updateRoom: (roomId, updates) =>
    set((state) => {
      // 前端 UI 字段写入 uiState，不污染后端数据模型
      const uiFields = ['preview_phase', 'mse_error', 'mse_reconnecting', 'preview_frame_data']
      const roomUpdates: Partial<RoomSession> = {}
      const uiUpdates: Partial<RoomUIState> = {}
      for (const [key, value] of Object.entries(updates)) {
        if (uiFields.includes(key)) {
          uiUpdates[key as keyof RoomUIState] = value as any
        } else {
          roomUpdates[key as keyof RoomSession] = value as any
        }
      }
      return {
        rooms: Object.keys(roomUpdates).length > 0
          ? state.rooms.map((r) => (r.room_id === roomId ? { ...r, ...roomUpdates } : r))
          : state.rooms,
        uiState: {
          ...state.uiState,
          [roomId]: { ...state.uiState[roomId], ...uiUpdates },
        },
      }
    }),

  setSelectedRoomId: (roomId) => set({ selectedRoomId: roomId }),

  setClips: (clips, meta?: { source: string; reason?: string }) => {
    // DEV 模式：记录调用来源，便于诊断切片计数异常
    if (import.meta.env.DEV) {
      console.groupCollapsed(`[store] setClips(${clips.length}) ${meta?.source ?? 'unknown'}`)
      console.trace('setClips called')
      console.log('reason:', meta?.reason)
      console.log('first 3:', clips.slice(0, 3).map(c => ({ id: c.clip_id, label: c.label })))
      console.groupEnd()
    }
    set({ clips })
  },

  addClip: (clip) =>
    set((state) => {
      // clip_id 去重：已存在则跳过
      if (clip.clip_id && state.clips.some(c => c.clip_id === clip.clip_id)) return state
      // DEV 模式：记录新增
      if (import.meta.env.DEV) {
        console.log(`[store] addClip(${clip.clip_id})`, clip.label)
      }
      // 上限 200 条，超出移除最旧
      return { clips: [...state.clips, clip].slice(-200) }
    }),

  setSettings: (settings) =>
    set((state) => ({
      settings: { ...state.settings, ...settings },
    })),

  setAppSettings: (s) =>
    set((state) => ({
      appSettings: { ...state.appSettings, ...s },
    })),

  setConnectionStatus: (connectionStatus) => set((state) => state.connectionStatus === connectionStatus ? state : { connectionStatus }),

  setSystemStats: (systemStats) =>
    set((state) => {
      const prev = state.systemStats
      if (
        prev?.cpu_percent === systemStats?.cpu_percent &&
        prev?.memory_percent === systemStats?.memory_percent &&
        prev?.memory_total_gb === systemStats?.memory_total_gb &&
        prev?.memory_used_gb === systemStats?.memory_used_gb &&
        prev?.disk_percent === systemStats?.disk_percent &&
        prev?.disk_total_gb === systemStats?.disk_total_gb &&
        prev?.disk_free_gb === systemStats?.disk_free_gb
      ) {
        return state
      }
      return { systemStats }
    }),

  setDependencyStatus: (dependencyStatus) => set({ dependencyStatus }),

  setTimelineContext: (timelineContext) => set({ timelineContext }),
  setTimelineInvalidated: (timelineInvalidated) => set({ timelineInvalidated }),
  setContinuousAnalysisStatus: (continuousAnalysisStatus) => set({ continuousAnalysisStatus }),
  setSettingsDrawerOpen: (open) => set({ settingsDrawerOpen: open }),
  setPreviewDegradationBanner: (previewDegradationBanner) => set({ previewDegradationBanner }),
  dismissPreviewDegradationBanner: () => set({ previewDegradationBanner: null }),
}))
