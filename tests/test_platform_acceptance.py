from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from lsc.platforms.acceptance import (
    AcceptanceOptions,
    AcceptanceReport,
    _event_summary,
    run_acceptance,
    run_acceptance_batch,
    run_acceptance_loops,
    run_acceptance_verification_suite,
    write_report,
)
from lsc.platforms.capabilities import get_platform_capabilities
from lsc.platforms.models import ProbeResult, ResolveResult, StreamCandidate
from lsc.platforms.resolver import probe_candidates


def test_acceptance_cli_rejects_partial_lifecycle_options():
    from scripts.platform_acceptance import main

    assert main(["--url", "https://cdn.example/live", "--duration", "30"]) == 2
    assert main(["--url", "https://cdn.example/live", "--record-dir", "out"]) == 2
    assert main(["--url", "https://cdn.example/live", "--preview"]) == 2


def test_acceptance_cli_verification_suite_requires_record_dir():
    from scripts.platform_acceptance import main

    assert main([
        "--url", "https://cdn.example/live",
        "--verification-suite",
    ]) == 2


def test_acceptance_verification_suite_runs_three_modes(tmp_path):
    seen = []

    def run_one(item):
        seen.append((item.room_id, item.duration_sec, item.recording, item.preview))
        return AcceptanceReport(
            started_at=1.0,
            finished_at=2.0,
            source_url=item.source_url,
            platform="direct",
            passed=True,
        )

    suite = run_acceptance_verification_suite(
        AcceptanceOptions(
            source_url="https://cdn.example/live",
            room_id="suite",
            record_dir=str(tmp_path),
        ),
        recording_duration_sec=900,
        preview_duration_sec=900,
        parallel_duration_sec=1800,
        run_fn=run_one,
    )
    assert suite.passed is False
    assert seen == [
        ("suite-recording_15m", 900.0, True, False),
        ("suite-preview_15m", 900.0, False, True),
        ("suite-parallel_30m", 1800.0, True, True),
    ]
    assert suite.external_gates["network_interrupt_recovery"] == "REQUIRES_OPERATOR"
    assert suite.external_gates["application_restart_recovery"] == "REQUIRES_OPERATOR"
    assert suite.platform == "direct"

    verified = run_acceptance_verification_suite(
        AcceptanceOptions(
            source_url="https://cdn.example/live",
            room_id="suite-verified",
            record_dir=str(tmp_path),
        ),
        recording_duration_sec=900,
        preview_duration_sec=900,
        parallel_duration_sec=1800,
        operator_evidence={
            "network_interrupt_recovery": {"status": "VERIFIED"},
            "application_restart_recovery": {"status": "PASSED"},
        },
        run_fn=run_one,
    )
    assert verified.passed is True


def test_acceptance_expected_platform_mismatch_fails_closed():
    candidate = StreamCandidate("c1", "https://cdn.example/live.flv", quality_id="origin")
    result = ResolveResult(
        platform="direct",
        room_url="https://cdn.example/room",
        live_status="LIVE",
        capabilities=get_platform_capabilities("direct"),
        candidates=(candidate,),
    )
    probe_called = False

    def probe(*_args, **_kwargs):
        nonlocal probe_called
        probe_called = True
        return {}

    report = run_acceptance(
        AcceptanceOptions(
            source_url="https://cdn.example/room",
            expected_platform="bilibili",
        ),
        resolve_fn=lambda _request: result,
        probe_fn=probe,
    )
    assert report.passed is False
    assert report.expected_platform == "bilibili"
    assert report.platform == "direct"
    assert report.failures[0]["code"] == "PLATFORM_MISMATCH"
    assert probe_called is False


def test_acceptance_cli_does_not_mix_connection_loops_with_recording():
    from scripts.platform_acceptance import main

    assert main([
        "--url", "https://cdn.example/live",
        "--iterations", "2",
        "--record-dir", "out",
        "--duration", "30",
    ]) == 2


def test_acceptance_control_plane_report_is_redacted(tmp_path):
    candidate = StreamCandidate(
        "c1",
        "https://cdn.example/live.flv?token=SECRET_TOKEN",
        request_headers={"Cookie": "session=SECRET_COOKIE"},
        quality_id="origin",
        protocol="flv",
    )
    result = ResolveResult(
        platform="bilibili",
        room_url="https://live.bilibili.com/1",
        live_status="LIVE",
        capabilities=get_platform_capabilities("bilibili"),
        candidates=(candidate,),
    )

    def resolve(_request):
        return result

    def probe(_candidates, **_kwargs):
        return {
            "c1": ProbeResult(
                "c1",
                reachable=True,
                has_video=True,
                has_audio=True,
                timestamp_ok=True,
                protocol="flv",
                container="flv",
                duration_ms=1000,
            )
        }

    report = run_acceptance(
        AcceptanceOptions(
            source_url="https://live.bilibili.com/1?token=SECRET_TOKEN",
            ffprobe_path="fake-ffprobe",
        ),
        resolve_fn=resolve,
        probe_fn=probe,
    )

    assert report.passed is True
    assert report.lifecycle["status"] == "SKIPPED"
    assert report.app_version
    assert report.commit
    assert report.requested_quality == ""
    assert report.actual_quality == "origin"
    assert report.timings_ms["resolve"] >= 0
    assert report.timings_ms["probe"] >= 0
    assert report.timings_ms["total"] >= 0
    encoded = json.dumps(report.to_dict(), ensure_ascii=False)
    assert "SECRET_TOKEN" not in encoded
    assert "SECRET_COOKIE" not in encoded
    path = write_report(report, str(tmp_path / "acceptance.json"))
    assert json.loads((tmp_path / "acceptance.json").read_text(encoding="utf-8"))["passed"] is True
    assert path.endswith("acceptance.json")


def test_acceptance_fails_when_no_candidate_passes_probe():
    candidate = StreamCandidate("c1", "https://cdn.example/live.flv", quality_id="origin")
    result = ResolveResult(
        platform="direct",
        room_url="https://cdn.example/room",
        live_status="LIVE",
        capabilities=get_platform_capabilities("direct"),
        candidates=(candidate,),
    )
    report = run_acceptance(
        AcceptanceOptions(source_url="https://cdn.example/room"),
        resolve_fn=lambda _request: result,
        probe_fn=lambda _candidates, **_kwargs: {
            "c1": ProbeResult("c1", reachable=False, failure_kind="CONNECT_TIMEOUT")
        },
    )
    assert report.passed is False
    assert any(item["code"] == "NO_PLAYABLE_CANDIDATE" for item in report.failures)


def test_acceptance_wires_bounded_resolver_probe_recovery(monkeypatch):
    candidate = StreamCandidate("c1", "https://cdn.example/live", quality_id="origin")
    result = ResolveResult(
        platform="direct",
        room_url="https://cdn.example/room",
        canonical_room_id="room-1",
        live_status="LIVE",
        capabilities=get_platform_capabilities("direct"),
        candidates=(candidate,),
    )
    probe = ProbeResult(
        "c1",
        reachable=True,
        has_video=True,
        timestamp_ok=True,
        protocol="flv",
        container="flv",
    )
    captured = {}

    def fake_lifecycle(options, *, recovery_fn, **_kwargs):
        captured["recovery_fn"] = recovery_fn
        return {"status": "SKIPPED"}

    monkeypatch.setattr("lsc.platforms.acceptance._run_lifecycle", fake_lifecycle)

    report = run_acceptance(
        AcceptanceOptions(source_url="https://cdn.example/room"),
        resolve_fn=lambda _request: result,
        probe_fn=lambda *_args, **_kwargs: {"c1": probe},
    )
    assert report.passed is True

    class FakeIngest:
        def start_preview(self):
            return SimpleNamespace(ok=True)

    class FakeSupervisor:
        def __init__(self):
            self.ingest = FakeIngest()
            self.started = False
            self.switched = False

        def set_lease_context(self, **_kwargs):
            return None

        def switch_upstream(self, *_args, **_kwargs):
            self.switched = True
            return True

        def health(self):
            return {"recording_active": self.started, "preview_pid": None}

        def start_recording(self, *_args, **_kwargs):
            self.started = True
            return True

    supervisor = FakeSupervisor()
    assert captured["recovery_fn"](supervisor, "out.mkv") is True
    assert supervisor.switched is True
    assert supervisor.started is True


def test_acceptance_batch_isolates_failures_and_preserves_order():
    options = (
        AcceptanceOptions(source_url="https://cdn.example/a?token=SECRET_A", room_id="a"),
        AcceptanceOptions(source_url="https://cdn.example/b?token=SECRET_B", room_id="b"),
    )

    def run_one(item):
        if item.room_id == "b":
            raise RuntimeError("upstream https://cdn.example/b?token=SECRET_B failed")
        return AcceptanceReport(
            started_at=1.0,
            finished_at=2.0,
            source_url=item.source_url,
            platform="direct",
            passed=True,
        )

    batch = run_acceptance_batch(options, max_concurrency=2, run_fn=run_one)
    payload = json.dumps(batch.to_dict(), ensure_ascii=False)
    assert batch.passed is False
    assert [item["platform"] for item in batch.reports] == ["direct", "unknown"]
    assert batch.reports[0]["passed"] is True
    assert batch.reports[1]["failures"][0]["code"] == "ACCEPTANCE_EXCEPTION"
    assert "SECRET_A" not in payload
    assert "SECRET_B" not in payload


def test_acceptance_batch_without_targets_fails_closed():
    batch = run_acceptance_batch(())
    assert batch.passed is False
    assert batch.reports[0]["failures"][0]["code"] == "NO_TARGETS"


def test_acceptance_event_summary_tracks_recovery_and_generation():
    summary = _event_summary(
        [
            {"event_type": "RECOVERY_STARTED", "recovery_id": "r1", "generation": 2},
            {"event_type": "RECOVERY_SUCCEEDED", "recovery_id": "r1", "generation": 2},
            {"event_type": "RECOVERY_STARTED", "recovery_id": "r2", "generation": 3},
        ]
    )
    assert summary["recovery_count"] == 3
    assert summary["recovery_ids"] == ["r1", "r2"]
    assert summary["generations"] == [2, 3]


def test_acceptance_rejects_unreleased_lifecycle_resources(monkeypatch):
    candidate = StreamCandidate("c1", "https://cdn.example/live", quality_id="origin")
    result = ResolveResult(
        platform="direct",
        room_url="https://cdn.example/room",
        live_status="LIVE",
        capabilities=get_platform_capabilities("direct"),
        candidates=(candidate,),
    )
    probe = ProbeResult(
        "c1",
        reachable=True,
        has_video=True,
        timestamp_ok=True,
        protocol="flv",
        container="flv",
    )
    monkeypatch.setattr(
        "lsc.platforms.acceptance._run_lifecycle",
        lambda *args, **kwargs: {"status": "PASSED", "resources_released": False},
    )
    report = run_acceptance(
        AcceptanceOptions(source_url="https://cdn.example/room", record_dir="out", duration_sec=1),
        resolve_fn=lambda _request: result,
        probe_fn=lambda *_args, **_kwargs: {"c1": probe},
    )
    assert report.passed is False
    assert any(item["code"] == "RESOURCE_CLEANUP_FAILED" for item in report.failures)


def test_acceptance_rejects_invalid_recording_validation(monkeypatch):
    candidate = StreamCandidate("c1", "https://cdn.example/live", quality_id="origin")
    result = ResolveResult(
        platform="direct",
        room_url="https://cdn.example/room",
        live_status="LIVE",
        capabilities=get_platform_capabilities("direct"),
        candidates=(candidate,),
    )
    probe = ProbeResult(
        "c1",
        reachable=True,
        has_video=True,
        timestamp_ok=True,
        protocol="flv",
        container="flv",
    )
    monkeypatch.setattr(
        "lsc.platforms.acceptance._run_lifecycle",
        lambda *args, **kwargs: {
            "status": "PASSED",
            "resources_released": True,
            "segments_valid": False,
        },
    )
    report = run_acceptance(
        AcceptanceOptions(source_url="https://cdn.example/room", record_dir="out", duration_sec=1),
        resolve_fn=lambda _request: result,
        probe_fn=lambda *_args, **_kwargs: {"c1": probe},
    )
    assert report.passed is False
    assert any(item["code"] == "RECORDING_VALIDATION_FAILED" for item in report.failures)


def test_acceptance_batch_enforces_room_and_preview_capacity():
    options = tuple(
        AcceptanceOptions(
            source_url=f"https://cdn.example/{index}",
            room_id=f"room-{index}",
            preview=True,
        )
        for index in range(6)
    )
    seen: list[tuple[str, bool]] = []

    def run_one(item):
        seen.append((item.room_id, item.preview))
        return AcceptanceReport(
            started_at=1.0,
            finished_at=2.0,
            source_url=item.source_url,
            lifecycle={"status": "PASSED"},
            passed=True,
        )

    batch = run_acceptance_batch(
        options,
        max_concurrency=3,
        max_targets=5,
        preview_limit=2,
        run_fn=run_one,
    )
    assert len(seen) == 5
    assert sum(preview for _, preview in seen) == 2
    assert batch.reports[2]["lifecycle"]["preview_status"] == "SKIPPED_CAPACITY"
    assert batch.reports[-1]["failures"][0]["code"] == "RESOURCE_CAPACITY_EXCEEDED"
    assert batch.passed is False


def test_acceptance_preview_capacity_counts_only_preview_requests():
    options = (
        AcceptanceOptions(source_url="https://cdn.example/record-only", room_id="record"),
        AcceptanceOptions(source_url="https://cdn.example/preview-a", room_id="preview-a", preview=True),
        AcceptanceOptions(source_url="https://cdn.example/record-only-2", room_id="record-2"),
        AcceptanceOptions(source_url="https://cdn.example/preview-b", room_id="preview-b", preview=True),
    )

    seen: list[tuple[str, bool]] = []

    def run_one(item):
        seen.append((item.room_id, item.preview))
        return AcceptanceReport(
            started_at=1.0,
            finished_at=2.0,
            source_url=item.source_url,
            lifecycle={"status": "PASSED"},
            passed=True,
        )

    batch = run_acceptance_batch(
        options,
        max_concurrency=1,
        max_targets=4,
        preview_limit=2,
        run_fn=run_one,
    )

    assert seen == [
        ("record", False),
        ("preview-a", True),
        ("record-2", False),
        ("preview-b", True),
    ]
    assert all(item.get("lifecycle", {}).get("preview_status") != "SKIPPED_CAPACITY" for item in batch.reports)
    assert batch.passed is True


def test_acceptance_connection_loops_pass_at_nineteen_of_twenty():
    seen: list[AcceptanceOptions] = []

    def run_one(item):
        seen.append(item)
        attempt = len(seen)
        return AcceptanceReport(
            started_at=1.0,
            finished_at=2.0,
            source_url=item.source_url,
            platform="direct",
            passed=attempt != 3,
        )

    report = run_acceptance_loops(
        AcceptanceOptions(
            source_url="https://cdn.example/live?token=SECRET",
            room_id="loop-room",
            record_dir="must-not-run",
            duration_sec=7200,
            preview=True,
        ),
        iterations=20,
        run_fn=run_one,
    )

    assert report.mode == "connection_loops"
    assert report.attempts == 20
    assert report.successful_attempts == 19
    assert report.required_successes == 19
    assert report.success_rate == pytest.approx(0.95)
    assert report.passed is True
    assert all(not item.preview for item in seen)
    assert all(item.record_dir == "" and item.duration_sec == 0 for item in seen)


def test_acceptance_connection_loops_fail_below_gate():
    calls = 0

    def run_one(item):
        nonlocal calls
        calls += 1
        return AcceptanceReport(
            started_at=1.0,
            finished_at=2.0,
            source_url=item.source_url,
            platform="direct",
            passed=calls <= 18,
        )

    report = run_acceptance_loops(
        AcceptanceOptions(source_url="https://cdn.example/live"),
        iterations=20,
        run_fn=run_one,
    )

    assert report.successful_attempts == 18
    assert report.required_successes == 19
    assert report.passed is False


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real-media acceptance requires ffmpeg and ffprobe",
)
def test_acceptance_runs_real_probe_and_parallel_record_preview(tmp_path: Path):
    """Exercise the local control plane through actual ffprobe and FFmpeg sinks."""
    media = tmp_path / "stream.flv"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=44100",
            "-t",
            "60",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-f",
            "mpegts",
            str(media),
        ],
        check=True,
    )

    class Handler(SimpleHTTPRequestHandler):
        daemon_threads = True

        def log_message(self, *_args):
            pass

        def do_GET(self):  # noqa: N802 - local controlled CDN
            if self.path != "/stream.flv":
                self.send_error(404)
                return
            data = media.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                for offset in range(0, len(data), 188 * 20):
                    self.wfile.write(data[offset : offset + 188 * 20])
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        Handler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/stream.flv"
        candidate = StreamCandidate(
            "e2e|origin",
            url,
            quality_id="origin",
            quality_label="origin",
            protocol="flv",
            source_kind="official",
        )
        result = ResolveResult(
            platform="direct",
            room_url=url,
            canonical_room_id="e2e-room",
            live_status="LIVE",
            capabilities=get_platform_capabilities("direct"),
            candidates=(candidate,),
        )
        report = run_acceptance(
            AcceptanceOptions(
                source_url=url,
                room_id="e2e-room",
                ffprobe_path=shutil.which("ffprobe") or "ffprobe",
                record_dir=str(tmp_path / "recording"),
                duration_sec=3.0,
                preview=True,
                segmented=True,
                segment_seconds=2,
            ),
            resolve_fn=lambda _request: result,
            probe_fn=probe_candidates,
        )
        assert report.passed, report.to_dict()
        assert report.lifecycle["recording_started"] is True
        assert report.lifecycle["preview_started"] is True
        health = report.lifecycle["health"]
        assert health["upstream_pid"] is not None
        assert health["recording_pid"] is not None
        assert health["preview_pid"] is not None
        assert report.lifecycle["resources_released"] is True
        assert report.lifecycle["cleanup_health"]["upstream_pid"] is None
        assert Path(report.lifecycle["manifest_path"]).is_file()
        assert report.lifecycle["segments_valid"] is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
