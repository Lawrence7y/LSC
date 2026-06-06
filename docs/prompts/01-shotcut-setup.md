# Task 1: 环境搭建 — 编译 Shotcut

## 项目背景

我们要基于 Shotcut 开源视频编辑器构建一个「直播切片大师」软件。

## 任务目标

配置编译环境，成功编译运行 Shotcut。

## 编译方式

**Shotcut 官方使用 MSYS2 + MinGW64 编译**，源码中使用了 POSIX 头文件，MSVC 不支持。

## 执行步骤

### Step 1: 安装 MSYS2

```powershell
winget install MSYS2.MSYS2
```

### Step 2: 安装 MinGW64 工具链

```bash
# 打开 MSYS2 MinGW64 终端
C:/msys64/msys2_shell.cmd -defterm -no-start -mingw64

# 更新系统
pacman -Syu

# 安装工具链
pacman -S --noconfirm mingw-w64-x86_64-toolchain mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja
```

### Step 3: 安装 Shotcut 依赖

```bash
# 在 MSYS2 MinGW64 终端中执行
pacman -S --noconfirm \
  mingw-w64-x86_64-qt6-base \
  mingw-w64-x86_64-qt6-charts \
  mingw-w64-x86_64-qt6-declarative \
  mingw-w64-x86_64-qt6-multimedia \
  mingw-w64-x86_64-qt6-svg \
  mingw-w64-x86_64-qt6-tools \
  mingw-w64-x86_64-qt6-translations \
  mingw-w64-x86_64-qt6-websockets \
  mingw-w64-x86_64-fftw \
  mingw-w64-x86_64-mlt
```

### Step 4: 克隆并编译 Shotcut

```bash
# 克隆 Shotcut
git clone https://github.com/mltframework/shotcut.git shotcut-source
cd shotcut-source

# 创建构建目录
mkdir build-mingw && cd build-mingw

# 配置 CMake
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release

# 编译
cmake --build . --config Release
```

### Step 5: 验证

```bash
# 检查编译输出
ls -la src/shotcut.exe

# 检查依赖
ldd src/shotcut.exe
```

## 预期结果

- Shotcut 编译成功
- 可正常启动主界面

## 注意事项

- **必须使用 MSYS2 MinGW64**，不能用 MSVC
- 如果遇到中文路径问题，将项目复制到英文路径
- 镜像源可能需要更换为清华镜像：`mirrors.tuna.tsinghua.edu.cn`
