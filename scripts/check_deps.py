#!/usr/bin/env python3
"""一键检测 LSC 开发环境依赖是否齐全。

用法:
    python scripts/check_deps.py

退出码:
    0 - 所有依赖满足
    1 - 有依赖缺失或版本不满足
"""
from __future__ import annotations

import importlib
import shutil
import sys
from dataclasses import dataclass


@dataclass
class DepCheck:
    name: str
    required: bool
    min_version: tuple[int, ...] | None = None
    import_name: str | None = None
    check_cmd: list[str] | None = None


CHECKS = [
    DepCheck("Python 3.10+", required=True, min_version=(3, 10)),
    DepCheck("PySide6", required=True, import_name="PySide6"),
    DepCheck("websockets", required=True, import_name="websockets"),
    DepCheck("numpy", required=True, import_name="numpy"),
    DepCheck("psutil", required=True, import_name="psutil"),
    DepCheck("FFmpeg", required=True, check_cmd=["ffmpeg", "-version"]),
    DepCheck("ffprobe", required=True, check_cmd=["ffprobe", "-version"]),
    DepCheck("Node.js 18+", required=True, check_cmd=["node", "--version"]),
    DepCheck("npm", required=True, check_cmd=["npm" if sys.platform != "win32" else "npm.cmd", "--version"]),
]


def check_python_version(min_version: tuple[int, ...]) -> tuple[bool, str]:
    current = sys.version_info[:2]
    if current >= min_version:
        return True, f"{current[0]}.{current[1]}"
    return False, f"{current[0]}.{current[1]} (需要 >= {'.'.join(map(str, min_version))})"


def check_import(name: str) -> tuple[bool, str]:
    try:
        mod = importlib.import_module(name)
        version = getattr(mod, "__version__", "已安装")
        return True, str(version)
    except ImportError:
        return False, "未安装"


def check_cmd(cmd: list[str]) -> tuple[bool, str]:
    path = shutil.which(cmd[0])
    if path is None:
        return False, "未找到"
    import subprocess
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            first_line = result.stdout.strip().split("\n")[0]
            return True, first_line
        return False, "执行失败"
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    print("=" * 50)
    print("LSC 开发环境依赖检测")
    print("=" * 50)

    all_ok = True
    for check in CHECKS:
        if check.min_version and check.name.startswith("Python"):
            ok, info = check_python_version(check.min_version)
        elif check.import_name:
            ok, info = check_import(check.import_name)
        elif check.check_cmd:
            ok, info = check_cmd(check.check_cmd)
        else:
            ok, info = False, "未知检查类型"

        status = "[OK]" if ok else ("[FAIL]" if check.required else "[WARN]")
        req = "[必需]" if check.required else "[可选]"
        print(f"  {status} {check.name} {req}: {info}")

        if not ok and check.required:
            all_ok = False

    print("=" * 50)
    if all_ok:
        print("[OK] 所有必需依赖已满足，可以开始开发！")
        return 0
    else:
        print("[FAIL] 有必需依赖缺失，请安装后重试。")
        print("\n安装命令:")
        print("  pip install -r requirements.txt")
        print("  cd lsc-electron && npm install")
        return 1


if __name__ == "__main__":
    sys.exit(main())
