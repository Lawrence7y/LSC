import { describe, expect, it } from 'vitest'
import { pickAutoBindRoomId, previewToRecordingLocal, recordingToPreviewLocal } from './timelineCoords'

describe('local recording/preview clock mapping', () => {
  it('maps a preview that starts 15 seconds after recording', () => {
    const room = { recording_to_preview_delta: -15 }

    expect(recordingToPreviewLocal(room, 135)).toBe(120)
    expect(previewToRecordingLocal(room, 120)).toBe(135)
  })

  it('returns null until a runtime clock sample is available', () => {
    const room = { recording_to_preview_delta: null }

    expect(recordingToPreviewLocal(room, 135)).toBeNull()
    expect(previewToRecordingLocal(room, 120)).toBeNull()
  })

  it('rejects a mapping from an old preview epoch', () => {
    const room = {
      recording_to_preview_delta: -15,
      preview_clock_epoch_id: 'old-preview',
      preview_epoch_id: 'new-preview',
    }

    expect(recordingToPreviewLocal(room, 135)).toBeNull()
    expect(previewToRecordingLocal(room, 120)).toBeNull()
  })
})

describe('P3-2 未勾选房间时的时间线自动绑定', () => {
  const rec = (id: string) => ({ room_id: id, is_recording: true, preview_enabled: true, is_connected: true })
  const prev = (id: string) => ({ room_id: id, is_recording: false, preview_enabled: true, is_connected: true })
  const idle = (id: string) => ({ room_id: id, is_recording: false, preview_enabled: false, is_connected: true })

  it('唯一录制房自动跟随（这是「不勾选时轴长恒为 1s」的主场景）', () => {
    expect(pickAutoBindRoomId([idle('a'), rec('b'), idle('c')])).toBe('b')
  })

  it('没有录制房时，唯一已开预览且已连接的房间自动跟随', () => {
    expect(pickAutoBindRoomId([idle('a'), prev('b')])).toBe('b')
  })

  it('多路录制不猜：返回 null，由 UI 提示勾选', () => {
    expect(pickAutoBindRoomId([rec('a'), rec('b')])).toBeNull()
  })

  it('多路预览也不猜', () => {
    expect(pickAutoBindRoomId([prev('a'), prev('b')])).toBeNull()
  })

  it('录制房优先于预览房：有录制房时只看录制房', () => {
    expect(pickAutoBindRoomId([rec('a'), prev('b')])).toBe('a')
    expect(pickAutoBindRoomId([rec('a'), rec('b'), prev('c')])).toBeNull()
  })

  it('未连接的预览房不算候选（没有画面就没有可显示的轴）', () => {
    expect(pickAutoBindRoomId([{ room_id: 'a', preview_enabled: true, is_connected: false }])).toBeNull()
  })

  it('空列表 / 全空闲返回 null', () => {
    expect(pickAutoBindRoomId([])).toBeNull()
    expect(pickAutoBindRoomId([idle('a'), idle('b')])).toBeNull()
  })
})
