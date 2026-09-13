import { describe, expect, it } from 'vitest'
import {
  Fmp4BoxSplitter,
  parseVideoTrack,
  pickStartFragmentIndex,
  readBoxHeader,
  readTfdtSeconds,
} from './fmp4Box'

function u32(value: number): Uint8Array {
  return new Uint8Array([(value >>> 24) & 0xff, (value >>> 16) & 0xff, (value >>> 8) & 0xff, value & 0xff])
}

function u64(value: number): Uint8Array {
  return new Uint8Array([...u32(Math.floor(value / 4294967296)), ...u32(value >>> 0)])
}

function box(type: string, payload: Uint8Array): Uint8Array {
  const out = new Uint8Array(8 + payload.length)
  out.set(u32(out.length), 0)
  out.set([...type].map((c) => c.charCodeAt(0)), 4)
  out.set(payload, 8)
  return out
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0)
  const out = new Uint8Array(total)
  let cursor = 0
  for (const p of parts) {
    out.set(p, cursor)
    cursor += p.length
  }
  return out
}

/** 最小可用 moov：1 条视频轨（tkhd/mdhd/hdlr） */
function buildMoov(timescale = 90000, trackId = 1): Uint8Array {
  const tkhd = box('tkhd', concat(new Uint8Array([0, 0, 0, 0]), u32(0), u32(0), u32(trackId), u32(0), u32(0), u32(0), u32(0), u32(0)))
  const mdhd = box('mdhd', concat(new Uint8Array([0, 0, 0, 0]), u32(0), u32(0), u32(timescale), u32(0), new Uint8Array([0x55, 0xc4, 0, 0])))
  const hdlr = box('hdlr', concat(new Uint8Array([0, 0, 0, 0]), u32(0), new Uint8Array([0x76, 0x69, 0x64, 0x65]), new Uint8Array(12)))
  const mdia = box('mdia', concat(mdhd, hdlr))
  const trak = box('trak', concat(tkhd, mdia))
  return box('moov', trak)
}

function buildMoof(ptsTicks: number, trackId = 1): Uint8Array {
  const tfhd = box('tfhd', concat(new Uint8Array([0, 0, 0, 0]), u32(trackId)))
  const tfdt = box('tfdt', concat(new Uint8Array([1, 0, 0, 0]), u64(ptsTicks)))
  const traf = box('traf', concat(tfhd, tfdt))
  return box('moof', traf)
}

const FTYP = box('ftyp', new Uint8Array([0x69, 0x73, 0x6f, 0x6d, 0, 0, 2, 0]))

describe('readBoxHeader', () => {
  it('解析 32 位 size 的顶层盒', () => {
    const bytes = concat(FTYP, new Uint8Array(4))
    const header = readBoxHeader(bytes, 0)
    expect(header?.type).toBe('ftyp')
    expect(header?.headerSize).toBe(8)
    expect(header?.end).toBe(FTYP.length)
  })

  it('数据不足时返回 null', () => {
    expect(readBoxHeader(new Uint8Array([0, 0, 0]), 0)).toBeNull()
  })
})

describe('parseVideoTrack', () => {
  it('取出视频轨 trackId 与 timescale', () => {
    expect(parseVideoTrack(buildMoov(90000, 1))).toEqual({ trackId: 1, timescale: 90000 })
  })

  it('无 hdlr 时退化为首个 trak', () => {
    const noHdlr = box('moov', box('trak', concat(
      box('tkhd', concat(new Uint8Array(4), u32(0), u32(0), u32(7))),
      box('mdia', box('mdhd', concat(new Uint8Array(4), u32(0), u32(0), u32(48000)))),
    )))
    expect(parseVideoTrack(noHdlr)).toEqual({ trackId: 7, timescale: 48000 })
  })
})

describe('readTfdtSeconds', () => {
  it('按视频轨 timescale 换算 tfdt', () => {
    const track = { trackId: 1, timescale: 90000 }
    expect(readTfdtSeconds(buildMoof(90000 * 12), track)).toBeCloseTo(12, 6)
  })

  it('多轨时优先匹配指定轨道', () => {
    const moof = box('moof', concat(
      box('traf', concat(box('tfhd', concat(new Uint8Array(4), u32(2))), box('tfdt', concat(new Uint8Array([1, 0, 0, 0]), u64(48000 * 5))))),
      box('traf', concat(box('tfhd', concat(new Uint8Array(4), u32(1))), box('tfdt', concat(new Uint8Array([1, 0, 0, 0]), u64(90000 * 11))))),
    ))
    expect(readTfdtSeconds(moof, { trackId: 1, timescale: 90000 })).toBeCloseTo(11, 6)
  })
})

describe('Fmp4BoxSplitter', () => {
  it('分片推送也能拼出 init 段与 media 段', () => {
    const splitter = new Fmp4BoxSplitter()
    const moof = buildMoof(90000 * 3)
    const mdat = box('mdat', new Uint8Array(64))
    const stream = concat(FTYP, buildMoov(), moof, mdat)

    // 逐字节推送，验证增量解析
    let init: Uint8Array | null = null
    const segments: Array<{ tPtsSec: number | null; offset: number; bytes: Uint8Array }> = []
    for (let i = 0; i < stream.length; i += 1) {
      const result = splitter.push(stream.subarray(i, i + 1))
      if (result.init) init = result.init
      segments.push(...result.segments)
    }

    expect(init).not.toBeNull()
    expect(init!.length).toBe(FTYP.length + buildMoov().length)
    expect(splitter.track).toEqual({ trackId: 1, timescale: 90000 })
    expect(segments).toHaveLength(1)
    expect(segments[0].tPtsSec).toBeCloseTo(3, 6)
    expect(segments[0].offset).toBe(init!.length)
    expect(segments[0].bytes.length).toBe(moof.length + mdat.length)
  })

  it('不完整 box 不产出分段，补齐后一次产出', () => {
    const splitter = new Fmp4BoxSplitter()
    splitter.push(concat(FTYP, buildMoov()))
    const moof = buildMoof(0)
    const mdat = box('mdat', new Uint8Array(32))
    const partial = concat(moof, mdat).subarray(0, moof.length + 4)
    expect(splitter.push(partial).segments).toHaveLength(0)
    const rest = concat(moof, mdat).subarray(moof.length + 4)
    expect(splitter.push(rest).segments).toHaveLength(1)
  })

  it('跳过 sidx/free 等元数据盒，styp 随 moof 一起进入分段', () => {
    const splitter = new Fmp4BoxSplitter()
    splitter.push(concat(FTYP, buildMoov()))
    const sidx = box('sidx', new Uint8Array(16))
    const free = box('free', new Uint8Array(4))
    const styp = box('styp', new Uint8Array(16))
    const moof = buildMoof(0)
    const mdat = box('mdat', new Uint8Array(16))
    const result = splitter.push(concat(sidx, free, styp, moof, mdat))
    expect(result.segments).toHaveLength(1)
    expect(result.segments[0].bytes.length).toBe(styp.length + moof.length + mdat.length)
  })

  it('多个 media 段按顺序产出且偏移连续', () => {
    const splitter = new Fmp4BoxSplitter()
    const initResult = splitter.push(concat(FTYP, buildMoov()))
    const initLen = initResult.init!.length
    const part = (n: number) => concat(buildMoof(90000 * n), box('mdat', new Uint8Array(16)))
    const a = part(1)
    const b = part(2)
    const result = splitter.push(concat(a, b))
    expect(result.segments.map((s) => s.tPtsSec)).toEqual([1, 2])
    expect(result.segments[0].offset).toBe(initLen)
    expect(result.segments[1].offset).toBe(initLen + a.length)
  })
})

describe('pickStartFragmentIndex', () => {
  const index = [{ tPtsSec: 10 }, { tPtsSec: 12 }, { tPtsSec: 14 }, { tPtsSec: 16 }]

  it('选中包含目标时间的分段', () => {
    expect(pickStartFragmentIndex(index, 13)).toBe(1)
    expect(pickStartFragmentIndex(index, 15.9)).toBe(2)
  })

  it('早于首段时退回 0，晚于末段时取末段', () => {
    expect(pickStartFragmentIndex(index, 0)).toBe(0)
    expect(pickStartFragmentIndex(index, 999)).toBe(3)
  })

  it('空索引返回 0', () => {
    expect(pickStartFragmentIndex([], 5)).toBe(0)
  })
})
