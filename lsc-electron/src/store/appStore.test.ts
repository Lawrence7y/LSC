import { describe, it, expect, beforeEach } from 'vitest'
import { useAppStore } from './appStore'

describe('appStore', () => {
  beforeEach(() => {
    // 重置 store 到初始状态
    useAppStore.setState({
      rooms: [],
      selectedRoomId: null,
      clips: [],
      connectionStatus: 'disconnected',
      systemStats: null,
      dependencyStatus: null,
      timelineContext: null,
      timelineInvalidated: false,
      continuousAnalysisStatus: null,
      settingsDrawerOpen: false,
      previewDegradationBanner: null,
    })
  })

  it('should initialize with default state', () => {
    const state = useAppStore.getState()
    expect(state.rooms).toEqual([])
    expect(state.clips).toEqual([])
    expect(state.selectedRoomId).toBeNull()
    expect(state.connectionStatus).toBe('disconnected')
  })

  it('should set rooms', () => {
    const rooms = [
      { room_id: 'r1', room_url: 'https://example.com/1' },
      { room_id: 'r2', room_url: 'https://example.com/2' },
    ]
    useAppStore.getState().setRooms(rooms as any)
    expect(useAppStore.getState().rooms).toHaveLength(2)
  })

  it('should set clips', () => {
    const clips = [
      { clip_id: 'c1', title: 'Clip 1' },
      { clip_id: 'c2', title: 'Clip 2' },
    ]
    useAppStore.getState().setClips(clips as any)
    expect(useAppStore.getState().clips).toHaveLength(2)
  })

  it('should set connection status', () => {
    useAppStore.getState().setConnectionStatus('connected')
    expect(useAppStore.getState().connectionStatus).toBe('connected')
    useAppStore.getState().setConnectionStatus('disconnected')
    expect(useAppStore.getState().connectionStatus).toBe('disconnected')
  })

  it('should set selected room id', () => {
    useAppStore.getState().setSelectedRoomId('room-1')
    expect(useAppStore.getState().selectedRoomId).toBe('room-1')
    useAppStore.getState().setSelectedRoomId(null)
    expect(useAppStore.getState().selectedRoomId).toBeNull()
  })
})
