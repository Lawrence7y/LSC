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
