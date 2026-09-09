import { describe, expect, it, beforeEach, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { type MutableRefObject } from 'react'
import {
  useKeyboardShortcuts,
  WORKBENCH_SHORTCUTS,
  WORKBENCH_SHORTCUT_LIST,
  SHORTCUT_DOCS,
  type WorkbenchShortcutEntry,
} from './useKeyboardShortcuts'

/**
 * 快捷键回归测试。
 *
 * 覆盖两类历史缺陷：
 * 1. 修饰键未声明时不参与判定，导致 `r`(切换录制) 吞掉 Ctrl+R(批量录制)、
 *    `ArrowLeft` 吞掉 Shift+ArrowLeft(逐帧)——命中结果取决于注册顺序。
 * 2. WORKBENCH_SHORTCUTS 表与 Workbench 内手抄的清单并行维护，
 *    表里定义的键位（逐帧 / C / Esc）实际从未接线。
 */

type KeyInit = { key: string; ctrlKey?: boolean; shiftKey?: boolean; altKey?: boolean }

function pressKey(init: KeyInit) {
  window.dispatchEvent(
    new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }),
  )
}

function Probe({ hits }: { hits: MutableRefObject<string[]> }) {
  useKeyboardShortcuts(WORKBENCH_SHORTCUT_LIST, id => {
    hits.current.push(id)
  })
  return null
}

describe('快捷键匹配：修饰键未声明即必须未按', () => {
  // 普通可变对象即可，不要在 hook 环境外调 useRef
  const hits: MutableRefObject<string[]> = { current: [] }

  beforeEach(() => {
    hits.current = []
    render(<Probe hits={hits} />)
  })

  afterEach(() => {
    cleanup()
  })

  const cases: [KeyInit, string[]][] = [
    [{ key: 'r' }, ['record:toggle']],
    [{ key: 'r', ctrlKey: true }, ['batch:record']],
    [{ key: 'r', ctrlKey: true, shiftKey: true }, ['batch:stop']],
    [{ key: 'ArrowLeft' }, ['seek:back-1']],
    [{ key: 'ArrowLeft', shiftKey: true }, ['seek:back-frame']],
    [{ key: 'ArrowRight' }, ['seek:fwd-1']],
    [{ key: 'ArrowRight', shiftKey: true }, ['seek:fwd-frame']],
    [{ key: ',' }, ['seek:back-fine']],
    [{ key: '.' }, ['seek:fwd-fine']],
    [{ key: '<', shiftKey: true }, ['rate:cycle-down']],
    [{ key: '>', shiftKey: true }, ['rate:cycle-up']],
    [{ key: '[' }, ['mark:nudge-out-back']],
    [{ key: '{', shiftKey: true }, ['mark:nudge-in-back']],
    [{ key: 'a', ctrlKey: true, shiftKey: true }, ['select:all']],
    [{ key: 'e', ctrlKey: true }, ['export:clip']],
    [{ key: 'c' }, ['toggle:clips']],
    [{ key: 'Escape' }, ['cancel:refine']],
    [{ key: 'z', ctrlKey: true }, ['undo:mark']],
    // 裸按键不得被组合键吞掉，反之亦然
    [{ key: 'a', ctrlKey: true }, []],
    [{ key: 'z' }, []],
    [{ key: 'e' }, []],
    [{ key: 'x' }, []],
  ]

  it.each(cases)('按下 %j → %j', (init, expected) => {
    pressKey(init)
    expect(hits.current).toEqual(expected)
  })
})

describe('快捷键表与文档同源', () => {
  const registeredIds = new Set<string>(WORKBENCH_SHORTCUT_LIST.map(s => s.id))
  const documentedIds = new Set<string>(SHORTCUT_DOCS.flatMap(d => d.ids))

  it('每个已注册的快捷键都有文档行（含 page:*）', () => {
    const tableIds = new Set<string>(Object.values(WORKBENCH_SHORTCUTS).map(s => s.id))
    const undocumented = [...tableIds].filter(id => !documentedIds.has(id))
    expect(undocumented).toEqual([])
  })

  it('文档不引用不存在的动作', () => {
    const tableIds = new Set<string>(Object.values(WORKBENCH_SHORTCUTS).map(s => s.id))
    const ghosts = [...documentedIds].filter(id => !tableIds.has(id))
    expect(ghosts).toEqual([])
  })

  it('同一「按键 + 修饰键」组合不会映射到两个不同动作', () => {
    const seen = new Map<string, string>()
    const conflicts: string[] = []
    // 表的 `as const` 使成员仅拥有自己声明过的属性，这里按统一形状读取
    const defs = Object.values(WORKBENCH_SHORTCUTS) as readonly WorkbenchShortcutEntry[]
    for (const s of defs) {
      const sig = [
        s.key.toLowerCase(),
        s.ctrl === undefined ? '-' : `ctrl${s.ctrl ? '+' : '-'}`,
        s.shift === undefined ? '-' : `shift${s.shift ? '+' : '-'}`,
        s.alt === undefined ? '-' : `alt${s.alt ? '+' : '-'}`,
      ].join(':')
      const prev = seen.get(sig)
      if (prev !== undefined && prev !== s.id) conflicts.push(`${sig}: ${prev} vs ${s.id}`)
      seen.set(sig, s.id)
    }
    expect(conflicts).toEqual([])
  })

  it('page:* 由 MainLayout 注册，不出现在 Workbench 清单里', () => {
    expect(WORKBENCH_SHORTCUT_LIST.some(s => s.id.startsWith('page:'))).toBe(false)
    expect(registeredIds.has('record:toggle')).toBe(true)
  })
})
