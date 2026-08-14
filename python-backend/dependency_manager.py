"""LSC 运行时依赖管理器。

负责在应用安装后检测并下载缺失的依赖：
- Python 包依赖（requirements.txt + requirements-ai.txt）
- FFmpeg / FFprobe 二进制

输出 JSON 进度行到 stdout，供 Electron 主进程解析并推送到前端。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

_log = logging.getLogger("lsc.dependency_manager")


def _hidden_subprocess_kwargs(**extra):
    """独立脚本可用的 Windows 隐藏进程参数（不能依赖项目包导入路径）。"""
    kwargs = dict(extra)
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs.setdefault("startupinfo", startupinfo)
    return kwargs

# ──────────────────────────────────────────────────────────────────────
# 路径常量
# ──────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_REQUIREMENTS_DIR = Path(os.environ.get("LSC_REQUIREMENTS_DIR", _PROJECT_ROOT))
_REQUIREMENTS_PATH = _REQUIREMENTS_DIR / "requirements.txt"
_REQUIREMENTS_AI_PATH = _REQUIREMENTS_DIR / "requirements-ai.txt"

# 运行时资源必须写入用户数据目录；安装目录在 Program Files 时不可写。
_APPDATA_DIR = Path(os.environ.get("APPDATA", _PROJECT_ROOT)) / "lsc-electron"
_RUNTIME_DIR = Path(os.environ.get("LSC_RUNTIME_DIR", _APPDATA_DIR / "runtime"))
_SITE_PACKAGES = Path(os.environ.get("LSC_PYTHON_PACKAGES", _RUNTIME_DIR / "python-packages"))
_FFMPEG_DIR = _RUNTIME_DIR / "ffmpeg"
_FFMPEG_EXE = _FFMPEG_DIR / "ffmpeg.exe"
_FFPROBE_EXE = _FFMPEG_DIR / "ffprobe.exe"
_BUNDLED_FFMPEG_DIR = Path(os.environ.get("LSC_BUNDLED_FFMPEG_DIR", ""))
_INSTALL_LOG_PATH = _APPDATA_DIR / "logs" / "dependency-install.log"

# NSIS 安装阶段直接执行本脚本，不会由 Electron 注入 PYTHONPATH。
# 让嵌入式 Python 与其启动的 pip 子进程都能发现用户目录中的运行时包。
_site_packages_text = str(_SITE_PACKAGES)
if _site_packages_text not in sys.path:
    sys.path.insert(0, _site_packages_text)
_existing_pythonpath = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = os.pathsep.join(
    part for part in (_site_packages_text, _existing_pythonpath) if part
)

# FFmpeg 下载源：GitHub 加速代理回退链（国内直连 GitHub 极慢/易超时）
# ghfast.top → gh-proxy.com → GitHub 直连兜底
_FFMPEG_URLS = [
    "https://ghfast.top/https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "latest/ffmpeg-master-latest-win64-gpl-shared.zip",
    "https://gh-proxy.com/https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "latest/ffmpeg-master-latest-win64-gpl-shared.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "latest/ffmpeg-master-latest-win64-gpl-shared.zip",
]
_FFMPEG_URL = _FFMPEG_URLS[-1]  # 兼容旧引用（单 URL）

# Python 包国内镜像（pypi 官方在国内极慢/易超时）
_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"

# ──────────────────────────────────────────────────────────────────────
# 进度报告
# ──────────────────────────────────────────────────────────────────────


def _emit(event: str, **kwargs: object) -> None:
    """输出一行 JSON 进度事件到 stdout。"""
    payload = {"event": event, "ts": time.time(), **kwargs}
    # 安装器的 nsExec 使用系统控制台编码（例如 GBK）。pip 输出可能包含
    # 该编码不支持的字符，使用 ASCII 转义能保证进度协议永不因编码而中断。
    line = json.dumps(payload, ensure_ascii=True)
    print(line, flush=True)
    try:
        _INSTALL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _INSTALL_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        # 日志记录失败不能妨碍安装流程。
        pass


def _log_unhandled_exception(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
    """将启动级异常写入安装日志，供 NSIS 失败提示后的人工排查。"""
    try:
        _INSTALL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _INSTALL_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write("\n--- 未处理异常 ---\n")
            traceback.print_exception(exc_type, exc, tb, file=handle)
    finally:
        sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _log_unhandled_exception


def _emit_progress(phase: str, current: int, total: int, detail: str = "") -> None:
    """输出进度事件。"""
    _emit(
        "progress",
        phase=phase,
        current=current,
        total=total,
        percent=round(current / total * 100, 1) if total > 0 else 0,
        detail=detail,
    )


def _emit_done(phase: str, success: bool, message: str = "") -> None:
    """输出阶段完成事件。"""
    _emit("done", phase=phase, success=success, message=message)


def _emit_error(phase: str, message: str) -> None:
    """输出错误事件。"""
    _emit("error", phase=phase, message=message)


# ──────────────────────────────────────────────────────────────────────
# Python 依赖检测
# ──────────────────────────────────────────────────────────────────────

# 核心依赖（必须安装）
_CORE_DEPS = [
    ("PySide6", "PySide6"),
    ("numpy", "numpy"),
    ("websockets", "websockets"),
    ("psutil", "psutil"),
]

# AI 依赖（可选，但默认安装）
_AI_DEPS = [
    ("faster_whisper", "faster-whisper"),
    ("torch", "torch"),
    ("open_clip", "open-clip-torch"),
    ("PIL", "Pillow"),
    ("rapidocr_onnxruntime", "rapidocr-onnxruntime"),
    ("cv2", "opencv-python-headless"),
]


def _is_package_importable(module_name: str) -> bool:
    """检测单个包是否可发现，避免启动时真实导入 torch/cv2 等大型模块。"""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def check_python_deps() -> dict[str, bool]:
    """检测所有 Python 依赖的安装状态。"""
    result: dict[str, bool] = {}
    for import_name, _ in _CORE_DEPS + _AI_DEPS:
        result[import_name] = _is_package_importable(import_name)
    result["onnx_accel"] = _has_windows_onnx_accel()
    return result


def check_core_deps_ok() -> bool:
    """核心依赖是否全部就绪。"""
    return all(_is_package_importable(name) for name, _ in _CORE_DEPS)


def check_ai_deps_ok() -> bool:
    """AI 依赖是否全部就绪。"""
    return (
        all(_is_package_importable(name) for name, _ in _AI_DEPS)
        and _has_windows_onnx_accel()
    )


def _has_windows_onnx_accel() -> bool:
    """Windows 必须具备 DirectML/CUDA Provider，避免持续分析静默退回 CPU。"""
    if sys.platform != "win32":
        return True
    try:
        import onnxruntime as ort

        providers = set(ort.get_available_providers())
        return bool({"DmlExecutionProvider", "CUDAExecutionProvider"} & providers)
    except (ImportError, OSError, RuntimeError):
        return False


# ──────────────────────────────────────────────────────────────────────
# Python 依赖安装
# ──────────────────────────────────────────────────────────────────────


def _get_python_exe() -> str:
    """获取当前运行的 Python 解释器路径。"""
    return sys.executable


def _ensure_pip(phase_name: str) -> bool:
    """为嵌入式 Python 安装 pip 到运行时包目录。"""
    try:
        __import__("pip")
        return True
    except ImportError:
        pass

    _SITE_PACKAGES.mkdir(parents=True, exist_ok=True)
    get_pip = Path(tempfile.gettempdir()) / "lsc-get-pip.py"
    _emit("start", phase=phase_name, message="正在初始化 pip 安装工具")
    try:
        # 嵌入式 Python 的证书库不包含部分企业/虚拟机网络的根证书。
        # 使用 PowerShell（Windows 证书库）下载引导脚本，避免 SSL 验证失败。
        get_pip_path = str(get_pip).replace("'", "''")
        download = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    "Invoke-WebRequest -UseBasicParsing "
                    "-Uri 'https://bootstrap.pypa.io/get-pip.py' "
                    f"-OutFile '{get_pip_path}'"
                ),
            ],
            **_hidden_subprocess_kwargs(
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            ),
        )
        if download.returncode != 0:
            raise OSError(download.stderr.strip() or "PowerShell 下载 get-pip.py 失败")
        completed = subprocess.run(
            [sys.executable, str(get_pip), "--target", str(_SITE_PACKAGES)],
            **_hidden_subprocess_kwargs(
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            ),
        )
    except OSError as exc:
        _emit_error(phase_name, f"安装 pip 失败: {exc}")
        return False
    finally:
        try:
            get_pip.unlink(missing_ok=True)
        except OSError:
            _log.debug("无法清理临时 get-pip 文件: %s", get_pip)

    if completed.returncode != 0:
        _emit_error(phase_name, f"安装 pip 失败: {completed.stderr[-500:]}")
        return False
    return True


def _pip_install_requirements(requirements_path: Path, phase_name: str) -> bool:
    """使用 pip 安装 requirements 文件中的依赖。

    通过解析 pip 输出估算进度。
    """
    if not requirements_path.exists():
        _emit_error(phase_name, f"requirements 文件不存在: {requirements_path}")
        return False

    if not _ensure_pip(phase_name):
        return False

    _SITE_PACKAGES.mkdir(parents=True, exist_ok=True)

    python_exe = _get_python_exe()
    # Windows embeddable Python 的 ._pth 配置会忽略 PYTHONPATH，
    # 因此不能使用 ``python -m pip``。显式插入运行时包目录后加载 pip。
    pip_runner = (
        "import sys;"
        f"sys.path.insert(0, {str(_SITE_PACKAGES)!r});"
        "from pip._internal.cli.main import main;"
        "raise SystemExit(main(sys.argv[1:]))"
    )
    cmd = [
        python_exe, "-c", pip_runner, "install",
        "--no-warn-script-location",
        "--use-feature=truststore",
        "--index-url", _PIP_INDEX_URL,
        "--target", str(_SITE_PACKAGES),
        "-r", str(requirements_path),
    ]

    _emit("start", phase=phase_name, message=f"开始安装 {requirements_path.name}")

    try:
        proc = subprocess.Popen(
            cmd,
            **_hidden_subprocess_kwargs(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(_PROJECT_ROOT),
            ),
        )
    except OSError as exc:
        _emit_error(phase_name, f"启动 pip 失败: {exc}")
        return False

    assert proc.stdout is not None
    installed_count = 0
    total_packages = _count_requirements(requirements_path)

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        # 完整保留 pip 输出。安装器环境下的最终错误通常只出现在末尾数行，
        # 不能仅记录下载进度，否则无法诊断退出码。
        _emit("log", phase=phase_name, level="info", message=line)
        # pip 安装成功时会输出 "Successfully installed ..."
        if line.startswith("Successfully installed") or "already satisfied" in line.lower():
            installed_count += 1
            _emit_progress(phase_name, installed_count, max(total_packages, 1), line)

    retcode = proc.wait()
    if retcode != 0:
        _emit_error(phase_name, f"pip install 退出码 {retcode}")
        return False

    _emit_done(phase_name, True, f"{requirements_path.name} 安装完成")
    return True


def _count_requirements(path: Path) -> int:
    """统计 requirements 文件中的包数量（粗略估计）。"""
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("-"):
            count += 1
    return count


def _install_windows_directml(phase_name: str) -> bool:
    """用 DirectML 发行版替换 rapidocr 拉取的 CPU onnxruntime。

    新版 onnxruntime-directml（1.24.x）在部分旧环境 DLL 加载失败（缺系统
    DirectML 组件/VC++ 运行库较旧）→ 回退旧版 1.21.1 再校验；仍失败则
    降级 CPU onnxruntime 并返回 True（分析降速但应用可用，不阻塞安装/启动）。
    """
    if sys.platform != "win32":
        return True
    if not _ensure_pip(phase_name):
        return False

    def _remove_onnxruntime() -> None:
        # 两个发行版都提供 onnxruntime 包，必须先删除旧文件再装新版本，
        # 否则 target 模式会保留旧 DLL，最终仍只有 CPUExecutionProvider。
        for candidate in (
            _SITE_PACKAGES / "onnxruntime",
            *_SITE_PACKAGES.glob("onnxruntime-*.dist-info"),
            *_SITE_PACKAGES.glob("onnxruntime_directml-*.dist-info"),
        ):
            try:
                if candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
                elif candidate.exists():
                    candidate.unlink()
            except OSError:
                _log.warning("无法清理旧 ONNX Runtime: %s", candidate)

    def _probe_dml() -> bool:
        # 当前进程可能缓存过 CPU 模块，用独立解释器校验最终落盘结果。
        probe = (
            "import sys;"
            f"sys.path.insert(0, {str(_SITE_PACKAGES)!r});"
            "import onnxruntime as ort;"
            "p=ort.get_available_providers();"
            "print(p);"
            "raise SystemExit(0 if ('DmlExecutionProvider' in p or 'CUDAExecutionProvider' in p) else 42)"
        )
        verified = subprocess.run(
            [_get_python_exe(), "-c", probe],
            **_hidden_subprocess_kwargs(
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            ),
        )
        if verified.returncode != 0:
            _log.warning("DirectML probe failed: %s", (verified.stdout or verified.stderr)[-300:])
            return False
        _emit_progress(phase_name, 100, 100, f"推理加速已启用: {verified.stdout.strip()}")
        return True

    pip_runner = (
        "import sys;"
        f"sys.path.insert(0, {str(_SITE_PACKAGES)!r});"
        "from pip._internal.cli.main import main;"
        "raise SystemExit(main(sys.argv[1:]))"
    )

    _emit("start", phase=phase_name, message="正在启用 Windows DirectML 分析加速")
    for dml_spec in ("onnxruntime-directml>=1.18,<2", "onnxruntime-directml==1.21.1"):
        _remove_onnxruntime()
        cmd = [
            _get_python_exe(), "-c", pip_runner, "install",
            "--no-warn-script-location",
            "--use-feature=truststore",
            "--index-url", _PIP_INDEX_URL,
            "--target", str(_SITE_PACKAGES),
            "--upgrade",
            "--force-reinstall",
            dml_spec,
        ]
        completed = subprocess.run(
            cmd,
            **_hidden_subprocess_kwargs(
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(_PROJECT_ROOT),
                check=False,
            ),
        )
        if completed.returncode != 0:
            _log.warning("DirectML install failed (%s): %s", dml_spec, (completed.stdout or completed.stderr)[-300:])
            continue
        if _probe_dml():
            return True
        _log.warning("DirectML 校验失败（%s），尝试旧版", dml_spec)

    # 降级 CPU：恢复 rapidocr 可用的 CPU onnxruntime，不阻塞安装/启动
    _log.warning("DirectML 在此环境不可用，降级 CPU onnxruntime（分析将运行在 CPU）")
    _remove_onnxruntime()
    cmd = [
        _get_python_exe(), "-c", pip_runner, "install",
        "--no-warn-script-location",
        "--use-feature=truststore",
        "--index-url", _PIP_INDEX_URL,
        "--target", str(_SITE_PACKAGES),
        "onnxruntime>=1.18,<2",
    ]
    subprocess.run(
        cmd,
        **_hidden_subprocess_kwargs(
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(_PROJECT_ROOT),
            check=False,
        ),
    )
    _emit_progress(phase_name, 100, 100, "DirectML 不可用，已降级 CPU 推理")
    return True


def install_python_deps(include_ai: bool = True) -> bool:
    """安装 Python 依赖。"""
    # 核心依赖
    if check_core_deps_ok():
        _emit_done("python_core", True, "核心依赖已存在，跳过重复安装")
    elif not _pip_install_requirements(_REQUIREMENTS_PATH, "python_core"):
        return False

    # AI 依赖
    if include_ai and _REQUIREMENTS_AI_PATH.exists():
        ai_modules_ready = all(
            _is_package_importable(name)
            for name, _ in _AI_DEPS
        )
        if ai_modules_ready:
            _emit_done("python_ai", True, "AI 基础依赖已存在，仅检查推理加速")
        elif not _pip_install_requirements(_REQUIREMENTS_AI_PATH, "python_ai"):
            return False
        if not _has_windows_onnx_accel() and not _install_windows_directml("python_ai"):
            return False

    return True


# ──────────────────────────────────────────────────────────────────────
# FFmpeg 下载
# ──────────────────────────────────────────────────────────────────────


def check_ffmpeg_ok() -> bool:
    """检测 FFmpeg 是否可用。"""
    # 1. 检查打包内目录
    if _FFMPEG_EXE.exists() and _FFPROBE_EXE.exists():
        return True
    # 2. 检查随单文件安装包携带的离线 FFmpeg
    if _BUNDLED_FFMPEG_DIR and (_BUNDLED_FFMPEG_DIR / "ffmpeg.exe").exists() and (
        _BUNDLED_FFMPEG_DIR / "ffprobe.exe"
    ).exists():
        return True
    # 3. 检查系统 PATH
    return bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))


def download_ffmpeg() -> bool:
    """下载并解压 FFmpeg。"""
    phase = "ffmpeg"

    if check_ffmpeg_ok():
        _emit_done(phase, True, "FFmpeg 已存在，跳过下载")
        return True

    _emit("start", phase=phase, message="开始下载 FFmpeg")

    tmp_dir = Path(tempfile.mkdtemp(prefix="lsc_ffmpeg_"))
    zip_path = tmp_dir / "ffmpeg.zip"

    try:
        # 下载（镜像回退链）
        _emit_progress(phase, 0, 100, "正在下载 FFmpeg...")

        def _progress_hook(block_num: int, block_size: int, total_size: int) -> None:
            if total_size > 0:
                percent = min(block_num * block_size / total_size * 100, 99)
                _emit_progress(phase, int(percent), 100, f"下载中... {percent:.0f}%")

        last_error: Exception | None = None
        for url in _FFMPEG_URLS:
            try:
                urllib.request.urlretrieve(url, zip_path, reporthook=_progress_hook)
                if zip_path.stat().st_size > 1024 * 1024:  # 至少 1MB，过滤错误页
                    break
                last_error = RuntimeError(f"下载内容过小: {zip_path.stat().st_size} bytes")
                zip_path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                _emit_progress(phase, 0, 100, f"镜像不可用，尝试下一个源: {url}")
                try:
                    zip_path.unlink(missing_ok=True)
                except OSError:
                    pass
        else:
            raise RuntimeError(f"全部下载源失败: {last_error}")
        _emit_progress(phase, 99, 100, "下载完成，正在解压...")

        # 解压
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir / "extracted")

        # 查找 bin 目录
        extracted_root = tmp_dir / "extracted"
        bin_dir = None
        for d in extracted_root.rglob("bin"):
            if (d / "ffmpeg.exe").exists():
                bin_dir = d
                break

        if not bin_dir:
            _emit_error(phase, "FFmpeg 压缩包结构异常，未找到 bin/ffmpeg.exe")
            return False

        # 复制到目标目录
        _FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            src = bin_dir / name
            if src.exists():
                shutil.copy2(src, _FFMPEG_DIR / name)

        # 复制 DLL（shared build 需要）
        for dll in bin_dir.glob("*.dll"):
            shutil.copy2(dll, _FFMPEG_DIR / dll.name)

        _emit_progress(phase, 100, 100, "FFmpeg 安装完成")
        _emit_done(phase, True, f"FFmpeg 已安装到 {_FFMPEG_DIR}")
        return True

    except Exception as exc:
        _emit_error(phase, f"FFmpeg 下载失败: {exc}")
        return False
    finally:
        # 清理临时文件
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────


def check_all() -> dict:
    """检测所有依赖状态，返回结构化结果。"""
    py_status = check_python_deps()
    ffmpeg_ok = check_ffmpeg_ok()

    core_ok = all(py_status.get(name, False) for name, _ in _CORE_DEPS)
    ai_ok = (
        all(py_status.get(name, False) for name, _ in _AI_DEPS)
        and py_status.get("onnx_accel", False)
    )

    return {
        "python": py_status,
        "core_ok": core_ok,
        "ai_ok": ai_ok,
        "ffmpeg_ok": ffmpeg_ok,
        "all_ok": core_ok and ai_ok and ffmpeg_ok,
    }


def install_all(include_ai: bool = True) -> bool:
    """安装所有缺失的依赖。"""
    _emit("start", phase="all", message="开始依赖安装流程")

    # 1. Python 核心依赖
    if not install_python_deps(include_ai=include_ai):
        _emit_error("all", "Python 依赖安装失败")
        return False

    # 2. FFmpeg
    if not download_ffmpeg():
        _emit_error("all", "FFmpeg 安装失败")
        return False

    _emit_done("all", True, "所有依赖安装完成")
    return True


# ──────────────────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    """CLI 入口。

    用法:
        python dependency_manager.py check     # 检测依赖状态
        python dependency_manager.py install   # 安装所有依赖
        python dependency_manager.py install --no-ai  # 不安装 AI 依赖
    """
    if len(argv) < 2:
        print("用法: python dependency_manager.py [check|install]", file=sys.stderr)
        return 1

    command = argv[1]

    if command == "check":
        result = check_all()
        _emit("result", **result)
        return 0 if result["all_ok"] else 2

    if command == "install":
        include_ai = "--no-ai" not in argv
        success = install_all(include_ai=include_ai)
        return 0 if success else 1

    _emit_error("cli", f"未知命令: {command}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
