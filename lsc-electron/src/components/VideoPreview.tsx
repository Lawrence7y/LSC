import { useEffect, useRef, useCallback, useState } from 'react'
import { LoadingOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { MsePlayer, MsePlayerState } from '@/services/mediaSourcePlayer'
import { clearMseRoomCache, drainPendingMseSegments, getMseInitCache } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { getAligner } from '@/utils/previewAudioAligner'

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
  const audioSourceRef = useRef<MediaElementAudioSourceNode | null>(null)
  const gainNodeRef = useRef<GainNode | null>(null)
  const [state, setState] = useState<MsePlayerState>('idle')
  const [error, setError] = useState<string | null>(null)
  // 后端自动重连状态（从 store 读取）
  const mseReconnecting = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.mse_reconnecting
  )
  // 预览启动阶段（refreshing_url/probing/streaming/error/idle）
  const previewPhase = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.preview_phase
  )
  const platform = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.platform
  )
  const previewMode = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.preview_mode,
  )
  const previewEpochId = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.preview_epoch_id,
  )
  // 预览源切换（live ↔ recording_review / epoch 轮换）时递增，强制重建 MsePlayer
  const [playerGeneration, setPlayerGeneration] = useState(0)
  const previewSourceRef = useRef<{ mode: string; epoch: string } | null>(null)
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

  // 完整销毁播放器、Web Audio 路由与全局注册表（预览源切换时调用）
  const disposePlayerFully = useCallback(() => {
    if (loadTimeoutRef.current) {
      clearTimeout(loadTimeoutRef.current)
      loadTimeoutRef.current = null
    }
    const currentPlayer = playerRef.current
    if (currentPlayer) {
      currentPlayer.stop()
      playerRef.current = null
    }
    if (audioSourceRef.current) {
      try { audioSourceRef.current.disconnect() } catch {}
      audioSourceRef.current = null
    }
    if (gainNodeRef.current) {
      try { gainNodeRef.current.disconnect() } catch {}
      gainNodeRef.current = null
    }
    const registry = (window as any).__msePlayers || {}
    if (currentPlayer && registry[roomId]?.player === currentPlayer) {
      delete registry[roomId]
    }
    autoRetriedRef.current = false
    setState('idle')
    setError(null)
  }, [roomId])

  // S6: 重试 loading 状态，防止用户连续点击
  const [retrying, setRetrying] = useState(false)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 重试预览：清理本地播放器并重新请求后端拉流（与开预览同一路径 enable_preview）
  const handleRetry = useCallback(() => {
    if (retrying) return
    setRetrying(true)
    cleanupPlayer()
    sendRef.current('enable_preview', {
      room_id: roomId,
      enabled: true,
      mode: 'mse',
    })
    retryTimerRef.current = setTimeout(() => {
      retryTimerRef.current = null
      setRetrying(false)
    }, 3000)
  }, [retrying, cleanupPlayer, roomId])

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

  // 预览源切换：live_mse ↔ recording_review 或 preview_epoch_id 轮换时重建播放器，
  // 避免旧实例 _initReceived=true 丢弃新 mse_init。
  useEffect(() => {
    if (!active) return

    const mode = previewMode ?? 'live_mse'
    const epoch = previewEpochId ?? ''
    const prev = previewSourceRef.current

    if (prev !== null) {
      const modeChanged = prev.mode !== mode
      const epochChanged = epoch !== '' && prev.epoch !== epoch
      if (modeChanged || epochChanged) {
        disposePlayerFully()
        clearMseRoomCache(roomId)
        setPlayerGeneration((g) => g + 1)
      }
    }

    previewSourceRef.current = { mode, epoch }
  }, [active, roomId, previewMode, previewEpochId, disposePlayerFully])

  // Auto-start when active. Deps are limited to [active, roomId, playerGeneration];
  // send/onReady/onError are accessed via refs so their identity changes do not
  // retrigger the effect. Backend enable_preview is managed by the parent —
  // VideoPreview only creates the MsePlayer and registers it; it does NOT send
  // enable_preview on init/cleanup.
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
      onSourceOpen: () => {
        // MediaSource.sourceopen 触发后 video.src 已绑定到新 MediaSource，
        // 此时创建 Web Audio 路由才安全。若在 player.start() 之前创建，
        // start() 内部的 stop() → video.load() 会断开 MediaElementSource 连接。
        if (!audioSourceRef.current && videoRef.current) {
          try {
            const ctx = getAligner().getContextSync()
            if (ctx.state === 'suspended') {
              ctx.resume().catch((e) => {
                console.warn(`[VideoPreview] Failed to resume AudioContext on sourceopen for ${roomId}:`, e)
              })
            }
            const source = ctx.createMediaElementSource(videoRef.current)
            const gain = ctx.createGain()
            gain.gain.value = (localMutedOverride ?? muted) ? 0 : 1
            source.connect(gain)
            gain.connect(ctx.destination)
            audioSourceRef.current = source
            gainNodeRef.current = gain
            const registry = (window as any).__msePlayers || {}
            registry[roomId] = {
              ...(registry[roomId] || {}),
              feedInit,
              feedMedia,
              player: playerRef.current,
              audioSource: audioSourceRef.current,
              gainNode: gainNodeRef.current,
            }
            ;(window as any).__msePlayers = registry
            console.log(`[VideoPreview] Web Audio routing created on sourceopen for ${roomId}`)
          } catch (e) {
            console.warn(`[VideoPreview] Failed to create Web Audio routing for ${roomId}:`, e)
          }
        }
      },
    })

    playerRef.current = player
    onReadyRef.current?.(player)

    // Start receiving segments (backend enable_preview is owned by the parent)
    // player.start() 内部会创建 MediaSource 并触发 sourceopen → onSourceOpen 回调
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
      // 清理 Web Audio 路由，防止重新挂载时旧 source 绑定到已移除的 video 元素
      if (audioSourceRef.current) {
        try { audioSourceRef.current.disconnect() } catch {}
        audioSourceRef.current = null
      }
      if (gainNodeRef.current) {
        try { gainNodeRef.current.disconnect() } catch {}
        gainNodeRef.current = null
      }
      autoRetriedRef.current = false
      setState('idle')
      setError(null)
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current)
        retryTimerRef.current = null
      }
    }
  }, [active, roomId, playerGeneration])

  // Expose feed methods via window for WebSocket handler
  useEffect(() => {
    if (active && videoRef.current) {
      // Register this room's player in a global registry for WS handler access
      const registry = (window as any).__msePlayers || {}
      registry[roomId] = { feedInit, feedMedia, player: playerRef.current, audioSource: audioSourceRef.current, gainNode: gainNodeRef.current }
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

  // 同步 muted prop 到 GainNode（替代 video.muted）
  // GainNode 控制扬声器输出音量，不影响 MediaElementSource 的音频数据
  useEffect(() => {
    const effectiveMuted = localMutedOverride ?? muted
    if (gainNodeRef.current) {
      gainNodeRef.current.gain.value = effectiveMuted ? 0 : 1
    }
    // video.muted 仅用于原生控件的显示状态，不影响 Web Audio 路由
    if (videoRef.current) {
      videoRef.current.muted = effectiveMuted
    }
    // 取消静音时显式 resume AudioContext，确保 Web Audio 路由有输出
    if (!effectiveMuted) {
      const ctx = getAligner().getContextSync()
      if (ctx.state === 'suspended') {
        ctx.resume().catch((e) => {
          console.warn(`[VideoPreview] Failed to resume AudioContext on unmute for ${roomId}:`, e)
        })
      }
    }
  }, [muted, localMutedOverride, roomId])

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
      if ((video as any).__lscSuppressMuteSync) return
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

  const showError = state === 'error' || error
  const showIdle = state === 'idle'
  // 预览已启用但尚未出画（拉流/转码中）
  const showStarting =
    active &&
    !mseReconnecting &&
    !showError &&
    state !== 'playing'

  // 阶段进度文案：B站等平台首次刷新流地址耗时较长，单独提示
  const phaseText =
    previewPhase === 'refreshing_url' ? '正在刷新流地址…' :
    previewPhase === 'probing' ? '正在探测/转码…' :
    '正在拉流/转码…'
  const phaseHint =
    previewPhase === 'refreshing_url' && platform && /bilibili/i.test(platform)
      ? '首次刷新通常需要 10–30 秒'
      : null

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
        muted={false}
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

      {/* 预览启动中：已 enable 但尚无首帧 / 未 playing */}
      {showStarting && (
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
            zIndex: 2,
          }}
        >
          <LoadingOutlined style={{ fontSize: 24, color: 'var(--brand-500)' }} />
          <span style={{ fontSize: 12, color: 'var(--text-300)' }}>{phaseText}</span>
          {phaseHint && (
            <span style={{ fontSize: 11, color: 'var(--text-400)' }}>{phaseHint}</span>
          )}
        </div>
      )}

      {/* Error overlay */}
      {showError && !mseReconnecting && (
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
            onClick={handleRetry}
            disabled={retrying}
            style={{
              marginTop: 8,
              padding: '4px 12px',
              fontSize: 12,
              background: retrying ? 'var(--text-500)' : 'var(--brand-500)',
              color: '#fff',
              border: 'none',
              borderRadius: 4,
              cursor: retrying ? 'not-allowed' : 'pointer',
            }}
          >
            {retrying ? '重试中...' : '重试'}
          </button>
        </div>
      )}

      {/* Reconnecting overlay */}
      {mseReconnecting && (
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
          <LoadingOutlined style={{ fontSize: 24, color: 'var(--brand-500)' }} />
          <span style={{ fontSize: 12, color: 'var(--text-300)', textAlign: 'center' }}>
            正在恢复预览 ({mseReconnecting.attempt}/{mseReconnecting.maxAttempts})...
          </span>
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


    </div>
  )
}
