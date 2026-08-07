# 代码开发规范（LSC）

> **权威参考**：本文档约束本项目 Python / TypeScript 开发行为。工具配置见 `pyproject.toml`、`pytest.ini`、`.pre-commit-config.yaml`、`lsc-electron/tsconfig*.json`、`.github/workflows/`。架构与业务约束另见根目录 `CLAUDE.md`。

---

## 1. 工具链与命令

### 1.1 Python（要求 `>=3.10`，`pyproject.toml:5`）

| 工具 | 命令 | 说明 |
| :--- | :--- | :--- |
| lint | `ruff check lsc/ python-backend/` | target `py310`，line-length 100，规则集 `E,F,W,I,UP,B,SIM,T20`（`pyproject.toml:30-44`） |
| format 检查 | `ruff format --check lsc/core/ lsc/analyzer/ python-backend/handlers/` | 强制双引号、4 空格（core-guard CI 用） |
| 类型检查 | `mypy`（根目录运行，即 `lsc/ python-backend/`） | `strict=false` 但 `disallow_untyped_defs=true`、`disallow_incomplete_defs=true`、`warn_return_any=true`；`follow_imports=silent`（`pyproject.toml:73-96`） |
| 测试 | `pytest -v` | `testpaths=tests`；覆盖率 `pytest -v --cov=lsc --cov-report=term` |

CI（`ci.yml`）另加：`--cov-report=xml`；`QT_QPA_PLATFORM=offscreen`；测试矩阵 Python 3.10/3.11/3.12 × Ubuntu/Windows。

### 1.2 TypeScript（严格模式）

| 命令 | 说明 |
| :--- | :--- |
| `npx tsc --noEmit` | 渲染进程全量类型检查（`strict: true`, `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch`） |
| `npx tsc --noEmit --project tsconfig.electron.json` | Electron 主进程专项检查 |
| `npm test` | vitest（`globals: true`，happy-dom，include `src/**/*.{test,spec}.{ts,tsx}`） |
| `npm run test:coverage` | vitest + v8 coverage |

### 1.3 提交前必跑

```bash
ruff check lsc/ python-backend/
mypy
pytest -v
npx tsc --noEmit
npm test
```

PR 模板强制列出上述测试结果；CI 在 push/PR 时全量执行（`.github/pull_request_template.md`、`ci.yml`）。

---

## 2. Python 代码约定

### 2.1 领域模型

- 所有 DTO 使用 `@dataclass(slots=True)`，只含数据与序列化方法（`lsc/core/models.py:32,40,62`）。
- 枚举使用 `class X(str, Enum)`，**值为小写字符串**（如 `RecordingStatus.IDLE = "idle"`，`models.py:20-29`）。

### 2.2 协议与插件模式

- 可扩展点使用 **`typing.Protocol` + 注册中心（Registry）**（`lsc/platforms/base.py:310-332`）。
- 需要共享默认实现时提供 `abc.ABC` 基类（如 `BasePlatformAdapter`）。

### 2.3 无状态约束（重要）

> **PlatformAdapter 实例必须无状态**：`parse()` 不得修改实例属性、不得依赖跨调用共享可变状态；所有解析上下文用局部变量并填充到返回值。多线程并发解析依赖此约束（`base.py:313-323`）。

### 2.4 命名

| 类别 | 规则 | 示例 |
| :--- | :--- | :--- |
| 模块级常量 | 全大写 | `MAX_CONCURRENT_PREVIEWS` |
| 模块私有 | `_` 前缀 | `_MIN_FREE_BYTES_WHILE_RECORDING` |
| 日志器 | 模块级 `_log = logging.getLogger(__name__)` | 全项目统一 |
| 类型标注 | 内置泛型 + PEP 604 | `dict[str, str] \| None` |

### 2.5 import 规范（硬性）

- **标准库 import 一律置于文件顶部**，禁止函数内局部 import（曾导致 P0 BUG，`CLAUDE.md:767`）。
- 例外：重量级库（`numpy` 等）可延迟导入，但必须注释原因（如 `room_handler.py:4343`）。

### 2.6 防御性设计（安全防线，不可删）

- 网络请求统一走 `lsc/platforms/base.py` 的 `fetch_url/fetch_json/fetch_head`（12s 超时、2 次重试、`_is_private_ip` + 重定向校验防 SSRF）。
- 路径安全：Electron 主进程 `openPath`/`showItemInFolder` 必须过 `_isSafePath`（白名单 + 可执行后缀黑名单 `.exe/.bat/.ps1/.cmd/.vbs/.scr`，`CLAUDE.md:719-726`）。
- 子进程环境变量白名单透传（`CLAUDE.md:730-731`）。
- 强杀 FFmpeg 统一入口 `lsc/utils/process_launcher.py: kill_process_tree()`，**禁止 `proc.kill()`**（`CLAUDE.md:743`）。

---

## 3. 错误处理规范（`CLAUDE.md:745-800`，2026-07-05 确立）

1. **禁止静默吞异常**：`except Exception: pass` 仅限资源清理路径，且必须 `_log.debug`。
2. **禁止 `assert` 做运行时校验**（`-O` 下失效）：用显式 `if + raise ValueError` 或返回错误结果（测试中的 assert 允许，`S101` 已对 `tests/*` 放行）。
3. **异常分类捕获**：优先具体类型（`HTTPError`/`OSError`/`JSONDecodeError`），避免过宽 `except Exception`。
4. **日志级别**：

   | 级别 | 适用 |
   | :--- | :--- |
   | DEBUG | 高频消息、清理路径异常、可忽略失败 |
   | INFO | 业务关键节点、重连尝试 |
   | WARNING | 可恢复异常、配置回退、降级操作 |
   | ERROR | 不可恢复异常（需 traceback 时 `exc_info=True`） |

5. **WebSocket handler**：可预见错误主动 try/except 并 `humanize_error()` 转中文友好提示；未捕获异常由 `server.py` 自动回 `{success:false, error}`。
6. **错误友好化**：新正则必须**中英文错误都覆盖**（`lsc/utils/error_messages.py`）。
7. **重连控制流**：MSE 重连用 while 循环（非递归），`_MSE_MAX_RECONNECT=3`，退避 2s→4s→8s。

---

## 4. TypeScript 代码约定

- **具名导出纯函数**，工具函数集中于 `src/utils/`。
- **类型导入用 `import type`**；多参数函数用对象参数（见 `timelineCoords.ts`）。
- **字符串字面量联合类型**优于枚举（`'ready' | 'local' | 'invalidated'`）。
- **Zustand 全局状态**集中在 `src/store/appStore.ts`。
- **三套时间轴坐标系禁止混用**（`preview_local` / `common` / `recording_local`，见 `CLAUDE.md:511-522`）：`ControlBar`/`timelineView` 计算 `windowStart` 时 `elapsed` 只允许与播放头同一轴。
- **MSE 契约**：`broadcast_mse(kind,...)` 的 kind 只用 `init`/`segment`；`preview_phase` 等事件态须同时镜像到 `uiState` 与 `RoomSession`（`CLAUDE.md:372`）。
- **快捷键约束**：焦点在 `input/textarea/select` 时自动拦截；未声明 Ctrl 的快捷键（`i`/`o`/`m`/`f`/空格）必须在 Ctrl/Cmd 组合下失效（`CLAUDE.md:814-815`）。
- **Ant Design 样式覆盖**：`global.css` 中 `.ant-card` 等组件样式使用 `!important` 强制约束，勿依赖运行时注入优先级（`CLAUDE.md:284-285`）。

---

## 5. 测试规范

### 5.1 Python（pytest）

- 测试位于 `tests/`（`pytest.ini: testpaths=tests`），文件命名 `test_*.py`。
- `tests/conftest.py` 自动注入 `QT_QPA_PLATFORM=offscreen` 并把仓库根与 `python-backend/` 加入 `sys.path`。
- **硬件/外部依赖 mock 约定**：
  - 用 `monkeypatch.setattr(module, "attr", fake)` 依赖注入；
  - 重量级调用打桩到函数级，如 `@patch("lsc.editor.audio_aligner.extract_audio_pcm")`；
  - 环境开关用 `monkeypatch.setenv("LSC_WS_TOKEN", ...)`；
  - 测试不得依赖宿主机 FFmpeg/GPU：`sample_config` fixture 故意用不存在的 ffmpeg/ffprobe 路径使 `capture.start()` 确定性失败（`tests/conftest.py`）。
- CI 强制：`pytest -v --cov=lsc --cov-report=xml --cov-report=term`。
- 关键覆盖域（新增功能需补对应测试）：平台适配器、录制/导出、MSE 分片、音频对齐、WS 鉴权与调度、共享进样、持续分析（valorant）、剪映草稿、持久化、错误友好化。

### 5.2 TypeScript（vitest）

- 测试文件与被测文件同目录：`src/**/*.test.ts(x)`。
- 现有覆盖：`appStore`、`websocket`（断连入队/重连）、`analysisProgress`、`Workbench`、`ClipList`、`Timeline`。

### 5.3 断言与用例语言

- 测试断言允许 `assert`（ruff `S101` 对 tests 放行）。
- 测试用例描述用中文（前后端一致）。

---

## 6. Git 约定

### 6.1 提交信息格式

```
<type>(<scope>): <中文描述>
```

- type：`feat / fix / style / refactor / docs / test / chore`
- fix 的 scope 带优先级：`fix(P0):` / `fix(P1):` / `fix(P2):`
- 类型专项：`fix(mypy):`、`style:`（ruff 批量修复）等

示例（来自 git log）：`fix(P1): security hardening, error handling, resource cleanup`、`style: fix all 25 ruff lint errors (SIM102/SIM103/SIM112)`。

### 6.2 提交前钩子（`.pre-commit-config.yaml`）

- ruff `--fix --exit-non-zero-on-fix` + ruff-format（仅 `^(lsc|python-backend)/.*\.py$`）
- trailing-whitespace / end-of-file-fixer / check-yaml / check-json
- check-added-large-files（**500KB 上限**）
- debug-statements（禁止调试残留） / check-merge-conflict

### 6.3 分支与审查

- 核心路径 `lsc/core/**`、`lsc/analyzer/**`、`python-backend/handlers/**`、`lsc-electron/electron/**` 由 `CODEOWNERS` 指定 `@project-core` 审查；改动自动触发 core-guard CI（分路径 ruff + format 检查 + electron tsc）。
- PR 必须填写模板测试清单。
- CI 触发：push/PR 到 `main`/`master`；`v*` tag 触发发布。

---

## 7. 文档级约束（无工具强制，靠审查）

以下规范在 `CLAUDE.md` 中有明确表述但当前无 lint/CI 约束，**代码评审时必须人工检查**：

| # | 约束 | 出处 |
| :--- | :--- | :--- |
| 1 | 无状态 PlatformAdapter（并发解析安全） | `CLAUDE.md:163-164` |
| 2 | 同步调用超时=「结果未知」，禁止盲目重试 | `CLAUDE.md:85` |
| 3 | Semaphore 热更新禁区：禁止读 `_waiters` | `CLAUDE.md:219` |
| 4 | 录制文件三层验证（路径/大小>0.1MB/格式签名） | `CLAUDE.md:202-210` |
| 5 | 音频对齐参数规范（16kHz mono float32、3s 窗、0.3 阈值） | `CLAUDE.md:248-260` |
| 6 | 磁盘满防线：剩余 <2GB 强制停录 | `CLAUDE.md:200` |
| 7 | 单房 controller 崩溃不得终止 Orchestrator 线程 | `CLAUDE.md:373` |
| 8 | 广播 kind 前缀契约、rooms_updated 整表替换保留事件态 | `CLAUDE.md:372` |

---

## 8. 禁止事项清单（红线）

1. 运行时 `assert` 校验、`except: pass`、函数内标准库局部 import。
2. `proc.kill()` 直接杀 FFmpeg；绕过 `kill_process_tree()`。
3. WebSocket token 放进 URL；移除 Origin 白名单。
4. 前端把 `record_started_at` 墙钟差直接参与 `windowStart` 计算。
5. 导出直接使用 `mark_in/mark_out`（预览轴 currentTime）作为 `-ss` 参数（必须走墙钟映射）。
6. 在 `mse_` 前缀之外自行拼接广播 kind。
7. 新增 `settings.json` 键/协议消息而不同步前端类型（`WSPayloadMap`）与本文档。
