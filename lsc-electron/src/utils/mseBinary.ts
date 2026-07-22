/** MSE WebSocket 二进制帧解析（与 python-backend/mse_ws_frames.py 对齐）。 */

const MSE_MAGIC_0 = 0x4d // 'M'
const MSE_MAGIC_1 = 0x53 // 'S'
const MSE_MAGIC_2 = 0x45 // 'E'
const KIND_INIT = 1
const KIND_SEGMENT = 2

export type MseBinaryMessage = {
  type: 'mse_init' | 'mse_segment'
  roomId: string
  payload: ArrayBuffer
}

/** 若为 MSE 二进制帧则解析，否则返回 null（调用方按 JSON 文本处理）。 */
export function tryParseMseBinaryFrame(data: ArrayBuffer): MseBinaryMessage | null {
  if (data.byteLength < 6) return null
  const view = new DataView(data)
  if (
    view.getUint8(0) !== MSE_MAGIC_0 ||
    view.getUint8(1) !== MSE_MAGIC_1 ||
    view.getUint8(2) !== MSE_MAGIC_2
  ) {
    return null
  }
  const kind = view.getUint8(3)
  const type = kind === KIND_INIT ? 'mse_init' : kind === KIND_SEGMENT ? 'mse_segment' : null
  if (!type) return null
  const ridLen = view.getUint16(4, false)
  const headerEnd = 6 + ridLen
  if (ridLen <= 0 || data.byteLength < headerEnd) return null
  const ridBytes = new Uint8Array(data, 6, ridLen)
  let roomId: string
  try {
    roomId = new TextDecoder('utf-8').decode(ridBytes)
  } catch {
    return null
  }
  if (!roomId) return null
  const payload = data.slice(headerEnd)
  return { type, roomId, payload }
}
