type MessageHandler = (data: unknown) => void

type WsLike = {
  send: (type: string, data: unknown) => boolean
  on: (event: string, handler: MessageHandler) => () => void
}

let _requestSeq = 0

/**
 * 发送 WebSocket 请求并等待 `{type}_response`。
 *
 * 防串话：payload 为对象时自动注入 `request_id`（后端 server.py 会在响应中
 * 原样回显），响应处理器只采纳携带匹配 request_id 的响应——并发同类型请求
 * 不会交叉 resolve。payload 非对象或后端未回显时退化为"首个响应胜出"（旧行为）。
 *
 * fail-fast：`ws.send` 返回 false（断连且不可入队）时立即 reject，不干等超时。
 */
export function sendRequest(
  ws: WsLike,
  type: string,
  data: unknown,
  timeoutMs = 10000,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const responseType = `${type}_response`
    const requestId = `req-${Date.now()}-${++_requestSeq}`
    const payload =
      data !== null && typeof data === 'object' && !Array.isArray(data)
        ? { ...(data as Record<string, unknown>), request_id: requestId }
        : data

    const timer = setTimeout(() => {
      unsub()
      reject(new Error(`timeout waiting for ${responseType}`))
    }, timeoutMs)
    const unsub = ws.on(responseType, (resp) => {
      // 后端回显了 request_id 的响应必须匹配；未回显的响应按旧行为直接采纳
      const respReqId = (resp as Record<string, unknown> | null)?.request_id
      if (respReqId !== undefined && respReqId !== requestId) return
      clearTimeout(timer)
      unsub()
      resolve(resp)
    })
    if (!ws.send(type, payload)) {
      clearTimeout(timer)
      unsub()
      reject(new Error(`send failed (not connected): ${type}`))
    }
  })
}
