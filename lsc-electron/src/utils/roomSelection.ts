/**
 * 房间多选：范围选择必须基于「用户看到的顺序」。
 *
 * 历史缺陷：Workbench 用 store 原始数组的下标做 Shift 区间，而卡片网格渲染的是
 * 排序后的 `sortedRooms`。一旦用户用过「按状态 / 平台 / 名称」排序，Shift 框选
 * 命中的就是视觉上不连续的一批房间，进而污染批量录制 / 一键对齐 / 分析目标。
 */

/**
 * 取可见顺序上 [anchorId, targetId] 闭区间的房间 id。
 *
 * @returns 锚点或目标已不在可见列表时返回空数组（调用方按「无区间」降级为单选）
 */
export function computeVisibleRangeIds(
  visibleOrder: readonly string[],
  anchorId: string | null | undefined,
  targetId: string,
): string[] {
  if (!anchorId) return []
  const from = visibleOrder.indexOf(anchorId)
  const to = visibleOrder.indexOf(targetId)
  if (from < 0 || to < 0) return []
  const [start, end] = from <= to ? [from, to] : [to, from]
  return visibleOrder.slice(start, end + 1)
}

/** 集合的不可变增删，供多选 toggle 复用 */
export function toggleInSet(prev: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(prev)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

/** 并集（Shift 扩展选区用：只做加法，绝不静默丢掉已有选择） */
export function unionWith(prev: ReadonlySet<string>, ids: Iterable<string>): Set<string> {
  const next = new Set(prev)
  for (const id of ids) next.add(id)
  return next
}
