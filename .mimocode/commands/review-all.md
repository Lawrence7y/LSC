---
name: review-all
description: 运行所有LSC项目审查和分析技能
agent: main
---

# 全面审查命令

运行所有LSC项目的审查和分析技能，生成综合报告。

## 使用方法

```bash
# 运行所有审查
python -m mimocode command run review-all

# 只运行特定审查
python -m mimocode command run review-all --skills "code-review,product-review"
```

## 执行步骤

1. **代码审查** - 运行lsc-code-review技能
2. **产品评估** - 运行lsc-product-review技能
3. **主题检查** - 运行lsc-theme-fix技能
4. **项目分析** - 运行lsc-project-analysis技能
5. **生成综合报告** - 合并所有报告

## 输出格式

```markdown
## LSC项目综合审查报告

### 执行摘要
- 审查时间: YYYY-MM-DD
- 审查范围: 代码/产品/主题/架构
- 总体评分: X/10
- 关键问题: X个

### 代码审查结果
- Blocker: X个
- Suggestion: X个
- Nit: X个

### 产品评估结果
- 功能完整性: X/10
- 用户体验: X/10
- 性能表现: X/10

### 主题检查结果
- 深色主题: ✅/❌
- 浅色主题: ✅/❌
- 主题切换: ✅/❌

### 项目分析结果
- 代码质量: X/10
- 测试覆盖: XX%
- 架构设计: X/10

### 优先修复清单
#### P0 (必须修复)
1. 问题描述 - 影响 - 建议

#### P1 (应当修复)
1. 问题描述

#### P2 (可以改进)
1. 问题描述

### 改进建议
1. 短期改进 (1周内)
2. 中期改进 (1个月内)
3. 长期改进 (3个月内)

### 风险提示
- 技术债务: X/10
- 维护成本: X/10
- 扩展性: X/10
```

## 注意事项

- 确保项目可以正常编译和运行
- 审查前运行测试确保现有功能正常
- 记录具体的问题位置和修复建议
- 优先处理高影响问题
- 考虑项目的实际使用场景