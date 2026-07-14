import { useState, useRef, useEffect, useCallback, memo } from 'react'
import { Button, Tooltip } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

/* ── Types ── */

interface EnteringParticle {
  id: number
  edgeX: number
  edgeY: number
  targetX: number
  targetY: number
  size: number
  color: string
  opacity: number
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

/* ── Constants ── */

const PROGRESS_MS = 800
const MAX_PARTICLES = 30
const SHATTER_COUNT_MIN = 18
const SHATTER_COUNT_MAX = 28
const COLORS = [
  'hsla(207, 100%, 60%,',
  'hsla(210, 100%, 55%,',
  'hsla(220, 100%, 65%,',
  'hsla(200, 100%, 70%,',
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
      40% { background: rgba(255,255,255,0.6); }
      100% { background: rgba(255,255,255,0); }
    }
  `
  document.head.appendChild(s)
}

/* ── Helpers ── */

function randomColor(): string {
  return COLORS[Math.floor(Math.random() * COLORS.length)]
}

function randomShatterColor(): string {
  const h = 207 + Math.floor(Math.random() * 15)
  const s = 80 + Math.floor(Math.random() * 20)
  const l = 40 + Math.floor(Math.random() * 25)
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

function spawnEnteringParticle(id: number, w: number, h: number): EnteringParticle {
  const size = 2 + Math.random() * 3.5
  const pad = 3
  const edge = Math.floor(Math.random() * 4)
  let edgeX: number, edgeY: number
  switch (edge) {
    case 0: edgeX = pad + Math.random() * (w - pad * 2); edgeY = -size - 2; break
    case 1: edgeX = w + size + 2; edgeY = pad + Math.random() * (h - pad * 2); break
    case 2: edgeX = pad + Math.random() * (w - pad * 2); edgeY = h + size + 2; break
    default: edgeX = -size - 2; edgeY = pad + Math.random() * (h - pad * 2); break
  }
  return {
    id,
    edgeX, edgeY,
    targetX: pad + Math.random() * (w - pad * 2),
    targetY: pad + Math.random() * (h - pad * 2),
    size,
    color: randomColor(),
    opacity: 0.5 + Math.random() * 0.5,
  }
}

/* ── Sub component: entering particle ── */

const EnteringParticleDiv = memo(function EnteringParticleDiv({
  particle,
}: {
  particle: EnteringParticle
}) {
  const divRef = useRef<HTMLDivElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const el = divRef.current
    if (!el) return

    // 放在边缘（无动画）
    el.style.transition = 'none'
    el.style.left = `${particle.edgeX}px`
    el.style.top = `${particle.edgeY}px`
    el.style.opacity = '0'
    el.style.transform = 'scale(0)'
    void el.offsetHeight

    // 飞入目标位
    el.style.transition = 'left 0.45s cubic-bezier(.34,1.56,.64,1), top 0.45s cubic-bezier(.34,1.56,.64,1), transform 0.3s ease-out, opacity 0.2s ease-out'
    el.style.left = `${particle.targetX}px`
    el.style.top = `${particle.targetY}px`
    el.style.transform = 'scale(1)'
    el.style.opacity = `${particle.opacity}`

    // 短暂停留后淡出（融入蓝色填充）
    timerRef.current = setTimeout(() => {
      if (!el) return
      el.style.transition = 'opacity 0.12s ease-out, transform 0.12s ease-out'
      el.style.opacity = '0'
      el.style.transform = 'scale(0.3)'
    }, 150 + Math.random() * 100)

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
        background: `${particle.color} ${particle.opacity})`,
        boxShadow: `0 0 ${particle.size + 2}px ${particle.color} 0.4)`,
        pointerEvents: 'none',
        zIndex: 2,
        willChange: 'left, top, opacity, transform',
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
  tooltip = '点按刷新预览；长按 0.8s 刷新全部（将停止录制，需确认）',
}: RefreshButtonProps) {
  // ── Render state ──
  const [fillProgress, setFillProgress] = useState(0)
  const [enteringParticles, setEnteringParticles] = useState<EnteringParticle[]>([])
  const [shatterParticles, setShatterParticles] = useState<ShatterParticle[]>([])
  const [showFlash, setShowFlash] = useState(false)

  // ── Refs (for event handlers to read latest values) ──
  const buttonRef = useRef<HTMLButtonElement>(null)
  const fillProgressRef = useRef(0)
  const phaseRef = useRef<'idle' | 'triggered'>('idle')
  const particleIdRef = useRef(0)
  const progressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const spawnTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const shatterPolygonRef = useRef<string>('inset(0)')

  // Inject CSS once
  useEffect(() => { injectCss() }, [])

  // ── Cleanup timers ──
  const cleanupTimers = useCallback(() => {
    if (progressTimerRef.current) { clearTimeout(progressTimerRef.current); progressTimerRef.current = null }
    if (spawnTimerRef.current) { clearTimeout(spawnTimerRef.current); spawnTimerRef.current = null }
    if (flashTimerRef.current) { clearTimeout(flashTimerRef.current); flashTimerRef.current = null }
  }, [])

  // ── Trigger shatter (solid blue → particles fly outward) ──
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

    // After 350ms, clean up everything
    setTimeout(() => {
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

    phaseRef.current = 'triggered' // prevent double entry
    fillProgressRef.current = 0
    setFillProgress(0)
    setEnteringParticles([])
    setShatterParticles([])

    // Spawn initial burst
    const rect = buttonRef.current?.getBoundingClientRect() ?? { width: 72, height: 24 }
    const initial: EnteringParticle[] = []
    for (let i = 0; i < 8; i++) {
      initial.push(spawnEnteringParticle(particleIdRef.current++, rect.width, rect.height))
    }
    setEnteringParticles(initial)

    // Start progress timer (800ms → triggered)
    progressTimerRef.current = setTimeout(() => {
      // Long press triggered!
      phaseRef.current = 'triggered'
      fillProgressRef.current = 100
      setFillProgress(100)
      setShowFlash(true)

      // Flash 200ms then shatter + callback
      flashTimerRef.current = setTimeout(() => {
        setShowFlash(false)
        setEnteringParticles([])
        triggerShatter()
        onLongPress()
      }, 200)
    }, PROGRESS_MS)

    // Spawn particles & increase fill
    const tick = () => {
      if (progressTimerRef.current === null && flashTimerRef.current === null) return

      const current = fillProgressRef.current
      if (current >= 100) return

      const next = Math.min(100, current + 6.25)
      fillProgressRef.current = next
      setFillProgress(next)

      // Spawn new particles based on progress
      const count = next < 30 ? 1 : next < 60 ? 2 : 3
      setEnteringParticles(prev => {
        if (prev.length >= MAX_PARTICLES) return prev
        const rect = buttonRef.current?.getBoundingClientRect() ?? { width: 72, height: 24 }
        const news: EnteringParticle[] = []
        for (let i = 0; i < count; i++) {
          if (prev.length + news.length >= MAX_PARTICLES) break
          news.push(spawnEnteringParticle(particleIdRef.current++, rect.width, rect.height))
        }
        return [...prev, ...news]
      })

      spawnTimerRef.current = setTimeout(tick, 50)
    }
    spawnTimerRef.current = setTimeout(tick, 50)
  }, [disabled, triggerShatter, onLongPress])

  // ── Handle mouse up ──
  const handleMouseUp = useCallback(() => {
    cleanupTimers()

    if (phaseRef.current === 'triggered' && fillProgressRef.current >= 100) {
      // Long press already handled, shatter already triggered
      return
    }

    // Short click: shatter + callback
    const progress = fillProgressRef.current
    if (progress > 0) {
      setEnteringParticles([])
      triggerShatter()
    }
    fillProgressRef.current = 0
    setFillProgress(0)
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

    // Cancel: shatter without callback
    const progress = fillProgressRef.current
    if (progress > 0) {
      setEnteringParticles([])
      triggerShatter()
    }
    fillProgressRef.current = 0
    setFillProgress(0)
    phaseRef.current = 'idle'
  }, [cleanupTimers, triggerShatter])

  // ── Unmount cleanup ──
  useEffect(() => () => cleanupTimers(), [cleanupTimers])

  // ── Determine button text color based on fill depth ──
  const textColor = fillProgress > 55 ? '#fff' : undefined

  // ── Shatter clip-path (only applied when shattering) ──
  const clipPath = shatterParticles.length > 0 ? shatterPolygonRef.current : 'inset(0)'
  const isShattering = shatterParticles.length > 0

  return (
    <Tooltip title={disabled ? '' : tooltip}>
      <Button
        ref={buttonRef}
        size="small"
        icon={<ReloadOutlined />}
        disabled={disabled}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        style={{
          position: 'relative',
          overflow: 'hidden',
          userSelect: 'none',
        }}
      >
        {/* ① Blue fill layer */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background: 'linear-gradient(135deg, #31B3AE, #4DC4BF)',
            opacity: fillProgress / 100,
            borderRadius: 'inherit',
            pointerEvents: 'none',
            zIndex: 1,
            clipPath,
            transition: isShattering
              ? 'clip-path 0.35s ease-out, opacity 0.3s ease-out'
              : 'opacity 0.05s linear',
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

        {/* ③ Entering particles (briefly visible then dissolve into fill) */}
        {enteringParticles.map(p => (
          <EnteringParticleDiv key={p.id} particle={p} />
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
          刷新
        </span>
      </Button>
    </Tooltip>
  )
})

export default RefreshButton