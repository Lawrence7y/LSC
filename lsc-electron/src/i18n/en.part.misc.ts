/**
 * en-US 词典分片：启动页与录制规格选择（SplashScreen、RecordingSpecSelector）。
 * key 必须与代码中 t('...') 的参数逐字一致。
 */
export const enPartMisc: Record<string, string> = {
  // SplashScreen 依赖阶段标签
  'Python 核心依赖': 'Python Core Dependencies',
  'AI 分析依赖': 'AI Analysis Dependencies',
  'FFmpeg 多媒体框架': 'FFmpeg Multimedia Framework',
  // SplashScreen 状态/文案
  '直播切片系统': 'Live Stream Clipper',
  '正在检测运行环境...': 'Checking the runtime environment...',
  '首次使用需要下载依赖，请耐心等待（约 1.5 GB）':
    'Dependencies need to be downloaded on first use. Please wait patiently (about 1.5 GB)',
  '完成': 'Done',
  '等待中': 'Waiting',
  '失败': 'Failed',
  '安装失败': 'Installation failed',
  '依赖安装失败': 'Dependency installation failed',
  '启动安装失败': 'Failed to start installation',
  '重试': 'Retry',
  '跳过（部分功能不可用）': 'Skip (some features unavailable)',
  '环境就绪，正在启动...': 'Environment ready, starting up...',
  '依赖将安装至当前用户数据目录，不会污染系统环境':
    'Dependencies are installed into the current user data directory and will not pollute the system environment',

  // RecordingSpecSelector 规格方案
  '规格方案': 'Preset scheme',
  '高端默认规格（设置中的默认值）': 'High-end default preset (default values in Settings)',
  '原画直拷（体积较大、占用最低）': 'Lossless direct copy (large size, lowest CPU usage)',
  '1080p 30fps 均衡规格': 'Balanced 1080p 30fps preset',
  '自定义': 'Custom',
  '默认使用“设置 → 录制与编码”中的高端默认规格；本次调整不会修改全局设置。':
    'Uses the high-end default preset from "Settings → Recording & Encoding" by default; this change does not modify the global settings.',

  // RecordingSpecSelector 编码器
  '编码器': 'Encoder',
  'H.264 CPU（兼容）': 'H.264 CPU (compatible)',
  '原画直拷': 'Lossless direct copy',

  // RecordingSpecSelector 编码参数
  '编码参数': 'Encoding parameters',
  'CRF 质量': 'CRF quality',
  '自定义码率': 'Custom bitrate',
  '不限制': 'Unlimited',

  // RecordingSpecSelector 分辨率
  '分辨率': 'Resolution',
  '原画': 'Source',

  // RecordingSpecSelector 帧率
  '帧率': 'Frame rate',

  // RecordingSpecSelector CRF 滑块
  'CRF：{crf}': 'CRF: {crf}',
  '高质量': 'High quality',
  '推荐': 'Recommended',
  '小体积': 'Small size',

  // RecordingSpecSelector 码率
  '码率单位': 'Bitrate unit',
  '码率': 'Bitrate',

  // RecordingSpecSelector 音频
  '音频': 'Audio',
}
