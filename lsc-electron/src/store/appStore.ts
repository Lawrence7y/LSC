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
}

const defaultAppSettings: AppSettings = {
  theme: 'dark',
  language: 'zh-CN',
  autoLaunch: false,
  minimizeToTray: false,
  default_export_preset: 'douyin_vertical',
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
  timelineInvalidated: false,
  continuousAnalysisStatus: null,
  settingsDrawerOpen: false,
  previewDegradationBanner: null,

  setRooms: (rooms) => set((state) => {
    if (state.rooms === rooms) return state
    // rooms_updated 来自后端 _room_to_dict，不含 preview_phase 等前端字段；
    // 整表替换会冲掉 updateRoom 写入的 phase，导致 LIVE 胶囊 / DVR 紫标条件失效。
    const merged = rooms.map((incoming) => {
      const prev = state.rooms.find((r) => r.room_id === incoming.room_id)
      if (!prev) return incoming
      return {
        ...incoming,
        preview_phase: incoming.preview_phase ?? prev.preview_phase,
        mse_error: incoming.mse_error ?? prev.mse_error,
        mse_reconnecting: incoming.mse_reconnecting ?? prev.mse_reconnecting,
        preview_frame_data: incoming.preview_frame_data ?? prev.preview_frame_data,
      }
    })
    return { rooms: merged }
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
    set((state) => ({
      rooms: state.rooms.map((r) =>
        r.room_id === roomId ? { ...r, ...updates } : r
      ),
    })),

  setSelectedRoomId: (roomId) => set({ selectedRoomId: roomId }),

  setClips: (clips) => set({ clips }),

  addClip: (clip) =>
    set((state) => {
      // clip_id 去重：已存在则跳过
      if (clip.clip_id && state.clips.some(c => c.clip_id === clip.clip_id)) return state
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
