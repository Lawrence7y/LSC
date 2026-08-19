import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { message } from 'antd'
import { t } from '@/i18n'

type OnFn = (type: string, handler: (data: any) => void) => () => void
type SendFn = (type: string, data?: any) => boolean

/** 单次添加房间的最大链接数 */
const MAX_ROOM_URLS_PER_ADD = 12

/** 模块级操作计数器，确保 validate request_id 唯一 */
let _addRoomOpCounter = 0

export type RoomUrlValidationState = {
  status: 'idle' | 'checking' | 'success' | 'error'
  message: string
}

export type RoomUrlValidationResult = {
  valid?: boolean
  url?: string
  normalized_url?: string
  platform_name?: string
  streamer?: string
  is_live?: boolean
  warning?: string
  message?: string
  error?: string
  error_code?: string
}

/** 校验用户输入的直播间链接（支持多行多链接），返回规范化 URL 列表或错误。 */
export function parseRoomUrlsForValidation(raw: string): { urls: string[]; error?: string } {
  const urls = raw.split(/\r?\n/).map(item => item.trim()).filter(Boolean)
  if (urls.length === 0) return { urls: [], error: t('请输入直播间链接') }
  if (urls.length > MAX_ROOM_URLS_PER_ADD) {
    return { urls: [], error: t('一次最多添加 {count} 个直播间', { count: MAX_ROOM_URLS_PER_ADD }) }
  }

  const seen = new Set<string>()
  for (const url of urls) {
    if (url.length > 2048) return { urls: [], error: t('直播间链接过长') }
    if (/\s/.test(url)) return { urls: [], error: t('每行只能填写一个完整链接，链接中不能包含空格') }
    let parsed: URL
    try {
      parsed = new URL(url)
    } catch {
      return { urls: [], error: t('链接格式无效：{url}', { url }) }
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return { urls: [], error: t('仅支持 http:// 或 https:// 直播间链接') }
    }
    if (!parsed.hostname || parsed.username || parsed.password) {
      return { urls: [], error: t('链接缺少有效域名或包含不安全的登录信息：{url}', { url }) }
    }
    const duplicateKey = `${parsed.protocol}//${parsed.host}${parsed.pathname.replace(/\/+$/, '')}${parsed.search}`.toLowerCase()
    if (seen.has(duplicateKey)) {
      return { urls: [], error: t('输入中存在重复链接：{url}', { url }) }
    }
    seen.add(duplicateKey)
  }
  return { urls }
}

/**
 * 添加房间流程（从 Workbench 拆出，降低巨型组件体积）。
 *
 * 职责：直播间链接输入态、批量校验、逐条 add_room 提交与结果汇总。
 *
 * @param pendingRoomSavesRef 由 Workbench 持有并共享的房间变更计数（rooms_updated /
 *        useRoomActions 也会读写），本 hook 在 add_room 成功入队时递增、失败时递减。
 */
export function useAddRoom(opts: {
  on: OnFn
  send: SendFn
  pendingRoomSavesRef: MutableRefObject<number>
}) {
  const { on, send, pendingRoomSavesRef } = opts

  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [roomUrlValidation, setRoomUrlValidation] = useState<RoomUrlValidationState>({
    status: 'idle',
    message: '',
  })

  const pendingAddCountRef = useRef(0)
  const pendingAddUrlRef = useRef('')
  const pendingAddHadErrorRef = useRef(false)
  const pendingValidationRequestRef = useRef('')

  useEffect(() => {
    const unsubs: (() => void)[] = []

    unsubs.push(on('validate_room_urls_response', (data: {
      success?: boolean
      valid?: boolean
      results?: RoomUrlValidationResult[]
      error?: string
      request_id?: string
    }) => {
      if (data?.request_id && data.request_id !== pendingValidationRequestRef.current) return
      pendingValidationRequestRef.current = ''

      const results = Array.isArray(data?.results) ? data.results : []
      const invalidResults = results.filter(result => !result.valid)
      if (!data?.valid || invalidResults.length > 0 || results.length === 0) {
        const detail = invalidResults
          .slice(0, 2)
          .map(result => `${result.url || t('输入内容')}：${result.error || t('无法识别')}`)
          .join('；')
        const error = detail || data?.error || t('直播间链接未通过验证')
        setLoading(false)
        setRoomUrlValidation({ status: 'error', message: error })
        message.error(error)
        return
      }

      const normalizedUrls = results.map(result => result.normalized_url || result.url || '').filter(Boolean)
      if (normalizedUrls.length !== results.length) {
        const error = t('验证结果缺少有效链接，请重新输入后再试')
        setLoading(false)
        setRoomUrlValidation({ status: 'error', message: error })
        message.error(error)
        return
      }

      const firstResult = results[0]
      const validatedMessage = results.length === 1
        ? (firstResult.warning || `${firstResult.message || t('链接验证通过')}${firstResult.streamer ? `：${firstResult.streamer}` : ''}`)
        : t('{count} 个直播间链接均已验证通过，正在添加', { count: results.length })
      setRoomUrlValidation({ status: 'success', message: validatedMessage })

      pendingAddCountRef.current = normalizedUrls.length
      pendingAddHadErrorRef.current = false
      normalizedUrls.forEach(validatedUrl => {
        const sent = send('add_room', { url: validatedUrl })
        if (sent) {
          pendingRoomSavesRef.current += 1
        } else {
          pendingAddHadErrorRef.current = true
          pendingAddCountRef.current -= 1
        }
      })
      if (pendingAddCountRef.current === 0) {
        setLoading(false)
        setRoomUrlValidation({ status: 'error', message: t('后端未连接，已取消添加') })
      }
    }))

    unsubs.push(on('add_room_response', (data: { success?: boolean; error?: string; room_id?: string }) => {
      const failed = data?.success === false || !!data?.error
      if (failed) {
        pendingAddHadErrorRef.current = true
        if (pendingRoomSavesRef.current > 0) {
          pendingRoomSavesRef.current -= 1
        }
        message.error(data.error || t('添加房间失败'))
      }

      if (pendingAddCountRef.current > 0) {
        pendingAddCountRef.current -= 1
        if (pendingAddCountRef.current === 0) {
          setLoading(false)
          if (pendingAddHadErrorRef.current) {
            setUrl(pendingAddUrlRef.current)
            setRoomUrlValidation({
              status: 'error',
              message: t('链接验证已通过，但部分房间添加失败，请根据提示重试'),
            })
          } else {
            setUrl('')
            setRoomUrlValidation({ status: 'success', message: t('链接有效，直播间已添加') })
          }
          pendingAddUrlRef.current = ''
        }
      } else {
        setLoading(false)
        if (failed && pendingAddUrlRef.current) {
          setUrl(pendingAddUrlRef.current)
        }
        pendingAddUrlRef.current = ''
      }
    }))

    unsubs.push(on('remove_room_response', (data: { error?: string }) => {
      if (data?.error && pendingRoomSavesRef.current > 0) {
        pendingRoomSavesRef.current -= 1
      }
    }))

    return () => unsubs.forEach(u => u())
  }, [on, send, pendingRoomSavesRef])

  const handleAddRoom = async () => {
    if (loading) return
    const input = url.trim()
    const parsed = parseRoomUrlsForValidation(input)
    if (parsed.error) {
      setRoomUrlValidation({ status: 'error', message: parsed.error })
      message.warning(parsed.error)
      return
    }

    const requestId = `validate-room-${Date.now()}-${++_addRoomOpCounter}`
    pendingValidationRequestRef.current = requestId
    pendingAddUrlRef.current = input
    pendingAddHadErrorRef.current = false
    pendingAddCountRef.current = 0
    setLoading(true)
    setRoomUrlValidation({
      status: 'checking',
      message: parsed.urls.length > 1
        ? t('正在验证 {count} 个直播间链接…', { count: parsed.urls.length })
        : t('正在连接平台验证直播间链接…'),
    })
    const sent = send('validate_room_urls', {
      urls: parsed.urls,
      request_id: requestId,
    })
    if (!sent) {
      pendingValidationRequestRef.current = ''
      setLoading(false)
      setRoomUrlValidation({ status: 'error', message: t('后端未连接，无法验证直播间链接') })
    }
  }

  return {
    url,
    setUrl,
    loading,
    roomUrlValidation,
    setRoomUrlValidation,
    handleAddRoom,
  }
}
