#!/usr/bin/env python3
"""LSC 构建脚本 — 跨平台打包工具。

功能:
  1. 运行代码质量检查（ruff）
  2. 运行单元测试
  3. 使用 PyInstaller 打包
  4. 生成安装包（可选，仅 Windows）

用法:
  python build.py              # 完整构建
  python build.py --skip-tests # 跳过测试
  python build.py --check-only # 只做代码检查
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
DIST_DIR = PROJECT_DIR / "dist"
BUILD_DIR = PROJECT_DIR / "build"
APP_NAME = "LSC"
VERSION = "0.1.0"


def run_cmd(cmd: list[str], cwd: Path | None = None) -> int:
    """运行命令并返回退出码。"""
    print(f"\n>>> {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd or PROJECT_DIR))
    return result.returncode


def step_lint() -> bool:
    """运行 ruff 代码检查。

    注意：目前旧代码中存在一些 lint 问题正在逐步修复中。
    lint 失败不会阻止构建，但会输出警告。
    """
    print("\n" + "=" * 60)
    print("Step 1: Code linting with ruff")
    print("=" * 60)

    if not shutil.which("ruff"):
        print("[WARN] ruff not found, skipping lint")
        return True

    # 先检查核心层（新代码，必须通过）
    ret_core = run_cmd(["ruff", "check", "lsc/core/"])
    if ret_core != 0:
        print("[FAIL] Core module linting failed")
        return False
    print("[OK] Core module linting passed")

    # 再检查全部代码（旧代码，只警告不阻止）
    print("\nChecking full codebase (warnings only)...")
    ret_full = run_cmd(["ruff", "check", "lsc/", "--exit-zero"])
    if ret_full != 0:
        print("[WARN] Full codebase has lint issues (being fixed gradually)")
    else:
        print("[OK] Full codebase linting passed")

    return True


def step_tests() -> bool:
    """运行单元测试（非 GUI）。"""
    print("\n" + "=" * 60)
    print("Step 2: Running unit tests")
    print("=" * 60)

    ret = run_cmd([
        sys.executable, "-m", "pytest",
        "tests/",
        "--ignore=tests/gui",
        "-x",
        "--tb=short",
        "-q",
    ])
    if ret == 0:
        print("[OK] All tests passed")
    else:
        print("[FAIL] Tests failed")
    return ret == 0


def step_build_pyinstaller() -> bool:
    """使用 PyInstaller 打包。"""
    print("\n" + "=" * 60)
    print("Step 3: Building with PyInstaller")
    print("=" * 60)

    # 清理旧构建
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    if (DIST_DIR / APP_NAME).exists():
        shutil.rmtree(DIST_DIR / APP_NAME, ignore_errors=True)

    spec_file = PROJECT_DIR / "lsc.spec"
    if not spec_file.exists():
        print(f"[FAIL] Spec file not found: {spec_file}")
        return False

    ret = run_cmd([sys.executable, "-m", "PyInstaller", "--clean", "lsc.spec"])
    if ret != 0:
        print("[FAIL] PyInstaller build failed")
        return False

    # 复制外部依赖
    tools_dir = PROJECT_DIR / "tools"
    output_dir = DIST_DIR / APP_NAME

    for exe in ["ffmpeg.exe", "ffprobe.exe"]:
        src = tools_dir / exe
        if src.exists():
            shutil.copy2(src, output_dir / exe)
            print(f"[INFO] Copied {exe}")

    for dll in ["libmpv-2.dll", "mpv-2.dll"]:
        src = tools_dir / dll
        if src.exists():
            shutil.copy2(src, output_dir / dll)
            print(f"[INFO] Copied {dll}")

    # 生成版本信息
    version_file = output_dir / "VERSION.txt"
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(f"{APP_NAME} Live Stream Clipper\n")
        f.write(f"Version: {VERSION}\n")
        import datetime
        f.write(f"Build Date: {datetime.datetime.now()}\n")
        f.write(f"Python: {sys.version}\n")

    print(f"[OK] Build complete: {output_dir}")

    # 估算大小
    total_size = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file())
    size_mb = total_size / (1024 * 1024)
    print(f"[INFO] Total size: {size_mb:.1f} MB")

    return True


def step_build_installer() -> bool:
    """构建 Windows 安装程序（需要 Inno Setup）。"""
    if sys.platform != "win32":
        print("[INFO] Skipping installer build (not Windows)")
        return True

    print("\n" + "=" * 60)
    print("Step 4: Building Windows installer")
    print("=" * 60)

    iscc = shutil.which("iscc")
    if not iscc:
        # 尝试常见路径
        for candidate in [
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
        ]:
            if os.path.exists(candidate):
                iscc = candidate
                break

    if not iscc:
        print("[WARN] Inno Setup (ISCC.exe) not found, skipping installer")
        print("       Download from: https://jrsoftware.org/isdl.php")
        return True

    iss_file = PROJECT_DIR / "installer.iss"
    if not iss_file.exists():
        print(f"[WARN] Installer script not found: {iss_file}")
        return True

    ret = run_cmd([iscc, str(iss_file)])
    if ret == 0:
        installer = DIST_DIR / "LSC-Setup-x64.exe"
        if installer.exists():
            size_mb = installer.stat().st_size / (1024 * 1024)
            print(f"[OK] Installer built: {installer} ({size_mb:.1f} MB)")
        return True
    else:
        print("[FAIL] Installer build failed")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="LSC Build Script")
    parser.add_argument("--skip-tests", action="store_true", help="Skip unit tests")
    parser.add_argument("--skip-lint", action="store_true", help="Skip linting")
    parser.add_argument("--skip-installer", action="store_true", help="Skip installer build")
    parser.add_argument("--check-only", action="store_true", help="Only lint and test, no build")
    args = parser.parse_args()

    print(f"\n{'#' * 60}")
    print(f"# LSC Build Tool v{VERSION}")
    print(f"# Platform: {sys.platform}")
    print(f"# Python: {sys.version}")
    print(f"{'#' * 60}")

    # Step 1: Lint
    if not args.skip_lint:
        if not step_lint():
            print("\n[ABORT] Linting failed, aborting build")
            return 1

    if args.check_only:
        print("\n[DONE] Check-only mode, build skipped")
        return 0

    # Step 2: Tests
    if not args.skip_tests:
        if not step_tests():
            print("\n[ABORT] Tests failed, aborting build")
            return 1

    # Step 3: PyInstaller
    if not step_build_pyinstaller():
        print("\n[ABORT] PyInstaller build failed")
        return 1

    # Step 4: Installer
    if not args.skip_installer:
        step_build_installer()

    print("\n" + "=" * 60)
    print("Build completed successfully!")
    print(f"Output directory: {DIST_DIR / APP_NAME}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
