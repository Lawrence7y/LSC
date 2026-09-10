@echo off
rem ============================================================
rem  LSC 直播切片系统 — 纯 Python 后端启动脚本
rem
rem  启动内容：WebSocket 服务 + RoomOrchestrator 编排线程（端口 9876）
rem  ⚠️ 这不是完整应用。要启动 Electron 桌面端（含前端界面）请用：
rem         cd lsc-electron && npm run dev
rem
rem  修复背景（2026-09-10）：原脚本执行的是 <仓库根>\main.py，但根目录
rem  并无 main.py，真实入口是 python-backend\main.py，故原脚本必然失败。
rem  同时原脚本硬编码绝对路径，已改为相对脚本自身的路径。
rem ============================================================
setlocal
cd /d "%~dp0python-backend"

if not exist "main.py" (
    echo [错误] 未找到 %CD%\main.py
    pause
    exit /b 1
)

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python main.py
    goto :done
)

where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py -3 main.py
    goto :done
)

echo [错误] 未在 PATH 中找到 python 或 py，请先安装 Python 3.10+ 并加入 PATH。
pause
exit /b 1

:done
pause
