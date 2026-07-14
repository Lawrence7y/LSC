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
 * 判断是否存在可见的 Modal / 对话框
 */
function hasVisibleModal(): boolean {
  return !!document.querySelector('.ant-modal-wrap:not([style*="display: none"])')
    || !!document.querySelector('[role="dialog"]')
}

/**
 * 判断当前焦点是否在可输入元素中，或存在可见对话框
 */
function isInputFocused(): boolean {
  const el = document.activeElement
  if (!el) return false
  const tag = el.tagName.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if ((el as HTMLElement).isContentEditable) return true
  if (hasVisibleModal()) return true
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
      const isNav = e.key === '1' || e.key === '2' || e.key === '3' || e.key === 'F5'
      if (isInputFocused() && !isNav) return

      for (const s of shortcutsRef.current) {
        if (!matchesShortcut(e, s)) continue
        // 步进/微调允许连按；其它快捷键忽略 key repeat
        if (e.repeat && !s.id.startsWith('seek:') && !s.id.startsWith('mark:nudge')) {
          return
        }
        if (s.preventDefault !== false) {
          e.preventDefault()
          e.stopPropagation()
        }
        onShortcutRef.current(s.id, e)
        break // 只触发第一个匹配的快捷键
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
  PAGE_WORKBENCH:    { key: '1', ctrl: true, id: 'page:workbench' },
  PAGE_SETTINGS:     { key: '2', ctrl: true, id: 'page:settings' },
  PLAY_PAUSE:        { key: ' ',                 id: 'play:toggle' },
  PLAY_PAUSE_K:      { key: 'k',                 id: 'play:toggle' },
  MARK_IN:           { key: 'i',                 id: 'mark:in' },
  MARK_OUT:          { key: 'o',                 id: 'mark:out' },
  SEEK_BACK_1:       { key: 'ArrowLeft',         id: 'seek:back-1' },
  SEEK_FWD_1:        { key: 'ArrowRight',        id: 'seek:fwd-1' },
  SEEK_BACK_FINE:    { key: ',', shift: false,   id: 'seek:back-fine' },
  SEEK_FWD_FINE:     { key: '.', shift: false,   id: 'seek:fwd-fine' },
  SEEK_BACK_2:       { key: 'j',                 id: 'seek:back-2' },
  SEEK_FWD_2:        { key: 'l',                 id: 'seek:fwd-2' },
  NUDGE_OUT_BACK:    { key: '[', shift: false,   id: 'mark:nudge-out-back' },
  NUDGE_OUT_FWD:     { key: ']', shift: false,   id: 'mark:nudge-out-fwd' },
  // Shift+[ 在多数键盘上产生 `{`/`}`；Shift+,/. 产生 `<>`
  NUDGE_IN_BACK:     { key: '{',                 id: 'mark:nudge-in-back' },
  NUDGE_IN_FWD:      { key: '}',                 id: 'mark:nudge-in-fwd' },
  RATE_CYCLE_DOWN:   { key: '<',                 id: 'rate:cycle-down' },
  RATE_CYCLE_UP:     { key: '>',                 id: 'rate:cycle-up' },
  TOGGLE_RECORD:     { key: 'r',                 id: 'record:toggle' },
  TOGGLE_MUTE:       { key: 'm',                 id: 'mute:toggle' },
  FULLSCREEN:        { key: 'f',                 id: 'fullscreen' },
  RELOAD_PAGE:       { key: 'F5',                id: 'page:reload' },
  BATCH_RECORD:      { key: 'r', ctrl: true,     id: 'batch:record' },
  BATCH_STOP:        { key: 'r', ctrl: true, shift: true, id: 'batch:stop' },
  SELECT_ALL:        { key: 'a', ctrl: true, shift: true, id: 'select:all' },
  EXPORT_CLIP:       { key: 'e', ctrl: true,     id: 'export:clip' },
} as const

/** 播放速率档位（ControlBar / 快捷键共用） */
export const PLAYBACK_RATE_STEPS = [0.5, 1, 1.5, 2] as const
export type PlaybackRate = (typeof PLAYBACK_RATE_STEPS)[number]

export type WorkbenchShortcutId = typeof WORKBENCH_SHORTCUTS[keyof typeof WORKBENCH_SHORTCUTS]['id']
