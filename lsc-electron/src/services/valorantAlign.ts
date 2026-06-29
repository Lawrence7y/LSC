import Tesseract from 'tesseract.js';
import { getTesseractPreloadPromise, getCachedTesseractWorker, markCachedTesseractWorkerFailed } from '@/hooks/useTesseractPreload';

// 无畏契约计时器 ROI（基于 1920x1080 画面）
// 顶部居中区域：水平 35%-65%，垂直 3%-12%
const VALORANT_TIMER_ROI = {
  left: 0.35,
  top: 0.03,
  width: 0.30,
  height: 0.09,
};

// 无畏契约分类关键词（匹配 room.category）
const VALORANT_CATEGORY_KEYWORDS = ['无畏契约', 'Valorant', 'VALORANT', 'valorant'];

/**
 * 判断房间是否为无畏契约直播
 */
export function isValorantStream(category: string): boolean {
  if (!category) return false;
  return VALORANT_CATEGORY_KEYWORDS.some(kw =>
    category.toLowerCase().includes(kw.toLowerCase())
  );
}

/**
 * 从 video 元素截取计时器区域的 canvas
 */
function captureTimerFrame(video: HTMLVideoElement): HTMLCanvasElement {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  const canvas = document.createElement('canvas');
  canvas.width = Math.floor(vw * VALORANT_TIMER_ROI.width);
  canvas.height = Math.floor(vh * VALORANT_TIMER_ROI.height);
  const ctx = canvas.getContext('2d')!;
  
  ctx.drawImage(
    video,
    Math.floor(vw * VALORANT_TIMER_ROI.left),  // sx
    Math.floor(vh * VALORANT_TIMER_ROI.top),   // sy
    canvas.width,                               // sw
    canvas.height,                              // sh
    0, 0,                                       // dx, dy
    canvas.width,                               // dw
    canvas.height,                              // dh
  );
  
  return canvas;
}

/**
 * OCR 识别无畏契约计时器，返回回合时间（秒）
 * 格式：M:SS（如 1:23 = 83秒）或 SS（如 45 = 45秒）
 * 失败返回 null
 */
export async function ocrValorantTimer(
  video: HTMLVideoElement
): Promise<number | null> {
  if (!video || video.videoWidth === 0) return null;

  const canvas = captureTimerFrame(video);

  try {
    const cachedWorker = getCachedTesseractWorker();
    let result;
    if (cachedWorker) {
      try {
        result = await cachedWorker.recognize(canvas);
      } catch (err) {
        markCachedTesseractWorkerFailed();
        result = await Tesseract.recognize(
          canvas,
          'eng',
          {
            logger: () => {},
            workerPath: '/tessdata/worker.min.js',
            corePath: '/tessdata/tesseract-core-simd-lstm.wasm.js',
            langPath: '/tessdata',
          }
        );
      }
    } else {
      result = await Tesseract.recognize(
        canvas,
        'eng',
        {
          logger: () => {},
          workerPath: '/tessdata/worker.min.js',
          corePath: '/tessdata/tesseract-core-simd-lstm.wasm.js',
          langPath: '/tessdata',
        }
      );
    }
    const text = result.data.text.trim();

    // 解析 M:SS 或 SS 格式 (支持分号、冒号等，Tesseract 有时会把冒号误识为分号或点，我们包容它)
    // 比如：1:23, 1;23, 1.23, 1，23, 1 23
    const match = text.match(/(\d{1,2})\s*[:;\.\s，-]\s*(\d{2})/);
    if (match) {
      const minutes = parseInt(match[1], 10);
      const seconds = parseInt(match[2], 10);
      return minutes * 60 + seconds;
    }
    // 纯数字（炸弹阶段，如 "12"）
    const numMatch = text.match(/^(\d{1,2})$/);
    if (numMatch) {
      return parseInt(numMatch[1], 10);
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * 对齐策略：判断选中房间是否都是无畏契约直播
 */
export function checkSameValorant(
  rooms: Array<{ category?: string; room_id: string }>,
  selectedIds: Set<string>
): boolean {
  const selected = rooms.filter(r => selectedIds.has(r.room_id));
  if (selected.length < 2) return false;
  return selected.every(r => isValorantStream(r.category || ''));
}

export interface AlignResult {
  success: boolean;
  method: 'ocr' | 'live_edge';
  message: string;
  details?: Array<{ roomId: string; gameTimer: number | null; seeked: boolean }>;
}

/**
 * 执行无畏契约 OCR 对齐
 * 1. 对每个房间的 video 截帧并 OCR 识别计时器
 * 2. 计算最大游戏时间（最新进度）
 * 3. seek 滞后的房间到匹配位置
 */
export async function alignValorantStreams(
  registry: Record<string, { player?: { videoElement?: HTMLVideoElement } }>,
  selectedRoomIds: Set<string>
): Promise<AlignResult> {
  const preload = getTesseractPreloadPromise()
  if (preload) {
    await preload
  }
  // 1. 并行 OCR 所有房间
  const ocrResults: Array<{ roomId: string; timer: number | null }> = [];
  const promises = Array.from(selectedRoomIds).map(async (rid) => {
    const video = registry[rid]?.player?.videoElement;
    if (!video) {
      return { roomId: rid, timer: null };
    }
    const timer = await ocrValorantTimer(video);
    return { roomId: rid, timer };
  });

  const resolved = await Promise.all(promises);
  ocrResults.push(...resolved);

  // 2. 检查 OCR 结果
  const validResults = ocrResults.filter(r => r.timer !== null);
  if (validResults.length < 2) {
    return {
      success: false,
      method: 'ocr',
      message: 'OCR 识别失败，无法对齐。请确保画面中有计时器',
      details: ocrResults.map(r => ({
        roomId: r.roomId,
        gameTimer: r.timer,
        seeked: false,
      })),
    };
  }

  // 3. 计算时间差并 seek
  // 取最大的游戏时间作为目标（最慢的房间 = 游戏时间最晚）
  const maxTimer = Math.max(...validResults.map(r => r.timer!));
  let seekedCount = 0;

  for (const result of ocrResults) {
    if (result.timer === null) continue;
    const diff = maxTimer - result.timer;  // 快房间比最慢房间快多少秒
    if (diff > 0.3) {
      // 快房间后退 diff 秒，对齐到最慢房间的游戏时间
      const video = registry[result.roomId]?.player?.videoElement;
      if (video) {
        const newTime = video.currentTime - diff;
        const bufStart = video.buffered.length > 0 && typeof video.buffered.start === 'function'
          ? video.buffered.start(0)
          : video.currentTime;
        if (newTime >= bufStart) {
          try { video.currentTime = newTime; } catch {}
          video.play().catch(() => {});
          seekedCount++;
        } else {
          // 超出缓冲区起点，后退到起点（部分对齐）
          try { video.currentTime = bufStart; } catch {}
          video.play().catch(() => {});
          seekedCount++;
        }
      }
    }
  }

  // 如果没有实际 seek 任何房间，返回失败让流程降级到音频互相关
  if (seekedCount === 0) {
    return {
      success: false,
      method: 'ocr',
      message: '各房间游戏时间差极小，OCR 对齐已无法进一步优化',
      details: ocrResults.map(r => ({
        roomId: r.roomId,
        gameTimer: r.timer,
        seeked: false,
      })),
    };
  }

  return {
    success: true,
    method: 'ocr',
    message: `已通过 OCR 对齐 ${seekedCount} 个房间（游戏时间差最大 ${maxTimer - Math.min(...validResults.map(r => r.timer!))} 秒）`,
    details: ocrResults.map(r => ({
      roomId: r.roomId,
      gameTimer: r.timer,
      seeked: r.timer !== null && maxTimer - r.timer > 0.3,
    })),
  };
}
