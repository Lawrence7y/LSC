"""LSC - LiveStreamClipper: 直播切片系统。"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def _configure_logging() -> None:
    """Configure root logging handler if not already configured.

    Reads the log level from the ``LSC_LOG_LEVEL`` environment variable
    (default ``INFO``). Ensures DEBUG-level messages from ``lsc.*`` loggers
    are visible during development without requiring every entrypoint to
    call ``logging.basicConfig``.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.environ.get("LSC_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    # Only add our handler if the root logger has none, to avoid duplicating
    # handlers when the host application already configured logging.
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(level)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name.

    Ensures logging is configured on first call so that DEBUG/INFO messages
    are actually emitted to stderr.
    """
    if not _CONFIGURED:
        _configure_logging()
    return logging.getLogger(name)


# Configure logging on import so that simply importing ``lsc`` is enough.
_configure_logging()


__all__ = ["get_logger"]
