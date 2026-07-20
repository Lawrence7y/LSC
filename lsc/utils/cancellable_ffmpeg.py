from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from lsc.utils.process_launcher import get_creation_flags, hidden_run_kwargs

_log = logging.getLogger(__name__)


class FFmpegCancelled(RuntimeError):
    pass


class CancellableFFmpeg:
    def __init__(
        self,
        cmd: list[str],
        *,
        cancel_check: Callable[[], bool] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self._cmd = list(cmd)
        self._cancel_check = cancel_check
        self._env = env
        self._cwd = cwd
        self._proc: subprocess.Popen[Any] | None = None

    def start(self) -> None:
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": self._env,
            "cwd": self._cwd,
        }
        flags = get_creation_flags()
        if flags:
            kwargs["creationflags"] = flags
        self._proc = subprocess.Popen(self._cmd, **kwargs)  # noqa: S603

    def poll(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.poll()

    @property
    def returncode(self) -> int | None:
        return None if self._proc is None else self._proc.returncode

    def cancel(self, timeout_sec: float = 1.0) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        self._terminate_tree()
        try:
            self._proc.wait(timeout=timeout_sec)
            return
        except subprocess.TimeoutExpired:
            self._kill_tree()
        try:
            self._proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffmpeg process tree did not exit") from exc

    def wait(self, timeout_sec: float = 120.0) -> subprocess.CompletedProcess[bytes]:
        if self._proc is None:
            raise RuntimeError("not started")
        deadline = time.monotonic() + timeout_sec
        while True:
            if self._cancel_check and self._cancel_check():
                self.cancel()
                raise FFmpegCancelled("ffmpeg cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.cancel()
                raise TimeoutError("ffmpeg timeout")
            try:
                out, err = self._proc.communicate(timeout=min(0.1, remaining))
                return subprocess.CompletedProcess(
                    self._cmd, self._proc.returncode, out, err
                )
            except subprocess.TimeoutExpired:
                continue

    def _terminate_tree(self) -> None:
        assert self._proc is not None
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/PID", str(self._proc.pid)],
                capture_output=True,
                **hidden_run_kwargs(),
            )
        else:
            self._proc.terminate()

    def _kill_tree(self) -> None:
        assert self._proc is not None
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self._proc.pid)],
                capture_output=True,
                **hidden_run_kwargs(),
            )
        else:
            self._proc.kill()
