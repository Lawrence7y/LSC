"""Record page components."""
from .video_preview import VideoPreview, _FullscreenOverlayButton, _RecBadge
from .icon_widgets import (
    IconButton,
    ExportedCard,
    ExportedClipsGrid,
    AnalysisResultsGrid,
    _icon_seek_back,
    _icon_seek_fwd,
    _icon_stop,
    _icon_play,
    _icon_pause,
)
from .config_panel import ConfigPanel
from .page import RecordPage

__all__ = [
    "VideoPreview",
    "_FullscreenOverlayButton",
    "_RecBadge",
    "IconButton",
    "ExportedCard",
    "ExportedClipsGrid",
    "AnalysisResultsGrid",
    "_icon_seek_back",
    "_icon_seek_fwd",
    "_icon_stop",
    "_icon_play",
    "_icon_pause",
    "ConfigPanel",
    "RecordPage",
]
