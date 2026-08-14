from __future__ import annotations

import json

from lsc.recorder.manifest import ManifestStore, RecordingManifest


def test_manifest_save_is_atomic_and_roundtrips(tmp_path):
    store = ManifestStore(tmp_path / "manifest.json")
    manifest = RecordingManifest.create("room-1", "bilibili", "100")
    segment = manifest.add_segment("segments/000001.mkv", generation=2)
    manifest.complete_segment(
        segment,
        duration_ms=60000,
        size_bytes=1234,
        validation={"readable": True},
    )
    manifest.close()
    store.save(manifest)
    loaded = store.load()
    assert loaded.recording_session_id == manifest.recording_session_id
    assert loaded.segments[0].state == "COMPLETE"
    assert loaded.aggregate_duration_ms == 60000
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_recovery_is_idempotent(tmp_path):
    store = ManifestStore(tmp_path / "manifest.json")
    manifest = RecordingManifest.create("room-1", "direct")
    segment = manifest.add_segment("segments/000001.mkv")
    manifest.close(state="PARTIAL", unclean=True)
    (tmp_path / "segments").mkdir()
    (tmp_path / segment.path).write_bytes(b"media")
    store.save(manifest)
    recovered = store.recover()
    assert recovered.segments[0].state == "RECOVERED"
    assert recovered.segments[0].size_bytes == 5
    recovered_again = store.recover()
    assert recovered_again.segments[0].state == "RECOVERED"


def test_manifest_recovery_rejects_path_escape(tmp_path):
    store = ManifestStore(tmp_path / "manifest.json")
    manifest = RecordingManifest.create("room-1", "direct")
    manifest.add_segment("../outside.mkv")
    manifest.close(state="PARTIAL", unclean=True)
    store.save(manifest)
    recovered = store.recover()
    assert recovered.segments[0].state == "MISSING"


def test_manifest_recovery_discovers_orphan_segment(tmp_path):
    store = ManifestStore(tmp_path / "manifest.json")
    manifest = RecordingManifest.create("room-1", "direct")
    manifest.close(state="PARTIAL", unclean=True)
    (tmp_path / "segments").mkdir()
    (tmp_path / "segments" / "000002.mkv").write_bytes(b"media")
    store.save(manifest)
    recovered = store.recover()
    assert len(recovered.segments) == 1
    assert recovered.segments[0].state == "COMPLETE"
