import { useState, useRef, useEffect, useCallback, memo } from 'react'
import { Button, Tooltip } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useI18n } from '@/i18n'

/* ── Types ── */

interface BubbleParticle {
  id: number
  x: number
  startY: number
  riseY: number
  size: number
  opacity: number
  delay: number
}

interface ShatterParticle {
  id: number
  x: number
  y: number
  scatterX: number
  scatterY: number
  size: number
  rotation: number
  color: string
}

interface RefreshButtonProps {
  onShortClick: () => void
  onLongPress: () => void
  disabled?: boolean
  tooltip?: string
}

/* ── Constants（主题色：accent-primary 青绿 #4dc4bf / #31b3ae） ── */

const PROGRESS_MS = 800
const MAX_BUBBLES = 22
const SHATTER_COUNT_MIN = 18
const SHATTER_COUNT_MAX = 26
const FILL_TOP = '#5ad8c7'     // 填充顶（亮青绿）
const FILL_BOTTOM = '#2bb5a8'  // 填充底（主题 accent 深一档）
const BUBBLE_COLORS = [
  'hsla(172, 62%, 72%,',
  'hsla(168, 70%, 66%,',
  'hsla(176, 78%, 74%,',
]

/* ── CSS ── */

let cssInjected = false
function injectCss() {
  if (cssInjected) return
  cssInjected = true
  const s = document.createElement('style')
  s.id = 'refresh-btn-v2'
  s.textContent = `
    @keyframes rfbFlash {
      0% { background: rgba(255,255,255,0); }
      40% { background: rgba(255,255,255,0.65); }
      100% { background: rgba(255,255,255,0); }
    }
  `
  document.head.appendChild(s)
}

/* ── Helpers ── */

function randomBubbleColor(): string {
  return BUBBLE_COLORS[Math.floor(Math.random() * BUBBLE_COLORS.length)]
}

function randomShatterColor(): string {
  // 主题青绿色系
  const h = 168 + Math.floor(Math.random() * 12)
  const s = 65 + Math.floor(Math.random() * 25)
  const l = 42 + Math.floor(Math.random() * 26)
  return `hsl(${h}, ${s}%, ${l}%)`
}

function generateShatterPolygon(): string {
  const n = 10 + Math.floor(Math.random() * 6)
  const pts: string[] = []
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + (Math.random() - 0.5) * 0.35
    const r = 30 + Math.random() * 85
    const x = 50 + Math.cos(a) * r / 2
    const y = 50 + Math.sin(a) * r / 2
    pts.push(`${x.toFixed(1)}% ${y.toFixed(1)}%`)
  }
  return `polygon(${pts.join(', ')})`
}

/** 在填充液面处生成上浮气泡（长按进度视觉：按钮从底部变绿，光点随液面上升） */
function spawnBubble(id: number, w: number, h: number, liquidPct: number): BubbleParticle {
  const size = 2 + Math.random() * 3
  const pad = 4
  const liquidY = h - (h * Math.min(100, liquidPct)) / 100
  return {
    id,
    x: pad + Math.random() * (w - pad * 2),
    startY: Math.max(0, liquidY - 2),
    riseY: Math.max(0, liquidY - 2 - (14 + Math.random() * 30)),
    size,
    opacity: 0.45 + Math.random() * 0.45,
    delay: Math.random() * 60,
  }
}

/* ── Sub component: rising bubble ── */

const BubbleParticleDiv = memo(function BubbleParticleDiv({
  particle,
}: {
  particle: BubbleParticle
}) {
  const divRef = useRef<HTMLDivElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const el = divRef.current
    if (!el) return

    el.style.transition = 'none'
    el.style.left = `${particle.x}px`
    el.style.top = `${particle.startY}px`
    el.style.opacity = '0'
    void el.offsetHeight

    const delay = particle.delay
    timerRef.current = setTimeout(() => {
      if (!el) return
      el.style.transition = `top 0.45s ease-out, opacity 0.45s ease-out`
      el.style.top = `${particle.riseY}px`
      el.style.opacity = `${particle.opacity}`
      // 上升结束后淡出
      setTimeout(() => {
        if (!el) return
        el.style.transition = 'opacity 0.15s ease-out'
        el.style.opacity = '0'
      }, 380)
    }, delay)

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [particle])

  return (
    <div
      ref={divRef}
      style={{
        position: 'absolute',
        width: particle.size,
        height: particle.size,
        borderRadius: '50%',
        background: `${randomBubbleColor()} ${particle.opacity})`,
        boxShadow: `0 0 ${particle.size + 2}px ${randomBubbleColor()} 0.5)`,
        pointerEvents: 'none',
        zIndex: 2,
        willChange: 'top, opacity',
      }}
    />
  )
})

/* ── Sub component: shatter particle ── */

const ShatterParticleDiv = memo(function ShatterParticleDiv({
  particle,
}: {
  particle: ShatterParticle
}) {
  const divRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = divRef.current
    if (!el) return

    // 立即触发向外飞散
    el.style.transition = 'none'
    el.style.transform = 'translate(0, 0) rotate(0deg)'
    el.style.opacity = '0.9'
    void el.offsetHeight

    el.style.transition = 'transform 0.35s cubic-bezier(.25,.46,.45,.94), opacity 0.3s ease-out'
    el.style.transform = `translate(${particle.scatterX}px, ${particle.scatterY}px) rotate(${particle.rotation}deg)`
    el.style.opacity = '0'
  }, [particle])

  return (
    <div
      ref={divRef}
      style={{
        position: 'absolute',
        left: particle.x,
        top: particle.y,
        width: particle.size,
        height: particle.size,
        borderRadius: '50%',
        background: particle.color,
        boxShadow: `0 0 ${particle.size}px ${particle.color}`,
        pointerEvents: 'none',
        zIndex: 3,
        willChange: 'transform, opacity',
      }}
    />
  )
})

/* ── Main component ── */

export const RefreshButton = memo(function RefreshButton({
  onShortClick,
  onLongPress,
  disabled = false,
  tooltip,
}: RefreshButtonProps) {
  const { t } = useI18n()
  const resolvedTooltip = tooltip ?? t('点按刷新预览；长按 0.8s 刷新全部（将停止录制，需确认）')
  // ── Render state ──
  // 长按进度 = 按钮从底部向上整体填充主题青绿（按键全绿），
  // 液面处生成上浮光点；完成时白光一闪 + 主题色碎片向外爆发。
  const [fillProgress, setFillProgress] = useState(0)
  const [bubbles, setBubbles] = useState<BubbleParticle[]>([])
  const [shatterParticles, setShatterParticles] = useState<ShatterParticle[]>([])
  const [showFlash, setShowFlash] = useState(false)

  // ── Refs (for event handlers to read latest values) ──
  const buttonRef = useRef<HTMLButtonElement>(null)
  const fillProgressRef = useRef(0)
  const phaseRef = useRef<'idle' | 'triggered'>('idle')
  const particleIdRef = useRef(0)
  const progressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const bubbleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const shatterPolygonRef = useRef<string>('inset(0)')
  const shatterTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 长按已触发标记：粒子动画结束后 phaseRef 复位，但本次按压尚未松手，
  // 用该标记吞掉后续 mouseup/mouseleave，防止长按后松手误触发短按。
  const longPressFiredRef = useRef(false)
  const mountedRef = useRef(true)

  // Inject CSS once
  useEffect(() => { injectCss() }, [])

  // ── Mounted guard ──
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  // ── Cleanup timers ──
  // ⚠️ 不要清理 shatterTimerRef：粒子动画的清理定时器属于「已触发」展示阶段。
  // 若被 mouseup/mouseleave 清掉，shatter 粒子将永不消失，且 phaseRef 卡在
  // 'triggered'，后续所有 mousedown 都会被拦截（按钮「失灵」）。
  const cleanupTimers = useCallback(() => {
    if (progressTimerRef.current) { clearTimeout(progressTimerRef.current); progressTimerRef.current = null }
    if (bubbleTimerRef.current) { clearTimeout(bubbleTimerRef.current); bubbleTimerRef.current = null }
    if (flashTimerRef.current) { clearTimeout(flashTimerRef.current); flashTimerRef.current = null }
  }, [])

  // ── Trigger shatter (solid green → particles fly outward) ──
  const triggerShatter = useCallback(() => {
    const rect = buttonRef.current?.getBoundingClientRect() ?? { width: 72, height: 24 }

    // Generate shatter polygon
    shatterPolygonRef.current = generateShatterPolygon()

    // Generate shatter particles
    const count = SHATTER_COUNT_MIN + Math.floor(Math.random() * (SHATTER_COUNT_MAX - SHATTER_COUNT_MIN))
    const particles: ShatterParticle[] = []
    for (let i = 0; i < count; i++) {
      const pid = particleIdRef.current++
      const size = 3 + Math.random() * 5
      const spread = 1.5 + Math.random() * 1.0
      const angle = Math.random() * Math.PI * 2
      particles.push({
        id: pid,
        x: 3 + Math.random() * (rect.width - 6),
        y: 3 + Math.random() * (rect.height - 6),
        scatterX: Math.cos(angle) * rect.width * spread,
        scatterY: Math.sin(angle) * rect.height * spread,
        size,
        rotation: Math.random() * 720,
        color: randomShatterColor(),
      })
    }
    setShatterParticles(particles)
    setBubbles([])

    // After 380ms, clean up everything
    shatterTimerRef.current = setTimeout(() => {
      shatterTimerRef.current = null
      if (!mountedRef.current) return
      setFillProgress(0)
      fillProgressRef.current = 0
      setShatterParticles([])
      phaseRef.current = 'idle'
    }, 380)
  }, [])

  // ── Handle mouse down ──
  const handleMouseDown = useCallback(() => {
    if (disabled) return
    if (phaseRef.current !== 'idle') return

    longPressFiredRef.current = false // 新一轮按压开始，重置长按标记
    phaseRef.current = 'triggered' // prevent double entry
    fillProgressRef.current = 0
    setFillProgress(0)
    setBubbles([])
    setShatterParticles([])

    // Start progress timer (800ms → triggered)
    progressTimerRef.current = setTimeout(() => {
      // Long press triggered!
      phaseRef.current = 'triggered'
      fillProgressRef.current = 100
      setFillProgress(100)
      setShowFlash(true)

      // Flash 200ms then shatter + callback
      flashTimerRef.current = setTimeout(() => {
        if (!mountedRef.current) return
        setShowFlash(false)
        longPressFiredRef.current = true
        triggerShatter()
        onLongPress()
      }, 200)
    }, PROGRESS_MS)

    // 填充进度：按钮从底部向上整体变绿（16 tick × 6.25% = 800ms 满），
    // 液面处持续生成上浮光点
    const tick = () => {
      if (progressTimerRef.current === null && flashTimerRef.current === null) return
      const current = fillProgressRef.current
      if (current >= 100) return
      const next = Math.min(100, current + 6.25)
      fillProgressRef.current = next
      setFillProgress(next)

      // 按进度生成气泡（液面附近）
      const rect = buttonRef.current?.getBoundingClientRect() ?? { width: 72, height: 24 }
      setBubbles(prev => {
        if (prev.length >= MAX_BUBBLES) return prev
        const count = next < 30 ? 1 : next < 70 ? 2 : 2
        const news: BubbleParticle[] = []
        for (let i = 0; i < count; i++) {
          if (prev.length + news.length >= MAX_BUBBLES) break
          news.push(spawnBubble(particleIdRef.current++, rect.width, rect.height, next))
        }
        return [...prev, ...news]
      })

      bubbleTimerRef.current = setTimeout(tick, 50)
    }
    bubbleTimerRef.current = setTimeout(tick, 50)
  }, [disabled, triggerShatter, onLongPress])

  // ── Handle mouse up ──
  const handleMouseUp = useCallback(() => {
    cleanupTimers()

    if (phaseRef.current === 'triggered' && fillProgressRef.current >= 100) {
      // Long press already handled, shatter already triggered
      return
    }

    if (longPressFiredRef.current) {
      // 长按已触发（粒子动画结束、phaseRef 已复位），本次按压的松手
      // 只负责结束按压，不得再触发短按
      longPressFiredRef.current = false
      return
    }

    // Short click: shatter + callback
    const progress = fillProgressRef.current
    if (progress > 0) {
      triggerShatter()
    }
    fillProgressRef.current = 0
    setFillProgress(0)
    setBubbles([])
    phaseRef.current = 'idle'
    onShortClick()
  }, [cleanupTimers, triggerShatter, onShortClick])

  // ── Handle mouse leave ──
  const handleMouseLeave = useCallback(() => {
    cleanupTimers()

    if (phaseRef.current === 'triggered' && fillProgressRef.current >= 100) {
      // Long press already handled
      return
    }

    if (longPressFiredRef.current) {
      // 长按已触发，鼠标移出只结束按压，不触发短按
      longPressFiredRef.current = false
      return
    }

    // Cancel: shatter without callback
    const progress = fillProgressRef.current
    if (progress > 0) {
      triggerShatter()
    }
    fillProgressRef.current = 0
    setFillProgress(0)
    setBubbles([])
    phaseRef.current = 'idle'
  }, [cleanupTimers, triggerShatter])

  // ── Handle keyboard (accessibility) ──
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (disabled) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      // Enter/Space 视为短按
      onShortClick()
    }
  }, [disabled, onShortClick])

  // ── Unmount cleanup ──
  // 卸载时需完整清理（含 shatter 粒子动画定时器，防止卸载后 setState）
  useEffect(() => () => {
    cleanupTimers()
    if (shatterTimerRef.current) { clearTimeout(shatterTimerRef.current); shatterTimerRef.current = null }
  }, [cleanupTimers])

  // ── Determine button text color based on fill depth ──
  const textColor = fillProgress > 55 ? 'var(--overlay-text, #f5f5f7)' : undefined

  // ── Shatter clip-path (only applied when shattering) ──
  const clipPath = shatterParticles.length > 0 ? shatterPolygonRef.current : 'inset(0)'
  const isShattering = shatterParticles.length > 0

  return (
    <Tooltip title={disabled ? '' : resolvedTooltip}>
      <Button
        ref={buttonRef}
        size="middle"
        className="workbench-toolbar__refresh"
        icon={<ReloadOutlined />}
        disabled={disabled}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        onKeyDown={handleKeyDown}
        style={{
          position: 'relative',
          overflow: 'hidden',
          userSelect: 'none',
        }}
      >
        {/* ① Green fill layer：从底部向上整体填充（主题青绿），长按过程按键逐渐全绿 */}
        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            bottom: 0,
            height: `${fillProgress}%`,
            background: `linear-gradient(180deg, ${FILL_TOP}, ${FILL_BOTTOM})`,
            borderRadius: 'inherit',
            pointerEvents: 'none',
            zIndex: 1,
            clipPath,
            transition: isShattering
              ? 'clip-path 0.35s ease-out'
              : 'height 0.05s linear',
          }}
        />

        {/* ② Flash overlay */}
        {showFlash && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              animation: 'rfbFlash 0.2s ease-out forwards',
              borderRadius: 'inherit',
              pointerEvents: 'none',
              zIndex: 2,
            }}
          />
        )}

        {/* ③ Rising bubbles（沿填充液面上浮的主题色光点） */}
        {bubbles.map(p => (
          <BubbleParticleDiv key={p.id} particle={p} />
        ))}

        {/* ④ Shatter particles (fly outward) */}
        {shatterParticles.map(p => (
          <ShatterParticleDiv key={p.id} particle={p} />
        ))}

        {/* ⑤ Button text (always on top) */}
        <span
          style={{
            position: 'relative',
            zIndex: 4,
            color: textColor,
            transition: 'color 0.15s ease',
          }}
        >
          {t('刷新')}
        </span>
      </Button>
    </Tooltip>
  )
})

export default RefreshButton
