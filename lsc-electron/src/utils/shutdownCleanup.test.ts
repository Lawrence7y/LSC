import { describe, expect, it } from 'vitest'

import { performShutdownCleanup } from './shutdownCleanup'

class FakeWs {
  calls: Array<{ type: string; data: Record<string, unknown> }> = []
  handlers = new Map<string, Set<(data: unknown) => void>>()

  on(type: string, handler: (data: unknown) => void): () => void {
    const set = this.handlers.get(type) || new Set()
    set.add(handler)
    this.handlers.set(type, set)
    return () => set.delete(handler)
  }

  send(type: string, data: unknown): boolean {
    const payload = data as Record<string, unknown>
    this.calls.push({ type, data: payload })
    queueMicrotask(() => {
      const response = type === 'stop_continuous_analysis'
        ? { success: true, finalization_job_id: 'job-1', request_id: payload.request_id }
        : { success: true, request_id: payload.request_id }
      for (const handler of this.handlers.get(`${type}_response`) || []) {
        handler(response)
      }
    })
    return true
  }
}

class FinalizingWs extends FakeWs {
  override send(type: string, data: unknown): boolean {
    const payload = data as Record<string, unknown>
    this.calls.push({ type, data: payload })
    queueMicrotask(() => {
      const response = type === 'stop_continuous_analysis'
        ? {
            success: true,
            finalization_job_id: 'job-finalizing',
            finalization_state: 'finalizing',
            request_id: payload.request_id,
          }
        : type === 'get_continuous_analysis_status'
          ? {
              running: false,
              phase: 'completed',
              finalization_state: 'completed',
              finalization_job_id: 'job-finalizing',
              request_id: payload.request_id,
            }
          : { success: true, request_id: payload.request_id }
      for (const handler of this.handlers.get(`${type}_response`) || []) {
        handler(response)
      }
    })
    return true
  }
}

describe('performShutdownCleanup', () => {
  it('checkpoints before and after a finalized recording stop', async () => {
    const ws = new FakeWs()
    const result = await performShutdownCleanup(
      ws,
      [{ room_id: 'room-1', is_recording: true, preview_enabled: true }],
      { running: true, room_id: 'room-1' },
    )

    expect(result).toEqual({
      success: true,
      finalization_state: 'checkpoint_saved',
      finalization_job_id: 'job-1',
      stopped_recording_room_ids: ['room-1'],
      errors: [],
    })
    expect(ws.calls.map((call) => call.type)).toEqual([
      'stop_continuous_analysis',
      'stop_recording',
      'stop_continuous_analysis',
      'enable_preview',
    ])
    expect(ws.calls[1].data).toMatchObject({
      room_id: 'room-1',
      wait_for_finalize: true,
    })
  })

  it('always resolves with structured errors when the socket is unavailable', async () => {
    const ws = new FakeWs()
    ws.send = () => false

    const result = await performShutdownCleanup(
      ws,
      [{ room_id: 'room-1', is_recording: true }],
      { running: true, room_id: 'room-1' },
    )

    expect(result.success).toBe(false)
    expect(result.errors.some((error) => error.startsWith('stop_recording:room-1'))).toBe(true)
    expect(result.errors.filter((error) => error.startsWith('stop_continuous_analysis'))).toHaveLength(2)
  })

  it('waits for a finalization terminal state inside the shutdown budget', async () => {
    const ws = new FinalizingWs()
    const result = await performShutdownCleanup(
      ws,
      [],
      { running: true, room_id: 'room-1' },
    )

    expect(result.finalization_state).toBe('completed')
    expect(ws.calls.map((call) => call.type)).toContain('get_continuous_analysis_status')
  })
})
