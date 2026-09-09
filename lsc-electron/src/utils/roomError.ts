/**
 * Classify room connection errors for user-facing recovery actions.
 *
 * credential_status is a passive credential snapshot. It must not, by itself,
 * decide that the current connection failed because credentials are missing:
 * a room can be offline while the platform still reports NOT_CONFIGURED.
 */

export interface RoomErrorContext {
  last_error?: string | null
  mse_error?: string | null
  pipeline_health?: {
    platform?: string | null
    failure_kind?: string | null
    error?: string | null
  } | null
}

const AUTH_FAILURE_KINDS = new Set(['AUTH_REQUIRED', 'AUTH_EXPIRED'])

const OFFLINE_ERROR_RE = /(?:未开播|未直播|下播|直播已结束|直播间已结束|不在直播|\boffline\b|\bnot\s+live\b)/i

const AUTH_ERROR_RE = /(?:cookie|验证中间页|验证码|login\s+required|auth[_ -]?required|auth[_ -]?expired|需要登录|登录态已过期|需要.{0,8}(?:凭证|cookie))/i

function errorText(room: RoomErrorContext): string {
  return [
    room.last_error,
    room.mse_error,
    room.pipeline_health?.error,
  ]
    .filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
    .join(' ')
}

/** Whether the current room error requires the user to configure credentials. */
export function isCredentialError(room: RoomErrorContext): boolean {
  const health = room.pipeline_health
  const failureKind = String(health?.failure_kind || '').toUpperCase()
  const platformStatus = String(health?.platform || '').toUpperCase()
  const text = errorText(room)

  // Offline is a terminal room-state result for this attempt. It has
  // precedence over passive credential metadata and misleading mixed text.
  if (failureKind === 'OFFLINE' || OFFLINE_ERROR_RE.test(text)) return false

  // Typed auth state is authoritative even when the user-facing message is
  // localized or omitted. Platform AUTH_REQUIRED is also an active failure,
  // unlike credential_status=NOT_CONFIGURED.
  if (AUTH_FAILURE_KINDS.has(failureKind) || platformStatus === 'AUTH_REQUIRED') return true

  // Legacy adapters may only provide a human-readable error string.
  return AUTH_ERROR_RE.test(text)
}

