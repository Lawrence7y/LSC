@echo off
REM ============================================================
REM LSC 直播切片系统 - Windows 打包脚本
REM ============================================================
REM 功能：
REM   1. 使用 PyInstaller 打包成单目录应用
REM   2. 复制 FFmpeg 和 libmpv 到输出目录（如果存在）
REM   3. 生成版本信息文件
REM ============================================================

setlocal enabledelayedexpansion

set "PROJECT_DIR=%~dp0"
set "DIST_DIR=%PROJECT_DIR%dist"
set "BUILD_DIR=%PROJECT_DIR%build"
set "APP_NAME=LSC"
set "VERSION=0.1.0"

echo ========================================
echo LSC Live Stream Clipper - Build Script
echo Version: %VERSION%
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+.
    exit /b 1
)

REM 检查 PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller...
    pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        exit /b 1
    )
)

REM 清理旧的构建
echo [INFO] Cleaning previous build...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%\%APP_NAME%" rmdir /s /q "%DIST_DIR%\%APP_NAME%"

REM 运行 PyInstaller
echo [INFO] Building with PyInstaller...
cd /d "%PROJECT_DIR%"
pyinstaller --clean lsc.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

REM 复制 FFmpeg（如果存在）
if exist "%PROJECT_DIR%tools\ffmpeg.exe" (
    echo [INFO] Copying FFmpeg...
    copy /y "%PROJECT_DIR%tools\ffmpeg.exe" "%DIST_DIR%\%APP_NAME%\" >nul
)
if exist "%PROJECT_DIR%tools\ffprobe.exe" (
    echo [INFO] Copying FFprobe...
    copy /y "%PROJECT_DIR%tools\ffprobe.exe" "%DIST_DIR%\%APP_NAME%\" >nul
)

REM 复制 libmpv（如果存在）
if exist "%PROJECT_DIR%tools\libmpv-2.dll" (
    echo [INFO] Copying libmpv...
    copy /y "%PROJECT_DIR%tools\libmpv-2.dll" "%DIST_DIR%\%APP_NAME%\" >nul
)
if exist "%PROJECT_DIR%tools\mpv-2.dll" (
    echo [INFO] Copying mpv-2.dll...
    copy /y "%PROJECT_DIR%tools\mpv-2.dll" "%DIST_DIR%\%APP_NAME%\" >nul
)

REM 复制 README 和许可证
if exist "%PROJECT_DIR%README.md" (
    copy /y "%PROJECT_DIR%README.md" "%DIST_DIR%\%APP_NAME%\" >nul
)
if exist "%PROJECT_DIR%LICENSE" (
    copy /y "%PROJECT_DIR%LICENSE" "%DIST_DIR%\%APP_NAME%\" >nul
)

REM 生成版本信息
echo [INFO] Writing version info...
echo LSC Live Stream Clipper > "%DIST_DIR%\%APP_NAME%\VERSION.txt"
echo Version: %VERSION% >> "%DIST_DIR%\%APP_NAME%\VERSION.txt"
echo Build Date: %date% %time% >> "%DIST_DIR%\%APP_NAME%\VERSION.txt"
echo Python: >> "%DIST_DIR%\%APP_NAME%\VERSION.txt"
python --version >> "%DIST_DIR%\%APP_NAME%\VERSION.txt" 2>&1

echo.
echo ========================================
echo Build completed successfully!
echo Output: %DIST_DIR%\%APP_NAME%\
echo ========================================

REM 显示输出目录大小
for /f "delims=" %%D in ('dir /s /a "%DIST_DIR%\%APP_NAME%" 2^>nul ^| find "File(s)"') do (
    echo Total size info: %%D
)

echo.
pause
