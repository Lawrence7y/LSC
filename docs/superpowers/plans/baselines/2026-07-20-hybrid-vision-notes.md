# Hybrid Vision Regression Baseline — 2026-07-20

Branch: `codex/analyze-continuous-analysis`

## Full suite (post Task 14 + synced test fix)

Command:

```powershell
$env:QT_QPA_PLATFORM="offscreen"
$env:PYTHONPATH="D:/Project/直播切片多人;D:/Project/直播切片多人/python-backend"
python -m pytest -v --tb=line
```

Recorded run (before synced-test fix): **931 passed, 5 failed** in ~34s  
(see `2026-07-20-hybrid-vision-post.txt`)

After `afc8b5e` (synced hybrid gate test update): expected **932 passed, 4 failed**.

After hybrid review hardening: **952 passed, 4 failed**. The four failures remain the
same frontend guards listed below; all hybrid-related tests pass.

## Known unrelated failures (must list individually — not “tests are stale”)

| Test | Notes |
|------|-------|
| `tests/test_frontend_stability_guards.py::test_clip_list_batch_export_includes_pending_confirmable_clips` | Pre-existing Workbench batch-export vs pending-confirm guard mismatch; present before hybrid Task 9 |
| `tests/test_frontend_stability_guards.py::test_batch_export_confirms_each_clip_with_own_bounds` | Same family — expects `handleConfirmClip` / `syncTargets` pattern no longer in `handleExportMany` |
| `tests/test_frontend_stability_guards.py::test_scrub_mark_surfaces_approximate_precision` | Pre-existing approximate-precision UI string guard |
| `tests/test_frontend_stability_guards.py::test_recording_review_timeline_guards` | Pre-existing `targetsIncludeNoDvrMode` / recording-review guard |

## Hybrid-related suite (all green)

- `tests/test_valorant_frame_classifier.py`
- `tests/test_valorant_round_fsm.py`
- `tests/test_cancellable_ffmpeg.py`
- `tests/test_valorant_dense_refine.py`
- `tests/test_valorant_hybrid_detect.py`
- `tests/test_valorant_eval_gates.py`
- `tests/test_hybrid_vision_lifecycle.py`
- Hybrid / continuous guards in `tests/test_continuous_analysis_guards.py`
- Updated `tests/test_synced_continuous_analysis.py` pending→vision_confirmed cases

## Release blockers (not regression failures)

1. **Real INT8 model + labeled dataset** — exporter now writes the quantized runtime artifact, but production `lsc/analyzer/models/valorant_phase_v1.onnx` is not shipped
2. **Blind-test gates** — run `eval_blind.py --enforce-gates` on held-out broadcast + POV VODs
3. **Shadow soak** — `LSC_VALORANT_VISION_SHADOW=1` on one broadcast + one POV session; then remove shadow switch
4. **Performance hand checks** — steady lag P95 ≤10s (≤15s CPU); stop→idle ≤3s; no orphan FFmpeg; finalize from cursor not full rescan

## Performance checklist (manual)

- [ ] Steady-state analysis lag P95 ≤ 10s (DML/CUDA) / ≤ 15s (CPU-only)
- [ ] Processing rate ≥ recording growth over ≥10 min
- [ ] Stop continuous analysis → `idle` within 3s; no orphan FFmpeg; semaphore released
- [ ] Stop recording finalize continues from cursor (no default full-file rescan)
