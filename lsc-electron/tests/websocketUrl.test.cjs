const assert = require('assert')
const fs = require('fs')
const path = require('path')
const ts = require('typescript')
const vm = require('vm')

function loadModule() {
  const sourcePath = path.resolve(__dirname, '../src/services/websocketUrl.ts')
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

async function main() {
  const { DEFAULT_WS_URL, resolveWebSocketUrl } = loadModule()

  assert.strictEqual(
    await resolveWebSocketUrl(
      { VITE_WS_URL: 'ws://localhost:9999' },
      undefined,
    ),
    'ws://localhost:9999',
  )

  assert.strictEqual(
    await resolveWebSocketUrl(
      {},
      { getBackendWsUrl: async () => 'ws://localhost:9878' },
    ),
    'ws://localhost:9878',
  )

  await assert.rejects(
    () => resolveWebSocketUrl(
      {},
      { getBackendWsUrl: async () => null },
    ),
    /not ready/i,
  )

  assert.strictEqual(
    await resolveWebSocketUrl({}, undefined),
    DEFAULT_WS_URL,
  )
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
