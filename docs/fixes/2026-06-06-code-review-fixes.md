# 代码审查修复总结

**日期**: 2026-06-06
**版本**: v2.2

---

## 修复概述

根据代码审查发现的问题，共修复了 8 个主要问题，涉及 12 个文件的修改和 2 个新文件的创建。

---

## 修复详情

### 1. ✅ 修复竞态条件 - RecordingSession 线程安全

**文件**: `livestream/RecordingSession.h`, `livestream/RecordingSession.cpp`

**问题**:
- `m_analysisRunning` 和 `m_engine->isRunning()` 的检查存在 TOCTOU 竞态
- 定时器回调和引擎完成回调可能并发执行

**修复**:
- 添加 `QMutex m_analysisMutex` 保护共享状态
- 在 `stopRecording()`, `onRealtimeAnalysisTimer()`, `onEngineFinished()` 中使用 `QMutexLocker`
- 实现双重检查锁定模式（double-checked locking）

---

### 2. ✅ 修复内存泄漏 - HighlightEngine 策略所有权

**文件**: `analyzer/HighlightEngine.h`, `analyzer/HighlightEngine.cpp`

**问题**:
- `setStrategy()` 不负责删除旧策略
- `CompositeHighlightStrategy` 内部存储的策略指针可能悬空

**修复**:
- 添加 `m_ownsStrategy` 标志明确所有权
- 添加 `cleanupStrategy()` 方法统一清理
- `setStrategy()` 现在接受 `takeOwnership` 参数（默认 true）
- 析构函数中调用 `cleanupStrategy()` 确保资源释放
- 使用 `deleteLater()` 延迟删除避免信号/槽问题

---

### 3. ✅ 修复阻塞 UI - BeatDetector 构造函数异步化

**文件**: `analyzer/BeatDetector.h`, `analyzer/BeatDetector.cpp`

**问题**:
- 构造函数中调用 `waitForFinished()` 阻塞 UI 线程
- 可能导致界面卡顿

**修复**:
- 将 aubio 检测改为异步模式
- 添加 `m_probeProcess` 专门用于检测
- 添加 `m_aubioChecked` 和 `m_probeRunning` 状态标志
- `detect()` 方法在 aubio 检测未完成时等待检测完成
- 使用 `Qt::SingleShotConnection` 确保回调只执行一次

---

### 4. ✅ 修复错误处理 - DialogStrategy 一致性

**文件**: `analyzer/DialogStrategy.h`, `analyzer/DialogStrategy.cpp`

**问题**:
- 语音识别失败时仍然尝试计算对话片段
- 错误处理逻辑不一致

**修复**:
- 添加 `m_speechError` 和 `m_silenceError` 错误标志
- 分离 `onSpeechError()` 和 `onSilenceError()` 槽函数
- 在 `computeDialogSegments()` 中检查错误状态
- 语音识别完全失败时报告错误并返回
- 静音检测失败时仍可继续（降级处理）

---

### 5. ✅ 提取重复代码 - 创建 HighlightUtils 公共工具类

**新文件**: `analyzer/HighlightUtils.h`, `analyzer/HighlightUtils.cpp`

**问题**:
- `overlapRatio()` 和 `mergeKeywords()` 在 3 个文件中重复定义
- 维护困难

**修复**:
- 创建 `HighlightUtils` 命名空间
- 提供以下工具函数：
  - `overlapRatio()` - 计算片段重叠比率
  - `mergeKeywords()` - 合并关键词列表（去重）
  - `mergeSegmentInto()` - 合并片段属性
  - `shouldMergeSegments()` - 判断是否应合并
  - `normalizeSegments()` - 归一化片段列表
  - `deduplicateSegments()` - 去重片段列表
- 更新 `HighlightEngine`, `AnalysisDock`, `CompositeHighlightStrategy` 使用新工具类

---

### 6. ✅ 修复不安全的 const_cast - StreamCapture

**文件**: `livestream/StreamCapture.h`, `livestream/StreamCapture.cpp`

**问题**:
- `encoderArgsForTest()` 使用 `const_cast` 破坏 const 语义
- 临时修改成员变量再恢复是脆弱的模式

**修复**:
- 将 `buildEncoderArgs()` 改为 const 方法
- 添加静态方法 `buildEncoderArgsStatic()` 用于测试
- 静态方法不依赖实例状态，更安全

---

### 7. ✅ 改进资源清理 - AudioAnalyzer cancel

**文件**: `analyzer/AudioAnalyzer.cpp`

**问题**:
- `cancel()` 直接 `kill()` 进程，不够优雅
- 缺少日志记录

**修复**:
- 先尝试 `terminate()` 优雅退出
- 等待 1 秒后才 `kill()` 强制结束
- 添加日志记录进程状态
- 添加 `LscLog.h` 头文件

---

### 8. ✅ 统一硬编码值 - 使用 LscConfig 配置

**文件**: `LscConfig.h`, `analyzer/HighlightDetector.cpp`, `analyzer/HighlightUtils.h`

**问题**:
- 多处硬编码权重和阈值
- 与 `LscConfig` 中的配置不一致

**修复**:
在 `LscConfig.h` 中添加新配置项：
- `weightAudioDetector`, `weightVideoDetector`, `weightSpeechDetector` - HighlightDetector 权重
- `highlightMinScore` - 最低分数阈值
- `motionNormalizationBase` - 运动归一化基准
- `highlightOverlapThreshold` - 重叠阈值
- `highlightAdjacentGapSec` - 相邻合并间隔

更新 `HighlightDetector.cpp` 使用配置值。

---

## 新增文件

| 文件 | 说明 |
|------|------|
| `analyzer/HighlightUtils.h` | 公共工具函数头文件 |
| `analyzer/HighlightUtils.cpp` | 公共工具函数实现 |

---

## 修改文件清单

| 文件 | 修改类型 |
|------|----------|
| `livestream/RecordingSession.h` | 添加 QMutex |
| `livestream/RecordingSession.cpp` | 修复竞态条件 |
| `analyzer/HighlightEngine.h` | 添加策略所有权管理 |
| `analyzer/HighlightEngine.cpp` | 实现策略清理逻辑 |
| `analyzer/BeatDetector.h` | 异步化 aubio 检测 |
| `analyzer/BeatDetector.cpp` | 实现异步检测 |
| `analyzer/DialogStrategy.h` | 添加错误状态标志 |
| `analyzer/DialogStrategy.cpp` | 改进错误处理 |
| `analyzer/HighlightDetector.cpp` | 使用配置值 |
| `analyzer/AudioAnalyzer.cpp` | 改进进程清理 |
| `analyzer/CompositeHighlightStrategy.cpp` | 使用 HighlightUtils |
| `docks/AnalysisDock.cpp` | 使用 HighlightUtils |
| `livestream/StreamCapture.h` | 重构 encoderArgsForTest |
| `livestream/StreamCapture.cpp` | 实现静态方法 |
| `LscConfig.h` | 添加新配置项 |
| `CMakeLists.txt` | 添加新文件 |

---

## 测试建议

1. **并发测试**: 多次启停录制，验证无死锁
2. **内存测试**: 使用 Valgrind 或 AddressSanitizer 检查内存泄漏
3. **UI 响应测试**: 在分析过程中操作 UI，验证无卡顿
4. **错误恢复测试**: 模拟 whisper 失败，验证降级处理
5. **长时间录制测试**: 录制 1 小时以上，验证资源释放

---

## 后续改进建议

1. 为 `HighlightUtils` 添加单元测试
2. 考虑使用 `std::unique_ptr` 替代原始指针
3. 添加配置文件持久化支持
4. 实现更完善的日志系统（文件输出、日志级别）
