@echo off
chcp 65001 >nul
echo ========================================
echo LSC 直播切片系统 - 安装包构建脚本
echo ========================================
echo.

cd /d "%~dp0"

echo [1/5] 检查 Node.js 环境...
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  错误: 未检测到 Node.js，请先安装 Node.js
    pause
    exit /b 1
)
echo ✓ Node.js 已安装

echo.
echo [2/5] 安装依赖...
call npm install
if %errorlevel% neq 0 (
    echo ❌ 错误: npm install 失败
    pause
    exit /b 1
)
echo ✓ 依赖安装完成

echo.
echo [3/5] 编译 TypeScript...
call npx tsc --noEmit
if %errorlevel% neq 0 (
    echo ⚠️  TypeScript 编译有警告，继续构建...
)
echo ✓ TypeScript 检查完成

echo.
echo [4/5] 构建前端资源...
call npx vite build
if %errorlevel% neq 0 (
    echo ❌ 错误: Vite 构建失败
    pause
    exit /b 1
)
echo ✓ 前端构建完成

echo.
echo [5/5] 构建 Electron 安装包...
call npx electron-builder
if %errorlevel% neq 0 (
    echo ❌ 错误: electron-builder 失败
    pause
    exit /b 1
)
echo ✓ 安装包构建完成

echo.
echo ========================================
echo ✅ 构建成功!
echo ========================================
echo.
echo 安装包位置: release\
dir /b release\*.exe 2>nul
echo.
pause
