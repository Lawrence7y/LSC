const assert = require('assert')
const fs = require('fs')
const path = require('path')
const ts = require('typescript')
const vm = require('vm')

function loadShouldQueueWhenDisconnected() {
  const sourcePath = path.resolve(__dirname, '../src/services/websocket.ts')
  const source = fs.readFileSync(sourcePath, 'utf8')
  const setBlock = source.match(
    /const DISCONNECTED_QUEUEABLE_TYPES = new Set\(\[[\s\S]*?\]\)/,
  )
  const fnBlock = source.match(
    /export function shouldQueueWhenDisconnected\(type: string\): boolean \{[\s\S]*?\n\}/,
  )
  if (!setBlock || !fnBlock) {
    throw new Error('Failed to extract disconnected queue policy from websocket.ts')
  }
  const isolated = `${setBlock[0]}\n${fnBlock[0].replace('export function', 'function')}\nmodule.exports = { shouldQueueWhenDisconnected }`
  const compiled = ts.transpileModule(isolated, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText
  const module = { exports: {} }
  vm.runInNewContext(compiled, { exports: module.exports, module, console }, {
    filename: sourcePath,
  })
  return module.exports.shouldQueueWhenDisconnected
}

function main() {
  const shouldQueueWhenDisconnected = loadShouldQueueWhenDisconnected()

  assert.strictEqual(shouldQueueWhenDisconnected('start_recording'), false)
  assert.strictEqual(shouldQueueWhenDisconnected('get_rooms'), true)
  assert.strictEqual(shouldQueueWhenDisconnected('save_settings'), false)
  assert.strictEqual(shouldQueueWhenDisconnected('export_clip'), false)

  const websocketSource = fs.readFileSync(
    path.resolve(__dirname, '../src/services/websocket.ts'),
    'utf8',
  )
  assert.match(websocketSource, /send\(type: string, data: unknown\): boolean/)
  assert.match(websocketSource, /return false/)

  const hookSource = fs.readFileSync(
    path.resolve(__dirname, '../src/hooks/useWebSocket.ts'),
    'utf8',
  )
  assert.match(hookSource, /DISCONNECTED_SEND_WARNING/)
  assert.match(hookSource, /未连接后端，操作未发送/)
  assert.match(hookSource, /message\.warning\(DISCONNECTED_SEND_WARNING\)/)
}

main()
