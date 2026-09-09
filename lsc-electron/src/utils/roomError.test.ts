import { describe, expect, it } from 'vitest'
import { isCredentialError, type RoomErrorContext } from './roomError'

function room(overrides: RoomErrorContext = {}): RoomErrorContext {
  return {
    last_error: '',
    mse_error: '',
    pipeline_health: {
      platform: 'ERROR',
      failure_kind: '',
      error: '',
    },
    ...overrides,
  }
}

describe('room error action classification', () => {
  it('does not show credential action for a Douyin offline room', () => {
    expect(isCredentialError(room({
      last_error: '该直播间当前未开播。',
      pipeline_health: {
        platform: 'OFFLINE',
        failure_kind: 'OFFLINE',
      },
    }))).toBe(false)
  })

  it('offline text takes precedence over mixed legacy credential text', () => {
    expect(isCredentialError(room({
      last_error: '主播未开播，Cookie 尚未配置',
    }))).toBe(false)
  })

  it('does not treat passive credential status as an active failure', () => {
    expect(isCredentialError(room({
      pipeline_health: {
        platform: 'OFFLINE',
        failure_kind: '',
      },
    }))).toBe(false)
  })

  it('shows credential action for typed authentication failures', () => {
    expect(isCredentialError(room({
      pipeline_health: {
        platform: 'AUTH_REQUIRED',
        failure_kind: 'AUTH_REQUIRED',
      },
    }))).toBe(true)
    expect(isCredentialError(room({
      last_error: '需要登录凭证，请检查 Cookie 配置。',
    }))).toBe(true)
  })
})

