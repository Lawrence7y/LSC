"""时间线「本地文件回看」契约守卫（方案 A）。

每条都对应一次真实实测暴露的缺陷，改动时不要顺手回退：

1. 回看播放头 scrubOverride 与回看播放器必须**同轴**：override 记录制轴目标，
   采样侧把播放器时间（文件原始 PTS）换算到录制轴再比较。两处不同轴时
   `|t - override| < 0.35s` 的释放条件永不满足，播放头被钉死在进入位置
   （2026-09-11 实测：读数 9 分钟不动，回看画面却在前进）。
2. 进回看不得依赖直播 MSE 已出画（`buffered.length > 0`）：预览未出画/已停/
   出错时点时间线回看必须照常进回看，此前整段被挡在缓冲判断里 ⇒「点回看没反应」。
3. 回看失败必须写进 `uiState.review_error` 并走底部「回看不可用」条；
   回看模式下**不得**复用直播的全屏错误遮罩（实测遮罩取的是直播侧错误槽，
   只显示无信息量的「预览不可用」并把仍在正常播放的直播画面整屏盖住）。
4. 文件源读到末尾要调 `MsePlayer.markEndOfStream()`：之后缓冲不再增长属正常结束，
   不得判成「直播流连接中断」或强制 seek 回缓冲起点重播。
5. 对齐（commonMode）下的回看轴换算必须走 `recordingToCommon`，不能把文件 PTS
   直接当 preview 轴喂 `previewToCommon`（否则内容终点被撑到 PTS 基座量级）。
6. `request_mse_init` 失败时的前端 init 缓存补喂必须投给**直播播放器**
   （`registry[roomId]` 是条目对象，不是播放器；旧写法抛 TypeError 被吞成死代码）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH = ROOT / "lsc-electron/src/pages/Workbench/index.tsx"
SAMPLING = ROOT / "lsc-electron/src/hooks/usePlayheadSampling.ts"
PREVIEW = ROOT / "lsc-electron/src/components/VideoPreview.tsx"
PLAYER = ROOT / "lsc-electron/src/services/mediaSourcePlayer.ts"
SOURCE = ROOT / "lsc-electron/src/services/localFileMseSource.ts"
VIEWMODEL = ROOT / "lsc-electron/src/utils/timelineViewModel.ts"
WS = ROOT / "lsc-electron/src/hooks/useWebSocket.ts"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_scrub_override_axis_matches_review_player() -> None:
    workbench = _text(WORKBENCH)
    # 进回看时 override 写录制轴目标（recTime），而不是调用方给的预览轴 t
    assert "scrubOverrideRef.current[roomId] = Math.max(0, recTime)" in workbench

    sampling = _text(SAMPLING)
    # 比较前把回看播放器时间换算到录制轴
    assert "isRecordingReviewMode(room?.preview_mode)" in sampling
    assert "t + (Number(room?.preview_review_start_sec) || 0)" in sampling


def test_review_entry_does_not_require_live_buffer() -> None:
    workbench = _text(WORKBENCH)
    assert "const hasBuffer" in workbench
    marker = "切换到本地文件回看"
    assert marker in workbench
    # 回看分支必须能在"无缓冲"时到达：日志里显式区分无缓冲
    assert "'无缓冲'" in workbench


def test_review_failure_surfaces_in_store_not_live_overlay() -> None:
    preview = _text(PREVIEW)
    # 回看模式不盖全屏直播错误遮罩
    assert "const showError = isReviewActive" in preview
    # 播放器错误也必须写进 store（底部「回看不可用」条只认 store）
    assert "setReviewErrorStore(roomId, err)" in preview
    # 生命周期日志：启动 / 首帧 / 播放器错误 / 源错误
    assert "回看启动" in preview
    assert "回看首帧已喂入" in preview
    assert "回看播放器错误" in preview
    assert "回看源错误" in preview


def test_file_source_signals_end_of_stream() -> None:
    source = _text(SOURCE)
    assert "markEndOfStream?.()" in source
    assert "回看读到文件末尾" in source
    player = _text(PLAYER)
    assert "markEndOfStream(): void" in player
    assert "_eofReached" in player
    # EOF 后停在末尾，而不是回跳重播/报流中断
    assert "EOF: playback reached end of loaded file, pausing (no error)" in player


def test_review_axis_conversion_uses_recording_axis() -> None:
    vm = _text(VIEWMODEL)
    assert "recordingToCommon(timelineContext, referenceRoomId, reviewAxisPos)" in vm
    sampling = _text(SAMPLING)
    assert "recordingToCommon(ctx, refId" in sampling


def test_mse_init_cache_delivery_targets_live_player() -> None:
    ws = _text(WS)
    assert "entry?.live ?? entry?.player" in ws
