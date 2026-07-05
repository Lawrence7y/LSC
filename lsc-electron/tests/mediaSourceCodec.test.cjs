const assert = require('assert')
const fs = require('fs')
const path = require('path')
const ts = require('typescript')
const vm = require('vm')

function loadModule() {
  const sourcePath = path.resolve(__dirname, '../src/services/mediaSourcePlayer.ts')
  const source = fs.readFileSync(sourcePath, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText
  const module = { exports: {} }
  vm.runInNewContext(compiled, {
    exports: module.exports,
    module,
    require,
    console,
    setTimeout,
    clearTimeout,
  }, { filename: sourcePath })
  return module.exports
}

function bytes(...values) {
  return new Uint8Array(values)
}

function ascii(text) {
  return Uint8Array.from(Buffer.from(text, 'ascii'))
}

function concat(...parts) {
  const total = parts.reduce((sum, part) => sum + part.length, 0)
  const out = new Uint8Array(total)
  let offset = 0
  for (const part of parts) {
    out.set(part, offset)
    offset += part.length
  }
  return out
}

function main() {
  const { getMp4MimeFromInitSegment } = loadModule()

  const init = concat(
    bytes(0, 0, 0, 24),
    ascii('ftyp'),
    ascii('isom'),
    bytes(0, 0, 0, 0),
    bytes(0, 0, 0, 18),
    ascii('avcC'),
    bytes(1, 0x42, 0xc0, 0x2a),
    bytes(0xff, 0xe1, 0, 0, 0, 0),
    bytes(0, 0, 0, 8),
    ascii('mp4a'),
  )

  assert.strictEqual(
    getMp4MimeFromInitSegment(init),
    'video/mp4; codecs="avc1.42c02a,mp4a.40.2"',
  )

  const videoOnly = concat(
    bytes(0, 0, 0, 18),
    ascii('avcC'),
    bytes(1, 0x64, 0x00, 0x1f),
    bytes(0xff, 0xe1, 0, 0, 0, 0),
  )

  assert.strictEqual(
    getMp4MimeFromInitSegment(videoOnly),
    'video/mp4; codecs="avc1.64001f"',
  )
}

main()
