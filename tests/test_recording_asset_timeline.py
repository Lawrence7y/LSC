from __future__ import annotations

from types import SimpleNamespace

from lsc.recorder.assets import RecordingAsset
from lsc.recorder.manifest import ManifestStore, RecordingManifest
from lsc.recorder.timeline import TimelineMapper


def test_timeline_mapper_never_moves_backwards_across_reset_and_gap():
    manifest = RecordingManifest.create("room-1", "direct")
    first = manifest.add_segment("segments/000001.mkv", generation=1)
    first.media_start_ms = 0
    first.media_end_ms = 10_000
    manifest.complete_segment(first, duration_ms=10_000, size_bytes=1)
    second = manifest.add_segment("segments/000002.mkv", generation=2)
    second.media_start_ms = 0
    second.media_end_ms = 5_000
    manifest.complete_segment(second, duration_ms=5_000, size_bytes=1)
    manifest.gaps.append({"before_sequence": 2, "duration_ms": 2_000})

    mapper = TimelineMapper(manifest)
    values = [mapper.media_to_content(value) for value in (0, 5, 10, 0, 2, 5)]
    assert all(right >= left for left, right in zip(values, values[1:], strict=False))
    assert mapper.map_range(0, 3)[1] >= mapper.map_range(0, 3)[0]


def test_recording_asset_reads_only_manifest_declared_segments(tmp_path):
    store = ManifestStore(tmp_path / "manifest.json")
    manifest = RecordingManifest.create("room-1", "direct")
    entry = manifest.add_segment("segments/000001.mkv")
    (tmp_path / "segments").mkdir()
    (tmp_path / entry.path).write_bytes(b"media")
    manifest.complete_segment(entry, duration_ms=1000, size_bytes=5)
    manifest.close()
    store.save(manifest)

    asset = RecordingAsset.load(str(store.path))
    assert len(asset.segment_paths()) == 1
    assert asset.to_dict()["segment_count"] == 1


def test_recording_asset_legacy_file_adapter(tmp_path):
    media = tmp_path / "legacy.mp4"
    media.write_bytes(b"media")
    asset = RecordingAsset.from_file(str(media), room_session_id="room-1")
    assert asset.segment_paths() == (str(media.resolve()),)
    assert asset.manifest.room_session_id == "room-1"


def test_recording_asset_concat_descriptor_uses_manifest_order(tmp_path):
    store = ManifestStore(tmp_path / "manifest.json")
    manifest = RecordingManifest.create("room-1", "direct")
    segments = tmp_path / "segments"
    segments.mkdir()
    for sequence in (1, 2):
        entry = manifest.add_segment(f"segments/{sequence:06d}.mkv")
        (tmp_path / entry.path).write_bytes(str(sequence).encode())
        manifest.complete_segment(entry, duration_ms=1000, size_bytes=1)
    manifest.segments.reverse()
    store.save(manifest)
    asset = RecordingAsset.load(str(store.path))
    descriptor = asset.create_concat_descriptor()
    try:
        lines = (tmp_path / descriptor.split(str(tmp_path))[1].lstrip("/\\")).read_text(encoding="utf-8").splitlines()
        assert lines[0].endswith("000001.mkv'")
        assert lines[1].endswith("000002.mkv'")
    finally:
        asset.remove_concat_descriptor(descriptor)


def test_recording_asset_materializes_multiple_segments_and_cleans_temp(tmp_path, monkeypatch):
    store = ManifestStore(tmp_path / "manifest.json")
    manifest = RecordingManifest.create("room-1", "direct")
    segments = tmp_path / "segments"
    segments.mkdir()
    for sequence in (1, 2):
        entry = manifest.add_segment(f"segments/{sequence:06d}.mkv")
        (tmp_path / entry.path).write_bytes(b"media")
        manifest.complete_segment(entry, duration_ms=1000, size_bytes=5)
    store.save(manifest)
    asset = RecordingAsset.load(str(store.path))

    def fake_run(command, **kwargs):
        output = command[-1]
        (tmp_path / output.split(str(tmp_path))[1].lstrip("/\\")).write_bytes(b"joined")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("lsc.recorder.assets.subprocess.run", fake_run)
    with asset.materialized_input(ffmpeg_path="ffmpeg") as media_path:
        assert media_path.endswith(".mkv")
        assert (tmp_path / media_path.split(str(tmp_path))[1].lstrip("/\\")).is_file()
    assert not (tmp_path / media_path.split(str(tmp_path))[1].lstrip("/\\")).exists()


def test_recording_asset_rejects_unreadable_segment_before_consumer(tmp_path, monkeypatch):
    store = ManifestStore(tmp_path / "manifest.json")
    manifest = RecordingManifest.create("room-1", "direct")
    entry = manifest.add_segment("segments/000001.mkv")
    (tmp_path / "segments").mkdir()
    (tmp_path / entry.path).write_bytes(b"not-media")
    manifest.complete_segment(entry, duration_ms=1000, size_bytes=9)
    store.save(manifest)
    asset = RecordingAsset.load(str(store.path))

    monkeypatch.setattr(
        "lsc.recorder.assets.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="{}",
            stderr="invalid data",
        ),
    )
    assert asset.validate_segments(ffprobe_path="ffprobe")[str((tmp_path / entry.path).resolve())]["readable"] is False
    try:
        with asset.materialized_input(ffprobe_path="ffprobe"):
            raise AssertionError("unreadable media must not reach the consumer")
    except RuntimeError as exc:
        assert "unreadable" in str(exc)
