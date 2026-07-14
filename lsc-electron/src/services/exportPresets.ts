import { ExportPreset } from '@/types'

/**
 * 导出预设定义
 *
 * 预设不包含房间/时间信息，仅包含编码参数。
 * 房间和时间信息在用户选择切片时自动绑定。
 */
export const EXPORT_PRESETS: ExportPreset[] = [
  {
    id: 'douyin_vertical',
    name: '抖音竖屏',
    description: '1080x1920 竖屏（原画居中+上下黑边）, 30fps, H.264, CRF 23',
    resolution: '1080:1920',
    framerate: '30',
    codec: 'h264_nvenc',
    crf: 23,
    vertical_crop: true,
    audio_bitrate: '128k',
  },
  {
    id: 'bilibili_horizontal',
    name: 'B站横屏',
    description: '1920x1080 横屏, 30fps, H.264, CRF 23',
    resolution: '1920:1080',
    framerate: '30',
    codec: 'h264_nvenc',
    crf: 23,
    vertical_crop: false,
    audio_bitrate: '128k',
  },
  {
    id: 'original',
    name: '原画直出',
    description: '保持原始分辨率/帧率, 流复制, 最快',
    resolution: '',
    framerate: '原画',
    codec: 'copy',
    crf: 0,
    vertical_crop: false,
    audio_bitrate: '128k',
  },
  {
    id: 'high_quality',
    name: '高品质存档',
    description: '原画分辨率, 60fps, H.264 CRF 18, AAC 256k',
    resolution: '',
    framerate: '60',
    codec: 'h264_nvenc',
    crf: 18,
    vertical_crop: false,
    audio_bitrate: '256k',
  },
  {
    id: 'small_file',
    name: '小文件快速',
    description: '720p, 24fps, H.265, CRF 28, 适合快速分享',
    resolution: '1280:720',
    framerate: '24',
    codec: 'hevc_nvenc',
    crf: 28,
    vertical_crop: false,
    audio_bitrate: '96k',
  },
]

/**
 * 根据 ID 查找预设
 */
export function getPresetById(id: string): ExportPreset | undefined {
  return EXPORT_PRESETS.find(p => p.id === id)
}

/**
 * 获取默认预设
 */
export function getDefaultPreset(): ExportPreset {
  return EXPORT_PRESETS[0]
}
