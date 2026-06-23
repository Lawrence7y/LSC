"""Timeline widget re-export.

Delegates to the canonical implementation in ``lsc.gui.components.timeline``
to maintain backward compatibility for any external imports.
"""
from ..components.timeline import InlineTimeline

__all__ = ["InlineTimeline"]
