import { useEffect, useRef, useCallback, useState } from 'react'
import { LoadingOutlined, PlayCircleOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { MsePlayer, MsePlayerState } from '@/services/mediaSourcePlayer'
import {
  clearMseRoomCache,
  drainPendingMseReviewSegments,
  drainPendingMseSegments,
  getMseInitCache,
  getMseReviewInitCache,
  wsClient,
} from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { getAligner } from '@/utils/previewAudioAligner'
import { isMuteSyncSuppressed, withMuteSyncSuppressed } from '@/utils/muteSyncGuard'
import { useI18n } from '@/i18n'
import {
  DEFAULT_TIMELINE_REPLAY_SECONDS,
  normalizeReplayBufferSeconds,
} from '@/utils/replaySettings'

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
  const { t } = useI18n()
  const videoRef = useRef<HTMLVideoElement>(null)
  const playerRef = useRef<MsePlayer | null>(null)
  const reviewVideoRef = useRef<HTMLVideoElement>(null)
  const reviewPlayerRef = useRef<MsePlayer | null>(null)
  const audioSourceRef = useRef<MediaElementAudioSourceNode | null>(null)
  const gainNodeRef = useRef<GainNode | null>(null)
  const [state, setState] = useState<MsePlayerState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [reviewState, setReviewState] = useState<MsePlayerState>('idle')
  const [reviewError, setReviewError] = useState<string | null>(null)
  // 后端自动重连状态（从 uiState 读取，避免 rooms_updated 冲掉）
  const mseReconnecting = useAppStore(
    (s) => s.uiState[roomId]?.mse_reconnecting
  )
  // 预览启动阶段（refreshing_url/probing/streaming/error/idle）
  const previewPhase = useAppStore(
    (s) => s.uiState[roomId]?.preview_phase
  )
  const previewMode = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.preview_mode,
  )
  const replayBufferSeconds = useAppStore(
    (s) => s.settings.timeline_replay_seconds,
  )
  const previewEpochId = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.preview_epoch_id,
  )
  const recordingId = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.recording_id,
  )
  const recordingMediaStart = useAppStore(
    (s) => {
      const r = s.rooms.find((item) => item.room_id === roomId)
      return r?.recording_media_start_mono ?? (r?.recording_start_mono ? Number(r.recording_start_mono) : undefined)
    },
  )
  const activePreviewChannel = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.active_preview_channel,
  )
  const reviewSessionId = useAppStore(
    (s) => s.rooms.find((r) => r.room_id === roomId)?.review_session_id,
  )
  const isReviewActive = (activePreviewChannel === 'review' || previewMode === 'recording_review') && Boolean(reviewSessionId)
  const previewClockAcceptedRef = useRef<string | null>(null)
  const previewClockSampleEpochRef = useRef<number | null>(null)
  // 预览源切换（live ↔ recording_review / epoch 轮换）时递增，强制重建 MsePlayer
  const [playerGeneration, setPlayerGeneration] = useState(0)
  const previewSourceRef = useRef<{ mode: string; epoch: string } | null>(null)
  // 超时检测：加载后 30 秒未收到任何帧则报错。
  // B站等平台首次预览需要 refresh_stream_url（重新解析直播页面），耗时可达 10+ 秒。
  const loadTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hasReceivedDataRef = useRef(false)
  // 首次超时后自动重试一次，避免 B站首次预览因 URL 刷新慢而失败
  const autoRetriedRef = useRef(false)
  const previewPhaseRef = useRef(previewPhase)
  previewPhaseRef.current = previewPhase

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

  // 设置页修改回放时长后，已存在的播放器即时收敛到新上限；
  // 已经清理的历史分片不会被重新构造。
  useEffect(() => {
    playerRef.current?.setReplayBufferSeconds(
      normalizeReplayBufferSeconds(replayBufferSeconds ?? DEFAULT_TIMELINE_REPLAY_SECONDS),
    )
  }, [replayBufferSeconds])

  // 直播 MSE 首次稳定播放后采集一次预览时钟样本。样本绑定 recording/preview
  // 两个 epoch；录制首帧校正或预览重建时依赖变化会自动重新采样。
  useEffect(() => {
    if (!active || state !== 'playing' || (previewMode ?? 'live_mse') !== 'live_mse' || !recordingId) return
    const sampleKey = [
      previewEpochId || '',
      recordingId || '',
      recordingMediaStart == null ? '' : String(recordingMediaStart),
    ].join(':')
    if (previewClockAcceptedRef.current === sampleKey) return

    let stopped = false
    let retryTimer: number | null = null
    const stop = () => {
      stopped = true
      if (retryTimer !== null) window.clearInterval(retryTimer)
      window.clearTimeout(initialTimer)
      window.clearTimeout(expiryTimer)
      unsubscribe()
    }
    const report = () => {
      if (stopped) return
      const video = videoRef.current
      const currentTime = video?.currentTime
      if (!video || currentTime == null || !Number.isFinite(currentTime) || currentTime < 0) return
      const sampleEpochMs = Date.now()
      previewClockSampleEpochRef.current = sampleEpochMs
      sendRef.current('set_preview_clock', {
        room_id: roomId,
        preview_epoch_id: previewEpochId || '',
        preview_current_time: currentTime,
        sample_epoch_ms: sampleEpochMs,
      })
    }
    const unsubscribe = wsClient.on('set_preview_clock_response', (data: any) => {
      if (
        data?.success
        && data.room_id === roomId
        && data.sample_epoch_ms === previewClockSampleEpochRef.current
        && (!data.preview_clock_epoch_id || !previewEpochId || data.preview_clock_epoch_id === previewEpochId)
      ) {
        previewClockAcceptedRef.current = sampleKey
        stop()
      }
    })
    const initialTimer = window.setTimeout(() => {
      report()
      if (!stopped) retryTimer = window.setInterval(report, 1000)
    }, 250)
    const expiryTimer = window.setTimeout(stop, 20000)
    return stop
  }, [active, state, roomId, previewMode, previewEpochId, recordingId, recordingMediaStart])

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

  // 完整销毁播放器与全局注册表（预览源切换时调用）。
  // 注意：不得 disconnect MediaElementSource——同一 <video> 只能 createMediaElementSource 一次。
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
    const registry = window.__msePlayers || {}
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

  // Feed init segment to player (双通道路由)
  const feedInit = useCallback((data: ArrayBuffer) => {
    hasReceivedDataRef.current = true
    if (isReviewActive && reviewPlayerRef.current) {
      reviewPlayerRef.current.feedInit(data)
    } else {
      playerRef.current?.feedInit(data)
    }
  }, [isReviewActive])

  // Feed media segment to player (双通道路由)
  const feedMedia = useCallback((data: ArrayBuffer) => {
    hasReceivedDataRef.current = true
    if (isReviewActive && reviewPlayerRef.current) {
      reviewPlayerRef.current.feedMedia(data)
    } else {
      playerRef.current?.feedMedia(data)
    }
  }, [isReviewActive])

  // 预览源切换：直播 preview_epoch_id 真正变化时重建 LivePlayer；
  // recording_review 由独立的 reviewPlayer 处理，切换 mode 不会销毁 LivePlayer。
  useEffect(() => {
    if (!active) return

    const mode = previewMode ?? 'live_mse'
    const epoch = previewEpochId ?? ''
    const prev = previewSourceRef.current

    if (prev !== null) {
      const epochChanged = epoch !== '' && prev.epoch !== epoch
      // 只有直播 epoch 真正变化才触发 LivePlayer 重建与缓存清理（review 由独立 ReviewPlayer 承载）
      if (epochChanged) {
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
        // 后端正在刷新流地址（B站等平台需 10-30s）或正在重连，重置超时等待
        if (previewPhaseRef.current === 'refreshing_url' || mseReconnecting) {
          if (loadTimeoutRef.current) clearTimeout(loadTimeoutRef.current)
          loadTimeoutRef.current = setTimeout(() => {
            if (!hasReceivedDataRef.current && playerRef.current) {
              setError(t('预览加载超时，请检查直播流是否正常'))
            }
          }, 30000)
          return
        }
        console.warn(`[VideoPreview] 预览加载超时 (${roomId})`)
        // 首次超时自动重试一次：重新请求 init 段，适用于 B站首次预览 URL 刷新慢的场景
        if (!autoRetriedRef.current) {
          autoRetriedRef.current = true
          sendRef.current('request_mse_init', { room_id: roomId })
          // 重新设置 30 秒超时等待重试结果（B站 URL 刷新可能需要 10+ 秒）
          if (loadTimeoutRef.current) {
            clearTimeout(loadTimeoutRef.current)
          }
          loadTimeoutRef.current = setTimeout(() => {
            if (!hasReceivedDataRef.current && playerRef.current) {
              setError(t('预览加载超时，请检查直播流是否正常'))
            }
          }, 30000)
          return
        }
        setError(t('预览加载超时，请检查直播流是否正常'))
      }
    }, 30000)

    const player = new MsePlayer({
      videoElement: videoRef.current,
      replayBufferSeconds: normalizeReplayBufferSeconds(
        replayBufferSeconds ?? DEFAULT_TIMELINE_REPLAY_SECONDS,
      ),
      isFile: previewMode === 'recording_review',
      debug: false,
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
      onBackpressure: (bpState, pending) => {
        sendRef.current('mse_backpressure', { room_id: roomId, state: bpState, pending })
      },
      onSourceOpen: () => {
        // MediaSource.sourceopen 触发后 video.src 已绑定到新 MediaSource，
        // 此时创建 Web Audio 路由才安全。若在 player.start() 之前创建，
        // start() 内部的 stop() → video.load() 会断开 MediaElementSource 连接。
        // HTMLMediaElement 一生只能 createMediaElementSource 一次；播放器重建时复用已有图。
        if (!videoRef.current) return
        try {
          if (!audioSourceRef.current) {
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
            // MES 建立后元素必须 unmuted，否则后续对齐/播放易卡死
            if (videoRef.current.muted) {
              withMuteSyncSuppressed(videoRef.current, () => {
                videoRef.current!.muted = false
              })
            }
            audioSourceRef.current = source
            gainNodeRef.current = gain
          }
          const registry = window.__msePlayers || {}
          registry[roomId] = {
            ...(registry[roomId] || {}),
            feedInit,
            feedMedia,
            player: isReviewActive && reviewPlayerRef.current ? reviewPlayerRef.current : playerRef.current,
            live: playerRef.current,
            review: reviewPlayerRef.current,
            audioSource: audioSourceRef.current,
            gainNode: gainNodeRef.current,
          }
          ;window.__msePlayers = registry
        } catch (e) {
          console.warn(`[VideoPreview] Failed to create Web Audio routing for ${roomId}:`, e)
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
    }

    return () => {
      // Clear timeout on cleanup
      if (loadTimeoutRef.current) {
        clearTimeout(loadTimeoutRef.current)
        loadTimeoutRef.current = null
      }
      // Local cleanup only — do NOT notify backend (parent owns backend state)
      const stopping = playerRef.current
      if (stopping) {
        stopping.stop()
        playerRef.current = null
      }
      // playerGeneration 重建时同步注销注册表，避免 stale player 被读播放头
      const registry = window.__msePlayers || {}
      if (stopping && registry[roomId]?.player === stopping) {
        delete registry[roomId]
      }
      // 保留 Web Audio 路由：同一 video 元素上 createMediaElementSource 只能成功一次，
      // MSE 重连 / playerGeneration 递增时复用已有 MediaElementSource + GainNode。
      autoRetriedRef.current = false
      setState('idle')
      setError(null)
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current)
        retryTimerRef.current = null
      }
    }
  }, [active, roomId, playerGeneration])

  // MSE 重连成功后重建播放器，接受新 init 段
  useEffect(() => {
    const unsub = wsClient.on('mse_reconnected', (data: { room_id: string }) => {
      if (data?.room_id === roomId) {
        clearMseRoomCache(roomId)
        setPlayerGeneration((g) => g + 1)
      }
    })
    return () => unsub()
  }, [roomId])

  // 组件卸载或切换房间时才拆掉 Web Audio 图（此时 video 元素随之销毁）
  useEffect(() => {
    return () => {
      if (audioSourceRef.current) {
        try { audioSourceRef.current.disconnect() } catch { /* ignore */ }
        audioSourceRef.current = null
      }
      if (gainNodeRef.current) {
        try { gainNodeRef.current.disconnect() } catch { /* ignore */ }
        gainNodeRef.current = null
      }
    }
  }, [roomId])

  // Expose feed methods via window for WebSocket handler
  useEffect(() => {
    if (active && videoRef.current) {
      // Register this room's player in a global registry for WS handler access
      const registry = window.__msePlayers || {}
      const prev = registry[roomId]
      registry[roomId] = {
        ...(prev || {}),
        feedInit,
        feedMedia,
        player: isReviewActive && reviewPlayerRef.current ? reviewPlayerRef.current : playerRef.current,
        live: playerRef.current,
        review: reviewPlayerRef.current,
        // feed 回调重建时勿用 null 冲掉已创建的 Web Audio 图（否则对齐走 captureStream 易采到静音）
        audioSource: audioSourceRef.current ?? prev?.audioSource ?? null,
        gainNode: gainNodeRef.current ?? prev?.gainNode ?? null,
      }
      ;window.__msePlayers = registry
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
        const currentRegistry = window.__msePlayers || {}
        if (currentRegistry[roomId]?.player === playerRef.current) {
          delete currentRegistry[roomId]
        }
      }
    }
  }, [active, roomId, feedInit, feedMedia, playerGeneration])

  // 同步 muted prop 到 GainNode（扬声器）。
  // MediaElementSource 建立后元素必须保持 unmuted：Chromium 在 muted=true 时
  // 会对 MES 输出全零，对齐结束后 remute 还可能卡死 MSE play()。
  useEffect(() => {
    const effectiveMuted = localMutedOverride ?? muted
    if (gainNodeRef.current) {
      gainNodeRef.current.gain.value = effectiveMuted ? 0 : 1
    }
    const video = videoRef.current
    if (video && video.muted) {
      withMuteSyncSuppressed(video, () => {
        video.muted = false
      })
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

  // 全屏原生 controls 改静音：偏好进 store，但元素保持 unmuted（GainNode 控声）。
  useEffect(() => {
    if (!active || !videoRef.current) return
    const video = videoRef.current
    const handleVolumeChange = () => {
      if (isMuteSyncSuppressed(video)) return
      if (video.muted) {
        // 用户点了静音：记偏好，强制元素 unmute，改用 GainNode=0
        withMuteSyncSuppressed(video, () => {
          video.muted = false
        })
        localMutedOverrideRef.current = true
        setLocalMutedOverride(true)
        useAppStore.getState().updateRoom(roomId, { preview_muted: true })
        sendRef.current('set_preview_muted', { room_id: roomId, muted: true })
        if (gainNodeRef.current) gainNodeRef.current.gain.value = 0
        return
      }
      if (mutedRef.current) {
        // 元素已 unmuted 且 UI 认为静音：用户在取消静音
        localMutedOverrideRef.current = false
        setLocalMutedOverride(false)
        useAppStore.getState().updateRoom(roomId, { preview_muted: false })
        sendRef.current('set_preview_muted', { room_id: roomId, muted: false })
        if (gainNodeRef.current) gainNodeRef.current.gain.value = 1
      }
    }
    video.addEventListener('volumechange', handleVolumeChange)
    return () => video.removeEventListener('volumechange', handleVolumeChange)
  }, [active, roomId])

  // 录制文件回看独立播放器管理（C-01/C-03）
  useEffect(() => {
    if (!active || !isReviewActive || !reviewVideoRef.current || !reviewSessionId) {
      if (reviewPlayerRef.current) {
        reviewPlayerRef.current.stop()
        reviewPlayerRef.current = null
      }
      const registry = window.__msePlayers || {}
      if (registry[roomId]) {
        registry[roomId].review = null
        registry[roomId].player = playerRef.current
      }
      setReviewState('idle')
      setReviewError(null)
      return
    }

    if (!reviewPlayerRef.current || reviewPlayerRef.current.sessionId !== reviewSessionId) {
      if (reviewPlayerRef.current) {
        reviewPlayerRef.current.stop()
        reviewPlayerRef.current = null
      }
      const revPlayer = new MsePlayer({
        videoElement: reviewVideoRef.current,
        channel: 'review',
        sessionId: reviewSessionId,
        isFile: true,
        replayBufferSeconds: 60,
        debug: false,
        onStateChange: (s) => setReviewState(s),
        onError: (err) => setReviewError(err),
      })
      revPlayer.start('')
      reviewPlayerRef.current = revPlayer

      // 1. 立即更新全局注册表，确保后续 WS 帧能命中 review player
      const registry = window.__msePlayers || {}
      registry[roomId] = {
        ...(registry[roomId] || {}),
        player: revPlayer,
        review: revPlayer,
        live: playerRef.current,
        feedInit,
        feedMedia,
      }
      window.__msePlayers = registry

      // 2. 补喂已缓存的 review init 段，消除早到竞态
      const cachedRevInit = getMseReviewInitCache(roomId, reviewSessionId)
      if (cachedRevInit) {
        revPlayer.feedInit(cachedRevInit)
      } else {
        sendRef.current('request_mse_init', { room_id: roomId, channel: 'review' })
      }

      // 3. 回放已排队的 review media 段
      const pendingRevSegments = drainPendingMseReviewSegments(roomId, reviewSessionId)
      if (pendingRevSegments.length > 0) {
        setTimeout(() => {
          pendingRevSegments.forEach((buf) => {
            try {
              revPlayer.feedMedia(buf)
            } catch (e) {
              console.warn(`[VideoPreview] drain pending review segment failed for ${roomId}:`, e)
            }
          })
        }, 0)
      }
    }
  }, [active, isReviewActive, reviewSessionId, roomId, feedInit, feedMedia])

  // 回看视频元素静音同步
  useEffect(() => {
    if (reviewVideoRef.current) {
      reviewVideoRef.current.muted = Boolean(localMutedOverride ?? muted)
    }
  }, [muted, localMutedOverride, isReviewActive])

  // 监听 reviewVideo controls 的静音变化
  useEffect(() => {
    if (!active || !reviewVideoRef.current || !isReviewActive) return
    const v = reviewVideoRef.current
    const onVol = () => {
      setLocalMutedOverride(v.muted)
    }
    v.addEventListener('volumechange', onVol)
    return () => v.removeEventListener('volumechange', onVol)
  }, [active, isReviewActive])

  const showError = (isReviewActive ? (reviewState === 'error' || reviewError) : (state === 'error' || error))
  const showIdle = state === 'idle'
  // 暂停态是用户主动操作，不算"未出画"：不得显示"正在拉流/转码…"
  const isPaused = state === 'paused'
  // 预览已启用但尚未出画（拉流/转码中）
  const showStarting =
    active &&
    !mseReconnecting &&
    !showError &&
    !isPaused &&
    state !== 'playing'

  // 阶段进度文案：首次刷新流地址可能需要等待上游解析完成
  const phaseText =
    previewPhase === 'refreshing_url' ? t('正在刷新流地址…') :
    previewPhase === 'probing' ? t('正在探测/转码…') :
    t('正在拉流/转码…')
  const phaseHint =
    previewPhase === 'refreshing_url'
      ? t('首次刷新可能需要 10–30 秒')
      : null

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        // 父容器负责 16:9 尺寸；子元素不能用最小高度破坏比例。
        minHeight: 0,
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
        data-room-id={roomId}
        controls={controls}
        muted={false}
        playsInline
        style={{
          width: '100%',
          height: '100%',
          display: isReviewActive ? 'none' : (state === 'idle' ? 'none' : 'block'),
          objectFit: 'contain',
          background: '#000',
          willChange: 'transform',
          transform: 'translateZ(0)',
          backfaceVisibility: 'hidden',
        }}
      />
      <video
        ref={reviewVideoRef}
        data-room-id={roomId}
        controls={controls}
        muted={Boolean(localMutedOverride ?? muted)}
        playsInline
        style={{
          width: '100%',
          height: '100%',
          display: isReviewActive ? (reviewState === 'idle' ? 'none' : 'block') : 'none',
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
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{phaseHint}</span>
          )}
        </div>
      )}

      {/* 用户主动暂停：轻提示（非"拉流/转码"误报） */}
      {isPaused && !showError && (
        <div
          style={{
            position: 'absolute',
            top: 8,
            left: '50%',
            transform: 'translateX(-50%)',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            background: 'rgba(0, 0, 0, 0.55)',
            backdropFilter: 'blur(6px)',
            borderRadius: 999,
            padding: '3px 10px',
            zIndex: 3,
            pointerEvents: 'none',
          }}
        >
          <PauseCircleOutlined style={{ fontSize: 13, color: 'var(--text-secondary)' }} />
          <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{t('已暂停')}</span>
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
            {t(error || '') || t('预览不可用')}
          </span>
          <button
            onClick={handleRetry}
            disabled={retrying}
            style={{
              marginTop: 8,
              padding: '4px 12px',
              fontSize: 12,
              background: retrying ? 'var(--text-500)' : 'var(--brand-500)',
              color: 'var(--overlay-text, #f5f5f7)',
              border: 'none',
              borderRadius: 4,
              cursor: retrying ? 'not-allowed' : 'pointer',
            }}
          >
            {retrying ? t('重试中...') : t('重试')}
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
            {t('正在恢复预览 ({attempt}/{max})...', { attempt: mseReconnecting.attempt, max: mseReconnecting.maxAttempts })}
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
            {t('点击启用预览')}
          </span>
        </div>
      )}


    </div>
  )
}
