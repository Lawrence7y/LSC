import { useEffect } from 'react'
import Tesseract from 'tesseract.js'

let _cachedWorker: any = null
let _preloadPromise: Promise<void> | null = null
let _workerFailed = false

export function useTesseractPreload() {
  useEffect(() => {
    if (typeof window === 'undefined') return

    _preloadPromise = (async () => {
      if (_cachedWorker || _workerFailed) return
      try {
        _cachedWorker = await Tesseract.createWorker('eng', 1, {
          workerPath: '/tessdata/worker.min.js',
          corePath: '/tessdata/tesseract-core-simd-lstm.wasm.js',
          langPath: '/tessdata',
          logger: () => {},
        })
      } catch {
        _workerFailed = true
      }
    })()

    return () => {
      // 不终止 worker，保持缓存供后续 OCR 复用
    }
  }, [])
}

export function getTesseractPreloadPromise(): Promise<void> | null {
  return _preloadPromise
}

export function getCachedTesseractWorker(): any {
  return _cachedWorker
}

export function setCachedTesseractWorker(worker: any) {
  _cachedWorker = worker
  _workerFailed = false
}

export function markCachedTesseractWorkerFailed() {
  _workerFailed = true
  _cachedWorker = null
}
