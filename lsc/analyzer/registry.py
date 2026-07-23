from __future__ import annotations

import logging
import threading

from lsc.analyzer.base import AnalyzerPlugin
from lsc.analyzer.generic_plugin import GenericAnalyzerPlugin

_log = logging.getLogger(__name__)
_lock = threading.RLock()
_plugins: dict[str, AnalyzerPlugin] = {}
_default_game = "generic"


def register(plugin: AnalyzerPlugin) -> None:
    with _lock:
        _plugins[plugin.game] = plugin
        _log.info("analyzer registered: %s (%s)", plugin.game, plugin.display_name)


def get(game: str | None) -> AnalyzerPlugin:
    with _lock:
        if game and game in _plugins:
            return _plugins[game]
        if game and game not in _plugins:
            _log.warning("analyzer %r not found, fallback to %s", game, _default_game)
        return _plugins[_default_game]


def list_plugins() -> list[AnalyzerPlugin]:
    with _lock:
        return list(_plugins.values())


def default() -> AnalyzerPlugin:
    return get(_default_game)


def _ensure_builtins() -> None:
    with _lock:
        if _default_game not in _plugins:
            register(GenericAnalyzerPlugin())
        if "valorant" not in _plugins:
            from lsc.analyzer.valorant_plugin import ValorantAnalyzerPlugin

            register(ValorantAnalyzerPlugin())


_ensure_builtins()

# 友好别名
get_analyzer = get
list_analyzers = list_plugins
