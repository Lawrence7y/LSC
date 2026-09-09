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
 *
 * `[role="dialog"]` 必须检查可见性：常驻 DOM 但隐藏的 dialog（Drawer、
 * 未销毁的 Modal 容器）若没有布局矩形，不应拦截全局快捷键。
 */
function hasVisibleModal(): boolean {
  if (document.querySelector('.ant-modal-wrap:not([style*="display: none"])')) return true
  const dialogs = document.querySelectorAll('[role="dialog"]')
  for (const el of Array.from(dialogs)) {
    if ((el as HTMLElement).getClientRects().length > 0) return true
  }
  return false
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
 *
 * 修饰键语义为「未声明 = 必须未按」：早期实现里未声明的修饰键不参与判定，
 * 导致 `r`(record:toggle) 同时命中 Ctrl+R(批量录制)、`ArrowLeft` 同时命中
 * Shift+ArrowLeft(逐帧)——注册顺序决定结果，行为不可预测。
 * 现在 ctrl/shift/alt 三者一致：要响应组合键就必须在表里显式声明。
 */
function matchesShortcut(e: KeyboardEvent, def: ShortcutDef): boolean {
  const ctrlOrMeta = e.ctrlKey || e.metaKey
  if (def.ctrl === undefined ? ctrlOrMeta : def.ctrl !== ctrlOrMeta) return false
  if (def.shift === undefined ? e.shiftKey : def.shift !== e.shiftKey) return false
  if (def.alt === undefined ? e.altKey : def.alt !== e.altKey) return false
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
      // 导航快捷键（page:*）在输入框聚焦时也放行；从 SHORTCUTS 表派生，单一真相
      const isNav = NAV_SHORTCUT_KEYS.has(e.key.toLowerCase())
      if (isInputFocused() && !isNav) return

      for (const s of shortcutsRef.current) {
        if (!matchesShortcut(e, s)) continue
        if (s.preventDefault !== false) {
          e.preventDefault()
          e.stopPropagation()
        }
        // 步进/微调允许连按；其它快捷键忽略 key repeat
        // （已 preventDefault，避免长按空格/方向键触发页面滚动等默认行为）
        if (e.repeat && !s.id.startsWith('seek:') && !s.id.startsWith('mark:nudge')) {
          break
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
 * 预定义的工作区快捷键（Workbench 使用）—— 唯一真相源。
 *
 * 【约束】Workbench 必须直接注册 WORKBENCH_SHORTCUT_LIST，不得再手抄一份
 * 按键清单。历史上两处并行维护导致：手抄版漏掉了逐帧/C/Tab/Enter/Esc，
 * 而这几个 id 在表里有定义但从未接线（等于承诺了一个不存在的功能）。
 * 新增/修改键位只改本表；文档由 SHORTCUT_DOCS 与本表同文件维护。
 */
export const WORKBENCH_SHORTCUTS = {
  PAGE_WORKBENCH:    { key: '1', ctrl: true, shift: false, id: 'page:workbench' },
  PAGE_SETTINGS:     { key: '2', ctrl: true, shift: false, id: 'page:settings' },
  PLAY_PAUSE:        { key: ' ',                                        id: 'play:toggle' },
  PLAY_PAUSE_K:      { key: 'k',                                        id: 'play:toggle' },
  MARK_IN:           { key: 'i',                                        id: 'mark:in' },
  MARK_OUT:          { key: 'o',                                        id: 'mark:out' },
  SEEK_BACK_FRAME:   { key: 'ArrowLeft',  shift: true,                  id: 'seek:back-frame' },
  SEEK_FWD_FRAME:    { key: 'ArrowRight', shift: true,                  id: 'seek:fwd-frame' },
  SEEK_BACK_1:       { key: 'ArrowLeft',                                 id: 'seek:back-1' },
  SEEK_FWD_1:        { key: 'ArrowRight',                                id: 'seek:fwd-1' },
  SEEK_BACK_2:       { key: 'j',                                         id: 'seek:back-2' },
  SEEK_FWD_2:        { key: 'l',                                         id: 'seek:fwd-2' },
  SEEK_BACK_FINE:    { key: ',',                                         id: 'seek:back-fine' },
  SEEK_FWD_FINE:     { key: '.',                                         id: 'seek:fwd-fine' },
  NUDGE_OUT_BACK:    { key: '[',                                         id: 'mark:nudge-out-back' },
  NUDGE_OUT_FWD:     { key: ']',                                         id: 'mark:nudge-out-fwd' },
  // 这四个键在 US 布局上必须 Shift 才能输入（Shift+[ → `{`，Shift+, → `<`），
  // 而匹配语义现在是「未声明 = 必须未按」，所以必须显式声明 shift: true。
  NUDGE_IN_BACK:     { key: '{',   shift: true,                         id: 'mark:nudge-in-back' },
  NUDGE_IN_FWD:      { key: '}',   shift: true,                         id: 'mark:nudge-in-fwd' },
  RATE_CYCLE_DOWN:   { key: '<',   shift: true,                         id: 'rate:cycle-down' },
  RATE_CYCLE_UP:     { key: '>',   shift: true,                         id: 'rate:cycle-up' },
  TOGGLE_RECORD:     { key: 'r',                       preventDefault: false, id: 'record:toggle' },
  TOGGLE_MUTE:       { key: 'm',                                        id: 'mute:toggle' },
  FULLSCREEN:        { key: 'f',                                        id: 'fullscreen' },
  TOGGLE_CLIPS:      { key: 'c',                                        id: 'toggle:clips' },
  CANCEL_REFINE:     { key: 'Escape',                                   id: 'cancel:refine' },
  UNDO_MARK:         { key: 'z', ctrl: true, shift: false,              id: 'undo:mark' },
  RELOAD_PAGE:       { key: 'F5',                                       id: 'page:reload' },
  BATCH_RECORD:      { key: 'r', ctrl: true, shift: false,              id: 'batch:record' },
  BATCH_STOP:        { key: 'r', ctrl: true, shift: true,               id: 'batch:stop' },
  SELECT_ALL:        { key: 'a', ctrl: true, shift: true,               id: 'select:all' },
  EXPORT_CLIP:       { key: 'e', ctrl: true, shift: false,              id: 'export:clip' },
} as const

/** 未声明修饰键时默认值（保持类型可读）。 */
export type WorkbenchShortcutEntry = { key: string; id: string; ctrl?: boolean; shift?: boolean; alt?: boolean; preventDefault?: boolean }

/**
 * Workbench 实际注册的快捷键清单：由 WORKBENCH_SHORTCUTS 派生。
 * 排除 page:*（由 MainLayout 注册，避免同一按键被两处消费）。
 */
export const WORKBENCH_SHORTCUT_LIST: WorkbenchShortcutEntry[] = Object
  .values(WORKBENCH_SHORTCUTS)
  .filter(s => !s.id.startsWith('page:'))
  .map(s => ({ ...s } as WorkbenchShortcutEntry))

/** 导航类快捷键（id 以 page: 开头）的按键集合，输入聚焦时仍放行。
 * 从 WORKBENCH_SHORTCUTS 派生，避免两处维护同一按键清单。
 * 注意：必须定义在 WORKBENCH_SHORTCUTS 之后（const TDZ）。 */
const NAV_SHORTCUT_KEYS: ReadonlySet<string> = new Set(
  Object.values(WORKBENCH_SHORTCUTS)
    .filter((s) => s.id.startsWith('page:'))
    .map((s) => s.key.toLowerCase()),
)

/** 播放速率档位（ControlBar / 快捷键共用） */
export const PLAYBACK_RATE_STEPS = [0.5, 1, 1.5, 2] as const
export type PlaybackRate = (typeof PLAYBACK_RATE_STEPS)[number]

export type WorkbenchShortcutId = typeof WORKBENCH_SHORTCUTS[keyof typeof WORKBENCH_SHORTCUTS]['id']

/**
 * 「设置 › 快捷键一览」的唯一数据源，与 WORKBENCH_SHORTCUTS 同文件维护。
 * `ids` 用于单测校验：每个注册的快捷键必须有文档行，反之亦然，
 * 避免再出现「文档只写了 13 条、实际有20+ 条」的漂移。
 */
export const SHORTCUT_DOCS: { ids: WorkbenchShortcutId[]; keys: string[]; label: string }[] = [
  { ids: ['page:workbench'], keys: ['Ctrl', '1'], label: '页面：工作台' },
  { ids: ['page:settings'], keys: ['Ctrl', '2'], label: '页面：设置' },
  { ids: ['page:reload'], keys: ['F5'], label: '刷新页面' },
  { ids: ['play:toggle'], keys: ['Space', '/ K'], label: '播放 / 暂停' },
  { ids: ['seek:back-1', 'seek:fwd-1'], keys: ['←', '→'], label: '后退 / 前进 1 秒' },
  { ids: ['seek:back-2', 'seek:fwd-2'], keys: ['J / L'], label: '后退 / 前进 2 秒' },
  { ids: ['seek:back-fine', 'seek:fwd-fine'], keys: [', / .'], label: '微调 0.2 秒（边界对齐常用）' },
  { ids: ['seek:back-frame', 'seek:fwd-frame'], keys: ['Shift + ← / →'], label: '逐帧（1/30 秒）步进' },
  { ids: ['mark:in'], keys: ['I'], label: '在播放头处标记入点' },
  { ids: ['mark:out'], keys: ['O'], label: '在播放头处标记出点' },
  { ids: ['mark:nudge-in-back', 'mark:nudge-in-fwd'], keys: ['Shift + [ / ]'], label: '入点左移 / 右移 0.5 秒' },
  { ids: ['mark:nudge-out-back', 'mark:nudge-out-fwd'], keys: ['[ / ]'], label: '出点左移 / 右移 0.5 秒' },
  { ids: ['rate:cycle-down', 'rate:cycle-up'], keys: ['Shift + , / .'], label: '播放速率降 / 升一档' },
  { ids: ['record:toggle'], keys: ['R'], label: '切换选中房间录制' },
  { ids: ['batch:record'], keys: ['Ctrl', 'R'], label: '批量开始录制' },
  { ids: ['batch:stop'], keys: ['Ctrl', 'Shift', 'R'], label: '批量停止录制' },
  { ids: ['select:all'], keys: ['Ctrl', 'Shift', 'A'], label: '全选房间' },
  { ids: ['mute:toggle'], keys: ['M'], label: '静音 / 取消静音' },
  { ids: ['fullscreen'], keys: ['F'], label: '放大 / 收起预览' },
  { ids: ['toggle:clips'], keys: ['C'], label: '展开 / 收起切片面板' },
  { ids: ['export:clip'], keys: ['Ctrl', 'E'], label: '导出所选（无勾选时导第一条可导）' },
  { ids: ['cancel:refine'], keys: ['Esc'], label: '退出精修 / 收起放大' },
  { ids: ['undo:mark'], keys: ['Ctrl', 'Z'], label: '撤销上一步入出点操作（打标 / 微调 / 拖标 / 删标）' },
]

/** 鼠标操作约定（与快捷键同处维护，避免只在代码里存在） */
export const MOUSE_DOCS: { label: string; desc: string }[] = [
  { label: '时间线上按住拖动', desc: '移动播放头（松手才正式 seek）' },
  { label: '拖动入/出点标记', desc: '微调边界，自动磁吸到已有区间端点与整秒' },
  { label: '右键入/出点标记', desc: '删除该标记' },
  { label: '按住 Alt 拖动', desc: '临时关闭磁吸，精确定位到任意秒' },
  { label: 'Ctrl + 滚轮（时间线上）', desc: '缩放时间线' },
  { label: 'Ctrl + 点击房间卡片', desc: '加选 / 减选；Shift + 点击连续选区' },
]
