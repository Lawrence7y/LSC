---
name: lsc-code-review
description: LSC项目自动化代码审查 - 基于lsc-code-review-standard.md的7步审查流程
---

# LSC 代码审查技能

自动化执行LSC项目的代码审查流程，基于`.claude/projects/d--Project-----/memory/lsc-code-review-standard.md`规范。

## 使用方法

```bash
# 审查指定文件
python -m mimocode skill run code-review --target "path/to/file.cpp"

# 审查最近修改的文件
python -m mimocode skill run code-review --recent

# 审查整个模块
python -m mimocode skill run code-review --module "analyzer"
```

## 审查流程

1. **代码分析** - 读取目标文件，分析代码结构
2. **Blocker检查** - 按照B-01到B-18清单检查关键问题
3. **Suggestion检查** - 按照E-I清单检查改进建议
4. **Nit检查** - 检查命名、风格、文档问题
5. **测试验证** - 运行相关测试确保修改不破坏现有功能
6. **生成报告** - 按照🔴/🟡/💭格式生成审查报告
7. **修复建议** - 提供具体的修复代码和建议

## 输出格式

```markdown
## 代码审查报告

### 🔴 Blocker (必须修复)
- [B-01] 文件:line - 问题描述
  ```cpp
  // 修复代码
  ```

### 🟡 Suggestion (应当修复)
- [E] 文件:line - 问题描述

### 💭 Nit (建议修复)
- 文件:line - 问题描述

### 测试结果
- 通过: X/Y
- 覆盖率: XX%

### 总结
- Blocker: X个
- Suggestion: X个
- Nit: X个
```

## 注意事项

- 审查前确保代码已编译通过
- 对于C++代码，特别注意内存安全和线程安全
- 对于Python代码，特别注意类型注解和异常处理
- 审查报告应具体到文件路径和行号