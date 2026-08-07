# 持续优化模型质量 - 方案 A 实施报告

## 📊 当前状态 (Phase 1: 诊断与数据验证)

### ✅ 已完成的工作

1. **创建端点优化器模块** (`lsc/analyzer/endpoint_optimizer.py`)
   - 实现多信号融合置信度计算
   - 支持 OCR timer@0:00、score_delta、result_screen 等信号
   - 目标：将 endpoint_confidence 从 ~0.3 提升到 >0.9
   
2. **创建诊断工具** (`_diagnose_ocr_availability.py`)
   - 自动扫描日志文件提取 OCR 可用性指标
   - 统计 timer@0:00 频率和 score delta 变化
   - 提供质量评估和建议

### 🔍 诊断结果

**测试对象**: `lsc-electron/source-restart.stdout.log` (前端日志)

**发现的问题**:
- Total frames analyzed: 0
- Frames with OCR: 0 (0%)
- Timer@0:00 count: 0

**结论**: 前端日志不包含后端回合检测的详细输出

### 🎯 下一步行动

#### Path A1: 寻找正确的后端日志
- 检查是否有独立的 Python 进程日志文件
- 查看 `logs/` 目录或自定义日志路径
- 配置后端增加详细日志输出级别

#### Path A2: 直接集成并测试
由于找不到现有日志，建议直接在代码中：
1. 在 `round_detector.py` line 2755-2767 增加调试日志
2. 重新运行一次分析
3. 捕获新的日志进行诊断

#### Path B: 修改确认阈值（快速见效）
如果不依赖 OCR 增强，可以先调整现有的 confirm 阈值：
- 降低 `_is_strong_confidence` 的 cutoff 从 0.8 -> 0.6
- 修改 `grade_round_confirmation` 接受 "start_strong + moderate_end"
- 这样即使 end_confidence 只有 0.4-0.5，也能达到 confirmed 状态

---

## 🚀 推荐方案

鉴于找到现有后端日志的难度，我建议采用 **混合策略**：

### Step 1: 立即生效 - 降低阈值（1-2 小时）
修改 `round_detector.py` 中的阈值，让更多 pending 回合变为 confirmed
- 影响：立即可看到更多切片自动入列
- 风险：可能引入少量误检

### Step 2: 中期优化 - OCR 数据集成（半天）
在 FSM 循环中保存 OCR 历史，并在闭合时使用优化器
- 影响：显著提升 endpoint_confidence
- 风险：需要充分测试确保不破坏现有逻辑

### Step 3: 长期改进 - 视觉模型增强（1-2 周）
如果有资源，可以收集更多 result screen 样本训练模型
- 影响：从根本上解决 detection 问题
- 风险：训练成本高，需要标注数据

---

## 📝 待用户决策的问题

1. **是否先应用阈值调整？** 
   - 这能立即改善用户体验（更多 confirmed 切片）
   
2. **能否找到或生成后端分析日志？**
   - 用于更精确的 OCR 可行性分析
   
3. **预算和资源分配：**
   - 短期优先（阈值调优）vs 长期优先（模型重训练）

请告诉我您希望采取的策略，我将继续实施对应的优化方案！
