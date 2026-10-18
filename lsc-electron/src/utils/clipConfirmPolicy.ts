import type { ClipSegment } from '@/types'

/**
 * 人工确认切片的三件纯判据（2026-09-19 从 `Workbench.handleConfirmClip` 闭包里搬出）。
 *
 * 这段逻辑的直接后果是"导出时切的是哪一段"与"确认会不会同步到别的房间"，
 * 而它此前只被文本守卫守着（切 `handleConfirmClip` 函数体找子串）。
 * 搬出来之后：优先级、有效性判据、房间作用域都能用输入/输出直接证。
 */

export interface ConfirmBoundsInput {
  /** 切片入列时自带的边界（预览/录制轴以调用方口径为准，本函数只做选择与有效性判断）。 */
  clipStart: number
  clipEnd: number
  /** 只有"当前正在精修的那条"才允许读时间线 mark；批量确认必须用自身边界。 */
  refining: boolean
  /** 公共轴 mark 已换算到录制轴；不可用时传 null（对齐未就绪/缺 rid/换算抛错）。 */
  commonMarks?: { start: number; end: number } | null
  /** 房间 mark 已换算到录制轴；不可用时传 null。 */
  roomMarks?: { start: number; end: number } | null
  /** 用户在时间轴上拖动但未落盘的单个游标，优先级最高。 */
  dragMark?: { type: 'in' | 'out'; time: number } | null
}

/**
 * 边界优先级：拖动游标 > 公共轴 mark > 房间 mark > 切片自身边界。
 * 出点不晚于入点 ⇒ 返回 null（调用方提示"入出点无效"并放弃确认）。
 */
export function resolveConfirmBounds(input: ConfirmBoundsInput): { start: number; end: number } | null {
  let start = input.clipStart
  let end = input.clipEnd
  if (input.refining) {
    const marks = input.commonMarks ?? input.roomMarks
    if (marks) {
      start = marks.start
      end = marks.end
    }
    if (input.dragMark) {
      if (input.dragMark.type === 'in') start = input.dragMark.time
      else end = input.dragMark.time
    }
  }
  return end > start ? { start, end } : null
}

export interface SyncTargetInput {
  clipRoomId: string
  /** 切片所属房间的对齐组；空串表示没有分组。 */
  alignGroupId?: string
  /** 本次持续/同步分析冻结的目标房间（优先级最高）。 */
  analysisTargetRoomIds: readonly string[]
  rooms: readonly { room_id: string; is_connected?: boolean; align_group_id?: string }[]
}

/**
 * 确认时要同步的房间集合。
 *
 * 判据：**本次分析冻结的目标集** 优先，其次**同对齐组**，都没有就返回空 ——
 * 绝不允许退回"所有已连接房间"（那会把切片边界写进没参与本次分析的房间）。
 * 同步目标始终排除切片自己所在的房间。
 */
export function selectSyncTargetRooms(input: SyncTargetInput): string[] {
  const groupId = input.alignGroupId || ''
  const isUsable = (r: { room_id: string; is_connected?: boolean; align_group_id?: string }): boolean =>
    !!r.is_connected && r.room_id !== input.clipRoomId && (!groupId || r.align_group_id === groupId)
  const byId = new Map(input.rooms.map(r => [r.room_id, r]))
  const analysisTargets = input.analysisTargetRoomIds
    .map(id => byId.get(id))
    .filter((r): r is { room_id: string; is_connected?: boolean; align_group_id?: string } => !!r && isUsable(r))
    .map(r => r.room_id)
  if (analysisTargets.length > 0) return analysisTargets
  if (groupId) {
    return input.rooms.filter(r => isUsable(r) && r.align_group_id === groupId).map(r => r.room_id)
  }
  return []
}

/**
 * 确认后的乐观更新：只改**同房间**里代表同一条切片的条目。
 *
 * 房间作用域是硬要求（同 `round_key` 的其它房间由后端映射后再广播），
 * 条目身份则按"key 命中 → 本条 id 命中 → 几何相同"依次判定，
 * 且 `clip_id` 缺失时不得用 `undefined === undefined` 命中所有条目。
 */
export function applyConfirmedClipUpdate(
  clips: readonly ClipSegment[],
  clip: ClipSegment,
  bounds: { start: number; end: number },
): ClipSegment[] {
  const clipKey = clip.round_key || clip.clip_id || ''
  return clips.map((c) => {
    if (c.room_id !== clip.room_id) return c
    const same
      = (!!clipKey && (c.round_key === clipKey || c.clip_id === clipKey))
      || (!!c.clip_id && c.clip_id === clip.clip_id)
      || (c.start === clip.start && c.end === clip.end)
    if (!same) return c
    return {
      ...c,
      start: bounds.start,
      end: bounds.end,
      ...(clip.is_ai_highlight
        ? { recording_start_sec: bounds.start, recording_end_sec: bounds.end }
        : {}),
      confirm_status: 'user_confirmed' as const,
    }
  })
}
