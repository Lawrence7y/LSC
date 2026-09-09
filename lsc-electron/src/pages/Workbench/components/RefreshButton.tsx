import { useState, useRef, useEffect, useCallback, memo } from 'react'
import { Button, Tooltip } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useI18n } from '@/i18n'

interface RefreshButtonProps {
  onShortClick: () => void
  onLongPress: () => void
  disabled?: boolean
  tooltip?: string
}

const PROGRESS_MS = 800

let cssInjected = false
function injectCss() {
  if (cssInjected) return
  cssInjected = true
  const s = document.createElement('style')
  s.id = 'refresh-btn-hex-v1'
  s.textContent = `
    @keyframes rfbHexFlash {
      0% { opacity: 0; }
      40% { opacity: 0.85; }
      100% { opacity: 0; }
    }
  `
  document.head.appendChild(s)
}

export const RefreshButton = memo(function RefreshButton({
  onShortClick,
  onLongPress,
  disabled = false,
  tooltip,
}: RefreshButtonProps) {
  const { t } = useI18n()
  const resolvedTooltip = tooltip ?? t('点按刷新预览；长按 0.8s 刷新全部（将停止录制，需确认）')

  const [fillProgress, setFillProgress] = useState(0)
  const [isFadingOut, setIsFadingOut] = useState(false)
  const [showFlash, setShowFlash] = useState(false)

  const buttonRef = useRef<HTMLButtonElement>(null)
  const fillProgressRef = useRef(0)
  const phaseRef = useRef<'idle' | 'pressing' | 'triggered'>('idle')
  const progressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const tickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const longPressFiredRef = useRef(false)
  const mountedRef = useRef(true)

  useEffect(() => {
    injectCss()
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const cleanupTimers = useCallback(() => {
    if (progressTimerRef.current) {
      clearTimeout(progressTimerRef.current)
      progressTimerRef.current = null
    }
    if (tickTimerRef.current) {
      clearTimeout(tickTimerRef.current)
      tickTimerRef.current = null
    }
    if (flashTimerRef.current) {
      clearTimeout(flashTimerRef.current)
      flashTimerRef.current = null
    }
  }, [])

  // 逐渐褪去进度并重置
  const fadeOutAndReset = useCallback((durationMs = 650) => {
    if (!mountedRef.current) return
    setIsFadingOut(true)
    if (fadeTimerRef.current) clearTimeout(fadeTimerRef.current)
    fadeTimerRef.current = setTimeout(() => {
      if (!mountedRef.current) return
      setFillProgress(0)
      fillProgressRef.current = 0
      setIsFadingOut(false)
      phaseRef.current = 'idle'
      fadeTimerRef.current = null
    }, durationMs)
  }, [])

  const handleMouseDown = useCallback(() => {
    if (disabled) return
    if (phaseRef.current !== 'idle') return

    longPressFiredRef.current = false
    phaseRef.current = 'pressing'
    fillProgressRef.current = 0
    setFillProgress(0)
    setIsFadingOut(false)
    if (fadeTimerRef.current) {
      clearTimeout(fadeTimerRef.current)
      fadeTimerRef.current = null
    }

    const startTime = Date.now()

    // 800ms 达到长按阈值触发
    progressTimerRef.current = setTimeout(() => {
      if (!mountedRef.current) return
      phaseRef.current = 'triggered'
      fillProgressRef.current = 100
      setFillProgress(100)
      setShowFlash(true)
      longPressFiredRef.current = true

      // 闪光 150ms 结束
      flashTimerRef.current = setTimeout(() => {
        if (!mountedRef.current) return
        setShowFlash(false)
        // 触发外部二次确认弹窗
        onLongPress()
        // 长按后进度逐渐褪去（650ms 优雅淡出）
        fadeOutAndReset(650)
      }, 150)
    }, PROGRESS_MS)

    // 进度动画更新（每 30ms 刷新一次）
    const tick = () => {
      if (phaseRef.current !== 'pressing') return
      const elapsed = Date.now() - startTime
      const progress = Math.min(99, (elapsed / PROGRESS_MS) * 100)
      fillProgressRef.current = progress
      setFillProgress(progress)

      if (progress < 99) {
        tickTimerRef.current = setTimeout(tick, 30)
      }
    }
    tickTimerRef.current = setTimeout(tick, 30)
  }, [disabled, onLongPress, fadeOutAndReset])

  const handleMouseUp = useCallback(() => {
    cleanupTimers()

    // 若已经触发了长按
    if (phaseRef.current === 'triggered' || fillProgressRef.current >= 100) {
      return
    }

    if (longPressFiredRef.current) {
      longPressFiredRef.current = false
      return
    }

    // 短按
    const progress = fillProgressRef.current
    if (progress > 0) {
      fadeOutAndReset(250)
    } else {
      phaseRef.current = 'idle'
    }
    onShortClick()
  }, [cleanupTimers, fadeOutAndReset, onShortClick])

  const handleMouseLeave = useCallback(() => {
    cleanupTimers()

    if (phaseRef.current === 'triggered' || fillProgressRef.current >= 100) {
      return
    }

    if (longPressFiredRef.current) {
      longPressFiredRef.current = false
      return
    }

    // 取消长按：平滑淡出并复位
    if (fillProgressRef.current > 0) {
      fadeOutAndReset(250)
    } else {
      phaseRef.current = 'idle'
    }
  }, [cleanupTimers, fadeOutAndReset])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (disabled) return
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        onShortClick()
      }
    },
    [disabled, onShortClick],
  )

  useEffect(() => {
    return () => {
      cleanupTimers()
      if (fadeTimerRef.current) clearTimeout(fadeTimerRef.current)
    }
  }, [cleanupTimers])

  const [isDark, setIsDark] = useState(
    typeof document !== 'undefined' ? document.documentElement.classList.contains('dark') : true,
  )
  useEffect(() => {
    const checkDark = () => setIsDark(document.documentElement.classList.contains('dark'))
    checkDark()
    const observer = new MutationObserver(checkDark)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  const isHolding = fillProgress > 0

  return (
    <Tooltip title={disabled ? t('刷新暂不可用：请等待当前刷新完成') : resolvedTooltip}>
      <span style={{ display: 'inline-flex' }}>
      <Button
        ref={buttonRef}
        size="middle"
        className="workbench-toolbar__refresh"
        disabled={disabled}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        onKeyDown={handleKeyDown}
        style={{
          position: 'relative',
          overflow: 'hidden',
          userSelect: 'none',
          backgroundColor: isHolding ? (isDark ? '#0a1012' : 'rgba(49, 179, 174, 0.08)') : undefined,
          borderColor: isHolding ? (isDark ? 'rgba(49, 179, 174, 0.45)' : 'var(--brand-500)') : undefined,
          transition: 'border-color 0.2s ease, background-color 0.2s ease',
        }}
      >
        {/* ① 底层蜂窝点阵槽（按压时浮现，浅色/暗色自适应） */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            opacity: isHolding ? (isFadingOut ? 0 : 1) : 0,
            backgroundColor: isDark ? '#0a1012' : 'rgba(49, 179, 174, 0.06)',
            backgroundImage: isDark
              ? `
                radial-gradient(circle, rgba(49, 179, 174, 0.16) 2px, transparent 2.3px),
                radial-gradient(circle, rgba(49, 179, 174, 0.16) 2px, transparent 2.3px)
              `
              : `
                radial-gradient(circle, rgba(49, 179, 174, 0.22) 2px, transparent 2.3px),
                radial-gradient(circle, rgba(49, 179, 174, 0.22) 2px, transparent 2.3px)
              `,
            backgroundSize: '9px 13px',
            backgroundPosition: '0 0, 4.5px 6.5px',
            pointerEvents: 'none',
            zIndex: 1,
            transition: isFadingOut ? 'opacity 0.65s ease-out' : 'opacity 0.15s ease',
          }}
        />

        {/* ② 动态青色高亮蜂窝填充层（横向推进充电，触发后逐渐褪去） */}
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            bottom: 0,
            width: `${fillProgress}%`,
            opacity: isHolding ? (isFadingOut ? 0 : 1) : 0,
            backgroundColor: isDark ? 'rgba(28, 125, 122, 0.45)' : 'rgba(49, 179, 174, 0.3)',
            backgroundImage: isDark
              ? `
                radial-gradient(circle, #66cfcf 2.2px, transparent 2.4px),
                radial-gradient(circle, #31b3ae 2.2px, transparent 2.4px)
              `
              : `
                radial-gradient(circle, #279e99 2.2px, transparent 2.4px),
                radial-gradient(circle, #31b3ae 2.2px, transparent 2.4px)
              `,
            backgroundSize: '9px 13px',
            backgroundPosition: '0 0, 4.5px 6.5px',
            borderRight: fillProgress > 0 && fillProgress < 100 ? '2px solid var(--brand-500)' : undefined,
            boxShadow:
              fillProgress > 0
                ? isDark
                  ? '3px 0 12px rgba(77, 196, 191, 0.9), 0 0 16px rgba(49, 179, 174, 0.4)'
                  : '3px 0 8px rgba(49, 179, 174, 0.6), 0 0 10px rgba(49, 179, 174, 0.25)'
                : undefined,
            pointerEvents: 'none',
            zIndex: 2,
            transition: isFadingOut
              ? 'opacity 0.65s cubic-bezier(0.4, 0, 0.2, 1)'
              : 'width 0.04s linear, opacity 0.15s ease',
          }}
        />

        {/* ③ 100% 达成时的青白柔光闪烁 */}
        {showFlash && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background: 'radial-gradient(ellipse at center, rgba(255,255,255,0.7) 0%, rgba(77,196,191,0.5) 50%, rgba(49,179,174,0) 100%)',
              animation: 'rfbHexFlash 0.15s ease-out forwards',
              pointerEvents: 'none',
              zIndex: 3,
            }}
          />
        )}

        {/* ④ 前景图标（长按过程随蜂窝进度平滑旋转 360°）与文字 */}
        <span
          style={{
            position: 'relative',
            zIndex: 4,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            color: fillProgress > 25 ? (isDark ? '#ffffff' : '#0a3d39') : undefined,
            textShadow: fillProgress > 25
              ? isDark
                ? '0 1px 3px rgba(0,0,0,0.95), 0 0 6px rgba(0,0,0,0.9)'
                : '0 1px 2px rgba(255,255,255,0.95)'
              : undefined,
            transition: isFadingOut ? 'color 0.65s ease, text-shadow 0.65s ease' : 'color 0.15s ease',
          }}
        >
          <ReloadOutlined
            style={{
              transform: `rotate(${fillProgress * 3.6}deg)`,
              transition: isHolding && !isFadingOut ? 'none' : 'transform 0.3s ease',
            }}
          />
          <span>{t('刷新')}</span>
        </span>
      </Button>
      </span>
    </Tooltip>
  )
})

export default RefreshButton
