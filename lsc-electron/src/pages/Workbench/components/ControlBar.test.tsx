import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { ControlBar } from './ControlBar'
import type { RoomSession, ClipSegment } from '@/types'

const dummyRoom: RoomSession = {
  room_id: 'r1',
  room_url: 'https://www.douyu.com/12345',
  platform: 'douyu',
  platform_name: '斗鱼',
  streamer_name: 'Streamer 1',
  stream_title: 'Title',
  is_connecting: false,
  is_connected: true,
  is_recording: false,
  preview_enabled: true,
  preview_paused: false,
  record_output_path: '/path/to/rec.mp4',
  record_started_at: null,
  mark_in: null,
  mark_out: null,
  record_size_mb: 0,
  last_error: '',
  preview_muted: false,
  stream_url: '',
  content_offset: 0,
}

const dummyClips: ClipSegment[] = [
  {
    room_id: 'r1',
    start: 10,
    end: 20,
    label: 'AI Clip 1',
    is_ai_highlight: true,
    confirm_status: 'pending',
  },
]

describe('ControlBar AI色带与时间线上方提示', () => {
  it('未对齐时不再显示「AI色带暂不上时间线」标签条', () => {
    const { container } = render(
      <ControlBar
        room={dummyRoom}
        clips={dummyClips}
        alignStatus="invalidated"
        onSeek={vi.fn()}
        onPlayPause={vi.fn()}
        onSeekBack={vi.fn()}
        onSeekFwd={vi.fn()}
        onMarkIn={vi.fn()}
        onMarkOut={vi.fn()}
        onAddClip={vi.fn()}
      />
    )

    // 验证：不会渲染任何带有「暂不上时间线」或「暂停显示」文案的标签
    expect(container.textContent).not.toContain('暂不上时间线')
    expect(container.textContent).not.toContain('暂停显示')
    expect(container.querySelector('.control-bar__meta-row')).toBeNull()
  })

  it('多选房间时正常展示多选提示', () => {
    const { container } = render(
      <ControlBar
        room={dummyRoom}
        multiSelectCount={3}
        alignStatus="ready"
        onSeek={vi.fn()}
        onPlayPause={vi.fn()}
        onSeekBack={vi.fn()}
        onSeekFwd={vi.fn()}
        onMarkIn={vi.fn()}
        onMarkOut={vi.fn()}
        onAddClip={vi.fn()}
      />
    )

    expect(container.querySelector('.control-bar__meta-row')).toBeTruthy()
    expect(container.textContent).toContain('3 房间 · 全局控制')
  })

  it('正在录制当前房间时，时间轴徽章正确显示为录制轴且具有 recording 类', () => {
    const recordingRoom = {
      ...dummyRoom,
      is_recording: true,
      record_started_at: new Date().toISOString(),
    }
    const { container } = render(
      <ControlBar
        room={recordingRoom}
        multiSelectCount={0}
        alignStatus="local"
        onSeek={vi.fn()}
        onPlayPause={vi.fn()}
        onSeekBack={vi.fn()}
        onSeekFwd={vi.fn()}
        onMarkIn={vi.fn()}
        onMarkOut={vi.fn()}
        onAddClip={vi.fn()}
      />
    )

    const badge = container.querySelector('.control-bar__align-badge')
    expect(badge).toBeTruthy()
    expect(badge?.textContent).toBe('录制轴')
    expect(badge?.className).toContain('control-bar__align-badge--recording')
  })
})
