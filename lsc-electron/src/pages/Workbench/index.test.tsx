import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// ─── Mock 重依赖 hooks / 服务 ────────────────────────────────────────

vi.mock('@/hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    isConnected: true,
    connectionStatus: 'connected',
    send: vi.fn(),
    on: vi.fn(() => () => {}),
    reconnect: vi.fn(),
  }),
  wsClient: { on: vi.fn(() => () => {}), send: vi.fn() },
}))

vi.mock('@/hooks/useExportProgressListeners', () => ({
  useExportProgressListeners: vi.fn(),
}))

vi.mock('@/hooks/usePlayheadSampling', () => ({
  usePlayheadSampling: vi.fn(),
}))

vi.mock('@/hooks/useRoomActions', () => ({
  useRoomActions: () => ({
    handleToggleMute: vi.fn(),
    handleStartRecord: vi.fn(),
    handleStopRecord: vi.fn(),
    handleTogglePreview: vi.fn(),
    handleFullscreen: vi.fn(),
    handleCollapse: vi.fn(),
    handleRemove: vi.fn(),
    handleConnect: vi.fn(),
    handleDisconnect: vi.fn(),
  }),
}))

vi.mock('@/hooks/useKeyboardShortcuts', () => ({
  useKeyboardShortcuts: vi.fn(),
  PLAYBACK_RATE_STEPS: [0.5, 1, 1.5, 2],
}))

vi.mock('@/hooks/useTimelineViewModel', () => ({
  useTimelineViewModel: () => null,
}))

vi.mock('@/hooks/useAddRoom', () => ({
  useAddRoom: () => ({
    url: '',
    setUrl: vi.fn(),
    loading: false,
    roomUrlValidation: { status: 'idle', message: '' },
    setRoomUrlValidation: vi.fn(),
    handleAddRoom: vi.fn(),
  }),
}))

vi.mock('@/hooks/useClipDelete', () => ({
  useClipDelete: () => ({ handleDeleteClip: vi.fn() }),
}))

vi.mock('@/utils/wsRequest', () => ({
  sendRequest: vi.fn(() => Promise.resolve({ success: true })),
}))

vi.mock('@/utils/previewAudioAligner', () => ({
  getAligner: vi.fn(() => null),
}))

vi.mock('@/utils/playheadStore', () => ({
  writePlayhead: vi.fn(),
  writeDisplayPlayhead: vi.fn(),
  subscribeDisplayPlayhead: vi.fn(() => () => {}),
  removePlayhead: vi.fn(),
}))

vi.mock('@/utils/toastBatch', () => ({
  scheduleBatchedToast: vi.fn(),
}))

// ─── Mock 子组件（隔离渲染，仅验证 Workbench 骨架） ──────────────────

vi.mock('./components/RoomCard', () => ({
  RoomCard: ({ room }: { room: { room_id: string; streamer_name: string } }) => (
    <div data-testid={`room-card-${room.room_id}`}>{room.streamer_name}</div>
  ),
}))

vi.mock('./components/ControlBar', () => ({
  ControlBar: () => <div data-testid="control-bar" />,
}))

vi.mock('./components/ExportQueuePanel', () => ({
  ExportQueuePanel: () => <div data-testid="export-queue-panel" />,
}))

vi.mock('./components/Onboarding', () => ({
  Onboarding: () => null,
}))

vi.mock('./components/RefreshButton', () => ({
  RefreshButton: () => <button data-testid="refresh-btn" />,
}))

vi.mock('@/components/AnalysisProgress', () => ({
  AnalysisProgress: () => <div data-testid="analysis-progress" />,
}))

vi.mock('@/components/RecordingSpecSelector', () => ({
  RecordingSpecSelector: () => <div data-testid="recording-spec" />,
  recordingSpecFromSettings: vi.fn(() => ({
    encoder: 'h264_nvenc',
    crf: 23,
    param_mode: 'crf',
    bitrate: '8000',
    bitrate_unit: 'kbps',
    quality: 'medium',
    resolution: '1920:1080',
    framerate: '30',
    audio_codec: 'aac',
    audio_bitrate: '128k',
  })),
}))

// ─── 测试 ────────────────────────────────────────────────────────────

import Workbench, { reconcileContinuousListedClips } from './index'
import { useAppStore } from '@/store/appStore'
import type { RoomSession, ClipSegment } from '@/types'

describe('持续分析切片快照', () => {
  it('重连后补齐并按 room_id + round_key 更新，不产生重复条目', () => {
    const snapshot = [{
      clip_id: 'room-1_330_720',
      room_id: 'room-1',
      start: 33,
      end: 72,
      label: 'R01',
      round_key: 'round-000003',
      confirm_status: 'audio_pending' as const,
      export_deferred: true,
    }]

    const restored = reconcileContinuousListedClips([], snapshot)
    const updated = reconcileContinuousListedClips(restored, [{
      ...snapshot[0],
      clip_id: 'room-1_335_718',
      start: 33.5,
      end: 71.8,
    }])

    expect(restored).toHaveLength(1)
    expect(updated).toHaveLength(1)
    expect(updated[0]).toMatchObject({
      clip_id: 'room-1_335_718',
      start: 33.5,
      end: 71.8,
      confirm_status: 'audio_pending',
      export_status: 'pending',
    })
  })
})

function makeRoom(overrides: Partial<RoomSession> = {}): RoomSession {
  return {
    room_id: 'room-1',
    room_url: 'https://live.example.com/123',
    platform: 'huya',
    platform_name: '虎牙',
    streamer_name: '测试主播',
    stream_title: '测试直播',
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
    stream_url: 'https://cdn.example.com/live.flv',
    mark_in: null,
    mark_out: null,
    ...overrides,
  }
}

function makeClip(overrides: Partial<ClipSegment> = {}): ClipSegment {
  return {
    start: 100,
    end: 130,
    label: '回合 1',
    room_id: 'room-1',
    clip_id: 'clip-001',
    confirm_status: 'user_confirmed',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  useAppStore.setState({
    rooms: [],
    clips: [],
    selectedRoomId: null,
    connectionStatus: 'connected',
    timelineContext: null,
    timelineInvalidated: false,
    continuousAnalysisStatus: null,
    settingsDrawerOpen: false,
    previewDegradationBanner: null,
    uiState: {},
  })
  ;(window as any).electronAPI = undefined
  ;(window as any).__msePlayers = undefined
})

describe('Workbench 渲染', () => {
  it('渲染工作台标题与工具栏', () => {
    render(<Workbench />)
    expect(screen.getByText('多房间工作台')).toBeTruthy()
  })

  it('无房间时显示空态引导', () => {
    render(<Workbench />)
    expect(screen.getByText('暂无房间，请添加直播间地址')).toBeTruthy()
  })

  it('有房间时渲染 RoomCard', () => {
    useAppStore.setState({ rooms: [makeRoom()], selectedRoomId: 'room-1' })
    render(<Workbench />)
    expect(screen.getByTestId('room-card-room-1')).toBeTruthy()
    expect(screen.getByText('测试主播')).toBeTruthy()
  })

  it('多房间时全部渲染', () => {
    useAppStore.setState({
      rooms: [makeRoom(), makeRoom({ room_id: 'room-2', streamer_name: '主播B' })],
      selectedRoomId: 'room-1',
    })
    render(<Workbench />)
    expect(screen.getByTestId('room-card-room-1')).toBeTruthy()
    expect(screen.getByTestId('room-card-room-2')).toBeTruthy()
  })

  it('切片列表区域渲染切片数据', () => {
    useAppStore.setState({
      rooms: [makeRoom()],
      clips: [makeClip()],
      selectedRoomId: 'room-1',
    })
    render(<Workbench />)
    expect(screen.getByText('回合 1')).toBeTruthy()
    expect(screen.getByText('切片列表')).toBeTruthy()
  })

  it('WebSocket 断开时不渲染错误 Alert（2 秒防抖内）', () => {
    useAppStore.setState({ connectionStatus: 'disconnected' })
    render(<Workbench />)
    // 防抖 2 秒内不应出现断连提示
    expect(screen.queryByText(/WebSocket 连接断开/)).toBeNull()
  })
})
