import asyncio
import json
import logging
import math
import time
from collections.abc import Callable
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed
from ws_auth import expected_ws_token, is_origin_allowed, validate_ws_token

_log = logging.getLogger('lsc.server')


def _truncate_for_log(data: Any, str_limit: int = 200, list_limit: int = 10) -> Any:
    """截断超大日志字段，避免日志文件暴增。"""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > str_limit:
                result[k] = f"<str of length {len(v)}>"
            elif isinstance(v, list) and len(v) > list_limit:
                result[k] = f"<list of length {len(v)}>"
            else:
                result[k] = v
        return result
    if isinstance(data, str) and len(data) > str_limit:
        return f"<str of length {len(data)}>"
    return data


class _NumpyJSONEncoder(json.JSONEncoder):
    """自定义 JSON encoder，处理 numpy 数值类型。"""

    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        return super().default(obj)


def _json_dumps(obj) -> str:
    """带 numpy 兼容的 JSON 序列化。"""
    return json.dumps(obj, cls=_NumpyJSONEncoder)


class LSCWebSocketServer:
    def __init__(self, host: str = 'localhost', port: int = 19876, fallback_ports: list[int] | None = None):
        self.host = host
        self.port = port
        self.fallback_ports = fallback_ports or [19877, 19878, 19879, 19880]
        self.clients: set = set()
        self.handlers: dict[str, Callable] = {}
        self.connect_handlers: list[Callable] = []
        self._server = None
        self._bound_port: int | None = None

    @property
    def bound_port(self) -> int | None:
        """返回实际绑定的端口（可能与配置的 port 不同，因端口回退）。"""
        return self._bound_port

    def on(self, message_type: str, handler: Callable | None = None):
        """注册消息处理器，支持装饰器用法：@server.on('type')"""
        def decorator(fn: Callable) -> Callable:
            self.handlers[message_type] = fn
            _log.debug("registered handler: %s -> %s", message_type, getattr(fn, '__name__', '?'))
            return fn

        if handler is None:
            return decorator
        return decorator(handler)

    def on_connect(self, handler: Callable | None = None):
        """注册客户端连接成功后的回调，支持装饰器用法。"""
        def decorator(fn: Callable) -> Callable:
            self.connect_handlers.append(fn)
            return fn

        if handler is None:
            return decorator
        return decorator(handler)

    async def handle_client(self, websocket):
        """处理客户端连接 — 握手后首帧认证（Token 不再通过 URL 传递）"""
        # S-4: Origin 校验 - 仅允许 Electron (file://) 和本地开发服务器
        origin = ''
        if hasattr(websocket, 'request_headers'):
            headers = websocket.request_headers
            getter = getattr(headers, 'get', None)
            if callable(getter):
                origin = getter('origin') or getter('Origin') or ''
            elif isinstance(headers, dict):
                origin = headers.get('origin') or headers.get('Origin') or ''
        # 打包后的 Electron ``file://`` 渲染器在部分 Chromium 版本中不会
        # 发送 Origin。后续首帧仍强制校验由主进程注入的随机 Token，且服务
        # 仅绑定到 loopback，因此可安全兼容这种本地受信调用。
        if origin and not is_origin_allowed(origin):
            _log.warning("Rejected WebSocket connection from origin: %s", origin)
            close_fn = getattr(websocket, 'close', None)
            if callable(close_fn):
                await close_fn(code=1008, reason='Origin not allowed')
            return

        # 握手后首帧认证：等待客户端发送 auth 消息，Token 不再出现在 URL 中
        authenticated = False
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            try:
                data = json.loads(raw) if isinstance(raw, str) else {}
            except Exception:
                data = {}
            token = data.get('token')
            if data.get('type') == 'auth' and validate_ws_token(token):
                authenticated = True
            elif data.get('type') == 'auth':
                _log.warning(
                    "Rejected WebSocket auth: provided token length=%d, expected token configured=%s",
                    len(token) if isinstance(token, str) else 0,
                    bool(expected_ws_token()),
                )
        except asyncio.TimeoutError:
            pass
        except websockets.ConnectionClosed:
            return

        if not authenticated:
            _log.warning("Rejected WebSocket connection: auth failed or timeout")
            close_fn = getattr(websocket, 'close', None)
            if callable(close_fn):
                await close_fn(code=1008, reason='Auth required')
            return

        self.clients.add(websocket)
        _log.info(f"Client connected (auth OK). Total: {len(self.clients)}")

        for handler in self.connect_handlers:
            try:
                await handler(websocket)
            except websockets.ConnectionClosed:
                _log.info("Client disconnected during connect handler")
                return
            except Exception as exc:
                _log.error(f"Connect handler error: {exc}", exc_info=True)

        pending: set[asyncio.Task] = set()

        # 令牌桶速率限制：每连接每秒最多 100 条消息
        _rate_limit_tokens = 100
        _rate_limit_refill_at = time.monotonic() + 1.0

        async def _check_rate_limit() -> bool:
            nonlocal _rate_limit_tokens, _rate_limit_refill_at
            now = time.monotonic()
            if now >= _rate_limit_refill_at:
                _rate_limit_tokens = 100
                _rate_limit_refill_at = now + 1.0
            if _rate_limit_tokens > 0:
                _rate_limit_tokens -= 1
                return True
            return False

        async def dispatch(message: str):
            msg_type = None
            request_id = None
            # 速率限制检查
            if not await _check_rate_limit():
                _log.warning("Rate limit exceeded, dropping message")
                return
            try:
                data = json.loads(message)
                msg_type = data.get('type')
                msg_data = data.get('data', {})
                log_data = _truncate_for_log(msg_data)
                high_freq_types = frozenset({
                    'mse_segment', 'mse_init', 'rooms_updated',
                    'export_progress', 'get_export_job_status', 'medium_tick',
                })
                if msg_type in high_freq_types:
                    _log.debug("Received WS message: type=%s", msg_type)
                else:
                    _log.info("Received WS message: type=%s, data=%s", msg_type, log_data)

                handler = self.handlers.get(msg_type)
                if handler is None:
                    _log.warning("Unknown message type: %s", msg_type)
                    return
                request_id = msg_data.pop('request_id', None) if isinstance(msg_data, dict) else None
                result = await handler(msg_data)
                if result is None:
                    return
                if request_id is not None and isinstance(result, dict):
                    result['request_id'] = request_id
                if msg_type not in high_freq_types:
                    _log.info("Sending WS response: type=%s_response, data=%s", msg_type, _truncate_for_log(result))
                await websocket.send(_json_dumps({'type': f'{msg_type}_response', 'data': result}))
            except json.JSONDecodeError:
                _log.warning("Invalid JSON format received (truncated): %s", message[:500])
            except Exception as exc:
                _log.error("Error handling message: %s", exc, exc_info=True)
                if msg_type is not None:
                    try:
                        error_data: dict[str, Any] = {'success': False, 'error': str(exc)}
                        if request_id is not None:
                            error_data['request_id'] = request_id
                        await websocket.send(_json_dumps({
                            'type': f'{msg_type}_response',
                            'data': error_data,
                        }))
                    except Exception as send_exc:
                        _log.debug("Failed to send error response: %s", send_exc)

        # Process messages sequentially per connection to guarantee in-order
        # handler execution. Previously each message spawned an independent
        # asyncio.create_task, so a later message (e.g. export_clip) could
        # complete before an earlier one (e.g. set_mark_in) whose state it
        # depends on. Handlers are non-blocking (long work is queued), so
        # serialization does not stall the connection.
        try:
            async for message in websocket:
                await dispatch(message)
        except ConnectionClosed:
            pass
        finally:
            # `pending` is kept for backward compatibility but is no longer
            # populated under sequential processing. If any stray tasks exist
            # (e.g. from connect handlers), bound the wait so a slow/stuck
            # task cannot block disconnect cleanup indefinitely.
            if pending:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=3.0,
                    )
                except asyncio.TimeoutError:
                    for t in pending:
                        t.cancel()
            self.clients.discard(websocket)
            _log.info(f"Client disconnected. Total: {len(self.clients)}")

    async def broadcast(self, message_type: str, data: Any):
        """广播消息给所有客户端。

        高频消息（mse_segment/mse_init/rooms_updated/export_progress）不记录 INFO，
        避免日志文件被淹没。确保 INFO 中 MSE 分片记录为 0。
        """
        if not self.clients:
            return

        message = _json_dumps({
            'type': message_type,
            'data': data
        })

        # 发送并检测慢客户端，超时客户端移出广播集合
        slow_clients: list = []
        async def _send_with_timeout(client):
            try:
                await asyncio.wait_for(client.send(message), timeout=1.0)
            except asyncio.TimeoutError:
                slow_clients.append(client)
            except Exception:
                slow_clients.append(client)

        await asyncio.gather(
            *[_send_with_timeout(client) for client in self.clients],
            return_exceptions=True,
        )

        # 移除慢客户端，避免单个客户端拖慢整个广播
        for client in slow_clients:
            self.clients.discard(client)
            _log.warning("Removed slow WebSocket client (send timeout)")

        # 高频消息不记录 INFO，确保 INFO 中 MSE 记录为 0
        _HIGH_FREQ_BROADCASTS = frozenset({
            'mse_segment', 'mse_init', 'rooms_updated',
            'export_progress', 'medium_tick',
        })
        if message_type not in _HIGH_FREQ_BROADCASTS:
            log_data = _truncate_for_log(data)
            _log.debug(f"Broadcasted WS message: type={message_type}, data={log_data}")

    async def broadcast_bytes(self, payload: bytes) -> None:
        """广播原始二进制帧（用于 MSE fMP4，避免 base64）。"""
        if not self.clients or not payload:
            return
        slow_clients: list = []
        async def _send_with_timeout(client):
            try:
                await asyncio.wait_for(client.send(payload), timeout=1.0)
            except asyncio.TimeoutError:
                slow_clients.append(client)
            except Exception:
                slow_clients.append(client)

        await asyncio.gather(
            *[_send_with_timeout(client) for client in self.clients],
            return_exceptions=True,
        )

        for client in slow_clients:
            self.clients.discard(client)
            _log.warning("Removed slow WebSocket client (send timeout, bytes)")

    async def broadcast_mse(self, kind: str, room_id: str, payload: bytes) -> None:
        """广播 MSE init/segment 为二进制帧。"""
        from mse_ws_frames import pack_mse_frame
        await self.broadcast_bytes(pack_mse_frame(kind, room_id, payload))

    async def _heartbeat_loop(self):
        """定期广播心跳（P3-2: 后端心跳检测）。"""
        while True:
            try:
                await asyncio.sleep(5)
                if self.clients:
                    await self.broadcast('heartbeat', {'ts': time.time()})
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _log.debug("heartbeat broadcast error: %s", exc)

    async def start(self):
        """启动服务器，支持端口回退（主端口被占用时尝试备用端口）。"""
        ports_to_try = [self.port] + [p for p in self.fallback_ports if p != self.port]
        last_error = None

        for port in ports_to_try:
            try:
                if port != self.port:
                    _log.warning(f"Port {self.port} unavailable, trying fallback port {port}...")
                async with websockets.serve(
                    self.handle_client, self.host, port,
                    max_size=1 * 1024 * 1024,  # 1MB 消息体上限（JSON 消息通常 < 100KB）
                    # Detect silent TCP drops (network partition, killed
                    # client). Without keepalive pings, a half-open socket
                    # stays in self.clients indefinitely (#98).
                    ping_interval=20, ping_timeout=20,
                ) as srv:
                    self._server = srv
                    self._bound_port = port
                    _log.info(f"WebSocket server listening on ws://{self.host}:{port}")
                    # 启动心跳任务
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    try:
                        await asyncio.Future()  # 永远运行
                        return  # 正常退出时返回
                    finally:
                        heartbeat_task.cancel()
            except OSError as e:
                last_error = e
                continue

        # 所有端口都失败
        raise RuntimeError(f"Failed to bind WebSocket server on any port ({ports_to_try}): {last_error}")


# 全局服务器实例
server = LSCWebSocketServer()


def drain_merge_broadcasts(bridge):
    """从 bridge 队列消费所有待发消息，按规则做 last-value coalesce。

    - rooms_updated / system_stats / continuous_analysis_status：按 type 合并
    - recording_stopped / clip_* / mse_* / export_progress：按 type + room_id/job_id 分桶
    - 其余消息：不合并（每条保留）

    返回保序的消息列表，供 _broadcast_coroutine 发送。
    """
    _LAST_VALUE_TYPES = frozenset({
        'rooms_updated',
        'system_stats',
        'continuous_analysis_status',
        'analysis_progress',
    })

    def _keyed(msg_type: str, data: dict[str, Any]) -> str | None:
        if msg_type == 'recording_stopped':
            return f"{msg_type}:{data.get('room_id', '')}"
        if msg_type == 'room_updated':
            return f"{msg_type}:{data.get('room_id', '')}"
        if msg_type in ('clip_completed', 'clip_failed', 'export_progress', 'clip_export_started'):
            return f"{msg_type}:{data.get('job_id') or data.get('clip_id', '')}"
        if msg_type in ('mse_error', 'mse_reconnecting', 'mse_reconnected', 'mse_init'):
            return f"{msg_type}:{data.get('room_id', '')}"
        if msg_type == 'clip_queued':
            return f"{msg_type}:{data.get('room_id', '')}:{data.get('clip_id') or data.get('round_key', '')}"
        if msg_type == 'highlight_stream':
            return f"{msg_type}:{data.get('room_id', '')}:{data.get('start', '')}"
        if msg_type in ('timeline_invalidated', 'timeline_invalidated_broadcast'):
            return f"{msg_type}:{data.get('timeline_id') or data.get('reason', '')}"
        if msg_type == 'timeline_ready':
            return msg_type
        if msg_type == 'continuous_highlights':
            return f"{msg_type}:{data.get('room_id', '')}"
        if msg_type == 'clip_confirm_status':
            return f"{msg_type}:{data.get('room_id', '')}:{data.get('round_key', '')}"
        return None

    messages: list[dict[str, Any]] = []
    while True:
        msg = bridge.get_broadcast(block=False)
        if msg is None:
            break
        messages.append(msg)

    coalesced: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for msg in messages:
        msg_type = msg.get('type', '')
        data = msg.get('data') if isinstance(msg.get('data'), dict) else {}
        if msg_type in _LAST_VALUE_TYPES:
            key = msg_type
        else:
            keyed = _keyed(msg_type, data or {})
            key = keyed if keyed is not None else f"{msg_type}:{id(msg)}"
        if key not in coalesced:
            order.append(key)
        coalesced[key] = msg
    return [coalesced[k] for k in order]


def main():
    """独立入口：参考 main.py 的两线程模型启动后端。

    RoomOrchestrator 在独立编排线程运行；WebSocket 在工作线程；
    BroadcastHub 负责事件→WS 广播队列。
    """
    import threading

    from broadcast_hub import BroadcastHub
    from handlers.room_handler import register_room_handlers

    from lsc.core.orchestrator import RoomOrchestrator

    manager = RoomOrchestrator()
    manager.start()
    bridge = BroadcastHub(manager)

    loop = asyncio.new_event_loop()
    stop_event = threading.Event()

    async def _drain_broadcasts():
        """从 bridge 队列消费广播消息并推送给 WebSocket 客户端（事件驱动）。"""
        wake = asyncio.Event()
        bridge.bind_async_wake(loop, wake)
        while True:
            merged = drain_merge_broadcasts(bridge)
            if not merged:
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                continue
            for msg in merged:
                await server.broadcast(msg.get('type'), msg.get('data', {}))

    async def _start_export_queue():
        """启动全局导出队列 worker。"""
        from handlers.room_handler import _ensure_export_queue
        await _ensure_export_queue()

    def _run_ws():
        asyncio.set_event_loop(loop)
        register_room_handlers(server, bridge)
        loop.create_task(_drain_broadcasts())
        loop.create_task(_start_export_queue())
        try:
            loop.run_until_complete(server.start())
        except asyncio.CancelledError:
            pass

    ws_thread = threading.Thread(target=_run_ws, daemon=True)
    ws_thread.start()

    try:
        stop_event.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        try:
            manager.shutdown(timeout_sec=10.0)
        except Exception:
            pass
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass


if __name__ == '__main__':
    main()
