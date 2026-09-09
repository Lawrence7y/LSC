import { sendRequest } from './wsRequest'

export type ShutdownRoomSnapshot = {
  room_id: string
  is_recording?: boolean
  preview_enabled?: boolean
}

export type ShutdownAnalysisSnapshot = {
  running?: boolean
  room_id?: string | null
  phase?: string
  finalization_state?: string
} | null

export type ShutdownCleanupResult = {
  success: boolean
  /** Resource cleanup status; checkpoint_saved is not analysis completion. */
  finalization_state: 'idle' | 'checkpoint_saved' | 'finalizing' | 'completed' | 'error'
  finalization_job_id: string | null
  stopped_recording_room_ids: string[]
  errors: string[]
}

type WsRequestClient = {
  send: (type: string, data: unknown) => boolean
  on: (...args: any[]) => () => void
}

function request(
  ws: WsRequestClient,
  type: string,
  data: unknown,
  timeoutMs: number,
): Promise<unknown> {
  return sendRequest(ws as Parameters<typeof sendRequest>[0], type, data, timeoutMs)
}

function responseError(response: unknown): string | null {
  const value = response as { success?: boolean; error?: unknown } | null
  if (value?.success === false || value?.error) {
    return String(value?.error || '后端返回失败')
  }
  return null
}

/**
 * Persist analysis state first, seal active recordings second, then persist
 * the checkpoint again against the finalized recording path.
 */
export async function performShutdownCleanup(
  ws: WsRequestClient,
  rooms: ShutdownRoomSnapshot[],
  analysis: ShutdownAnalysisSnapshot,
): Promise<ShutdownCleanupResult> {
  // Stay below Electron's 45s handshake watchdog. Each individual request is
  // capped by this same deadline.
  const deadline = Date.now() + 40_000
  const errors: string[] = []
  const stoppedRecordingRoomIds: string[] = []
  const analysisRoomId = analysis?.room_id && (
    analysis.running
    || analysis.phase === 'stopping'
    || analysis.phase === 'finalizing'
    || analysis.phase === 'checkpoint_saved'
    || analysis.finalization_state === 'finalizing'
    || analysis.finalization_state === 'checkpoint_saved'
  )
    ? String(analysis.room_id)
    : ''
  let finalizationJobId: string | null = null
  let finalizationState: string = 'idle'

  const remainingMs = (): number => Math.max(0, deadline - Date.now())
  const boundedRequest = async (type: string, data: unknown, timeoutMs: number): Promise<unknown> => {
    const timeout = Math.min(timeoutMs, remainingMs())
    if (timeout <= 0) throw new Error('退出清理已达到总预算，checkpoint 状态已保留')
    return request(ws, type, data, timeout)
  }

  const persistCheckpoint = async (): Promise<void> => {
    if (!analysisRoomId) return
    try {
      const response = await boundedRequest(
        'stop_continuous_analysis',
        { main_room_id: analysisRoomId, stop_with_finalize: true },
        12_000,
      ) as { finalization_job_id?: string | null; finalization_state?: string }
      const error = responseError(response)
      if (error) throw new Error(error)
      finalizationJobId = response.finalization_job_id || finalizationJobId
      if (response.finalization_state === 'completed') {
        finalizationState = 'completed'
      } else if (response.finalization_state === 'finalizing') {
        finalizationState = 'finalizing'
      } else if (finalizationJobId) {
        finalizationState = 'checkpoint_saved'
      }
    } catch (error) {
      errors.push(`stop_continuous_analysis: ${String(error)}`)
    }
  }

  // The first checkpoint protects the in-progress filename if recording stop
  // itself fails.  A second checkpoint below updates it to the finalized path.
  await persistCheckpoint()

  for (const room of rooms) {
    if (!room.is_recording) continue
    try {
      const response = await boundedRequest(
        'stop_recording',
        { room_id: room.room_id, wait_for_finalize: true },
        30_000,
      )
      const error = responseError(response)
      if (error) throw new Error(error)
      stoppedRecordingRoomIds.push(room.room_id)
    } catch (error) {
      errors.push(`stop_recording:${room.room_id}: ${String(error)}`)
    }
  }

  if (analysisRoomId) {
    await persistCheckpoint()
  }

  // The backend may still be running the final scan after the checkpoint was
  // saved. Wait for a terminal status while the bounded shutdown budget has
  // room; otherwise leave the durable checkpoint explicitly recoverable.
  if (analysisRoomId && finalizationState === 'finalizing') {
    let completed = false
    while (remainingMs() > 0) {
      try {
        const response = await boundedRequest(
          'get_continuous_analysis_status',
          {},
          Math.min(2_000, remainingMs()),
        ) as {
          phase?: string
          finalization_state?: string
          finalization_job_id?: string | null
        }
        finalizationJobId = response.finalization_job_id || finalizationJobId
        const state = response.finalization_state || response.phase
        if (state === 'completed' || response.phase === 'completed') {
          finalizationState = 'completed'
          completed = true
          break
        }
        if (state === 'error') {
          finalizationState = 'checkpoint_saved'
          break
        }
        if (state === 'checkpoint_saved' || state === 'idle') {
          finalizationState = 'checkpoint_saved'
          break
        }
      } catch (error) {
        if (remainingMs() > 0) errors.push(`wait_finalization: ${String(error)}`)
        break
      }
      await new Promise<void>((resolve) => {
        setTimeout(resolve, Math.min(250, remainingMs()))
      })
    }
    if (!completed && finalizationState === 'finalizing') {
      finalizationState = 'checkpoint_saved'
    }
  }

  for (const room of rooms) {
    if (!room.preview_enabled) continue
    try {
      const response = await boundedRequest(
        'enable_preview',
        { room_id: room.room_id, enabled: false, mode: 'mse' },
        8_000,
      )
      const error = responseError(response)
      if (error) throw new Error(error)
    } catch (error) {
      // Preview cleanup is still reported, but recording/checkpoint safety has
      // already been attempted before this best-effort resource cleanup.
      errors.push(`stop_preview:${room.room_id}: ${String(error)}`)
    }
  }

  return {
    success: errors.length === 0,
    finalization_state: finalizationState as ShutdownCleanupResult['finalization_state'],
    finalization_job_id: finalizationJobId,
    stopped_recording_room_ids: stoppedRecordingRoomIds,
    errors,
  }
}
