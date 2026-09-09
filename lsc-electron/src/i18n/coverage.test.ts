import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { enDict } from './en'

/**
 * en-US 词典覆盖率守卫。
 *
 * 背景：`t()` 未命中时回退中文原文，所以漏翻译不会崩，只会让英文界面中英混排
 * —— 属于「看起来没问题、实际到处掉细节」的那类缺陷，人工发现不了，CI 也不管。
 * 本测试把「源码里出现的每个 t() 文案都必须在 en-US 词典里有键」变成硬约束。
 *
 * 同时反向报告「词典里有、代码里已不再用」的僵键（只 warn，不阻断，
 * 避免历史文案改动被这个测试卡住）。
 */

const SRC = join(process.cwd(), 'src')

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      if (entry === 'i18n') continue // 词典自身不参与扫描
      sourceFiles(full, out)
    } else if (/\.(tsx|ts)$/.test(entry) && !/\.test\.(tsx|ts)$/.test(entry)) {
      out.push(full)
    }
  }
  return out
}

/** 抽取 t('...') 与 t("...") 的字面量首参（含 ${} 模板的静态前缀不参与，只取纯字面量） */
function extractKeys(code: string): string[] {
  const keys: string[] = []
  const re = /\bt\(\s*((?:'(?:[^'\\]|\\.)*')|(?:"(?:[^"\\]|\\.)*"))/g
  for (const m of code.matchAll(re)) {
    const raw = m[1].slice(1, -1)
    // 还原转义
    keys.push(raw.replace(/\\'/g, "'").replace(/\\"/g, '"').replace(/\\\\/g, '\\'))
  }
  // 词典分片里 label: '...' 形式的文档键（SHORTCUT_DOCS / MOUSE_DOCS 经 t(row.label) 消费）
  const labelRe = /\blabel:\s*'([^']+)'/g
  for (const m of code.matchAll(labelRe)) keys.push(m[1])
  const descRe = /\bdesc:\s*'([^']+)'/g
  for (const m of code.matchAll(descRe)) keys.push(m[1])
  return keys
}

const files = sourceFiles(SRC)

function rel(p: string): string {
  return relative(process.cwd(), p).replace(/\\/g, '/')
}

describe('en-US 词典覆盖率', () => {
  it('源码中每个中文 t() 文案都有 en-US 翻译（否则英文界面会漏出中文）', () => {
    const missing: string[] = []
    for (const f of files) {
      for (const k of new Set(extractKeys(readFileSync(f, 'utf8')))) {
        // 纯 ASCII 文案（如 'LIVE'）本身即英文，不需要词典条目
        if (/[\u4e00-\u9fff]/.test(k) && !(k in enDict)) {
          missing.push(`${rel(f)}\n    ${k}`)
        }
      }
    }
    expect(missing, `缺少 en-US 翻译的文案：\n${missing.join('\n')}`).toEqual([])
  })
})

describe('en-US 词典分片一致性', () => {
  it('同一中文键在所有分片中的译文必须一致（对象展开合并时后写覆盖先写，冲突译文会被静默吞掉）', () => {
    const values = new Map<string, { file: string; value: string }[]>()
    for (const entry of readdirSync(__dirname)) {
      if (!/^en\.part\.\w+\.ts$/.test(entry)) continue
      const text = readFileSync(join(__dirname, entry), 'utf8')
      const re = /['"]([^'"]+)['"]\s*:\s*['"]([^'"]*)['"]/g
      for (const m of text.matchAll(re)) {
        const [, key, value] = m
        if (!/[\u4e00-\u9fff]/.test(key)) continue
        const list = values.get(key) ?? []
        list.push({ file: entry, value })
        values.set(key, list)
      }
    }
    const conflicts = [...values.entries()].filter(
      ([, list]) => new Set(list.map((item) => item.value)).size > 1,
    )
    const detail = conflicts
      .map(([key, list]) => `${key}\n${list.map((i) => `    ${i.file}: ${i.value}`).join('\n')}`)
      .join('\n')
    expect(conflicts, `同一键存在冲突译文（后加载分片会静默覆盖先加载的）：\n${detail}`).toEqual([])
  })
})
