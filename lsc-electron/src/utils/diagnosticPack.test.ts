import { describe, expect, it } from 'vitest'
import {
  buildDiagnosticEntries,
  buildManifest,
  buildZipStore,
  redactText,
  sanitizeLogTail,
  sanitizeSettings,
  tailLines,
} from '../../electron/diagnosticPack'

describe('redactText：Cookie/Token 不得外泄', () => {
  it('打码 Cookie 头整块（含多对 SESSDATA）', () => {
    const raw = 'ffmpeg -headers "Cookie: SESSDATA=abc123; bili_jct=def456"\nok'
    const out = redactText(raw)
    expect(out).not.toContain('abc123')
    expect(out).not.toContain('def456')
    expect(out).toContain('<redacted>')
  })

  it('打码 token= / Authorization: 行内凭据', () => {
    expect(redactText('token=super-secret-value')).not.toContain('super-secret-value')
    expect(redactText('Authorization: Bearer eyJhbGciOi')).not.toContain('eyJhbGciOi')
    expect(redactText('access_token: xyz')).not.toContain('xyz')
  })
})

describe('sanitizeSettings：设置快照禁 Cookie/Token', () => {
  it('敏感键整值打码，结构保留', () => {
    const input = {
      theme: 'dark',
      cookie: 'SESSDATA=leak',
      apiToken: 'tok_leak',
      nested: { password: 'pw_leak', quality: '原画' },
      list: [{ session: 'sess_leak' }, 'token=inline_leak'],
    }
    const out = sanitizeSettings(input) as Record<string, any>
    expect(out.theme).toBe('dark')
    expect(out.cookie).toBe('<redacted>')
    expect(out.apiToken).toBe('<redacted>')
    expect(out.nested.password).toBe('<redacted>')
    expect(out.nested.quality).toBe('原画')
    expect(out.list[0].session).toBe('<redacted>')
    expect(out.list[1]).not.toContain('inline_leak')
    const dump = JSON.stringify(out)
    expect(dump).not.toContain('leak')
    expect(dump).not.toContain('SESSDATA=')
  })

  it('普通字段与数值/布尔原样保留', () => {
    const out = sanitizeSettings({ crf: 23, dvr: true, note: 'ok' }) as Record<string, unknown>
    expect(out.crf).toBe(23)
    expect(out.dvr).toBe(true)
    expect(out.note).toBe('ok')
  })
})

describe('tailLines / sanitizeLogTail', () => {
  it('只保留尾部 N 行', () => {
    expect(tailLines('a\nb\nc\nd', 2)).toBe('c\nd')
    expect(tailLines('only', 10)).toBe('only')
  })

  it('尾部日志同样脱敏', () => {
    const log = ['line1', 'Cookie: sess=bad', 'line3'].join('\n')
    const out = sanitizeLogTail(log, 10)
    expect(out).toContain('line1')
    expect(out).not.toContain('bad')
  })
})

describe('buildManifest', () => {
  it('带上 version / platform / windowsStore', () => {
    const m = buildManifest({ version: '1.0.17', platform: 'win32', windowsStore: true })
    expect(m.version).toBe('1.0.17')
    expect(m.platform).toBe('win32')
    expect(m.windowsStore).toBe(true)
    expect(m.generatedAt).toBeTruthy()
  })
})

describe('buildZipStore / buildDiagnosticEntries', () => {
  it('zip 含 manifest、脱敏设置与日志尾部，且无 Cookie/Token 原文', () => {
    const entries = buildDiagnosticEntries({
      manifest: buildManifest({ version: '1.0.17', platform: 'win32', windowsStore: true }),
      settingsSnapshot: { cookie: 'SESSDATA=topsecret', theme: 'dark' },
      logs: [{ name: 'debug.log', content: 'ok\ntoken=bottomsecret' }],
    })
    const names = entries.map((e: { name: string }) => e.name)
    expect(names).toContain('manifest.json')
    expect(names).toContain('settings.sanitized.json')
    expect(names).toContain('logs/debug.log')

    const zip = buildZipStore(entries)
    expect(zip.length).toBeGreaterThan(100)
    // ZIP 魔数
    expect(zip.readUInt32LE(0)).toBe(0x04034b50)
    // EOCD 魔数
    expect(zip.readUInt32LE(zip.length - 22)).toBe(0x06054b50)

    const asText = zip.toString('latin1')
    expect(asText).not.toContain('topsecret')
    expect(asText).not.toContain('bottomsecret')
    expect(asText).toContain('manifest.json')
    expect(asText).toContain('windowsStore')
  })
})
