import { create } from 'zustand'
import { RoomSession, ClipSegment, RecordSettings, AppSettings, DependencyStatus, SystemStats, TimelineContext, ContinuousAnalysisStatus } from '@/types'

export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected' | 'reconnect_failed'

export interface DiskUsage {
  total: number
  used: number
  free: number
}

export interface PreviewDegradationInfo {
  width: number
  height: number
  reason?: string
}

export interface PreviewDegradationBanner {
  width: number
  height: number
  reason?: string
}

interface AppState {
  rooms: RoomSession[]
  selectedRoomId: string | null
  clips: ClipSegment[]
  recentClips: ClipSegment[]
  settings: RecordSettings
  appSettings: AppSettings
  connectionStatus: ConnectionStatus
  diskUsage: DiskUsage | null
  systemStats: SystemStats | null
  exportProgress: { job_id: string; percent: number } | null
  dependencyStatus: DependencyStatus | null
  timelineContext: TimelineContext | null
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
  setRecentClips: (clips: ClipSegment[]) => void
  addRecentClip: (clip: ClipSegment) => void
  setSettings: (settings: Partial<RecordSettings>) => void
  setAppSettings: (s: Partial<AppSettings>) => void
  setConnectionStatus: (status: ConnectionStatus) => void
  setDiskUsage: (diskUsage: DiskUsage | null) => void
  setSystemStats: (stats: SystemStats | null) => void
  setExportProgress: (progress: { job_id: string; percent: number } | null) => void
  setDependencyStatus: (status: DependencyStatus | null) => void
  setTimelineContext: (ctx: TimelineContext | null) => void
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
  analysis_settings: {
    absolute_threshold: 0.15,
  },
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
  recentClips: [],
  settings: defaultSettings,
  appSettings: defaultAppSettings,
  connectionStatus: 'disconnected',
  diskUsage: null,
  systemStats: null,
  exportProgress: null,
  dependencyStatus: null,
  timelineContext: null,
  continuousAnalysisStatus: null,
  settingsDrawerOpen: false,
  previewDegradationBanner: null,

  setRooms: (rooms) => set((state) => {
    if (state.rooms === rooms) return state
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

  setRecentClips: (recentClips) => set({ recentClips }),

  addRecentClip: (clip) =>
    set((state) => ({
      recentClips: [clip, ...state.recentClips].slice(0, 20),
    })),

  setSettings: (settings) =>
    set((state) => ({
      settings: { ...state.settings, ...settings },
    })),

  setAppSettings: (s) =>
    set((state) => ({
      appSettings: { ...state.appSettings, ...s },
    })),

  setConnectionStatus: (connectionStatus) => set((state) => state.connectionStatus === connectionStatus ? state : { connectionStatus }),

  setDiskUsage: (diskUsage) =>
    set((state) => {
      const prev = state.diskUsage
      if (prev?.total === diskUsage?.total && prev?.used === diskUsage?.used && prev?.free === diskUsage?.free) {
        return state
      }
      return { diskUsage }
    }),

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

  setExportProgress: (exportProgress) => set({ exportProgress }),

  setDependencyStatus: (dependencyStatus) => set({ dependencyStatus }),

  setTimelineContext: (timelineContext) => set({ timelineContext }),
  setContinuousAnalysisStatus: (continuousAnalysisStatus) => set({ continuousAnalysisStatus }),
  setSettingsDrawerOpen: (open) => set({ settingsDrawerOpen: open }),
  setPreviewDegradationBanner: (previewDegradationBanner) => set({ previewDegradationBanner }),
  dismissPreviewDegradationBanner: () => set({ previewDegradationBanner: null }),
}))
