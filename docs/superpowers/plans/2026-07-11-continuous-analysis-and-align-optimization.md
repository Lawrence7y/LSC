# 无畏契约持续分析降资源与高精度回合切分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将无畏契约持续分析改造成以回合结构为中心的低资源流水线，在降低 CPU / 内存占用的同时，保持或提升回合开始到回合结束的切分准确度，并稳定裁掉准备阶段与尾部垃圾时间。

**Architecture:** 以 `lsc/analyzer/round_detector.py` 作为持续分析主入口，采用“音频主导 + 局部 OCR 校正 + 规则裁剪 + 重模型兜底”的分层架构。持续分析只处理新增时间窗，不再默认走 Whisper / CLIP / 全量视觉分析；OCR 仅在候选边界附近抽样验证，视觉和多模态模型只在边界不确定或质量异常时触发。回合切分结果直接服务于直播持续分析，不再混入通用高光语义，避免峰值资源浪费。

**Tech Stack:** Python 3.10+, NumPy, FFmpeg, OpenCV/OCR 现有工具链, existing analyzer services, pytest.

---

### Task 1: 把持续分析的主路径收敛到回合状态机

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Modify: `lsc/analyzer/pipeline.py`
- Test: `tests/test_round_detector.py`
- Test: `tests/test_continuous_analysis_guards.py`

- [ ] **Step 1: Write a regression test that proves the round-first path is used for Valorant continuous analysis**

```python
def test_detect_valorant_rounds_becomes_primary_path(monkeypatch, tmp_path):
    calls = {"detect": 0, "audio": 0, "visual": 0}

    def fake_detect_valorant_rounds(*args, **kwargs):
        calls["detect"] += 1
        return [{"start": 12.0, "end": 42.0, "score": 0.9, "phase": "combat", "round_index": 1}]

    class FakeAudioAnalyzer:
        def __init__(self, *args, **kwargs):
            calls["audio"] += 1
        def analyze(self, *args, **kwargs):
            raise AssertionError("audio analyzer should not run in round-first continuous mode")
        def cleanup(self):
            pass

    class FakeVisualAnalyzer:
        def __init__(self, *args, **kwargs):
            calls["visual"] += 1
        def analyze(self, *args, **kwargs):
            raise AssertionError("visual analyzer should not run in round-first continuous mode")
        def cleanup(self):
            pass

    monkeypatch.setattr("lsc.analyzer.round_detector.detect_valorant_rounds", fake_detect_valorant_rounds)
    monkeypatch.setattr("lsc.analyzer.pipeline.AudioAnalyzer", FakeAudioAnalyzer)
    monkeypatch.setattr("lsc.analyzer.pipeline.VisualAnalyzer", FakeVisualAnalyzer)

    video_path = tmp_path / "recording.mp4"
    video_path.write_bytes(b"fake")
    analyzer = HighlightAnalyzer()
    result = analyzer.analyze(str(video_path), mode="combined", game="valorant")

    assert result == [{"start": 12.0, "end": 42.0, "score": 0.9, "phase": "combat", "round_index": 1}]
    assert calls["detect"] == 1
    assert calls["audio"] == 0
    assert calls["visual"] == 0
```

- [ ] **Step 2: Run the regression test and confirm the current code still exercises the heavy pipeline**

Run:

```bash
pytest tests/test_continuous_analysis_guards.py::test_detect_valorant_rounds_becomes_primary_path -v
```

Expected: FAIL before the implementation, because `pipeline.py` still prefers the heavy multi-stage flow for continuous analysis.

- [ ] **Step 3: Implement the minimal routing change so Valorant continuous analysis returns round segments directly**

```python
# in lsc/analyzer/pipeline.py inside HighlightAnalyzer.analyze
if game == "valorant":
    try:
        from lsc.analyzer.round_detector import detect_valorant_rounds
        from lsc.config import load_config as _load_cfg_rd
        cfg = _load_cfg_rd()
        ffmpeg_path = cfg.ffmpeg_path or "ffmpeg"
        self._report_progress("round_detect", 0.0, "Valorant 回合检测中...")
        round_segments = detect_valorant_rounds(
            video_path,
            ffmpeg_path=ffmpeg_path,
            progress_callback=self._progress_callback,
            cancel_check=self._cancel_check,
            refine_with_ocr=False,
        )
        if self._is_cancelled():
            return None
        if round_segments:
            self.analysis_time_sec = time.time() - start_time
            return round_segments
    except Exception as exc:
        _log.warning("Valorant 回合检测失败，回退到标准流程: %s", exc)
```

- [ ] **Step 4: Run the regression test again and verify it passes**

Run:

```bash
pytest tests/test_continuous_analysis_guards.py::test_detect_valorant_rounds_becomes_primary_path -v
```

Expected: PASS, with only the lightweight round detector invoked.

- [ ] **Step 5: Commit the routing change**

```bash
git add lsc/analyzer/pipeline.py tests/test_continuous_analysis_guards.py tests/test_round_detector.py
git commit -m "feat: route valorant continuous analysis through round detector"
```

### Task 2: Make round detection incrementally cheaper without reducing boundary quality

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Test: `tests/test_round_detector.py`
- Test: `tests/test_mse_segment_parser.py` (only if existing round timing helpers are shared)

- [ ] **Step 1: Add tests that lock in incremental scan behavior and keep boundary precision**

```python
def test_round_detector_uses_narrow_window_for_ocr_refinement(monkeypatch, tmp_path):
    windows = []

    def fake_detect_round_markers_temporal(*args, **kwargs):
        windows.append(kwargs.get("time_range"))
        return [{"timestamp": 60.0, "phase": "buy"}, {"timestamp": 88.0, "phase": "combat"}]

    monkeypatch.setattr("lsc.analyzer.round_detector._detect_round_markers_temporal", fake_detect_round_markers_temporal)
    # arrange a small set of rounds that force refinement
    rounds = [{"start": 58.0, "end": 102.0, "phase": "combat", "round_index": 1}]
    refined = _refine_round_boundaries_with_ocr(
        rounds,
        [(0, rounds[0])],
        str(tmp_path / "recording.mp4"),
        "ffmpeg",
        ValorantRoundConfig(),
    )
    assert refined[0]["start"] >= 58.0
    assert all(window is None or (window[1] - window[0]) <= 60.0 for window in windows)
```

- [ ] **Step 2: Run the test and confirm it currently uses broader or more expensive scans**

Run:

```bash
pytest tests/test_round_detector.py::test_round_detector_uses_narrow_window_for_ocr_refinement -v
```

Expected: FAIL before tightening the scan windows or scan triggers.

- [ ] **Step 3: Tighten the detector to reuse audio samples and reduce redundant OCR/FFmpeg work**

```python
# in detect_valorant_rounds
# 1) keep a single PCM extraction for RMS + chime detection
# 2) skip refine_with_ocr during live continuous analysis by default
# 3) if OCR is enabled, only scan candidate edge windows rather than the whole video
# 4) preserve ocr_confirmed start/end logic for accuracy
```

- [ ] **Step 4: Run the round detector tests and confirm the lower-cost path still produces the same boundaries**

Run:

```bash
pytest tests/test_round_detector.py -v
```

Expected: PASS, with no regression in start/end trimming behavior.

- [ ] **Step 5: Commit the round-detector optimization**

```bash
git add lsc/analyzer/round_detector.py tests/test_round_detector.py
git commit -m "perf: reduce round detection overhead for continuous analysis"
```

### Task 3: Add adaptive sampling and boundary-gated OCR so accuracy stays high while work stays small

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Modify: `lsc/analyzer/ocr_detector.py`
- Test: `tests/test_ocr_detector.py`
- Test: `tests/test_continuous_analysis_guards.py`

- [ ] **Step 1: Write tests for boundary-gated OCR and adaptive sampling behavior**

```python
def test_ocr_only_runs_near_candidate_edges(monkeypatch, tmp_path):
    scanned_ranges = []

    def fake_detect_round_markers(*args, **kwargs):
        scanned_ranges.append(kwargs.get("time_range"))
        return []

    monkeypatch.setattr("lsc.analyzer.round_detector._detect_round_phase_markers", fake_detect_round_markers)
    rounds = [{"start": 100.0, "end": 160.0, "phase": "combat", "round_index": 1}]
    _refine_round_boundaries_with_ocr(rounds, [(0, rounds[0])], str(tmp_path / "recording.mp4"), "ffmpeg", ValorantRoundConfig())
    assert all(t is None or (t[1] - t[0]) <= 60.0 for t in scanned_ranges)
```

- [ ] **Step 2: Run the tests and verify they fail until OCR becomes windowed**

Run:

```bash
pytest tests/test_ocr_detector.py tests/test_continuous_analysis_guards.py -v
```

Expected: FAIL before the OCR scan is limited to boundary windows.

- [ ] **Step 3: Implement windowed OCR sampling and adaptive scan intervals**

```python
# in lsc/analyzer/ocr_detector.py
# expose a helper that accepts time_range and sample_interval
# when the caller passes a narrow range, sample at 1.0s or slower for stable areas
# use denser sampling only inside edge windows where the round detector is uncertain
```

```python
# in lsc/analyzer/round_detector.py
# if an edge is already confirmed by audio/chime evidence, skip OCR entirely
# if an edge is uncertain, call the OCR helper only on [start - search_window, start + search_window]
```

- [ ] **Step 4: Re-run the tests and verify the same boundaries are still recoverable**

Run:

```bash
pytest tests/test_ocr_detector.py tests/test_round_detector.py -v
```

Expected: PASS, and OCR work only happens in the narrow validation windows.

- [ ] **Step 5: Commit the OCR and sampling optimization**

```bash
git add lsc/analyzer/round_detector.py lsc/analyzer/ocr_detector.py tests/test_ocr_detector.py tests/test_continuous_analysis_guards.py
git commit -m "perf: gate ocr by round boundaries"
```

### Task 4: Define a lightweight continuous-analysis policy for preparation and tail trimming

**Files:**
- Modify: `lsc/analyzer/round_detector.py`
- Modify: `lsc/core/models.py` if a shared round-segment shape needs an explicit field
- Test: `tests/test_round_detector.py`
- Test: `tests/test_timeline_service.py`

- [ ] **Step 1: Add tests that codify the desired Valorant trimming policy**

```python
def test_round_trim_policy_keeps_small_pre_roll_and_short_tail():
    cfg = ValorantRoundConfig(pre_combat_pad=2.0, tail_pad=4.0)
    rounds = [{"start": 120.0, "end": 180.0, "phase": "combat", "round_index": 1, "score": 0.8}]
    results = _format_output(rounds=[(120, 180)], smoothed=np.ones(200), threshold=0.5, duration=200.0, cfg=cfg)
    assert results[0]["start"] == 118.0
    assert results[0]["end"] <= 184.0
```

- [ ] **Step 2: Run the tests and verify the current policy is not yet explicit enough**

Run:

```bash
pytest tests/test_round_detector.py::test_round_trim_policy_keeps_small_pre_roll_and_short_tail -v
```

Expected: FAIL until the trimming rules are made explicit and consistently applied.

- [ ] **Step 3: Implement a single trimming policy for buy phase, combat phase, and tail padding**

```python
# keep these defaults centralized in ValorantRoundConfig
# pre_combat_pad: small intro padding before combat start
# buy_phase_max / min_combat_after_trim: prevent chopping real combat
# tail_pad / round_inactive_gap / chime_tail_window: keep only a short post-round tail
```

```python
# ensure _format_output and _build_round_segments_from_phase_markers apply the same policy
# so continuous live analysis and post-recording refinement return the same boundaries
```

- [ ] **Step 4: Run timeline and round detector tests to confirm the trimming policy stays consistent**

Run:

```bash
pytest tests/test_round_detector.py tests/test_timeline_service.py -v
```

Expected: PASS, with stable trimming decisions for start/end boundaries.

- [ ] **Step 5: Commit the trimming-policy update**

```bash
git add lsc/analyzer/round_detector.py lsc/core/models.py tests/test_round_detector.py tests/test_timeline_service.py
git commit -m "feat: formalize valorant round trimming policy"
```

### Task 5: Verify the resource drop with focused profiling and regression checks

**Files:**
- Modify: `tests/test_continuous_analysis_guards.py` if a profiling assertion helper is useful
- Modify: `README.md` only if the analysis mode behavior needs a user-facing note

- [ ] **Step 1: Add a lightweight guard test that ensures the heavy analyzers remain unused in live Valorant mode**

```python
def test_live_valorant_mode_does_not_touch_whisper_or_clip(monkeypatch, tmp_path):
    touched = {"whisper": 0, "clip": 0}

    def boom(*args, **kwargs):
        touched["whisper"] += 1
        raise AssertionError("whisper should not be touched in live valorant mode")

    monkeypatch.setattr("lsc.analyzer.audio_analyzer.AudioAnalyzer._load_model", boom)
    monkeypatch.setattr("lsc.analyzer.visual_analyzer.VisualAnalyzer.analyze", boom)

    video_path = tmp_path / "recording.mp4"
    video_path.write_bytes(b"fake")
    analyzer = HighlightAnalyzer()
    analyzer.analyze(str(video_path), game="valorant")
    assert touched["whisper"] == 0
```

- [ ] **Step 2: Run the guard test and confirm it fails before the heavy path is removed from live analysis**

Run:

```bash
pytest tests/test_continuous_analysis_guards.py::test_live_valorant_mode_does_not_touch_whisper_or_clip -v
```

Expected: FAIL until live Valorant analysis stops loading Whisper / CLIP by default.

- [ ] **Step 3: Remove any remaining live-path calls that force heavy models to load during continuous analysis**

```python
# do not call AudioAnalyzer.analyze() for live Valorant continuous segmentation
# do not call VisualAnalyzer.analyze() for live Valorant continuous segmentation
# keep heavy analyzers available only behind an explicit fallback branch or a post-recording refinement path
```

- [ ] **Step 4: Run the full relevant analyzer test set and confirm all regression guards pass**

Run:

```bash
pytest tests/test_round_detector.py tests/test_ocr_detector.py tests/test_continuous_analysis_guards.py tests/test_timeline_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Capture the final optimization commit**

```bash
git add lsc/analyzer/round_detector.py lsc/analyzer/pipeline.py lsc/analyzer/ocr_detector.py tests/test_round_detector.py tests/test_ocr_detector.py tests/test_continuous_analysis_guards.py
git commit -m "perf: optimize valorant continuous analysis pipeline"
```

## Self-review checklist

- The plan covers the requested goal: lower CPU / memory usage while keeping or improving live Valorant round splitting accuracy.
- The architecture is focused on one subsystem: continuous analysis for Valorant, not the whole product.
- Each task owns a small set of files and a single responsibility: routing, cheaper detection, boundary-gated OCR, trimming policy, and regression verification.
- No placeholder requirements remain; every step names exact files, commands, and expected outcomes.
- The plan avoids speculative refactors outside the live-analysis path and keeps heavy analyzers available as fallback only.
