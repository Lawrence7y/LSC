"""解析失败分类：平台改版（parser_stale）不得再伪装成网络故障（parse_failed）。

审计事实：legacy 面只有 4 个错误码，`ERROR_PARSE_FAILED` 是 catch-all，且被 B站/斗鱼
等适配器大量用在"接口 JSON 字段变了"的场景 ⇒ 用户看到的是"解析失败/请检查网络"，
而真正需要的是"更新解析器"。分类集中在 `registry.parse_stream` 一处，9 个适配器共用。
"""
from __future__ import annotations

from lsc.platforms.base import ERROR_PARSE_FAILED, ERROR_PARSER_STALE
from lsc.platforms.registry import parse_stream


class _RaisingAdapter:
    platform = "fakeplatform"
    display_name = "假平台"

    def __init__(self, exc: Exception):
        self._exc = exc

    def can_handle(self, url: str) -> bool:
        return True

    def parse(self, url: str):
        raise self._exc


def test_结构类异常判为_parser_stale():
    """KeyError/TypeError/AttributeError/IndexError = 响应结构不再是解析器假设的样子。"""
    for exc in (KeyError("playInfo"), TypeError("NoneType not iterable"),
                AttributeError("no attribute 'data'"), IndexError("list index out of range")):
        info = parse_stream(
            "https://stale-probe-1.example.com/123",
            adapters=[_RaisingAdapter(exc)],
            force_refresh=True,
        )
        assert info.error_code == ERROR_PARSER_STALE, f"{type(exc).__name__} 应判为 parser_stale"
        assert "不是网络问题" in info.error
        assert info.is_live is False


def test_网络类异常仍是_parse_failed():
    """超时/连接失败不能被误标成"平台改版"，否则会误导用户去升级解析器。"""
    info = parse_stream(
        "https://stale-probe-2.example.com/123",
        adapters=[_RaisingAdapter(OSError("timed out"))],
        force_refresh=True,
    )
    assert info.error_code == ERROR_PARSE_FAILED
    assert "页面结构" not in info.error


def test_parser_stale_不得与_offline_混淆():
    """主播没开播和解析器过期必须是不同码——这正是"能不能重连/要不要报错"的判据。"""
    info = parse_stream(
        "https://stale-probe-3.example.com/123",
        adapters=[_RaisingAdapter(KeyError("live_status"))],
        force_refresh=True,
    )
    assert info.error_code not in {"offline", "", None}
    assert info.error_code != "unsupported_url"
