import { create } from 'zustand'
import { RoomSession, ClipSegment, RecordSettings, AppSettings } from '@/types'

export type ConnectionStatus = 'connected' | 'connecting' | 'disconnected'

export interface DiskUsage {
  total: number
  used: number
  free: number
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
}

const defaultAppSettings: AppSettings = {
  theme: 'dark',
  language: 'zh-CN',
  autoLaunch: false,
  minimizeToTray: false,
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

  setRooms: (rooms) => set({ rooms }),

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
    set((state) => ({
      // 上限 200 条，超出移除最旧
      clips: [...state.clips, clip].slice(-200),
    })),

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

  setConnectionStatus: (connectionStatus) => set({ connectionStatus }),

  setDiskUsage: (diskUsage) => set({ diskUsage }),
}))
