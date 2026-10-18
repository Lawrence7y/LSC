import { describe, expect, it } from 'vitest'
import type { TimelineContext } from '@/types'
import {
  commonToPreview,
  commonToRecording,
  computeRecordedDurationHint,
  getAlignStatus,
  isNoDvrPreviewMode,
  isRecordingReviewMode,
  panTimelineWindowStart,
  pickAutoBindRoomId,
  pickReferenceRoomId,
  previewToCommon,
  previewToRecordingLocal,
  recordingToCommon,
  recordingToPreviewLocal,
  resolveLiveContentSpan,
  resolveRecordingReviewSpan,
  summarizeTimelineProgress,
} from './timelineCoords'

function ctx(overrides: Partial<TimelineContext> = {}): TimelineContext {
  return {
    timeline_id: 't1',
    reference_room_id: 'r1',
    preview_ready: true,
    clip_ready: true,
    created_at: 0,
    room_snapshots: {
      r1: { preview_to_common_delta: 10, recording_to_common_delta: 5 },
      r2: { preview_to_common_delta: -2, recording_to_common_delta: 3 },
    },
    ...overrides,
  } as TimelineContext
}

describe('三轴换算：preview / common / recording', () => {
  it('commonToRecording / recordingToCommon 互逆', () => {
    const c = ctx()
    expect(commonToRecording(c, 'r1', 100)).toBe(95)
    expect(recordingToCommon(c, 'r1', 95)).toBe(100)
    expect(commonToRecording(c, 'r2', 100)).toBe(97)
    expect(recordingToCommon(c, 'r2', 97)).toBe(100)
  })

  it('previewToCommon / commonToPreview 互逆', () => {
    const c = ctx()
    expect(previewToCommon(c, 'r1', 50)).toBe(60)
    expect(commonToPreview(c, 'r1', 60)).toBe(50)
    expect(previewToCommon(c, 'r2', 50)).toBe(48)
    expect(commonToPreview(c, 'r2', 48)).toBe(50)
  })

  it('房间不在 timeline 中时抛错，而不是静默 NaN', () => {
    const c = ctx()
    expect(() => commonToRecording(c, 'nope', 1)).toThrow(/not in timeline/)
    expect(() => recordingToCommon(c, 'nope', 1)).toThrow(/not in timeline/)
    expect(() => previewToCommon(c, 'nope', 1)).toThrow(/not in timeline/)
    expect(() => commonToPreview(c, 'nope', 1)).toThrow(/not in timeline/)
  })

  it('录制↔预览本地钟：delta 缺失或 epoch 不一致时拒绝换算', () => {
    expect(recordingToPreviewLocal(null, 10)).toBeNull()
    // recording_to_preview_delta = preview − recording ⇒ preview = recording + delta
    expect(recordingToPreviewLocal({ recording_to_preview_delta: 15 }, 10)).toBe(25)
    expect(previewToRecordingLocal({ recording_to_preview_delta: 15 }, 25)).toBe(10)
    // epoch 漂移：旧标定不得继续用
    expect(recordingToPreviewLocal({
      recording_to_preview_delta: 15,
      preview_clock_epoch_id: 'old',
      preview_epoch_id: 'new',
    }, 10)).toBeNull()
    expect(previewToRecordingLocal({
      recording_to_preview_delta: 15,
      preview_clock_epoch_id: 'old',
      preview_epoch_id: 'new',
    }, 10)).toBeNull()
    // 同 epoch 可用
    expect(recordingToPreviewLocal({
      recording_to_preview_delta: 15,
      preview_clock_epoch_id: 'e1',
      preview_epoch_id: 'e1',
    }, 10)).toBe(25)
  })
})

describe('对齐状态与参考房', () => {
  it('getAlignStatus：invalidated 优先，其次 timeline_id，否则 local', () => {
    expect(getAlignStatus(ctx(), true)).toBe('invalidated')
    expect(getAlignStatus(ctx(), false)).toBe('ready')
    expect(getAlignStatus(null, false)).toBe('local')
  })

  it('pickReferenceRoomId：优先 timeline 参考房，其次选中集内有快照的房间', () => {
    const c = ctx()
    expect(pickReferenceRoomId(c, new Set(['r1', 'r2']))).toBe('r1')
    expect(pickReferenceRoomId(c, new Set(['r2']))).toBe('r2')
    expect(pickReferenceRoomId(c, new Set(['r9']), 'fb')).toBe('fb')
    expect(pickReferenceRoomId(null, new Set(['r1']), null)).toBeNull()
  })
})

describe('未勾选房间时的自动绑定（P3-2）', () => {
  it('唯一录制房自动跟随；多路不猜', () => {
    expect(pickAutoBindRoomId([
      { room_id: 'a', is_recording: true },
      { room_id: 'b' },
    ])).toBe('a')
    expect(pickAutoBindRoomId([
      { room_id: 'a', is_recording: true },
      { room_id: 'b', is_recording: true },
    ])).toBeNull()
  })

  it('无录制房时取唯一「已开预览且已连接」；未连接预览不算', () => {
    expect(pickAutoBindRoomId([
      { room_id: 'a', preview_enabled: true, is_connected: true },
    ])).toBe('a')
    expect(pickAutoBindRoomId([
      { room_id: 'a', preview_enabled: true, is_connected: false },
    ])).toBeNull()
  })

  it('录制房优先于预览房', () => {
    expect(pickAutoBindRoomId([
      { room_id: 'p', preview_enabled: true, is_connected: true },
      { room_id: 'r', is_recording: true },
    ])).toBe('r')
  })
})

describe('滑动窗 panTimelineWindowStart', () => {
  it('内容不超过 maxWindow 时左端恒为 0', () => {
    expect(panTimelineWindowStart(50, 100, 200, 0)).toBe(0)
  })

  it('播放头越出窗口才平移，窗内保持 prevWs（禁止持续钉位）', () => {
    // contentEnd=1000, maxWindow=200, prevWs=100 → 窗 [100,300]
    expect(panTimelineWindowStart(150, 1000, 200, 100)).toBe(100)
    expect(panTimelineWindowStart(50, 1000, 200, 100)).toBe(50)   // 左越出
    expect(panTimelineWindowStart(400, 1000, 200, 100)).toBe(200) // 右越出
  })

  it('左端永不小于 0，且不超过 contentEnd − maxWindow', () => {
    expect(panTimelineWindowStart(-10, 1000, 200, 100)).toBe(0)
    expect(panTimelineWindowStart(50, 250, 200, 0)).toBe(0)
    // playhead 越右沿：ws = playhead − maxWindow，再夹到 maxWs = contentEnd − maxWindow
    expect(panTimelineWindowStart(240, 250, 200, 0)).toBe(40)
  })
})

describe('回看 / no-DVR 模式判定', () => {
  it('recording_review 才是回看；degraded 也禁 DVR', () => {
    expect(isRecordingReviewMode('recording_review')).toBe(true)
    expect(isRecordingReviewMode('live')).toBe(false)
    expect(isNoDvrPreviewMode('recording_review')).toBe(true)
    expect(isNoDvrPreviewMode('degraded')).toBe(true)
    expect(isNoDvrPreviewMode('live')).toBe(false)
  })

  it('resolveRecordingReviewSpan 取播放/录制/文件/mark 的最大值，下限 1', () => {
    expect(resolveRecordingReviewSpan(10, 30, 20)).toBe(30)
    expect(resolveRecordingReviewSpan(10, 5, 20)).toBe(20)
    expect(resolveRecordingReviewSpan(10, 5, null, 40)).toBe(40)
    expect(resolveRecordingReviewSpan(10, 5, null, null, 50)).toBe(50)
    expect(resolveRecordingReviewSpan(0, 0, 0)).toBe(1)
  })
})

describe('内容右沿 resolveLiveContentSpan', () => {
  it('取播放头进度与切片末端的较大值', () => {
    expect(resolveLiveContentSpan({ axisProgress: 10 })).toBe(10)
    expect(resolveLiveContentSpan({ axisProgress: 10, clipEnds: [3, 40, 20] })).toBe(40)
  })

  it('有预览且 followLive 时禁止用 recorded 撑窗（§8.7）', () => {
    expect(resolveLiveContentSpan({
      axisProgress: 5,
      recordedHint: 3600,
      previewEnabled: true,
      followLive: true,
    })).toBe(5)
  })

  it('无预览 / 回看 / 非 followLive / 录制中 可用 recorded 时长撑开', () => {
    expect(resolveLiveContentSpan({
      axisProgress: 5,
      recordedHint: 3600,
      previewEnabled: false,
    })).toBe(3600)
    expect(resolveLiveContentSpan({
      axisProgress: 5,
      recordedHint: 3600,
      previewEnabled: true,
      followLive: true,
      recordingReview: true,
    })).toBe(3600)
    expect(resolveLiveContentSpan({
      axisProgress: 5,
      recordedHint: 3600,
      previewEnabled: true,
      followLive: false,
    })).toBe(3600)
    expect(resolveLiveContentSpan({
      axisProgress: 5,
      recordedHint: 3600,
      previewEnabled: true,
      followLive: true,
      isRecording: true,
    })).toBe(3600)
  })

  it('下限 1，非法输入不产生 NaN', () => {
    expect(resolveLiveContentSpan({ axisProgress: Number.NaN })).toBe(1)
    expect(resolveLiveContentSpan({ axisProgress: -3, clipEnds: [Number.NaN] })).toBe(1)
  })
})

describe('录制时长提示与进度摘要', () => {
  it('录制中用墙钟差，否则用 continuous 已录时长', () => {
    const now = 1_000_000
    const started = new Date(now - 30_000).toISOString()
    expect(computeRecordedDurationHint({ is_recording: true, record_started_at: started }, 0, now)).toBe(30)
    expect(computeRecordedDurationHint({ is_recording: false, record_started_at: started }, 12, now)).toBe(12)
  })

  it('summarizeTimelineProgress 组装预览位置 / 录制时长 / 分析扫描 / 预览延迟', () => {
    const s = summarizeTimelineProgress({
      previewPosition: 20,
      room: null,
      continuousRecorded: 50,
      continuousStatus: { analyzed_duration: 40 } as never,
      axis: 'common',
      nowMs: 0,
    })
    expect(s).toMatchObject({
      previewPosition: 20,
      recordedDuration: 50,
      analysisScannedDuration: 40,
      previewDelay: 30,
      axis: 'common',
    })
  })
})
