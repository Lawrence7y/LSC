import { useCallback, useRef } from 'react'
import { useUndoStack } from './useUndoStack'

/** 一次「入出点」改动的完整可回滚快照 */
export interface MarkSnapshot {
  /** 公共轴选区（Workbench 本地态，不在 store 里，故由调用方提供读写） */
  commonIn: number | null
  commonOut: number | null
  /** 各目标房间预览轴标记（store 中的 mark_in / mark_out 即预览时间） */
  rooms: Array<{ roomId: string; markIn: number | null; markOut: number | null }>
}

/**
 * 入出点（I/O、微调、拖拽标记、删除标记）的撤销栈。
 *
 * 此前只有「删切片」能撤销，标记类操作一旦打错只能重来；对逐帧调边界的工作流
 * 来说这是最常见的误操作。这里只负责栈与"改动前抓快照"的时序，
 * 快照怎么读、怎么恢复由调用方给出，因此不绑定任何具体的状态容器。
 */
export function useMarkUndo(opts: {
  /** 抓取当前标记状态（必须在改动之前调用） */
  getSnapshot: () => MarkSnapshot
  /** 应用快照：恢复本地选区 + 回写各房间标记 */
  applySnapshot: (snapshot: MarkSnapshot) => void
  /** 栈容量：微调是高频连按，留足回退步数 */
  maxSize?: number
}) {
  const { getSnapshot, applySnapshot, maxSize = 40 } = opts
  const stack = useUndoStack(maxSize)
  const { push, undoLast: undoLastCommand, canUndo, clear } = stack
  // 调用方传的是内联箭头函数，每轮渲染都是新引用；存进 ref 后
  // 下面导出的回调才能保持恒定身份，否则会打破 ControlBar 的 memo  comparator。
  const getSnapshotRef = useRef(getSnapshot)
  getSnapshotRef.current = getSnapshot
  const applySnapshotRef = useRef(applySnapshot)
  applySnapshotRef.current = applySnapshot
  // 同一次拖拽内 pointermove 会连续回调，用该标记把「一次拖拽」并成一步撤销
  const coalesceRef = useRef<{ key: string; id: string } | null>(null)

  /**
   * 记录一次改动前的状态。
   *
   * @param label 人类可读描述（用于未来的撤销入口/日志）
   * @param coalesceKey 同 key 的连续调用只记第一步（如一次 marker 拖拽）
   */
  const record = useCallback((label: string, coalesceKey?: string) => {
    if (coalesceKey && coalesceRef.current?.key === coalesceKey) return
    const snapshot = getSnapshotRef.current()
    const id = push(label, () => applySnapshotRef.current(snapshot))
    coalesceRef.current = coalesceKey ? { key: coalesceKey, id } : null
  }, [push])

  /** 结束一次可合并的连续操作（如松手），下一次同 key 调用会重新记一步 */
  const endCoalesce = useCallback(() => {
    coalesceRef.current = null
  }, [])

  /** @returns 是否真的撤销了一步；false 表示栈空 */
  const undoLast = useCallback((): boolean => {
    coalesceRef.current = null
    return undoLastCommand()
  }, [undoLastCommand])

  return { record, endCoalesce, undoLast, canUndo, clear }
}
