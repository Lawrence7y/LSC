# LSC项目工作流提炼报告

## 执行摘要

基于过去30天的会话分析，识别出5个重复的工作流，并创建了相应的可重用资产。

## 候选工作流

### 1. 代码审查和修复
- **频率**: 高 (多次代码审查会话)
- **证据**: ses_14adf5754ffe, ses_10fdd9099ffe, ses_116702dd3ffe
- **形式**: 技能 + 代理
- **价值**: 自动化7步审查流程，提高审查效率和一致性

### 2. 产品评估报告
- **频率**: 高 (多次产品评估)
- **证据**: ses_116702dd3ffe, ses_1299482adffe
- **形式**: 技能
- **价值**: 自动化多维度评估，生成结构化报告

### 3. UI主题修复
- **频率**: 中 (多次UI修复)
- **证据**: ses_10fdd914fffe, ses_10fdd9139ffe
- **形式**: 技能
- **价值**: 自动化主题一致性检查和修复

### 4. 项目分析
- **频率**: 中 (多次项目分析)
- **证据**: ses_10ced2985ffe, ses_10d156c43ffe
- **形式**: 技能
- **价值**: 自动化架构和代码质量分析

### 5. 视频下载处理
- **频率**: 中 (多次下载任务)
- **证据**: ses_14adf5df7ffe, ses_14adf56a0ffe
- **形式**: 技能
- **价值**: 自动化多平台视频下载和处理

## 创建的资产

### 技能 (5个)

1. **lsc-code-review** - 代码审查技能
   - 路径: `.mimocode/skills/code-review/SKILL.md`
   - 功能: 基于lsc-code-review-standard.md的自动化审查

2. **lsc-product-review** - 产品评估技能
   - 路径: `.mimocode/skills/product-review/SKILL.md`
   - 功能: 多维度产品评估和报告生成

3. **lsc-theme-fix** - 主题修复技能
   - 路径: `.mimocode/skills/theme-fix/SKILL.md`
   - 功能: 深浅主题一致性检查和修复

4. **lsc-project-analysis** - 项目分析技能
   - 路径: `.mimocode/skills/project-analysis/SKILL.md`
   - 功能: 架构、代码质量、依赖分析

5. **video-download** - 视频下载技能
   - 路径: `.mimocode/skills/video-download/SKILL.md`
   - 功能: 多平台视频下载和处理

### 命令 (1个)

1. **review-all** - 全面审查命令
   - 路径: `.mimocode/commands/review-all.md`
   - 功能: 运行所有审查技能，生成综合报告

### 代理 (1个)

1. **code-reviewer** - 代码审查代理
   - 路径: `.mimocode/agents/code-reviewer.md`
   - 功能: 专业代码审查，生成详细报告

## 跳过的工作流

### 1. Blender渲染
- **原因**: 特定项目工作流，通用性低
- **建议**: 保持手动执行

### 2. C++构建
- **原因**: 已有构建系统处理
- **建议**: 使用现有CMake构建

### 3. 进程检查
- **原因**: 简单的shell命令
- **建议**: 保持手动执行

## 需要更多证据的工作流

### 1. 性能优化
- **状态**: 多次性能优化，但每次优化点不同
- **建议**: 继续观察，可能需要特定优化技能

### 2. 错误处理
- **状态**: 多次错误处理，但错误类型多样
- **建议**: 继续观察，可能需要错误模式库

## 使用建议

### 日常开发
```bash
# 代码提交前审查
python -m mimocode skill run code-review --target "modified_files"

# 定期产品评估
python -m mimocode skill run product-review --quick
```

### 问题修复
```bash
# UI主题问题
python -m mimocode skill run theme-fix --check

# 代码质量问题
python -m mimocode command run review-all
```

### 项目维护
```bash
# 项目健康检查
python -m mimocode skill run project-analysis --full

# 视频下载任务
python -m mimocode skill run video-download --url "xxx"
```

## 后续改进建议

1. **技能集成** - 将技能集成到CI/CD流程
2. **自动化触发** - 基于文件变更自动触发审查
3. **报告合并** - 将多个技能的报告合并为统一仪表板
4. **性能监控** - 添加性能监控和预警
5. **机器学习** - 使用机器学习优化审查规则

## 总结

通过本次工作流提炼，将5个重复的手动工作流转化为可重用的自动化资产，预计可以：

- **提高效率**: 减少重复性工作，专注于创造性任务
- **提高质量**: 标准化审查流程，减少人为错误
- **提高一致性**: 确保每次审查都遵循相同标准
- **降低风险**: 自动化检查关键问题，提前发现潜在风险

所有资产都遵循项目的现有规范和最佳实践，可以立即投入使用。