# Valorant Round Endpoint Optimization Plan

## Problem Statement
Current round endpoint confidence is consistently <0.1 because the model relies on `next_buy` backward inference instead of direct `result` event detection.

## Root Cause Analysis
- Visual classifier (ValorantPhaseClassifier) cannot reliably detect result screens
- No direct signal from score change or timer reaching 0:00
- Current system waits for next buy phase to infer previous round end
- OCR timestamp reading exists but not used for endpoint scoring

## Optimization Strategy

### Level 1: Direct Result Event Detection Enhancement
**Goal**: Increase `end_confidence` from ~0.0 to >0.9 through reliable result screen detection

**Implementation Steps**:
1. Enhance OCR-based timer detection in top HUD strip
   - Monitor when timer shows "0:00" or disappears → strong result signal
   - Score differential detection (sudden score change = round end)
   
2. Implement post-combat visual cues detection
   - Kill feed / scoreboard appearance
   - Win/Lose text overlay detection
   - Round summary screen recognition

3. Fallback audio cue detection
   - Victory/defeat sound pattern matching
   - Round bell chime detection (if available in audio stream)

### Level 2: Phase Scheduler Integration
**Goal**: Use phase scheduling to optimize OCR sampling around expected endpoints

**Implementation Steps**:
1. In `phase_scheduler.py`:
   - When entering `post_combat` phase, reduce OCR interval to 0.5s
   - Add explicit endpoint confirmation logic
   
2. Modify `next_round_phase()` to:
   - Return stronger signals when result indicators detected
   - Track time since combat start with typical duration priors (~80s median)

### Level 3: Confidence Calculation Reformulation
**Goal**: Recalculate end_confidence based on multiple corroborating signals

**Implementation Steps**:
1. In `round_detector.py`, function `_confidence_from_probs()`:
   ```python
   def _confidence_from_probs(probs, label, thresholds):
       base_prob = float(probs.get(label, 0.0))
       
       # Multi-signal confidence boosting
       ocr_result_signal = False  # Detected via OCR
       timer_zero_signal = False  # Timer hit 0:00
       score_change_detected = False  # Sudden score delta
       
       combined_score = base_prob * 0.4  # Visual classifier weight
       if ocr_result_signal:
           combined_score += 0.35  # OCR evidence weight
       if timer_zero_signal:
           combined_score += 0.25  # Timer evidence weight
       
       return min(0.99, combined_score)
   ```

2. In `grade_round_confirmation()`:
   ```python
   def grade_round_confirmation(*, start_strong, end_strong, score_confirm):
       # Lower threshold for confirmed status
       if start_strong and (end_strong or score_confirm):
           return "vision_confirmed"
       return "pending"
   ```

### Level 4: Hybrid Evidence Fusion
**Goal**: Combine all available signals for robust endpoint determination

**Implementation Steps**:
1. Create new function `compute_hybrid_endpoint_confidence()`:
   ```python
   def compute_hybrid_endpoint_confidence(
       visual_classifier_conf,
       ocr_timer_at_zero,
       ocr_score_delta,
       result_screen_detected,
       audio_chime_detected,
       time_since_combat_start
   ):
       """Fusion of all endpoint signals."""
       weights = {
           'visual': 0.30,
           'ocr_timer': 0.30,
           'ocr_score': 0.20,
           'result_screen': 0.15,
           'audio': 0.05
       }
       
       scores = {
           'visual': visual_classifier_conf,
           'ocr_timer': 1.0 if ocr_timer_at_zero else 0.0,
           'ocr_score': 0.8 if ocr_score_delta else 0.1,
           'result_screen': 0.9 if result_screen_detected else 0.2,
           'audio': 0.7 if audio_chime_detected else 0.1
       }
       
       weighted_sum = sum(scores[k] * weights[k] for k in scores)
       
       # Time-based prior boost (longer combat = more likely ending)
       if time_since_combat_start > 60:
           weighted_sum += 0.1 * min(1.0, (time_since_combat_start - 60) / 60)
       
       return min(0.99, weighted_sum)
   ```

2. Update `closed_rounds` construction to use hybrid confidence

### Level 5: Threshold Calibration
**Goal**: Adjust decision thresholds to allow more rounds to reach confirmed status

**Implementation Steps**:
1. In `round_detector.py`, locate threshold configuration:
   ```python
   # Near line 2420-2432
   def _is_strong_confidence(confidence, thresholds, *, label=None):
       # Lower thresholds slightly to allow more confirmed rounds
       if label == "result":
           cutoff = 0.7  # Was 0.8, lowered to 0.7
       else:
           cutoff = 0.7  # Was 0.8, lowered to 0.7
       return confidence >= cutoff
   ```

2. In `grade_round_confirmation()`:
   - Accept `start_strong + moderate_end` as confirmed
   - Where moderate_end could be 0.6-0.8 range

## Expected Results After Implementation

### Before:
- Average boundary confidence: 0.3756
- End confidence: ~0.0-0.1 (next_buy fallback)
- Confirmed rounds: 0% (all pending)
- Low confidence rounds (<0.4): 10/15 (67%)

### After (Target):
- Average boundary confidence: >0.75
- End confidence: >0.85
- Confirmed rounds: >80%
- Low confidence rounds (<0.4): <10%

## Testing & Validation

### Unit Tests Required:
1. Test OCR timer detection accuracy
2. Test score delta detection logic
3. Test hybrid confidence fusion calculation
4. Test threshold calibration impact on confirmed status

### Integration Tests:
1. Re-run analysis on existing recording
2. Compare before/after metrics
3. Manual verification of 20 random segments

### Performance Metrics:
- End confidence distribution (target: mean > 0.85, std < 0.1)
- Confirmation rate (target: >80% vision_confirmed)
- Boundary precision (manual audit: <1s deviation from ground truth)

## Rollout Plan

### Phase 1: Core Detection (Priority HIGH)
- Implement OCR timer zero detection
- Implement score delta detection
- Add hybrid confidence calculation
- Adjust thresholds
**Timeline**: 2-3 hours coding + testing

### Phase 2: Visual Enhancements (Priority MEDIUM)
- Result screen classification
- Kill feed detection
- Audio chime integration
**Timeline**: 4-6 hours after Phase 1 validation

### Phase 3: Phase Scheduler Integration (Priority LOW)
- Adaptive OCR sampling rates
- Combat duration prediction
- Timing-based prior boosting
**Timeline**: 2-3 hours after Phase 2 validation

## Success Criteria

✅ Primary: All rounds have end_confidence > 0.9
✅ Secondary: >80% rounds reach vision_confirmed status
✅ Tertiary: Average boundary_confidence > 0.75

🚨 If goals not met after Phase 1:
- Consider ML model retraining with more result screen examples
- Add additional visual features for post-combat detection
- Explore temporal models (LSTM/Transformer) for sequence understanding
