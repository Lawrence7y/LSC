"""UX guard: shared ingest preview paths must rotate preview_epoch_id."""
from __future__ import annotations

from pathlib import Path


_HANDLER = (
    Path(__file__).resolve().parents[1]
    / "python-backend"
    / "handlers"
    / "room_handler.py"
)


def _shared_setter_bodies(source: str) -> list[str]:
    """Extract bodies of shared-ingest `_set_shared_preview_on` closures."""
    marker = "def _set_shared_preview_on():"
    bodies: list[str] = []
    start = 0
    while True:
        idx = source.find(marker, start)
        if idx < 0:
            break
        # Capture until the next blank line after `return True`
        end = source.find("\n\n", idx)
        if end < 0:
            end = len(source)
        bodies.append(source[idx:end])
        start = idx + len(marker)
    return bodies


def test_shared_preview_setters_rotate_epoch():
    """Shared attach / preview-only must call on_preview_epoch_change like legacy MSE."""
    source = _HANDLER.read_text(encoding="utf-8")
    assert "shared ingest preview attached" in source
    assert "shared ingest preview-only started" in source

    bodies = _shared_setter_bodies(source)
    assert len(bodies) >= 2, f"expected >=2 shared setters, got {len(bodies)}"

    for body in bodies:
        assert "preview_enabled = True" in body
        assert "preview_epoch_id = new_epoch" in body
        assert "uuid4().hex" in body
        assert "on_preview_epoch_change(room_id, new_epoch)" in body


def test_already_running_replay_does_not_rotate_epoch():
    """Same-session init replay must not open a new preview epoch."""
    source = _HANDLER.read_text(encoding="utf-8")
    marker = "already streaming, init replayed"
    assert marker in source
    # Narrow window around the already-running branch
    note_idx = source.find(marker)
    window_start = max(0, note_idx - 1200)
    window = source[window_start:note_idx]
    assert "preview_enabled = True" in window
    assert "on_preview_epoch_change" not in window
    assert "preview_epoch_id" not in window
