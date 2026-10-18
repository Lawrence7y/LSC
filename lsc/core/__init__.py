"""LSC 核心领域层 — 纯业务逻辑，无 GUI 依赖。

只做包声明，不做再导出：历史上这里 re-export 了 Clip/ExportService 等名字，
但全仓 `from lsc.core import X` 命中数为 0（一律走 `lsc.core.models` 等具体子模块），
空转的 barrel 会让人误改这里就以为改到了实现。
"""
