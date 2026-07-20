from __future__ import annotations

import sys
import time

import pytest

from lsc.utils.cancellable_ffmpeg import CancellableFFmpeg, FFmpegCancelled


def test_cancel_terminates_child_process() -> None:
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    proc = CancellableFFmpeg(cmd)
    proc.start()
    assert proc.poll() is None
    proc.cancel(timeout_sec=5.0)
    assert proc.poll() is not None
    assert proc.returncode is not None


def test_wait_raises_on_cancel_flag() -> None:
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    cancelled = {"v": False}

    def check() -> bool:
        return cancelled["v"]

    proc = CancellableFFmpeg(cmd, cancel_check=check)
    proc.start()
    cancelled["v"] = True
    with pytest.raises(FFmpegCancelled):
        proc.wait(timeout_sec=5.0)


def test_wait_drains_large_stdout_without_pipe_deadlock() -> None:
    cmd = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'x' * (5 * 1024 * 1024)); sys.stdout.flush()",
    ]
    proc = CancellableFFmpeg(cmd)
    proc.start()

    completed = proc.wait(timeout_sec=3.0)

    assert completed.returncode == 0
    assert len(completed.stdout) == 5 * 1024 * 1024
