const BACKEND_WS_URL_RE = /\bWebSocket server (?:ready at|listening on)\s+(ws:\/\/(?:localhost|127\.0\.0\.1):\d+)/i

export function extractBackendWsUrl(output: string): string | null {
  const match = BACKEND_WS_URL_RE.exec(output)
  return match ? match[1] : null
}
