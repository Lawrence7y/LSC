"""平台自检 WS 路由（2026-09-18 债务收口 P2-2c）。

`lsc/platforms/acceptance.py` 早就有一套完整的控制面验收器（resolve → probe →
lease → 生命周期），但它**只能由开发者跑 `scripts/platform_acceptance.py` 触达**：
用户/客服遇到"房间打不开"时无法区分"平台改版 / 我的网络 / Cookie 失效"，只能
截图描述症状。这里把那套逻辑以只读、无副作用的形式接到产品里，让 Settings 能点一下。

安全边界（刻意设计，不要放宽）：
- `record_dir=""` + `recording=False` + `preview=False`：不启动 FFmpeg、不写盘 ——
  即 acceptance 模块文档里标注的"CI 可安全执行的控制面验收"同一档；
- 跑在 executor 里，不占事件循环；用 `run_in_executor(None, ...)` 复用循环自带的
  线程池，不额外造池；
- 同一时刻只允许一个自检在飞（连点不会堆线程、不会叠加对外呼）；
- 目标 URL 由用户输入决定，因此**沿用添加房间那一条**结构校验（由 room_handler
  注入），避免自检变成绕过校验的第二入口。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from lsc.platforms.acceptance import AcceptanceOptions, run_acceptance
from lsc.platforms.redaction import redact_text

_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT = False

# 外层兜底。run_acceptance 自带 resolve/probe 超时，正常不会走到这里；
# 真走到了说明某个适配器卡在网络等待上。此时后端线程无法强制取消，只能
# 放它自己按内部 deadline 结束 —— 因此这个上限同时也是"最坏情况下的 zombie 时长"。
_SELF_CHECK_TIMEOUT_SEC = 90.0


def _try_acquire() -> bool:
    global _INFLIGHT
    with _INFLIGHT_LOCK:
        if _INFLIGHT:
            return False
        _INFLIGHT = True
        return True


def _release() -> None:
    global _INFLIGHT
    with _INFLIGHT_LOCK:
        _INFLIGHT = False


def _run_self_check(source_url: str, requested_quality: str) -> dict[str, Any]:
    options = AcceptanceOptions(
        source_url=source_url,
        requested_quality=requested_quality,
        record_dir="",
        recording=False,
        preview=False,
    )
    return run_acceptance(options).to_dict()


def register_selfcheck_handlers(
    server: Any,
    *,
    log: logging.Logger,
    validate_url: Callable[[object], tuple[str, str | None]],
) -> None:
    """注册 `run_platform_self_check`。

    `validate_url` 必须是 `room_handler._room_url_shape` —— 与添加房间同源判据。
    """
    _log = log

    @server.on('run_platform_self_check')
    async def handle_run_platform_self_check(data: Any) -> dict[str, Any]:
        """对单个直播间链接跑一次控制面自检（不录制、不落盘）。"""
        payload = data if isinstance(data, dict) else {}
        url, shape_error = validate_url(payload.get('source_url'))
        if shape_error is not None:
            return {'success': False, 'error': shape_error}
        if not _try_acquire():
            return {'success': False, 'error': '已有一次自检正在进行，请等待其结束'}
        requested_quality = str(payload.get('quality') or '')
        try:
            loop = asyncio.get_running_loop()
            report = await asyncio.wait_for(
                loop.run_in_executor(
                    None, _run_self_check, url, requested_quality
                ),
                timeout=_SELF_CHECK_TIMEOUT_SEC,
            )
            _log.info(
                "平台自检完成 passed=%s platform=%s failures=%d",
                report.get('passed'),
                report.get('platform'),
                len(report.get('failures') or []),
            )
            return {'success': True, 'report': report}
        except asyncio.TimeoutError:
            host = url.split('/', 3)[2] if '://' in url else ''
            _log.warning("平台自检超时（%ds）host=%s", int(_SELF_CHECK_TIMEOUT_SEC), host)
            return {
                'success': False,
                'error': f'自检超时（{int(_SELF_CHECK_TIMEOUT_SEC)} 秒），请稍后重试',
            }
        except Exception as exc:
            _log.error("平台自检异常: %s", redact_text(exc))
            return {'success': False, 'error': '自检执行失败，请查看日志'}
        finally:
            _release()
