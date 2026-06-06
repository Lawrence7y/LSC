# LSC Product Closure Implementation Plan

Goal: close the remaining P0/P1/P2 product gaps for the LSC pilot without over-promising unimplemented behavior.

## Batch 1: Persistent Workflow State

- Persist detected clips and exported clip status into `LscDatabase`.
- Mark active persisted tasks as interrupted on app startup.
- Keep diagnostics and history backed by persisted records, not transient UI state.

## Batch 2: Runtime Configuration

- Make SettingsDock changes apply to existing LivestreamDock controls without restarting the app.
- Keep SettingsDock, LivestreamDock, and `LscConfig` using the same values for profile, quality, output directory, reconnect, and export defaults.

## Batch 3: Platform And Recording Semantics

- Normalize platform parse errors into user-actionable messages.
- Keep Douyin/Bilibili native parser entrypoints tested.
- Treat selective recording honestly: first ship recording + state-marking + post-processing metadata, not unsafe live stop/start unless explicitly enabled later.

## Batch 4: Valorant Evaluation

- Add an offline evaluation runner and JSON feedback format so real Valorant samples can produce measurable Top-N hit rate and boundary offset metrics.
- Unit-test the evaluator with synthetic annotations; real quality targets require user-supplied samples.

## Verification

- Build `src/lsc` Release.
- Run full `ctest -C Release --output-on-failure`.
- Keep newly added tests in the normal CTest registry.
