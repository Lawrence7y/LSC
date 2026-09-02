export function calculateConfirmedAnalysisPercent(
  analyzedDuration: number | null | undefined,
  recordedDuration: number | null | undefined,
): number {
  const analyzed = Number(analyzedDuration)
  const recorded = Number(recordedDuration)
  if (!Number.isFinite(analyzed) || !Number.isFinite(recorded) || recorded <= 0) {
    return 0
  }
  return Math.min(100, Math.max(0, (analyzed / recorded) * 100))
}

/** 本轮正在扫的区间。analyzed_duration 要等整窗结束才前进，扫描中必须单独展示，否则会一直显示 0s。 */
export function inFlightScanWindow(status: {
  scan_running?: boolean
  scan_in_sec?: number | null
  scan_out_sec?: number | null
}): { from: number; to: number } | null {
  if (!status.scan_running) return null
  const from = Number(status.scan_in_sec)
  const to = Number(status.scan_out_sec)
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return null
  return { from, to }
}

