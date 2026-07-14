"""MseSender 背压控制测试 — TDD 先行。"""
from __future__ import annotations

import base64
import time
from unittest.mock import MagicMock, call

import pytest

from lsc.core.services.mse_sender import MseSender


@pytest.fixture
def mock_broadcast():
    return MagicMock()


@pytest.fixture
def sender(mock_broadcast):
    return MseSender(room_id="room-1", broadcast_fn=mock_broadcast)


class TestMseSenderBasics:
    def test_create_sender(self, sender):
        assert sender.room_id == "room-1"
        assert sender.dropped == 0

    def test_push_media_segment(self, sender, mock_broadcast):
        """推入媒体片到队列。"""
        data = b"x" * 100
        sender.push("media", data)
        assert sender.queued_bytes == 100
        assert len(sender.queue) == 1

    def test_drain_sends_base64(self, sender, mock_broadcast):
        """出队时 base64 编码并广播。"""
        data = b"test_data_12345"
        sender.push("media", data)
        sender.drain()
        mock_broadcast.assert_called_once()
        call_args = mock_broadcast.call_args
        assert call_args[0][0] == "mse_media"
        sent_data = call_args[0][1]["data"]
        # 验证数据是 base64 编码的
        decoded = base64.b64decode(sent_data)
        assert decoded == data

    def test_drain_clears_queue(self, sender, mock_broadcast):
        sender.push("media", b"data1")
        sender.push("media", b"data2")
        assert len(sender.queue) == 2
        sender.drain()
        assert len(sender.queue) == 0
        assert sender.queued_bytes == 0


class TestMseSenderBackpressure:
    def test_max_2_segments(self, sender, mock_broadcast):
        """队列硬上限 2 个媒体片，超出丢最旧。"""
        sender.push("media", b"first")
        sender.push("media", b"second")
        sender.push("media", b"third")  # 应丢 first
        assert len(sender.queue) == 2
        assert sender.dropped == 1

    def test_max_2mb(self, sender, mock_broadcast):
        """队列硬上限 2 MiB。"""
        # 推入 1.5 MiB
        big_data = b"x" * (1024 * 1024 + 512 * 1024)
        sender.push("media", big_data)
        assert sender.queued_bytes == len(big_data)
        # 再推入 1 MiB，应丢旧的
        sender.push("media", b"y" * (1024 * 1024))
        # 旧的 1.5 MiB 被丢弃
        assert sender.dropped == 1
        assert sender.queued_bytes == 1024 * 1024

    def test_drop_oldest_policy(self, sender, mock_broadcast):
        """拥塞时只丢最旧预览片。"""
        sender.push("media", b"oldest")
        sender.push("media", b"old")
        sender.push("media", b"new")  # 丢 oldest
        sender.drain()
        calls = mock_broadcast.call_args_list
        # 应只发送 old 和 new (oldest 被丢弃)
        assert len(calls) == 2
        sent_data_0 = base64.b64decode(calls[0][0][1]["data"])
        sent_data_1 = base64.b64decode(calls[1][0][1]["data"])
        assert sent_data_0 == b"old"
        assert sent_data_1 == b"new"


class TestMseSenderPriority:
    def test_init_sent_immediately(self, sender, mock_broadcast):
        """init 消息不排队，直接发送。"""
        data = b"init_segment_data"
        sender.push("init", data)
        # 立即广播
        mock_broadcast.assert_called_once()
        assert sender.queued_bytes == 0  # 不入队

    def test_error_sent_immediately(self, sender, mock_broadcast):
        """error 消息不排队，直接发送。"""
        sender.push("error", b"stream_error")
        mock_broadcast.assert_called_once()

    def test_control_sent_immediately(self, sender, mock_broadcast):
        """control 消息不排队，直接发送。"""
        sender.push("control", b"control_msg")
        mock_broadcast.assert_called_once()

    def test_init_over_media_priority(self, sender, mock_broadcast):
        """init 优先于媒体片。"""
        media_data = b"media_segment"
        init_data = b"init_segment"
        # 先推媒体
        sender.push("media", media_data)
        # 再推 init（直接发送）
        sender.push("init", init_data)
        # drain 发送剩余媒体
        sender.drain()
        calls = mock_broadcast.call_args_list
        assert len(calls) == 2
        # init 先发送
        assert base64.b64decode(calls[0][0][1]["data"]) == init_data
        assert base64.b64decode(calls[1][0][1]["data"]) == media_data


class TestMseSenderEncodeOnDequeue:
    def test_base64_encode_on_dequeue(self, sender, mock_broadcast):
        """编码在出队时进行，入队时存储原始字节。"""
        raw_data = b"\x00\x01\x02\xff\xfe" * 20
        sender.push("media", raw_data)
        # 入队时是原始字节
        assert sender.queue[0][1] == raw_data
        sender.drain()
        # 出队时编码为 base64
        sent = mock_broadcast.call_args[0][1]["data"]
        assert base64.b64decode(sent) == raw_data


class TestMseSenderStats:
    def test_dropped_counter(self, sender):
        """统计丢弃的段数。"""
        assert sender.dropped == 0
        # 填满队列
        sender.push("media", b"seg1")
        sender.push("media", b"seg2")
        # 丢弃
        sender.push("media", b"seg3")
        sender.push("media", b"seg4")
        assert sender.dropped == 2

    def test_queue_state(self, sender):
        """队列状态查询。"""
        sender.push("media", b"data1")
        sender.push("media", b"data2")
        assert sender.queued_bytes == 10
        assert len(sender.queue) == 2
