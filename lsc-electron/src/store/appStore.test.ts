import { beforeEach, describe, expect, it } from 'vitest'
import { useAppStore } from './appStore'
import type { RoomSession } from '@/types'

function makeRoom(overrides: Partial<RoomSession> = {}): RoomSession {
  return {
    room_id: 'room-1',
    room_url: 'https://example.test/room-1',
    platform: 'test',
    platform_name: 'Test',
    streamer_name: 'Streamer',
    stream_title: 'Title',
    is_connecting: false,
    is_connected: true,
    is_recording: false,
    record_output_path: '',
    record_started_at: null,
    record_size_mb: 0,
    last_error: '',
    preview_enabled: true,
    preview_paused: false,
    preview_muted: true,
    stream_url: 'https://cdn.example.test/live.flv',
    mark_in: null,
    mark_out: null,
    ...overrides,
  }
}

describe('appStore preview event state', () => {
  beforeEach(() => {
    useAppStore.setState({
      rooms: [makeRoom()],
      selectedRoomId: 'room-1',
      uiState: {},
    })
  })

  it('镜像 preview_phase 并在 rooms_updated 整表替换后保留', () => {
    useAppStore.getState().updateRoom('room-1', { preview_phase: 'probing' })

    expect(useAppStore.getState().rooms[0].preview_phase).toBe('probing')
    expect(useAppStore.getState().uiState['room-1']?.preview_phase).toBe('probing')

    useAppStore.getState().setRooms([makeRoom({ is_recording: true })])

    expect(useAppStore.getState().rooms[0]).toMatchObject({
      is_recording: true,
      preview_phase: 'probing',
    })
  })

  it('移除房间时清理对应的预览事件状态', () => {
    useAppStore.getState().updateRoom('room-1', { preview_phase: 'streaming' })
    useAppStore.getState().removeRoom('room-1')

    expect(useAppStore.getState().rooms).toHaveLength(0)
    expect(useAppStore.getState().uiState['room-1']).toBeUndefined()
  })
})
