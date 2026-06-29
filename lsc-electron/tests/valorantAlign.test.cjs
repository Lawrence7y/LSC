const assert = require('assert')
const fs = require('fs')
const path = require('path')
const ts = require('typescript')
const vm = require('vm')

// Mock canvas class
class MockCanvas {
  constructor() {
    this.width = 0
    this.height = 0
    this.drawImageCalls = []
  }
  getContext(type) {
    const self = this
    return {
      drawImage(...args) {
        self.drawImageCalls.push(args)
      },
    }
  }
}

// Mock Tesseract
function createMockTesseract() {
  const results = []
  let index = 0
  const instance = {
    pushResult(text) {
      results.push(text)
    },
    recognize: async () => {
      const text = results[index] || ''
      index++
      return { data: { text } }
    },
    reset() {
      results.length = 0
      index = 0
    },
  }
  const wrapper = { default: instance }
  wrapper.pushResult = instance.pushResult.bind(instance)
  wrapper.recognize = instance.recognize.bind(instance)
  wrapper.reset = instance.reset.bind(instance)
  return wrapper
}

function loadModule(mockTesseract) {
  const sourcePath = path.resolve(__dirname, '../src/services/valorantAlign.ts')
  const source = fs.readFileSync(sourcePath, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText
  const module = { exports: {} }
  const originalRequire = require
  const customRequire = function(request) {
    if (request === 'tesseract.js') {
      return mockTesseract
    }
    if (request === '@/hooks/useTesseractPreload') {
      return {
        getTesseractPreloadPromise: () => Promise.resolve(),
        getCachedTesseractWorker: () => null,
        setCachedTesseractWorker: () => {},
        markCachedTesseractWorkerFailed: () => {},
      }
    }
    return originalRequire.apply(this, arguments)
  }
  const context = vm.createContext({
    exports: module.exports,
    module,
    require: customRequire,
    console,
    setTimeout,
    clearTimeout,
    HTMLCanvasElement: MockCanvas,
    document: {
      createElement(tag) {
        if (tag === 'canvas') {
          return new MockCanvas()
        }
        return {}
      },
    },
  })
  vm.runInNewContext(compiled, context, { filename: sourcePath })
  return module.exports
}

function main() {
  const mockTesseract = createMockTesseract()
  const { isValorantStream, checkSameValorant, alignValorantStreams, ocrValorantTimer } = loadModule(mockTesseract)

  // isValorantStream tests
  assert.strictEqual(isValorantStream('无畏契约'), true)
  assert.strictEqual(isValorantStream('Valorant'), true)
  assert.strictEqual(isValorantStream('VALORANT'), true)
  assert.strictEqual(isValorantStream('valorant'), true)
  assert.strictEqual(isValorantStream('射击游戏-无畏契约'), true)
  assert.strictEqual(isValorantStream('英雄联盟'), false)
  assert.strictEqual(isValorantStream(''), false)
  assert.strictEqual(isValorantStream(undefined), false)

  // checkSameValorant tests
  assert.strictEqual(
    checkSameValorant(
      [{ room_id: '1', category: '无畏契约' }, { room_id: '2', category: 'Valorant' }],
      new Set(['1', '2'])
    ),
    true
  )
  assert.strictEqual(
    checkSameValorant(
      [{ room_id: '1', category: '无畏契约' }, { room_id: '2', category: '英雄联盟' }],
      new Set(['1', '2'])
    ),
    false
  )
  assert.strictEqual(
    checkSameValorant([{ room_id: '1', category: '' }], new Set(['1'])),
    false
  )

  // ocrValorantTimer tests
  async function testOcr() {
    const mockVideo = {
      videoWidth: 1920,
      videoHeight: 1080,
      currentTime: 100,
      buffered: { length: 1, end: () => 200 },
      play: () => Promise.resolve(),
    }

    // M:SS format with colon
    mockTesseract.reset()
    mockTesseract.pushResult('1:23')
    let result = await ocrValorantTimer(mockVideo)
    assert.strictEqual(result, 83)

    // M:SS format with semicolon (Tesseract misread)
    mockTesseract.reset()
    mockTesseract.pushResult('1;23')
    result = await ocrValorantTimer(mockVideo)
    assert.strictEqual(result, 83)

    // M:SS format with dot
    mockTesseract.reset()
    mockTesseract.pushResult('2.05')
    result = await ocrValorantTimer(mockVideo)
    console.log('dot test result:', result, 'expected:', 125)
    assert.strictEqual(result, 125)

    // SS format (bomb phase)
    mockTesseract.reset()
    mockTesseract.pushResult('45')
    result = await ocrValorantTimer(mockVideo)
    assert.strictEqual(result, 45)

    // Single digit pure number (matches numMatch pattern)
    mockTesseract.reset()
    mockTesseract.pushResult('5')
    result = await ocrValorantTimer(mockVideo)
    assert.strictEqual(result, 5)

    // Invalid text
    mockTesseract.reset()
    mockTesseract.pushResult('abc')
    result = await ocrValorantTimer(mockVideo)
    assert.strictEqual(result, null)

    // Empty text
    mockTesseract.reset()
    mockTesseract.pushResult('')
    result = await ocrValorantTimer(mockVideo)
    assert.strictEqual(result, null)

    // Zero videoWidth
    const zeroVideo = { videoWidth: 0, videoHeight: 1080 }
    mockTesseract.reset()
    result = await ocrValorantTimer(zeroVideo)
    assert.strictEqual(result, null)

    // Null video
    mockTesseract.reset()
    result = await ocrValorantTimer(null)
    assert.strictEqual(result, null)
  }

  // alignValorantStreams tests
  async function testAlign() {
    mockTesseract.reset()
    mockTesseract.pushResult('1:30')
    mockTesseract.pushResult('0:45')

    const registry = {
      room1: {
        player: {
          videoElement: {
            videoWidth: 1920,
            videoHeight: 1080,
            currentTime: 100,
            buffered: { length: 1, end: () => 200 },
            play: () => Promise.resolve(),
          },
        },
      },
      room2: {
        player: {
          videoElement: {
            videoWidth: 1920,
            videoHeight: 1080,
            currentTime: 45,
            buffered: { length: 1, end: () => 200 },
            play: () => Promise.resolve(),
          },
        },
      },
    }

    const result = await alignValorantStreams(registry, new Set(['room1', 'room2']))
    assert.strictEqual(result.success, true)
    assert.strictEqual(result.method, 'ocr')
    assert.strictEqual(result.details.length, 2)
    assert.strictEqual(result.details[0].gameTimer, 90)
    assert.strictEqual(result.details[1].gameTimer, 45)
    assert.strictEqual(result.details[0].seeked, false)
    assert.strictEqual(result.details[1].seeked, true)
  }

  // alignValorantStreams: OCR failure fallback
  async function testAlignFailure() {
    mockTesseract.reset()
    mockTesseract.pushResult('')
    mockTesseract.pushResult('')

    const registry = {
      room1: { player: { videoElement: { videoWidth: 1920, videoHeight: 1080, buffered: { length: 1, end: () => 200 }, play: () => Promise.resolve() } } },
      room2: { player: { videoElement: { videoWidth: 1920, videoHeight: 1080, buffered: { length: 1, end: () => 200 }, play: () => Promise.resolve() } } },
    }

    const result = await alignValorantStreams(registry, new Set(['room1', 'room2']))
    assert.strictEqual(result.success, false)
    assert.strictEqual(result.method, 'ocr')
  }

  testOcr().then(() => {
    return testAlign()
  }).then(() => {
    return testAlignFailure()
  }).then(() => {
    console.log('All valorantAlign tests passed')
  }).catch((err) => {
    console.error(err)
    process.exit(1)
  })
}

main()
