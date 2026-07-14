# OCR GPU / 硬解加速设计

> **日期：** 2026-07-14  
> **状态：** 已实施（见 [2026-07-14-ocr-gpu-hwaccel.md](../plans/2026-07-14-ocr-gpu-hwaccel.md)）  
> **前置：** 持续分析 CPU 占用过高；用户选定「方案 2：GPU OCR + 硬解抽帧」，并确认默认 `auto` + 设置页露出「OCR 加速」开关。

## Goal

在**不改**检测逻辑、确认门、相位调度的前提下，把持续分析 / 精修路径中的 OCR 推理与抽帧解码尽量从 CPU 挪到 GPU / 硬件解码器，降低边录边分析时的卡顿。

## 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 技术路线 | 方案 2：GPU OCR + FFmpeg 硬解抽帧（非省电关 OCR、非 HUD 模板替换） |
| 默认策略 | `ocr_accel = auto`：启动探针后选实测最快后端，并缓存 |
| 设置 UI | 设置页露出「OCR 加速」开关（Select：自动 / DirectML / CUDA / CPU） |
| DirectML | Windows 优先路径，覆盖 N/A/I 与核显，不强制装 CUDA |
| CUDA | 可选：仅当环境已装 `onnxruntime-gpu` 且探测可用 |
| 硬解抽帧 | OCR 抽帧命令可选 `-hwaccel`；失败自动回退软解 |
| 弱核显 | 探针发现 GPU 更慢则回退 CPU（写入探测缓存） |

## 非目标

- 不改确认门（仍须 OCR 可信起终点才可自动升格）
- 不改相位状态机 / profile 参数语义
- 不引入 Whisper / CLIP GPU 路径
- 不强制用户安装 CUDA Toolkit / cuDNN
- 不做 HUD 模板匹配、录制旁路分析轨（属其它方案）
- 不把硬解用于录制主链路或 MSE 预览（本轮仅 OCR 抽帧）

## 问题背景

持续分析卡顿的主因是 OCR：

1. **推理**：`rapidocr-onnxruntime` 默认 CPU EP，逐帧 ONNX 推理占满 CPU。
2. **抽帧**：`ocr_detector` / `round_detector` 用 FFmpeg 软解 + `crop`/`scale`，与录制、预览 FFmpeg 叠压。

相位调度已在买枪/交战中段降低 OCR 密度，但转场窗与 finalize 仍会密集跑 OCR。

---

## 1. 架构

```
settings.ocr_accel (auto|dml|cuda|cpu)
        │
        ▼
┌───────────────────────┐
│  ocr_accel.resolve()  │  ← 探针缓存（进程内 + 可选磁盘）
│  providers 优先级     │
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐     ┌────────────────────────────┐
│  RapidOCR 单例        │     │  FFmpeg OCR 抽帧           │
│  use_dml / use_cuda   │     │  -hwaccel d3d11va|cuda|…   │
│  失败 → CPU           │     │  失败 → 无 hwaccel 重试    │
└───────────────────────┘     └────────────────────────────┘
            │                              │
            └──────────┬───────────────────┘
                       ▼
              既有 OCR 事件 / 回合边界逻辑（不变）
```

核心改动集中在：

| 模块 | 职责 |
|------|------|
| `lsc/analyzer/ocr_accel.py`（新） | 解析设置、探测 providers、微基准、缓存选择结果 |
| `lsc/analyzer/ocr_detector.py` | `_get_ocr()` 按加速模式构造；抽帧注入 hwaccel |
| `lsc/analyzer/round_detector.py` | 回合 OCR 抽帧同样注入 hwaccel + 回退 |
| `python-backend/persistence` / settings 校验 | 持久化 `ocr_accel`，合法枚举 |
| `lsc-electron` Settings + types | 「OCR 加速」Select |

---

## 2. 设置契约

### 2.1 键与取值

| 键 | 类型 | 默认 | 合法值 |
|----|------|------|--------|
| `ocr_accel` | `str` | `"auto"` | `"auto"` \| `"dml"` \| `"cuda"` \| `"cpu"` |

非法值回退为 `"auto"`，打 WARNING 日志。

### 2.2 设置页文案

- 控件标签：**OCR 加速**
- 选项：
  - `自动（推荐）` → `auto`
  - `DirectML（Windows GPU）` → `dml`
  - `CUDA（NVIDIA）` → `cuda`
  - `仅 CPU` → `cpu`
- 辅助说明（一行 secondary）：「持续分析 OCR 推理加速；自动会探测并选最快后端，弱核显可能回退 CPU。」
- 放置位置：设置页「录制 / 导出」相关卡片附近（与 `shared_ingest`、导出并发同区），避免埋进「关于」。

### 2.3 生效时机

- **保存设置后**：下次新建 OCR 单例前生效；若已有单例，在设置变更时 **销毁并重建** OCR 实例（避免旧 EP 残留）。
- 持续分析进行中切换：允许；下一 tick / 下一次 OCR 调用使用新实例。不中断分析任务。

---

## 3. OCR 加速解析与探针

### 3.1 Provider 可用性

启动或首次 OCR 前探测：

1. `onnxruntime.get_available_providers()`（或 RapidOCR 暴露的等价信息）
2. Windows + build ≥ 18362 → DirectML 候选
3. `CUDAExecutionProvider` 在列表中 → CUDA 候选
4. 始终保留 CPU

### 3.2 `auto` 策略

1. 若磁盘/内存缓存有效（见下）→ 直接用缓存结果。
2. 否则按候选顺序做 **同一张小图** 各跑 1 次暖机 + 1 次计时（检测+识别），选耗时最短者。
3. 候选为空或全部失败 → `cpu`。
4. 将结果写入：
   - 进程内全局缓存
   - 可选：`%APPDATA%/lsc-electron/` 或项目 `data/ocr_accel_probe.json`（含：选择、各后端 ms、onnxruntime 版本、日期；TTL 7 天或 ORT 版本变化失效）

### 3.3 强制模式

- `dml` / `cuda`：仅尝试指定 EP；不可用则 **WARNING + 回退 CPU**（不崩溃）。
- `cpu`：不探测 GPU。

### 3.4 RapidOCR 构造

适配当前依赖 `rapidocr-onnxruntime`：

- 优先使用库支持的 `use_dml` / `use_cuda`（或等价构造参数）。
- 若当前版本参数名不同，在 `ocr_accel.py` 内集中适配，业务侧只调 `create_ocr(mode)`。
- 单例仍由 `_get_ocr()` 持有；`invalidate_ocr()` 供设置变更调用。

### 3.5 依赖说明

- **默认不新增**强制依赖；Windows 上若用户/安装器已有 `onnxruntime-directml` 则可用 DML。
- 文档 / README 注明可选：`pip install onnxruntime-directml`（与默认 `onnxruntime` 冲突时需替换安装，安装说明写清）。
- CUDA：可选 `onnxruntime-gpu`，同样在文档说明，不打进默认 `requirements.txt` 必选。

---

## 4. FFmpeg 硬解抽帧

### 4.1 作用范围

仅以下 OCR 抽帧命令：

- `lsc/analyzer/ocr_detector.py`（kill feed / round marker）
- `lsc/analyzer/round_detector.py`（phase / buy-exit OCR 抽帧）

不改：录制 `StreamCapture`、MSE `MseStreamer`、共享进样、导出。

### 4.2 命令形态

在 `-i` 之前按平台插入（示例）：

```text
ffmpeg -y -loglevel error -hwaccel d3d11va -ss … -t … -i input
  -vf fps=…,crop=…[,scale=…] ,showinfo -q:v … out_%05d.jpg
```

策略：

| `ocr_accel` | 硬解偏好 |
|-------------|---------|
| `auto` | Windows: 先试 `d3d11va`；若探测到 CUDA 编解码可用可记为次选（本轮以 d3d11va 为主） |
| `dml` | 仍用 `d3d11va`（硬解与 DML 推理独立） |
| `cuda` | 优先 `-hwaccel cuda`（若 ffmpeg 不支持则 d3d11va → 软解） |
| `cpu` | **不加** `-hwaccel` |

### 4.3 回退

1. 带 hwaccel 的 `subprocess.run` 非 0 退出 / 超时 / 0 帧 → 同一参数去掉 hwaccel 再跑一次。
2. 打 DEBUG/WARNING，不向上抛致命错误。
3. Growing MP4（仍在录制）若硬解异常增多，依赖现有软解回退即可。

### 4.4 预期

硬解收益通常 **小于** DirectML 推理；`crop/scale` 滤镜常迫使回读 CPU。硬解定位为补充，失败不影响功能。

---

## 5. 可观测性

- 首次选定后端时 INFO：`OCR accel selected: dml (probe: dml=12ms cpu=48ms)`。
- 设置页可选展示只读一行「当前生效：DirectML」（来自 `get_settings` / 或 `get_ocr_accel_status` 轻量接口）；**本轮最低要求**：日志可见；UI 只读状态为可选增强（若成本低则一并做）。
- 压力分级 `resource_monitor` 逻辑不变；GPU 加速不改变 pause 阈值语义。

---

## 6. 测试计划

| 测试 | 断言 |
|------|------|
| `ocr_accel` 非法值 | 回退 `auto` |
| `create_ocr("cpu")` | 不请求 DML/CUDA |
| `auto` 探针缓存命中 | 不重复跑基准（mock） |
| 强制 `dml` 但 provider 不可用 | 回退 CPU，不抛未捕获异常 |
| FFmpeg 命令构建 | `cpu` 无 `-hwaccel`；非 cpu 含 hwaccel；失败路径二次软解 |
| 前端类型 / Settings | Select 四选项写入 `save_settings` |

手工：Windows 独显机开持续分析，任务管理器对比 CPU；设置在 auto/cpu 间切换可重建 OCR。

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 弱核显 DML 更慢 | `auto` 探针 + 缓存回退 CPU |
| `onnxruntime` / `directml` / `gpu` 包冲突 | 文档说明互斥安装；代码容忍仅 CPU |
| RapidOCR API 版本差异 | 集中适配层 + 参数探测 |
| 硬解 + filter 无效或花屏 | 软解回退；不改主录制 |
| 设置热切换泄漏旧 Session | `invalidate_ocr()` 显式释放单例 |

---

## 8. 成功标准

1. 默认 `auto` 在可用 GPU 上选用非 CPU EP（探针证明更快时）。
2. 设置页可切换四档并持久化。
3. OCR 功能结果与加速前一致（边界关键词检测不回归）。
4. GPU 不可用或更慢时自动/强制均可安全落在 CPU，分析不中断。
5. 独显机器上持续分析期间 Python/ORT 的 CPU 占用相对 `cpu` 模式有可感知下降（定性验收即可）。
