import React, { useRef, useEffect, useState } from 'react'

export interface HexParticleProgressProps {
  /** 进度百分比 0 - 100 */
  percent: number
  /** 高度（默认 32px） */
  height?: number
  /** 自定义行数（默认 5 行） */
  rows?: number
  /** 粒子半径（默认 2.4px） */
  dotRadius?: number
  /** 左侧文本/标签 */
  leftLabel?: React.ReactNode
  /** 次要信息文本 */
  subLabel?: React.ReactNode
  /** 右侧百分比/状态文本（不传则默认展示 `${Math.round(percent)}%`） */
  rightLabel?: React.ReactNode
  /** 色调：brand(默认青色) / warning(琥珀) / error(红) / success(绿) */
  tone?: 'brand' | 'warning' | 'error' | 'success'
  /** 是否强制指定暗色模式，未传时自动读取 documentElement.classList.contains('dark') */
  isDark?: boolean
  className?: string
  style?: React.CSSProperties
  title?: string
}

const TONE_COLORS = {
  brand: {
    litCore: '#5fc3bf',
    litOuter: '#3ea8a4',
    spark: '#ffffff',
    glow: 'rgba(62, 168, 164, 0.45)',
    unlitDark: 'rgba(255, 255, 255, 0.07)',
    unlitLight: 'rgba(49, 179, 174, 0.14)',
    borderDark: 'rgba(255, 255, 255, 0.08)',
    borderLight: 'rgba(0, 0, 0, 0.08)',
    bgDark: '#0e1114',
    bgLight: 'rgba(49, 179, 174, 0.04)',
  },
  warning: {
    litCore: '#e0a96d',
    litOuter: '#c88a48',
    spark: '#ffffff',
    glow: 'rgba(200, 138, 72, 0.4)',
    unlitDark: 'rgba(255, 255, 255, 0.07)',
    unlitLight: 'rgba(200, 138, 72, 0.14)',
    borderDark: 'rgba(255, 255, 255, 0.08)',
    borderLight: 'rgba(0, 0, 0, 0.08)',
    bgDark: '#12100d',
    bgLight: 'rgba(200, 138, 72, 0.04)',
  },
  error: {
    litCore: '#e27b7b',
    litOuter: '#cf5a5a',
    spark: '#ffffff',
    glow: 'rgba(207, 90, 90, 0.4)',
    unlitDark: 'rgba(255, 255, 255, 0.07)',
    unlitLight: 'rgba(207, 90, 90, 0.14)',
    borderDark: 'rgba(255, 255, 255, 0.08)',
    borderLight: 'rgba(0, 0, 0, 0.08)',
    bgDark: '#140e0e',
    bgLight: 'rgba(207, 90, 90, 0.04)',
  },
  success: {
    litCore: '#6bc98e',
    litOuter: '#45ab6c',
    spark: '#ffffff',
    glow: 'rgba(69, 171, 108, 0.4)',
    unlitDark: 'rgba(255, 255, 255, 0.07)',
    unlitLight: 'rgba(69, 171, 108, 0.14)',
    borderDark: 'rgba(255, 255, 255, 0.08)',
    borderLight: 'rgba(0, 0, 0, 0.08)',
    bgDark: '#0e1410',
    bgLight: 'rgba(69, 171, 108, 0.04)',
  },
}

export function HexParticleProgress({
  percent,
  height = 24,
  rows = 3,
  dotRadius = 2.2,
  leftLabel,
  subLabel,
  rightLabel,
  tone = 'brand',
  isDark: forceDark,
  className = '',
  style,
  title,
}: HexParticleProgressProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [isDarkState, setIsDarkState] = useState(
    typeof document !== 'undefined' ? document.documentElement.classList.contains('dark') : true,
  )

  // 监听系统主题变化
  useEffect(() => {
    if (forceDark !== undefined) return
    const checkDark = () => {
      setIsDarkState(document.documentElement.classList.contains('dark'))
    }
    checkDark()
    const observer = new MutationObserver(checkDark)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [forceDark])

  const isDark = forceDark !== undefined ? forceDark : isDarkState
  const colors = TONE_COLORS[tone] || TONE_COLORS.brand

  // 动画状态：粒子能量呼吸与前沿电荷流动，粒子按位置一个一个点亮
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    let animId: number
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const clampedPercent = Math.max(0, Math.min(100, percent))

    const render = (timestamp: number) => {
      const rect = container.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      const width = Math.max(1, rect.width)
      const canvasHeight = height

      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(canvasHeight * dpr)) {
        canvas.width = Math.round(width * dpr)
        canvas.height = Math.round(canvasHeight * dpr)
      }

      ctx.save()
      ctx.scale(dpr, dpr)
      ctx.clearRect(0, 0, width, canvasHeight)

      // 布局计算：六边形/蜂窝交错阵列
      const rowGap = canvasHeight / (rows + 1)
      const colGap = Math.max(11, rowGap * 1.85)
      const cols = Math.ceil(width / colGap) + 1
      const paddingX = 6

      // 前沿位置（0.0 到 1.0）
      const normProgress = clampedPercent / 100

      // 遍历所有蜂窝粒子并绘制
      for (let c = 0; c < cols; c++) {
        for (let r = 0; r < rows; r++) {
          // 交替行水平偏移半个单元（六边形交错排列）
          const offsetX = (r % 2) * (colGap * 0.5)
          const x = paddingX + c * colGap + offsetX
          const y = (r + 1) * rowGap

          if (x < -dotRadius || x > width + dotRadius) continue

          // 单个粒子的离散点亮阈值（蜂窝交错自然推进）
          const particleThreshold = (c + (r % 2) * 0.5 + (r * 0.04)) / cols
          const isLit = normProgress > 0 && particleThreshold <= normProgress

          ctx.beginPath()
          ctx.arc(x, y, dotRadius, 0, Math.PI * 2)

          if (isLit) {
            const distToFront = normProgress - particleThreshold
            const isFrontier = distToFront >= 0 && distToFront < (2.4 / cols)

            if (isFrontier) {
              // 充电前沿粒子：纯白小电核 + 克制微光晕
              const pulse = 0.5 + 0.5 * Math.sin(timestamp * 0.009 + r * 1.3)
              ctx.fillStyle = colors.spark
              ctx.shadowColor = colors.glow
              ctx.shadowBlur = 5 + pulse * 3
              ctx.fill()
            } else {
              // 饱满已充能粒子：有机呼吸感
              const wave = 0.92 + 0.08 * Math.sin(timestamp * 0.003 + c * 0.4 + r * 0.7)
              ctx.fillStyle = colors.litOuter
              ctx.globalAlpha = wave
              ctx.shadowColor = colors.glow
              ctx.shadowBlur = 2
              ctx.fill()
              ctx.globalAlpha = 1.0
            }
          } else {
            // 未激活蜂窝待命单元：素雅微孔
            ctx.fillStyle = isDark ? colors.unlitDark : colors.unlitLight
            ctx.shadowBlur = 0
            ctx.fill()
          }
        }
      }

      ctx.restore()
      animId = requestAnimationFrame(render)
    }

    animId = requestAnimationFrame(render)
    return () => cancelAnimationFrame(animId)
  }, [percent, height, rows, dotRadius, isDark, colors])

  const hasOverlayText = Boolean(leftLabel || subLabel || rightLabel !== undefined)

  return (
    <div
      ref={containerRef}
      className={`hex-particle-progress ${className}`}
      title={title}
      style={{
        position: 'relative',
        height,
        backgroundColor: isDark ? colors.bgDark : colors.bgLight,
        border: `1px solid ${isDark ? colors.borderDark : colors.borderLight}`,
        borderRadius: 6,
        overflow: 'hidden',
        display: 'flex',
        alignItems: 'center',
        boxSizing: 'border-box',
        transition: 'background-color 0.2s ease, border-color 0.2s ease',
        ...style,
      }}
    >
      {/* 逐一点亮蜂窝粒子 Canvas 图层 */}
      <canvas
        ref={canvasRef}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
        }}
      />

      {/* 仅在传入 labels 时呈现浮层 */}
      {hasOverlayText && (
        <div
          style={{
            position: 'relative',
            zIndex: 2,
            width: '100%',
            padding: '0 8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            pointerEvents: 'none',
            userSelect: 'none',
          }}
        >
          {(leftLabel || subLabel) && (
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '1px 6px',
                borderRadius: 4,
                background: isDark ? 'rgba(10, 18, 20, 0.82)' : 'rgba(255, 255, 255, 0.9)',
                border: `1px solid ${isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.08)'}`,
                maxWidth: 'calc(100% - 50px)',
                minWidth: 0,
                overflow: 'hidden',
              }}
            >
              {leftLabel && (
                <span style={{ fontWeight: 600, fontSize: 11, color: isDark ? '#f0f2f5' : '#1a1d23', whiteSpace: 'nowrap' }}>
                  {leftLabel}
                </span>
              )}
              {subLabel && (
                <span style={{ color: isDark ? '#9ba1a6' : '#5f656b', fontFamily: 'var(--font-mono)', fontSize: 11, whiteSpace: 'nowrap' }}>
                  {subLabel}
                </span>
              )}
            </div>
          )}

          {rightLabel && (
            <div
              style={{
                flexShrink: 0,
                marginLeft: 'auto',
                padding: '1px 6px',
                borderRadius: 4,
                background: isDark ? 'rgba(10, 18, 20, 0.82)' : 'rgba(255, 255, 255, 0.9)',
                border: `1px solid ${isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.08)'}`,
              }}
            >
              <span style={{ fontWeight: 700, fontFamily: 'var(--font-mono)', fontSize: 11, color: isDark ? '#5fc3bf' : '#279e99' }}>
                {rightLabel}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default HexParticleProgress
