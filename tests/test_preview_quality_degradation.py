from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_compute_preview_quality_params_reports_degraded_when_multi_preview() -> None:
  from handlers import room_handler as rh

  registry = MagicMock()
  registry.active_count.return_value = 3

  with (
    patch.object(rh, "load_settings", return_value={"preview_quality": "高清"}),
    patch.object(rh, "_preview_stream_registry", return_value=registry),
    patch.object(rh, "get_resource_pressure", return_value={"level": "normal"}),
    patch("lsc.core.services.mse_streamer._check_nvenc", return_value=False),
  ):
    params = rh._compute_preview_quality_params()

  assert params["width"] <= 854
  assert params["height"] <= 480
  assert params["degraded"] is True
  assert params.get("reason")


def test_preview_quality_response_fields_shape() -> None:
  from handlers.room_handler import _preview_quality_response_fields

  fields = _preview_quality_response_fields(
    {
      "width": 640,
      "height": 360,
      "fps": 15,
      "degraded": True,
      "reason": "多路预览（4路）",
    }
  )
  assert fields == {
    "width": 640,
    "height": 360,
    "fps": 15,
    "degraded": True,
    "reason": "多路预览（4路）",
  }


def test_workbench_shows_preview_degradation_banner() -> None:
  workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
  websocket = (ROOT / "lsc-electron/src/hooks/useWebSocket.ts").read_text(encoding="utf-8")

  assert "previewDegradationBanner" in workbench or "preview_degradation" in workbench.lower()
  assert "degraded" in websocket
  assert "fps" in websocket
  assert "以保流畅" in workbench
  assert "@${fps}fps" in workbench or "fps" in workbench.split("formatPreviewDegradationLabel", 1)[1][:400]


def test_claude_select_all_shortcut_is_ctrl_shift_a() -> None:
  claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
  section = claude.split("## 12.", 1)[1].split("## 13.", 1)[0]
  assert "Ctrl + Shift + A" in section
  assert "`Ctrl + a`" not in section


def test_analysis_tooltip_explains_prerequisites() -> None:
  workbench = (ROOT / "lsc-electron/src/pages/Workbench/index.tsx").read_text(encoding="utf-8")
  tooltip_block = workbench.split("const analysisTooltip", 1)[1].split("const handleConfirmAnalysisExport", 1)[0]

  assert "请先选择" in tooltip_block
  assert "录制" in tooltip_block
  assert "一键对齐" in tooltip_block
  assert "去对齐" in workbench
  assert "scrollToAlignButton" in workbench
