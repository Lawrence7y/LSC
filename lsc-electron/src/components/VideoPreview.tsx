import { useEffect, useRef, useCallback, useState } from 'react'
import { LoadingOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { MsePlayer, MsePlayerState } from '@/services/mediaSourcePlayer'
import { drainPendingMseSegments, getMseInitCache } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'

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
  // 超时检测：加载后 30 秒未收到任何帧则报错。
  // B站等平台首次预览需要 refresh_stream_url（重新解析直播页面），耗时可达 10+ 秒。
  const loadTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hasReceivedDataRef = useRef(false)
  // 首次超时后自动重试一次，避免 B站首次预览因 URL 刷新慢而失败
  const autoRetriedRef = useRef(false)

  // Refs hold the latest props without being listed as useEffect deps,
  // so changing send/onReady/onError identities do not trigger init→stop→init loops.
  const onReadyRef = useRef(onReady)
  const onErrorRef = useRef(onError)
  const sendRef = useRef(send)
  const mutedRef = useRef(muted)
  onReadyRef.current = onReady
  onErrorRef.current = onError
  sendRef.current = send
  mutedRef.current = muted

  // 本地静音覆盖：解决全屏原生控件改变静音后经 WS→后端节流→rooms_updated
  // 用 stale prop 覆盖用户操作的竞态问题
  const [localMutedOverride, setLocalMutedOverride] = useState<boolean | null>(null)
  const localMutedOverrideRef = useRef<boolean | null>(null)

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
        // 首次超时自动重试一次：重新请求 init 段，适用于 B站首次预览 URL 刷新慢的场景
        if (!autoRetriedRef.current) {
          autoRetriedRef.current = true
          console.log(`[VideoPreview] Auto-retrying preview for ${roomId}`)
          sendRef.current('request_mse_init', { room_id: roomId })
          // 重新设置 30 秒超时等待重试结果（B站 URL 刷新可能需要 10+ 秒）
          if (loadTimeoutRef.current) {
            clearTimeout(loadTimeoutRef.current)
          }
          loadTimeoutRef.current = setTimeout(() => {
            if (!hasReceivedDataRef.current && playerRef.current) {
              setError('预览加载超时，请检查直播流是否正常')
            }
          }, 30000)
          return
        }
        setError('预览加载超时，请检查直播流是否正常')
      }
    }, 30000)

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
      autoRetriedRef.current = false
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
  // 优先使用本地覆盖值（用户通过原生控件操作后的最新状态），避免 stale prop 覆盖
  useEffect(() => {
    const effectiveMuted = localMutedOverride ?? muted
    if (playerRef.current) {
      playerRef.current.setMuted(effectiveMuted)
    }
    if (videoRef.current) {
      videoRef.current.muted = effectiveMuted
    }
  }, [muted, localMutedOverride])

  // 后端 rooms_updated 确认：当 muted prop 与本地覆盖值一致时清除覆盖
  useEffect(() => {
    if (localMutedOverrideRef.current !== null && muted === localMutedOverrideRef.current) {
      localMutedOverrideRef.current = null
      setLocalMutedOverride(null)
    }
  }, [muted])

  // 全屏时用户通过原生 controls 改变静音状态，需要同步回后端。
  // volumechange 事件在 video.muted 被 React prop 设置和原生控件设置时都会触发，
  // 通过 mutedRef 区分：若 video.muted !== mutedRef.current 说明是原生控件改的。
  // 同时设置本地覆盖 + 乐观更新 store，避免 rooms_updated 用 stale prop 覆盖用户操作。
  useEffect(() => {
    if (!active || !videoRef.current) return
    const video = videoRef.current
    const handleVolumeChange = () => {
      if (video.muted !== mutedRef.current) {
        // 设置本地覆盖，立即反映到 UI
        localMutedOverrideRef.current = video.muted
        setLocalMutedOverride(video.muted)
        // 乐观更新 store，房间卡片的静音图标立即响应
        useAppStore.getState().updateRoom(roomId, { preview_muted: video.muted })
        // 同步到后端
        sendRef.current('set_preview_muted', { room_id: roomId, muted: video.muted })
      }
    }
    video.addEventListener('volumechange', handleVolumeChange)
    return () => video.removeEventListener('volumechange', handleVolumeChange)
  }, [active, roomId])

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
