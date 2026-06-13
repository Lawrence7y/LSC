"""Session history persistence for recording sessions."""
from __future__ import annotations

import json
from pathlib import Path


class SessionHistoryStore:
    """JSON-based store for recording session history."""

    def __init__(self, path: Path):
        self._path = Path(path)

    def load_sessions(self) -> list[dict]:
        """Load all sessions from the store."""
        if not self._path.is_file():
            return []
        return json.loads(self._path.read_text(encoding="utf-8"))

    def append_session(self, session: dict) -> None:
        """Append a new session to the store (newest first)."""
        sessions = self.load_sessions()
        sessions.insert(0, session)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
