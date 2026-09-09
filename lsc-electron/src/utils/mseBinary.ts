/** MSE WebSocket 二进制帧解析（与 python-backend/mse_ws_frames.py 对齐，支持 v1 b'MSE' 与 v2 b'MS2' 双通道）。 */

const MSE_MAGIC_0 = 0x4d // 'M'
const MSE_MAGIC_1 = 0x53 // 'S'
const MSE_MAGIC_2 = 0x45 // 'E'
const MS2_MAGIC_2 = 0x32 // '2' (MS2)

const KIND_INIT = 1
const KIND_SEGMENT = 2

export type MseBinaryMessage = {
  type: 'mse_init' | 'mse_segment'
  roomId: string
  payload: ArrayBuffer
  channel: 'live' | 'review'
  streamId?: string
}

/** 若为 MSE 二进制帧则解析，否则返回 null（调用方按 JSON 文本处理）。 */
export function tryParseMseBinaryFrame(data: ArrayBuffer): MseBinaryMessage | null {
  if (data.byteLength < 6) return null
  const view = new DataView(data)
  if (view.getUint8(0) !== MSE_MAGIC_0 || view.getUint8(1) !== MSE_MAGIC_1) {
    return null
  }
  const magic2 = view.getUint8(2)

  // v1 格式: b'MSE'
  if (magic2 === MSE_MAGIC_2) {
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
    return { type, roomId, payload, channel: 'live', streamId: '' }
  }

  // v2 双通道格式: b'MS2'
  if (magic2 === MS2_MAGIC_2) {
    if (data.byteLength < 8) return null
    const kind = view.getUint8(3)
    const type = kind === KIND_INIT ? 'mse_init' : kind === KIND_SEGMENT ? 'mse_segment' : null
    if (!type) return null
    const chanByte = view.getUint8(4)
    const channel: 'live' | 'review' = chanByte === 1 ? 'review' : 'live'
    const sidLen = view.getUint8(5)
    const sidEnd = 6 + sidLen
    if (data.byteLength < sidEnd + 2) return null
    let streamId = ''
    if (sidLen > 0) {
      try {
        streamId = new TextDecoder('utf-8').decode(new Uint8Array(data, 6, sidLen))
      } catch {
        return null
      }
    }
    const ridLen = view.getUint16(sidEnd, false)
    const headerEnd = sidEnd + 2 + ridLen
    if (ridLen <= 0 || data.byteLength < headerEnd) return null
    let roomId: string
    try {
      roomId = new TextDecoder('utf-8').decode(new Uint8Array(data, sidEnd + 2, ridLen))
    } catch {
      return null
    }
    if (!roomId) return null
    const payload = data.slice(headerEnd)
    return { type, roomId, payload, channel, streamId }
  }

  return null
}
