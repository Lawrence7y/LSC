const assert = require('assert')
const fs = require('fs')
const path = require('path')
const ts = require('typescript')
const vm = require('vm')

function loadModule() {
  const sourcePath = path.resolve(__dirname, '../src/utils/roomSelection.ts')
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

function main() {
  const { resolveRoomSelection } = loadModule()

  // Helper to create rooms
  function rooms(count) {
    return Array.from({ length: count }, (_, i) => ({ room_id: String(i + 1) }))
  }

  // Helper to create set from array
  function set(...ids) {
    return new Set(ids)
  }

  // Normal click: empty selection → click room A
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(new Set(), rooms(3), '1', 'normal', null)),
    ['1']
  )

  // Normal click: {A} → click B → {A, B}
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1'), rooms(3), '2', 'normal', null)),
    ['1', '2']
  )

  // Normal click: {A, B} → click A → {A}
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1', '2'), rooms(3), '1', 'normal', null)),
    ['1']
  )

  // Normal click: {A} → click A → {A} (no change)
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1'), rooms(3), '1', 'normal', null)),
    ['1']
  )

  // Normal click: {A, B} → click C → {A, B, C}
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1', '2'), rooms(3), '3', 'normal', null)),
    ['1', '2', '3']
  )

  // Toggle: {C} → Ctrl+Click A → {C} (A was not selected)
  // Wait, toggle: if has, delete; if not, add
  // Starting with {C}, toggle A → {C, A}
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('3'), rooms(3), '1', 'toggle', null)),
    ['3', '1']
  )

  // Toggle: {A, B} → Ctrl+Click A → {B}
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1', '2'), rooms(3), '1', 'toggle', null)),
    ['2']
  )

  // Toggle: {A} → Ctrl+Click A → {}
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1'), rooms(3), '1', 'toggle', null)),
    []
  )

  // Range: rooms [A, B, C, D], lastIndex=0, click D → [A, B, C, D]
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1'), rooms(4), '4', 'range', 0)),
    ['1', '2', '3', '4']
  )

  // Range: rooms [A, B, C, D], lastIndex=2, click A → [A, B, C]
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1'), rooms(4), '1', 'range', 2)),
    ['1', '2', '3']
  )

  // Range: rooms [A, B, C, D], lastIndex=1, click D → [B, C, D]
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1'), rooms(4), '4', 'range', 1)),
    ['2', '3', '4']
  )

  // Edge: roomId not found in normal mode still adds to selection (matches handleSelect behavior)
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1'), rooms(3), '99', 'normal', null)),
    ['1', '99']
  )

  // Edge: roomId not found in range mode returns current selection unchanged
  assert.deepStrictEqual(
    Array.from(resolveRoomSelection(set('1'), rooms(3), '99', 'range', 0)),
    ['1']
  )

  console.log('All workbenchSelect tests passed')
  return Promise.resolve()
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
