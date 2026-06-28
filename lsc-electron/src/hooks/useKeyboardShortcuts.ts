import { useEffect, useRef } from 'react'

/**
 * 全局快捷键定义
 * 
 * 规则：
 * 1. 焦点在 input/textarea/select 时不触发
 * 2. 页面导航快捷键在 MainLayout 注册
 * 3. 工作区快捷键在 Workbench 注册
 */

type ShortcutDef = {
  /** 按键名称（KeyboardEvent.key） */
  key: string
  /** 是否需要 Ctrl/Cmd */
  ctrl?: boolean
  /** 是否需要 Shift */
  shift?: boolean
  /** 是否需要 Alt */
  alt?: boolean
  /** 是否阻止默认行为 */
  preventDefault?: boolean
}

type ShortcutHandler = (e: KeyboardEvent) => void

type ShortcutEntry = ShortcutDef & {
  handler: ShortcutHandler
  /** 快捷键标识，用于去重和调试 */
  id: string
}

/**
 * 判断当前焦点是否在可输入元素中
 */
function isInputFocused(): boolean {
  const el = document.activeElement
  if (!el) return false
  const tag = el.tagName.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if ((el as HTMLElement).isContentEditable) return true
  return false
}

/**
 * 检查按键是否匹配快捷键定义
 */
function matchesShortcut(e: KeyboardEvent, def: ShortcutDef): boolean {
  const ctrlOrMeta = e.ctrlKey || e.metaKey
  if (def.ctrl !== undefined && ctrlOrMeta !== def.ctrl) return false
  if (def.shift !== undefined && e.shiftKey !== def.shift) return false
  if (def.alt !== undefined && e.altKey !== def.alt) return false
  // key 对比：区分大小写，但忽略 CapsLock
  if (e.key.toLowerCase() !== def.key.toLowerCase()) return false
  return true
}

/**
 * 全局快捷键 Hook
 * 
 * 在组件中使用，自动处理注册/注销
 */
export function useKeyboardShortcuts(
  shortcuts: Omit<ShortcutEntry, 'handler'>[],
  onShortcut: (id: string, e: KeyboardEvent) => void
) {
  const onShortcutRef = useRef(onShortcut)
  onShortcutRef.current = onShortcut

  const shortcutsRef = useRef(shortcuts)
  shortcutsRef.current = shortcuts

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // 输入框内不触发快捷键（允许 Ctrl+1/2/3 页面切换始终生效）
      const isNav = e.key === '1' || e.key === '2' || e.key === '3'
      if (isInputFocused() && !isNav) return

      for (const s of shortcutsRef.current) {
        if (matchesShortcut(e, s)) {
          if (s.preventDefault !== false) {
            e.preventDefault()
            e.stopPropagation()
          }
          onShortcutRef.current(s.id, e)
          break // 只触发第一个匹配的快捷键
        }
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])
}

/**
 * 预定义的工作区快捷键（Workbench 使用）
 */
export const WORKBENCH_SHORTCUTS = {
  PAGE_DASHBOARD:   { key: '1', ctrl: true, id: 'page:dashboard' },
  PAGE_WORKBENCH:    { key: '2', ctrl: true, id: 'page:workbench' },
  PAGE_SETTINGS:     { key: '3', ctrl: true, id: 'page:settings' },
  PLAY_PAUSE:        { key: ' ',                 id: 'play:toggle' },
  MARK_IN:           { key: 'i',                 id: 'mark:in' },
  MARK_OUT:          { key: 'o',                 id: 'mark:out' },
  TOGGLE_RECORD:     { key: 'r',                 id: 'record:toggle' },
  TOGGLE_MUTE:       { key: 'm',                 id: 'mute:toggle' },
  FULLSCREEN:        { key: 'f',                 id: 'fullscreen' },
  BATCH_RECORD:      { key: 'r', ctrl: true,     id: 'batch:record' },
  BATCH_STOP:        { key: 'r', ctrl: true, shift: true, id: 'batch:stop' },
  SELECT_ALL:        { key: 'a', ctrl: true,     id: 'select:all' },
  EXPORT_CLIP:       { key: 'e', ctrl: true,     id: 'export:clip' },
} as const

export type WorkbenchShortcutId = typeof WORKBENCH_SHORTCUTS[keyof typeof WORKBENCH_SHORTCUTS]['id']
