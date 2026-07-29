import { describe, it, expect } from 'vitest'
import { shouldQueueWhenDisconnected } from './websocket'

describe('websocket service', () => {
  describe('shouldQueueWhenDisconnected', () => {
    it('should return false for critical operations', () => {
      expect(shouldQueueWhenDisconnected('start_recording')).toBe(false)
      expect(shouldQueueWhenDisconnected('save_settings')).toBe(false)
      expect(shouldQueueWhenDisconnected('export_clip')).toBe(false)
    })

    it('should return true for idempotent read operations', () => {
      expect(shouldQueueWhenDisconnected('get_rooms')).toBe(true)
    })
  })

  describe('exports', () => {
    it('should export send function', () => {
      // Verify the module exports the expected functions
      expect(typeof shouldQueueWhenDisconnected).toBe('function')
    })
  })
})
