"""Tests for the manual platform E2E helper."""
from __future__ import annotations

from pathlib import Path


def test_local_smoke_phase_records_generated_source(tmp_path: Path, monkeypatch) -> None:
    import e2e_platform_test as e2e

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "recordings" / "captured.mkv"
    highlight_calls = []

    class FakeController:
        def init_capture(self):
            pass

        def init_exporter(self):
            pass

        def start_recording_with_crf(self, **kwargs):
            assert kwargs["stream_url"] == str(source)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"recorded")
            self.video_path = str(output)
            return True, str(output), "Copy", ""

        def stop_recording(self):
            return True, output.stat().st_size / (1024 * 1024), str(output)

        def probe_video_duration(self):
            return 1.0

        def cleanup(self):
            pass

    monkeypatch.setattr(e2e, "_create_local_smoke_source", lambda work_dir: source)
    monkeypatch.setattr(e2e, "RecordingController", FakeController)
    monkeypatch.setattr(e2e.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        e2e,
        "_run_highlight",
        lambda url, video_path, highlights_dir, reporter: highlight_calls.append((url, video_path)),
    )

    reporter = e2e._TestReporter(tmp_path)

    e2e._phase_local_smoke(tmp_path / "recordings", tmp_path / "highlights", reporter)

    assert reporter.summary["local_smoke_ok"] == 1
    assert reporter.results[-1]["category"] == "本地自检"
    assert reporter.results[-1]["success"] is True
    assert highlight_calls == [("local_smoke", str(output))]
