import { useCallback, useRef } from 'react'

/**
 * 撤销命令（command pattern）。
 *
 * 每个可撤销操作压入一条命令，命令携带唯一 id 与 undo 回调；
 * 调用方（通常是 toast 上的「撤销」链接）凭 id 触发回滚。
 */
export interface UndoCommand {
  /** 唯一标识，用于从栈中定位并执行 */
  id: string
  /** 人类可读的操作描述（可用于 toast 展示） */
  label: string
  /** 回滚逻辑 */
  undo: () => void
}

/**
 * 轻量撤销栈。
 *
 * 设计取舍：
 * - 不做重做（redo）——面向「误删切片」等一次性破坏操作的兜底，撤销即弃。
 * - 命令在入栈时即捕获回滚所需的全部上下文（闭包），出栈时不依赖外部状态。
 * - 用 ref 持栈，避免 undo 回调读到过期闭包；栈本身不参与渲染。
 *
 * @param maxSize 栈容量上限，超出后丢弃最旧命令（默认 20）
 */
export function useUndoStack(maxSize = 20) {
  const stackRef = useRef<UndoCommand[]>([])

  /** 压入一条可撤销命令，返回其 id（供 toast 绑定撤销入口） */
  const push = useCallback(
    (label: string, undo: () => void): string => {
      const id = `undo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
      stackRef.current.push({ id, label, undo })
      if (stackRef.current.length > maxSize) {
        stackRef.current.shift()
      }
      return id
    },
    [maxSize],
  )

  /** 按 id 执行并移除命令；不存在时静默返回 false */
  const undo = useCallback((id: string): boolean => {
    const idx = stackRef.current.findIndex((c) => c.id === id)
    if (idx === -1) return false
    const [cmd] = stackRef.current.splice(idx, 1)
    cmd.undo()
    return true
  }, [])

  /** 丢弃指定命令（如操作已被后续动作固化，不再允许撤销） */
  const dismiss = useCallback((id: string): void => {
    stackRef.current = stackRef.current.filter((c) => c.id !== id)
  }, [])

  /** 清空全部命令 */
  const clear = useCallback((): void => {
    stackRef.current = []
  }, [])

  return { push, undo, dismiss, clear }
}
