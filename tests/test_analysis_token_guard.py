"""分析任务 token 世代守卫：超时/重启后旧 worker 不得覆盖新任务（F-14/T-306）。"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

from handlers.analysis_handlers import register_analysis_handlers


class _FakeServer:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def on(self, name, *args, **kwargs):
        def _deco(fn):
            self.handlers[name] = fn
            return fn
        return _deco


class _FakeBridge:
    def queue_broadcast(self, msg):
        pass


def test_stale_analysis_worker_cannot_overwrite_restarted_job(tmp_path):
    """超时重启场景：旧 worker 晚完成时，其结果不得覆盖新任务的 job dict。

    历史竞态：A 超时后 executor 线程仍在跑；用户立即重启登记 B（cancelled=False）；
    旧线程读到 B 的 cancelled=False 继续跑，完成时用 A 的结果整体覆盖 B 的 job。
    """
    video_file = tmp_path / "rec.mp4"
    video_file.write_bytes(b"fake")

    release_a = threading.Event()
    call_count = {"n": 0}

    def analyze(video_path, *, game, threshold, progress_callback, cancel_check, valorant_profile):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 任务 A：阻塞模拟长分析，唤醒后按 cancel_check 退出（真实现的契约）
            release_a.wait(timeout=10)
            if cancel_check():
                return None
            return [{"start": 1.0, "end": 2.0}]
        # 任务 B：立即完成
        return [{"start": 5.0, "end": 6.0}]

    @contextmanager
    def fake_input(room):
        yield str(video_file)

    room = SimpleNamespace(
        room_id="r1",
        record_output_path=str(video_file),
        record_manifest_path="",
        streamer_name="",
        stream_title="",
        room_url="",
    )
    jobs: dict = {}
    jobs_lock = threading.RLock()
    pool = ThreadPoolExecutor(max_workers=2)

    server = _FakeServer()
    register_analysis_handlers(
        server,
        bridge=_FakeBridge(),
        manager=SimpleNamespace(get_room=lambda rid: room),
        bridge_executor=pool,
        ai_executor=pool,
        load_settings=lambda: {},
        safe_float=lambda v, d: d,
        analyze_scene_or_rounds=analyze,
        validate_synced_analysis_targets=lambda *a, **k: (True, "", None, []),
        continuous_analysis_loop=None,
        auto_export_highlights=None,
        build_continuous_status_payload=lambda *a, **k: {},
        map_highlight_to_room=lambda *a, **k: {},
        recording_media_start=lambda *a, **k: 0.0,
        min_highlight_duration_for_queue=lambda *a, **k: 0.0,
        valorant_round_key=lambda h: "k",
        should_broadcast_clip_list_update=lambda *a, **k: False,
        analysis_jobs=jobs,
        analysis_jobs_lock=jobs_lock,
        continuous_tasks={},
        refined_round_keys=set(),
        refined_round_keys_lock=threading.Lock(),
    )
    start_analysis = server.handlers["start_analysis"]

    import handlers.analysis_handlers as ah

    saved_input = ah._room_recording_input
    saved_save = ah.save_analysis_results
    ah._room_recording_input = fake_input
    ah.save_analysis_results = MagicMock()
    try:
        async def scenario():
            # 1) 任务 A 启动（不等待完成，worker 阻塞在 release_a）
            task_a = asyncio.ensure_future(start_analysis({"room_id": "r1", "mode": "generic"}))
            for _ in range(200):
                with jobs_lock:
                    if "r1" in jobs:
                        break
                await asyncio.sleep(0.01)
            with jobs_lock:
                assert "r1" in jobs, "job A never registered"
                token_a = jobs["r1"].get("token")

            # 2) 模拟 A 超时：超时处置把 A 标记 cancelled（handler 超时路径行为），
            #    随后用户重启 → B 用新 token 取代 A 的 dict
            with jobs_lock:
                jobs["r1"]["cancelled"] = True
            result_b = await asyncio.ensure_future(
                start_analysis({"room_id": "r1", "mode": "generic"})
            )
            with jobs_lock:
                token_b = jobs["r1"].get("token")
            assert token_b != token_a
            assert result_b.get("success") is True

            # 3) 释放 A：旧 worker 恢复，应识别 token 失配并放弃
            release_a.set()
            result_a = await task_a
            assert result_a.get("cancelled") is True

            # 4) 关键断言：B 的 job dict 未被 A 的结果覆盖
            with jobs_lock:
                job = jobs["r1"]
                assert job.get("token") == token_b
                assert job.get("completed_at") is not None
                # B 的结果（5.0-6.0，经 setdefault 富化）；A 的结果（1.0-2.0）不得出现
                assert len(job.get("highlights") or []) == 1
                assert job["highlights"][0]["start"] == 5.0
                assert job["highlights"][0]["end"] == 6.0

        asyncio.run(scenario())
    finally:
        ah._room_recording_input = saved_input
        ah.save_analysis_results = saved_save
        release_a.set()
        pool.shutdown(wait=False)
