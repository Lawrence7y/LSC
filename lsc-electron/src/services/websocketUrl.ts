export const DEFAULT_WS_URL = 'ws://localhost:9876'

export interface WebSocketEnv {
  VITE_WS_URL?: string
}

export interface BackendElectronApi {
  getBackendWsUrl?: () => Promise<string | null>
}

export async function resolveWebSocketUrl(
  env: WebSocketEnv = {},
  electronAPI?: BackendElectronApi,
): Promise<string> {
  const envUrl = env.VITE_WS_URL?.trim()
  if (envUrl) {
    return envUrl
  }

  if (electronAPI?.getBackendWsUrl) {
    const backendUrl = await electronAPI.getBackendWsUrl()
    if (backendUrl?.trim()) {
      return backendUrl
    }
    throw new Error('Electron backend WebSocket URL is not ready')
  }

  return DEFAULT_WS_URL
}
