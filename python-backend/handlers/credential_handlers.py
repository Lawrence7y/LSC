"""平台凭证 WebSocket 处理器（2026-09-18 拆分自 `room_handler.py`）。

`FEATURE-INVENTORY §1` 把平台接入层列为产品唯一入口 P0，而这一簇此前藏在
`room_handler.py` 第六千行里，且**每个处理器各自在函数体内 import**（共 11 处）——
既是 `CLAUDE.md §11.4` 禁止的写法，也让真实依赖对读者和静态检查都不可见
（AST 扫描会把它读成"这一簇没有外部依赖"）。这里全部提到模块级。

注入面刻意保持最小：只需 `server` 与一个 logger。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from lsc.platforms.capabilities import all_platform_capabilities
from lsc.platforms.cookie_helper import (
    get_bilibili_cookie_status,
    get_douyin_cookie_status,
    get_huya_cookie_status,
    save_bilibili_cookies_from_text,
    save_douyin_cookies_from_text,
    save_huya_cookies_from_text,
)
from lsc.platforms.credentials import get_default_credential_provider
from lsc.platforms.redaction import redact_text
from lsc.utils.error_messages import humanize_error


def _credential_liveness(platform: str, log: logging.Logger) -> dict[str, Any]:
    """凭据的**实测活性**，与"配了没"分开报。

    `get_*_cookie_status()` 只读文件，回答的是"用户贴过没有"；真正决定房间能否
    打开的是这份 Cookie 在平台上还算不算数。后者来自运行时观测：解析/拉流被判
    AUTH_REQUIRED / AUTH_EXPIRED 时由 `recovery_policy.note_credential_auth_failure`
    打失效标记（见 CLAUDE.md §7.2.2）。这里只做只读汇总，不发网络探测 ——
    探测要打 CDN，节流与配额策略都不属于本层。
    """
    try:
        provider = get_default_credential_provider()
        status = provider.get_status(platform).value
        info = provider.invalidation_info(platform)
    except Exception as exc:
        log.debug("credential liveness lookup failed platform=%s: %s", platform, exc)
        return {}
    out: dict[str, Any] = {'credential_status': status}
    if info:
        out['credential_invalidated_at'] = info['invalidated_at']
        out['credential_invalidated_reason'] = info['invalidated_reason']
    return out


def register_credential_handlers(server: Any, *, log: logging.Logger) -> None:
    """注册 6 条平台凭证路由。`log` 由 room_handler 注入，日志归属与级别不变。"""
    _log = log

    @server.on('get_douyin_cookie_status')
    async def handle_get_douyin_cookie_status(data: Any) -> dict[str, Any]:
        """查询抖音 Cookie 是否已配置，以及实测活性。"""
        try:
            return {'success': True, **get_douyin_cookie_status(), **_credential_liveness('douyin', _log)}
        except Exception as exc:
            _log.warning("get_douyin_cookie_status failed: %s", redact_text(exc))
            return {'success': False, 'error': redact_text(exc), 'configured': False, 'count': 0}

    @server.on('save_douyin_cookies')
    async def handle_save_douyin_cookies(data: Any) -> dict[str, Any]:
        """保存用户粘贴的抖音 Cookie（JSON / Cookie 头）。"""
        raw = ''
        if isinstance(data, dict):
            raw = str(data.get('cookies') or data.get('text') or '')
        if not raw.strip():
            return {'success': False, 'error': '请粘贴 Cookie 内容'}
        # 限制 Cookie 输入大小，防止超大 payload 导致 OOM（正常 Cookie < 16KB）
        _MAX_COOKIE_BYTES = 1 * 1024 * 1024  # 1 MB
        if len(raw) > _MAX_COOKIE_BYTES:
            _log.warning("抖音 Cookie 输入过大: %d bytes (limit %d)", len(raw), _MAX_COOKIE_BYTES)
            return {'success': False, 'error': f'Cookie 内容过大（{len(raw)} 字节），请检查输入'}
        try:
            status = save_douyin_cookies_from_text(raw)
            _log.info("抖音 Cookie 已保存: count=%s", status.get('count'))
            credential_status = ''
            try:
                credential_status = get_default_credential_provider().refresh(
                    'douyin'
                ).status.value
            except Exception as exc:
                _log.warning(
                    "refresh douyin credential state failed: %s",
                    redact_text(exc),
                )
            return {
                'success': True,
                **status,
                'credential_status': credential_status or 'INVALID',
            }
        except (ValueError, json.JSONDecodeError) as exc:
            return {'success': False, 'error': redact_text(exc)}
        except OSError as exc:
            _log.error("保存抖音 Cookie 失败: %s", redact_text(exc))
            return {'success': False, 'error': humanize_error(str(exc))}

    @server.on('get_bilibili_cookie_status')
    async def handle_get_bilibili_cookie_status(data: Any) -> dict[str, Any]:
        """查询 B 站 Cookie 是否已配置，以及实测活性。"""
        try:
            return {'success': True, **get_bilibili_cookie_status(), **_credential_liveness('bilibili', _log)}
        except Exception as exc:
            _log.warning("get_bilibili_cookie_status failed: %s", redact_text(exc))
            return {'success': False, 'error': redact_text(exc), 'configured': False, 'count': 0}

    @server.on('get_platform_credential_status')
    async def handle_get_platform_credential_status(data: Any) -> dict[str, Any]:
        """Return redacted credential state for any registered platform."""
        platform = str(data.get('platform') or '').strip().lower() if isinstance(data, dict) else ''
        account_ref = str(data.get('account_ref') or 'default').strip() if isinstance(data, dict) else 'default'
        if platform not in all_platform_capabilities():
            return {'success': False, 'error': '不支持的平台', 'status': 'NOT_CONFIGURED'}
        if not account_ref or len(account_ref) > 128:
            return {'success': False, 'error': 'account_ref 无效', 'status': 'INVALID'}
        try:
            provider = get_default_credential_provider()
            status = provider.get_status(platform, account_ref)
            capabilities = all_platform_capabilities()[platform]
            return {
                'success': True,
                'platform': platform,
                'account_ref': account_ref,
                'status': status.value,
                'available': status.value in {'AVAILABLE', 'EXPIRING'},
                'support_level': capabilities.support_level,
                'credential_kinds': list(capabilities.credential_kinds),
                **provider.invalidation_info(platform, account_ref),
            }
        except Exception as exc:
            _log.warning("platform credential status failed platform=%s: %s", platform, redact_text(exc))
            return {'success': False, 'error': '读取凭据状态失败', 'status': 'INVALID'}

    @server.on('save_bilibili_cookies')
    async def handle_save_bilibili_cookies(data: Any) -> dict[str, Any]:
        """保存用户粘贴的 B 站 Cookie（JSON / Cookie 头）。"""
        raw = ''
        if isinstance(data, dict):
            raw = str(data.get('cookies') or data.get('text') or '')
        if not raw.strip():
            return {'success': False, 'error': '请粘贴 Cookie 内容'}
        _MAX_COOKIE_BYTES = 1 * 1024 * 1024  # 1 MB
        if len(raw) > _MAX_COOKIE_BYTES:
            _log.warning("B站 Cookie 输入过大: %d bytes (limit %d)", len(raw), _MAX_COOKIE_BYTES)
            return {'success': False, 'error': f'Cookie 内容过大（{len(raw)} 字节），请检查输入'}
        try:
            status = save_bilibili_cookies_from_text(raw)
            _log.info("B站 Cookie 已保存: count=%s", status.get('count'))
            credential_status = ''
            try:
                credential_status = get_default_credential_provider().refresh(
                    'bilibili'
                ).status.value
            except Exception as exc:
                _log.warning(
                    "refresh bilibili credential state failed: %s",
                    redact_text(exc),
                )
            return {
                'success': True,
                **status,
                'credential_status': credential_status or 'INVALID',
            }
        except (ValueError, json.JSONDecodeError) as exc:
            return {'success': False, 'error': redact_text(exc)}
        except OSError as exc:
            _log.error("保存 B站 Cookie 失败: %s", redact_text(exc))
            return {'success': False, 'error': humanize_error(str(exc))}

    @server.on('get_huya_cookie_status')
    async def handle_get_huya_cookie_status(data: Any) -> dict[str, Any]:
        """查询虎牙 Cookie 是否已配置，以及实测活性。"""
        try:
            return {'success': True, **get_huya_cookie_status(), **_credential_liveness('huya', _log)}
        except Exception as exc:
            _log.warning("get_huya_cookie_status failed: %s", redact_text(exc))
            return {'success': False, 'error': redact_text(exc), 'configured': False, 'count': 0}

    @server.on('save_huya_cookies')
    async def handle_save_huya_cookies(data: Any) -> dict[str, Any]:
        """保存用户粘贴的虎牙 Cookie（JSON / Cookie 头）。"""
        raw = ''
        if isinstance(data, dict):
            raw = str(data.get('cookies') or data.get('text') or '')
        if not raw.strip():
            return {'success': False, 'error': '请粘贴 Cookie 内容'}
        _MAX_COOKIE_BYTES = 1 * 1024 * 1024  # 1 MB
        if len(raw) > _MAX_COOKIE_BYTES:
            _log.warning("虎牙 Cookie 输入过大: %d bytes (limit %d)", len(raw), _MAX_COOKIE_BYTES)
            return {'success': False, 'error': f'Cookie 内容过大（{len(raw)} 字节），请检查输入'}
        try:
            status = save_huya_cookies_from_text(raw)
            _log.info("虎牙 Cookie 已保存: count=%s", status.get('count'))
            credential_status = ''
            try:
                credential_status = get_default_credential_provider().refresh(
                    'huya'
                ).status.value
            except Exception as exc:
                _log.warning(
                    "refresh huya credential state failed: %s",
                    redact_text(exc),
                )
            return {
                'success': True,
                **status,
                'credential_status': credential_status or 'INVALID',
            }
        except (ValueError, json.JSONDecodeError) as exc:
            return {'success': False, 'error': redact_text(exc)}
        except OSError as exc:
            _log.error("保存虎牙 Cookie 失败: %s", redact_text(exc))
            return {'success': False, 'error': humanize_error(str(exc))}
