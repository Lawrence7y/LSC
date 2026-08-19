/**
 * en-US 词典分片：切片列表与时间线（ClipList、Timeline）。
 * key 必须与代码中 t('...') 的参数逐字一致。
 */
export const enPartClips: Record<string, string> = {
  // ── ClipList ──
  '切片': 'clip',
  '近似定位': 'approx. positioning',
  '准备中': 'Preparing',
  '音频路径检测到，等待 OCR 复核边界；复核完成后会自动升格，无需手动确认': 'Detected via the audio path; awaiting OCR boundary review. It will upgrade automatically once reviewed — no manual confirmation needed.',
  'OCR 复核中': 'OCR reviewing',
  '已确认': 'Confirmed',
  '近似定位：边界为音频推断，建议精修后导出': 'Approximately positioned: boundaries inferred from audio. Consider refining before export.',
  '近似': 'Approx',
  '确认边界后即可导出': 'Confirm boundaries to enable export',
  '请先确认后再导出': 'Confirm first, then export',
  '确认并导出': 'Confirm & export',
  '重新导出': 'Re-export',
  '导出': 'Export',
  '取消导出': 'Cancel export',
  '打开文件': 'Open file',
  '打开目录': 'Open folder',
  '删除': 'Delete',
  '切片列表': 'Clip List',
  '全部': 'All',
  '待调': 'Review',
  '导出全部（{count}）': 'Export all ({count})',
  '导出所选（{count}）': 'Export selected ({count})',
  '确认全部（{count}）': 'Confirm all ({count})',
  '批量操作': 'Batch actions',
  '暂无切片': 'No clips yet',
  '没有待调切片': 'Nothing to review',
  '单击定位与回看 · I/O 精调入出点': 'Click to seek & review · I/O to fine-tune in/out points',

  // ── Timeline ──
  '已分析 {pct}%': '{pct}% analyzed',
  '扫描范围 {start}–{end}': 'Scan range {start}–{end}',
  'DVR 左边界 {time}：约实时−2分钟，左侧回跟播，右侧可回看': 'DVR left boundary {time}: ~2 min behind live. Left side follows the stream; right side can be rewound.',
  '入': 'In',
  '出': 'Out',
  '音频待复核': 'Awaiting audio review',
  '待确认': 'Pending confirmation',
  '调整中': 'Adjusting',
  'AI可导': 'AI ready',
  '视觉确认': 'Vision confirmed',
  '音频粗定位': 'Coarse audio position',
  'AI 高光': 'AI highlight',
}
