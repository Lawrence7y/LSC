import { useEffect, useRef, useCallback, useState } from 'react'
import { LoadingOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { MsePlayer, MsePlayerState } from '@/services/mediaSourcePlayer'
import { drainPendingMseSegments, getMseInitCache } from '@/hooks/useWebSocket'

interface VideoPreviewProps {
  /** Room ID for the video stream */
  roomId: string
  /** Whether MSE preview is active (when false, show placeholder) */
  active: boolean
  /** WebSocket send function for controlling backend */
  send: (type: string, data: any) => void
  /** Called when MSE player is ready */
  onReady?: (player: MsePlayer) => void
  /** Called on error */
  onError?: (error: string) => void
  /** Whether to show controls */
  controls?: boolean
  /** Width/height style override */
  style?: React.CSSProperties
  /** Whether audio is muted (defaults to true for autoplay policy) */
  muted?: boolean
}

export function VideoPreview({
  roomId,
  active,
  send,
  onReady,
  onError,
  controls = true,
  style,
  muted = true,
}: VideoPreviewProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const playerRef = useRef<MsePlayer | null>(null)
  const [state, setState] = useState<MsePlayerState>('idle')
  const [error, setError] = useState<string | null>(null)
  // 超时检测：加载后 15 秒未收到任何帧则报错
  const loadTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hasReceivedDataRef = useRef(false)

  // Refs hold the latest props without being listed as useEffect deps,
  // so changing send/onReady/onError identities do not trigger init→stop→init loops.
  const onReadyRef = useRef(onReady)
  const onErrorRef = useRef(onError)
  const sendRef = useRef(send)
  onReadyRef.current = onReady
  onErrorRef.current = onError
  sendRef.current = send

  // Clean up the local MsePlayer only — never notifies the backend.
  const cleanupPlayer = useCallback(() => {
    if (playerRef.current) {
      playerRef.current.stop()
      playerRef.current = null
    }
    setState('idle')
    setError(null)
  }, [])

  // Clean up locally AND tell the backend to stop previewing.
  // Used by user-initiated stop scenarios (e.g. the "重试" button).
  const stopAndNotify = useCallback(() => {
    cleanupPlayer()
    sendRef.current('enable_preview', {
      room_id: roomId,
      enabled: false,
      mode: 'mse',
    })
  }, [roomId, cleanupPlayer])

  // Feed init segment to player
  const feedInit = useCallback((data: ArrayBuffer) => {
    hasReceivedDataRef.current = true
    playerRef.current?.feedInit(data)
  }, [])

  // Feed media segment to player
  const feedMedia = useCallback((data: ArrayBuffer) => {
    hasReceivedDataRef.current = true
    playerRef.current?.feedMedia(data)
  }, [])

  // Auto-start when active. Deps are limited to [active, roomId]; send/onReady/onError
  // are accessed via refs so their identity changes do not retrigger the effect.
  // Backend enable_preview is managed by the parent — VideoPreview only creates the
  // MsePlayer and registers it; it does NOT send enable_preview on init/cleanup.
  useEffect(() => {
    if (!active) return
    if (!videoRef.current || playerRef.current) return

    hasReceivedDataRef.current = false

    // 超时检测：15 秒内未收到任何帧数据则触发错误
    if (loadTimeoutRef.current) {
      clearTimeout(loadTimeoutRef.current)
    }
    loadTimeoutRef.current = setTimeout(() => {
      if (!hasReceivedDataRef.current && playerRef.current) {
        console.warn(`[VideoPreview] 预览加载超时 (${roomId})`)
        setError('预览加载超时，请检查直播流是否正常')
        // 不再通知后端关闭预览 —— 该房间可能有其他 VideoPreview（全屏）正在使用
        // 后端 streamer 的生命周期应由用户主动点击"停止预览"控制
      }
    }, 15000)

    const player = new MsePlayer({
      videoElement: videoRef.current,
      debug: (import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV ?? false,
      onStateChange: (newState) => {
        setState(newState)
        if (newState === 'playing') {
          setError(null)
          hasReceivedDataRef.current = true
          // 成功播放后清除超时
          if (loadTimeoutRef.current) {
            clearTimeout(loadTimeoutRef.current)
            loadTimeoutRef.current = null
          }
        }
      },
      onError: (msg) => {
        setError(msg)
        hasReceivedDataRef.current = true  // 收到错误也算有回馈
        onErrorRef.current?.(msg)
      },
    })

    playerRef.current = player
    onReadyRef.current?.(player)

    // Start receiving segments (backend enable_preview is owned by the parent)
    player.start(roomId)

    // 优先用缓存的 init 段 feedInit，避免等待 request_mse_init 往返（200-500ms）
    // _mseInitCache 在后端推送 mse_init 时自动缓存，首次挂载无缓存则等 request_mse_init
    const cachedInit = getMseInitCache(roomId)
    if (cachedInit) {
      player.feedInit(cachedInit)
      console.log(`[VideoPreview] Used cached init segment (${roomId})`)
    }

    return () => {
      // Clear timeout on cleanup
      if (loadTimeoutRef.current) {
        clearTimeout(loadTimeoutRef.current)
        loadTimeoutRef.current = null
      }
      // Local cleanup only — do NOT notify backend (parent owns backend state)
      if (playerRef.current) {
        playerRef.current.stop()
        playerRef.current = null
      }
      setState('idle')
      setError(null)
    }
  }, [active, roomId])

  // Expose feed methods via window for WebSocket handler
  useEffect(() => {
    if (active && videoRef.current) {
      // Register this room's player in a global registry for WS handler access
      const registry = (window as any).__msePlayers || {}
      registry[roomId] = { feedInit, feedMedia, player: playerRef.current }
      ;(window as any).__msePlayers = registry
      // 主动请求后端补发 init 段，消除 mse_init 早于 rooms_updated 到达的竞态
      sendRef.current('request_mse_init', { room_id: roomId })
      // 回放在 player 未注册期间缓存的 media 段，避免初始几秒丢帧。
      // 这些 media 段是在 mse_segment 到达但 player 尚未注册时由
      // useWebSocket 模块级缓存保存的。
      const pendingSegments = drainPendingMseSegments(roomId)
      if (pendingSegments.length > 0 && playerRef.current) {
        // 异步回放，避免阻塞 sourceBuffer 创建流程
        setTimeout(() => {
          pendingSegments.forEach(buf => {
            try {
              playerRef.current?.feedMedia(buf)
            } catch (e) {
              console.warn(`[VideoPreview] drain pending segment failed for ${roomId}:`, e)
            }
          })
        }, 0)
      }

      return () => {
        // 仅当注册的还是当前 player 时才删除，避免删除其他实例的注册
        // （例如全屏 VideoPreview 卸载时，不应删除小预览区的注册）
        const currentRegistry = (window as any).__msePlayers || {}
        if (currentRegistry[roomId]?.player === playerRef.current) {
          delete currentRegistry[roomId]
        }
      }
    }
  }, [active, roomId, feedInit, feedMedia])

  // 同步 muted prop 到 MsePlayer 和 video 元素
  useEffect(() => {
    if (playerRef.current) {
      playerRef.current.setMuted(muted)
    }
    if (videoRef.current) {
      videoRef.current.muted = muted
    }
  }, [muted])

  const showLoading = state === 'loading'
  const showError = state === 'error' || error
  const showIdle = state === 'idle'

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        minHeight: 120,
        background: 'var(--background-900)',
        borderRadius: 8,
        overflow: 'hidden',
        willChange: 'transform',
        transform: 'translateZ(0)',
        backfaceVisibility: 'hidden',
        ...style,
      }}
    >
      <video
        ref={videoRef}
        controls={controls}
        muted={muted}
        playsInline
        style={{
          width: '100%',
          height: '100%',
          display: state === 'idle' ? 'none' : 'block',
          objectFit: 'contain',
          background: '#000',
          willChange: 'transform',
          transform: 'translateZ(0)',
          backfaceVisibility: 'hidden',
        }}
      />

      {/* Loading overlay */}
      {showLoading && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'rgba(0, 0, 0, 0.6)',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <LoadingOutlined style={{ fontSize: 24, color: 'var(--brand-500)' }} />
          <span style={{ fontSize: 12, color: 'var(--text-300)' }}>加载中...</span>
        </div>
      )}

      {/* Error overlay */}
      {showError && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'rgba(0, 0, 0, 0.7)',
            flexDirection: 'column',
            gap: 8,
            padding: 16,
          }}
        >
          <span style={{ fontSize: 20 }}>⚠️</span>
          <span style={{ fontSize: 12, color: 'var(--state-error)', textAlign: 'center' }}>
            {error || '预览不可用'}
          </span>
          <button
            onClick={stopAndNotify}
            style={{
              marginTop: 8,
              padding: '4px 12px',
              fontSize: 12,
              background: 'var(--brand-500)',
              color: '#fff',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
            }}
          >
            重试
          </button>
        </div>
      )}

      {/* Idle overlay */}
      {showIdle && !active && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <PlayCircleOutlined style={{ fontSize: 32, color: 'var(--text-500)' }} />
          <span style={{ fontSize: 12, color: 'var(--text-500)' }}>
            点击启用预览
          </span>
        </div>
      )}

      {/* MSE status badge */}
      {active && state !== 'idle' && (
        <div
          style={{
            position: 'absolute',
            top: 8,
            right: 8,
            padding: '2px 8px',
            borderRadius: 4,
            fontSize: 10,
            fontWeight: 600,
            background:
              state === 'playing'
                ? 'rgba(52, 199, 89, 0.8)'
                : state === 'loading'
                  ? 'rgba(255, 149, 0, 0.8)'
                  : 'rgba(255, 59, 48, 0.8)',
            color: '#fff',
          }}
        >
          {state === 'playing' ? 'MSE' : state === 'loading' ? '连接中' : '错误'}
        </div>
      )}
    </div>
  )
}
