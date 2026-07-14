"""Error statistics tracking for monitoring and diagnostics."""
from __future__ import annotations

import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class ErrorStats:
    """Track error occurrences and patterns."""
    # RLock (reentrant) is required because get_frequent_errors() and
    # get_summary() call other locked methods (get_error_rate / get_frequent_errors)
    # while already holding the lock. A plain Lock deadlocks on re-entry.
    _lock: RLock = field(default_factory=RLock, repr=False)
    _error_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _error_timestamps: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    _last_error: str = ""
    _last_error_code: str = ""
    _total_errors: int = 0

    def record_error(self, error_code: str, error_msg: str = "") -> None:
        """Record an error occurrence."""
        with self._lock:
            self._error_counts[error_code] += 1
            self._error_timestamps[error_code].append(_time.time())
            # Keep only last 100 timestamps per error type
            if len(self._error_timestamps[error_code]) > 100:
                self._error_timestamps[error_code] = self._error_timestamps[error_code][-100:]
            self._last_error = error_msg
            self._last_error_code = error_code
            self._total_errors += 1

    def get_error_count(self, error_code: str) -> int:
        """Get count for a specific error type."""
        with self._lock:
            return self._error_counts.get(error_code, 0)

    def get_total_errors(self) -> int:
        """Get total error count."""
        with self._lock:
            return self._total_errors

    def get_error_rate(self, error_code: str, window_seconds: float = 300) -> float:
        """Get error rate (errors per minute) for a specific error type within time window."""
        with self._lock:
            timestamps = self._error_timestamps.get(error_code, [])
            if not timestamps:
                return 0.0

            cutoff = _time.time() - window_seconds
            recent_count = sum(1 for ts in timestamps if ts >= cutoff)

            # Calculate rate per minute
            minutes = window_seconds / 60
            return recent_count / minutes if minutes > 0 else 0.0

    def get_frequent_errors(self, threshold: float = 1.0) -> list[tuple[str, int, float]]:
        """Get errors that occur more frequently than threshold (per minute).

        Returns:
            List of (error_code, count, rate_per_minute) tuples.
        """
        with self._lock:
            frequent = []
            for code in self._error_counts:
                rate = self.get_error_rate(code)
                if rate >= threshold:
                    frequent.append((code, self._error_counts[code], rate))
            return sorted(frequent, key=lambda x: x[2], reverse=True)

    def get_last_error(self) -> tuple[str, str]:
        """Get the last error code and message."""
        with self._lock:
            return self._last_error_code, self._last_error

    def clear(self) -> None:
        """Clear all error statistics."""
        with self._lock:
            self._error_counts.clear()
            self._error_timestamps.clear()
            self._last_error = ""
            self._last_error_code = ""
            self._total_errors = 0

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of error statistics."""
        with self._lock:
            return {
                "total_errors": self._total_errors,
                "error_types": dict(self._error_counts),
                "last_error": self._last_error,
                "last_error_code": self._last_error_code,
                "frequent_errors": self.get_frequent_errors(),
            }


# Global error stats instance
_global_error_stats = ErrorStats()


def get_error_stats() -> ErrorStats:
    """Get the global error statistics instance."""
    return _global_error_stats
