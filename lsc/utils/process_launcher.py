"""Cross-platform subprocess launcher.

Abstracts Windows-specific concerns (CREATE_NO_WINDOW flag, DLL directory
cleanup) behind a small API so that callers don't need ``sys.platform``
checks scattered throughout the codebase.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any

_log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# Cache for the cleaned PATH suffix (everything except the FFmpeg directory).
# Keyed by the FFmpeg directory since that's what determines conflict checks.
_clean_path_cache: dict[str, str] = {}


def get_creation_flags() -> int:
    """Return platform-appropriate creation flags for subprocess.

    On Windows, returns CREATE_NO_WINDOW to prevent console popups.
    On other platforms, returns 0.
    """
    if _IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        _log.debug("creation flags: CREATE_NO_WINDOW=0x%08x", flags)
        return flags
    return 0


def clear_dll_directory() -> None:
    """Clear DLL directories added by PySide6/Qt on Windows.

    AddDllDirectory is inherited by child processes and can cause avcodec
    version conflicts when launching FFmpeg. This clears the directory
    so the child process only uses FFmpeg's own DLLs.

    No-op on non-Windows platforms.
    """
    if not _IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except Exception as exc:
        _log.debug("SetDllDirectoryW failed: %s", exc)


_ENV_WHITELIST = frozenset({
    "PATH", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
    "HOME", "SYSTEMROOT", "PATHEXT", "PYTHONUNBUFFERED",
    # PYTHONPATH 已从白名单移除：防止通过环境变量注入恶意 Python 模块路径
    "LSC_LOG_DIR", "LSC_BUNDLED_FFMPEG_DIR", "LSC_LOG_LEVEL",
    # 安全：禁止把平台 Cookie 环境变量传给子进程（进程表/子进程 env 可被同用户读取）
    # 原 _ENV_WHITELIST 中的 LSC_BILIBILI_COOKIES 等已于批次1移除
    # #49: preserve CUDA-related env vars for multi-GPU NVENC selection
    "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER",
    "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES",
    "CUDA_PATH", "CUDA_BIN_PATH",
})


def build_clean_env(ffmpeg_path: str) -> dict[str, str]:
    """Build a clean environment for launching FFmpeg.

    Uses an environment variable whitelist to avoid polluting the subprocess
    with unnecessary parent-process variables. Prioritizes FFmpeg's directory
    in PATH and removes directories containing competing avcodec DLLs.
    """
    ffmpeg_dir = os.path.dirname(os.path.abspath(ffmpeg_path))

    cached_clean_path = _clean_path_cache.get(ffmpeg_dir)
    if cached_clean_path is None:
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        clean_path = []
        for d in path_dirs:
            if d and os.path.isdir(d):
                try:
                    entries = os.listdir(d)
                except OSError:
                    entries = []
                has_avcodec = any(
                    f.startswith("avcodec-") and f.endswith(".dll")
                    for f in entries
                    if os.path.isfile(os.path.join(d, f))
                )
                if has_avcodec and os.path.normcase(d) != os.path.normcase(ffmpeg_dir):
                    _log.debug("Removing conflicting DLL path: %s", d)
                    continue
            clean_path.append(d)
        cached_clean_path = os.pathsep.join(clean_path)
        _clean_path_cache[ffmpeg_dir] = cached_clean_path

    env: dict[str, str] = {}
    for key in _ENV_WHITELIST:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    env["PATH"] = ffmpeg_dir + os.pathsep + cached_clean_path
    return env


def prepare_launch(ffmpeg_path: str) -> tuple[dict[str, str], int, str | None]:
    """Prepare environment, flags, and cwd for launching a subprocess.

    Returns
    -------
    tuple[dict, int, str | None]
        (env, creation_flags, cwd) — pass these to subprocess.Popen.
    """
    env = build_clean_env(ffmpeg_path)
    creation_flags = get_creation_flags()
    clear_dll_directory()
    cwd = os.path.dirname(os.path.abspath(ffmpeg_path)) or None
    _log.debug("prepare_launch: ffmpeg=%s flags=0x%08x cwd=%s", ffmpeg_path, creation_flags, cwd)
    return env, creation_flags, cwd


def hidden_run_kwargs(**extra: object) -> dict[str, Any]:
    """Build kwargs for ``subprocess.run`` / ``Popen`` that hide consoles on Windows.

    Only injects ``creationflags`` when non-zero so POSIX callers stay clean.
    """
    out: dict[str, object] = dict(extra)
    flags = get_creation_flags()
    if flags:
        out.setdefault("creationflags", flags)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        out.setdefault("startupinfo", startupinfo)
    # Windows 中文系统 locale 编码是 GBK：text 模式下 FFmpeg/ffprobe 输出
    # （版本构建信息、中文录制路径）按 UTF-8 解码，否则 _readerthread 抛
    # UnicodeDecodeError 且 communicate() 拿不到输出（2026-09-01 依赖检查
    # 路径 4 次崩溃的根因）。
    if out.get("text") or out.get("universal_newlines"):
        out.setdefault("encoding", "utf-8")
        out.setdefault("errors", "replace")
    return out


# ── 全局派生闸与存活记账（CLAUDE.md §7.4）────────────────────────────
# 事实：全仓 33 个 Popen / create_subprocess 调用点分布在 12 个文件，此前**没有任何
# 总量上界**。已有的信号量取值全是 1 或 2（语义是"把某类操作串行化"），不是"给总量
# 封顶"，且预览/录制/导出/probe 之间的预算互不记账。已记录的三次事故同形：
# 2026-09-15 长预览冻结、2026-09-01 22:56 全房断流、2026-09-11 20:45 —— 都是太多
# FFmpeg 同时启动/同时重连把机器压过临界点。这里是唯一的中央记账点。
#
# 设计边界（刻意为之）：
#   * 名额只覆盖**启动瞬间**，长生命周期进程在 Popen 返回后立即 `note_child` 记账并
#     释放名额 —— 否则一个录制进程会永久占着闸，12 房直接死锁。
#   * 存活数超限只 WARNING 不强杀：杀掉正在录制的进程造成的数据损失比争抢更糟。
#     强与不强的分界放在"还没开始"这一步（拿不到名额就拒绝启动）。
MAX_CONCURRENT_SPAWNS = max(1, int(os.environ.get("LSC_MAX_CONCURRENT_SPAWNS", "2")))
MAX_LIVE_CHILDREN = max(1, int(os.environ.get("LSC_MAX_LIVE_CHILDREN", "48")))

_spawn_gate = threading.Semaphore(MAX_CONCURRENT_SPAWNS)
_children_lock = threading.Lock()
_live_children: dict[int, str] = {}
_inflight_spawns = 0
_rejected_spawn_total = 0
_failopen_total = 0
# 短命令（ffprobe / 抽帧 / OCR 预处理）走 `run_hidden`，**不占名额、只计数**：
# 既要在诊断里看得见"这一场跑了多少短命令"，又不能再给分析热路径加时延。
_transient_lock = threading.Lock()
_transient_spawn_total = 0
_transient_by_category: dict[str, int] = {}


def _count_transient_spawn(category: str) -> None:
    global _transient_spawn_total
    with _transient_lock:
        _transient_spawn_total += 1
        _transient_by_category[category] = _transient_by_category.get(category, 0) + 1


class SpawnBudgetExhausted(TimeoutError):
    """全局派生闸在预算内无名额：调用方应按各自的失败路径处理（不得静默继续启动）。"""


def spawn_slot(
    category: str,
    *,
    timeout_sec: float = 30.0,
    fail_open: bool = False,
) -> _Slot:
    """占一个全局派生名额。用法：``with spawn_slot("preview"): proc = Popen(...)``。

    `run_hidden` 已内置本闸，长生命周期站点需自行包一层并在启动后
    `note_child(proc, category)` / 退出时 `forget_child(proc)`。

    `fail_open=True` 的语义是"**削峰但不否决**"：等不到名额时记 WARNING + 计数后照样放行。
    给录制这类"启动失败 = 用户丢一整场数据"的路径必须用它；
    预览/分析这类可重试的才允许硬拒绝（默认 `False` 抛 `SpawnBudgetExhausted`）。
    """
    return _Slot(category, timeout_sec, fail_open)


class _Slot:
    def __init__(self, category: str, timeout_sec: float, fail_open: bool = False) -> None:
        self._category = category
        self._timeout_sec = timeout_sec
        self._fail_open = fail_open
        self._held = False

    def __enter__(self) -> _Slot:
        global _inflight_spawns, _rejected_spawn_total, _failopen_total
        if not _spawn_gate.acquire(timeout=self._timeout_sec):
            with _children_lock:
                if self._fail_open:
                    _failopen_total += 1
                    _log.warning(
                        "派生闸 %ss 内无名额，放行（category=%s，存活=%s）",
                        self._timeout_sec, self._category, _category_counts(),
                    )
                else:
                    _rejected_spawn_total += 1
                    _log.error(
                        "全局派生闸 %ss 内无名额，拒绝启动（category=%s，存活=%s）",
                        self._timeout_sec, self._category, _category_counts(),
                    )
            if not self._fail_open:
                raise SpawnBudgetExhausted(
                    f"系统资源紧张：{self._timeout_sec}s 内未取得 FFmpeg 派生名额"
                    f"（category={self._category}）"
                )
            return self
        with _children_lock:
            _inflight_spawns += 1
        self._held = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        global _inflight_spawns
        if not self._held:
            return
        self._held = False
        with _children_lock:
            _inflight_spawns -= 1
        _spawn_gate.release()


def note_child(child: Any, category: str) -> Any:
    """登记一个已启动的长生命周期子进程；调用方退出时必须 `forget_child`。"""
    pid = getattr(child, "pid", None)
    if pid is None:
        return child
    with _children_lock:
        _live_children[int(pid)] = category
        total = len(_live_children)
    if total > MAX_LIVE_CHILDREN:
        # 只告警不强杀：见上方设计边界。超限说明有站点忘了 forget_child 或房数失控。
        _log.warning(
            "存活 FFmpeg 进程数 %d 已超过软上限 %d（分类=%s）——"
            "检查是否有派生点未调用 forget_child",
            total, MAX_LIVE_CHILDREN, _category_counts(),
        )
    return child


def forget_child(child: Any) -> None:
    pid = getattr(child, "pid", None)
    if pid is None:
        return
    with _children_lock:
        _live_children.pop(int(pid), None)


def _category_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for category in _live_children.values():
        counts[category] = counts.get(category, 0) + 1
    return counts


def budget_snapshot() -> dict[str, Any]:
    """中央记账点的可观测出口：给 runtime_health / 诊断包用。"""
    with _children_lock:
        with _transient_lock:
            transient_total = _transient_spawn_total
            transient_by_category = dict(_transient_by_category)
        return {
            "live_total": len(_live_children),
            "live_by_category": dict(_category_counts()),
            "live_soft_ceiling": MAX_LIVE_CHILDREN,
            "inflight_spawns": _inflight_spawns,
            "spawn_gate_capacity": MAX_CONCURRENT_SPAWNS,
            "rejected_spawn_total": _rejected_spawn_total,
            "failopen_total": _failopen_total,
            # 短命令不受闸约束，但必须看得见量：分析吞吐异常时这是第一个要看的数
            "transient_spawn_total": transient_total,
            "transient_by_category": transient_by_category,
        }


def run_hidden(cmd: list[str] | tuple[str, ...], /, **kwargs: object) -> subprocess.CompletedProcess:
    """``subprocess.run`` with Windows ``CREATE_NO_WINDOW`` to avoid CMD flash.

    Continuous analysis fires many short FFmpeg/ffprobe calls; without this flag
    each invocation pops a console and steals focus / causes UI stutter.

    **本函数不占派生名额**（只计数、不阻塞）。曾在这里包过一层全局闸，实测是把
    持续分析打回半成品：闸的语义是"名额只覆盖启动瞬间"（见上方设计边界），
    而 ``subprocess.run`` 会**把名额持有到整条命令结束** —— 一次 300s 的抽帧
    就把 2 个名额占死，后面每个短探测各白等 5s 才 fail-open。现场后果：
    `scan_throughput` 0.93 → 0.42、backlog 涨到 ~580s、单场 3 次 270s 扫描超时。
    需要封顶的是长生命周期进程（预览/录制/进样/导出），它们已在各自站点用
    ``spawn_slot`` 只闸启动瞬间；短命令路径一律不拦（与 main 行为一致）。
    """
    spawn_category = str(kwargs.pop("spawn_category", "transient"))
    _count_transient_spawn(spawn_category)
    return subprocess.run(cmd, **hidden_run_kwargs(**kwargs))  # type: ignore[arg-type, call-overload, no-any-return]  # noqa: S603


def kill_process_tree(proc: Any, grace_sec: float = 3.0) -> None:
    """强杀整个进程树——分级优雅停机最后一级的统一入口（CLAUDE.md §11.3）。

    Windows: ``taskkill /T /F``（树杀 + 强制；容忍目标已退出的竞态）。
    POSIX: SIGTERM → 非阻塞轮询 ``grace_sec`` → SIGKILL。
    分级停机（q/terminate → 等待 → 强杀）的最终一级必须走本函数，
    禁止直接 ``proc.kill()``（Windows 上只杀主进程、不杀树）。
    进程已退出时静默返回（PID 竞态安全）。
    """
    pid = getattr(proc, "pid", None)
    if pid is None:
        return
    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
                **hidden_run_kwargs(),
            )
        except Exception as exc:
            _log.debug("taskkill /T /F failed for pid=%s: %s", pid, exc)
        return
    try:
        proc.terminate()
    except Exception as exc:
        _log.debug("terminate failed pid=%s: %s", pid, exc)
    deadline = time.monotonic() + grace_sec
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        proc.kill()
    except Exception as exc:
        _log.debug("kill failed pid=%s: %s", pid, exc)


def set_stream_nonblocking(pipe: Any) -> None:
    """Set a subprocess pipe (stdout/stderr) to non-blocking mode.

    Prevents pipe-buffer deadlock when the reader thread is temporarily
    busy. Uses ``fcntl`` on POSIX; no-op on Windows (threaded readers
    already prevent main-thread blockage there).
    """
    if pipe is None:
        _log.debug("set_stream_nonblocking: pipe is None, skipping")
        return
    try:
        fd = pipe.fileno()
    except (AttributeError, OSError) as exc:
        _log.debug("set_stream_nonblocking: fileno failed: %s", exc)
        return
    if sys.platform == 'win32':
        _log.debug("set_stream_nonblocking: Windows, no-op")
        return
    try:
        import fcntl
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        _log.debug("set_stream_nonblocking: fd=%d set O_NONBLOCK", fd)
    except (OSError, ImportError) as exc:
        _log.debug("set_stream_nonblocking: fcntl failed: %s", exc)


__all__ = [
    "get_creation_flags",
    "clear_dll_directory",
    "build_clean_env",
    "prepare_launch",
    "hidden_run_kwargs",
    "run_hidden",
    "kill_process_tree",
    "set_stream_nonblocking",
]
