const assert = require('assert')
const fs = require('fs')
const path = require('path')
const ts = require('typescript')
const vm = require('vm')

function loadModule() {
  const sourcePath = path.resolve(__dirname, '../electron/backendUrl.ts')
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
  }, { filename: sourcePath })
  return module.exports
}

function main() {
  const { extractBackendWsUrl } = loadModule()

  assert.strictEqual(
    extractBackendWsUrl('WebSocket server ready at ws://localhost:9878\n'),
    'ws://localhost:9878',
  )

  assert.strictEqual(
    extractBackendWsUrl('WebSocket server listening on ws://127.0.0.1:9877\r\n'),
    'ws://127.0.0.1:9877',
  )

  assert.strictEqual(
    extractBackendWsUrl('[info] no websocket URL here'),
    null,
  )
}

main()
