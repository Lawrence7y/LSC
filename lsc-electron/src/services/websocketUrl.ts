export const DEFAULT_WS_URL = 'ws://127.0.0.1:9876'

export interface WebSocketEnv {
  VITE_WS_URL?: string
}

export interface BackendElectronApi {
  getBackendWsUrl?: () => Promise<string | null>
  getBackendWsToken?: () => Promise<string | null>
}

export async function resolveWebSocketUrl(
  env: WebSocketEnv = {},
  electronAPI?: BackendElectronApi,
): Promise<string> {
  const envUrl = env.VITE_WS_URL?.trim()
  let resolvedUrl: string

  if (envUrl) {
    resolvedUrl = envUrl
  } else if (electronAPI?.getBackendWsUrl) {
    const backendUrl = await electronAPI.getBackendWsUrl()
    if (backendUrl?.trim()) {
      resolvedUrl = backendUrl
    } else {
      // IPC 返回 null（后端 URL 尚未就绪）时降级尝试默认 URL，
      // 避免 Electron 模式下因后端 URL 检测延迟而完全无法连接
      if (typeof console !== 'undefined' && console.warn) {
        console.warn('[resolveWebSocketUrl] Backend WS URL not ready via IPC, fallback to default:', DEFAULT_WS_URL)
      }
      resolvedUrl = DEFAULT_WS_URL
    }
  } else {
    resolvedUrl = DEFAULT_WS_URL
  }

  // Token 不再通过 URL 传递（安全修复：避免 Token 泄露到代理/网关日志）
  // 改为 WebSocket 握手后首帧认证（wsClient.connect 后发送 auth 消息）
  return resolvedUrl
}
