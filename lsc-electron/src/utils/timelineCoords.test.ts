import { describe, expect, it } from 'vitest'
import { previewToRecordingLocal, recordingToPreviewLocal } from './timelineCoords'

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
