import { describe, expect, it } from 'vitest'
import type { TimelineContext } from '@/types'
import { computeTimelineViewModel } from './timelineViewModel'

describe('computeTimelineViewModel', () => {
  it('1x 普通手动选区仍从 0 开始，不误用精修窗口', () => {
    const timelineContext: TimelineContext = {
      timeline_id: 'timeline-1',
      reference_room_id: 'room-1',
      preview_ready: true,
      clip_ready: true,
      created_at: 0,
      room_snapshots: {
        'room-1': {
          preview_epoch_id: 'preview-1',
          recording_id: 'recording-1',
          preview_to_common_delta: 0,
          recording_to_common_delta: 0,
          align_confidence: 1,
          media_start_mono: 0,
        },
      },
    }

    const result = computeTimelineViewModel({
      commonMode: true,
      timelineContext,
      referenceRoomId: 'room-1',
      rooms: [{ room_id: 'room-1', preview_enabled: true }],
      previewPositions: { 'room-1': 2400 },
      commonMarkIn: 1000,
      commonMarkOut: 1010,
      clips: [],
      timelineHighlights: [],
      refiningClipId: null,
      waveformPeaks: [],
      timelineFollowLive: true,
      timelineScrubbing: false,
      frozenWindowStart: null,
      prevContentEnd: 1,
      prevWindowStart: 0,
      contentEdgeRoomId: null,
      zoomLevel: 1,
    })

    expect(result.view?.windowStart).toBe(0)
    expect(result.view?.duration).toBe(2400)
  })
})
