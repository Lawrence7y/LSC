type MessageHandler = (data: unknown) => void

type WsLike = {
  send: (type: string, data: unknown) => void
  on: (event: string, handler: MessageHandler) => () => void
}

/** 发送 WebSocket 请求并等待 `{type}_response`。 */
export function sendRequest(
  ws: WsLike,
  type: string,
  data: unknown,
  timeoutMs = 10000,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const responseType = `${type}_response`
    const timer = setTimeout(() => {
      unsub()
      reject(new Error(`timeout waiting for ${responseType}`))
    }, timeoutMs)
    const unsub = ws.on(responseType, (resp) => {
      clearTimeout(timer)
      unsub()
      resolve(resp)
    })
    ws.send(type, data)
  })
}
