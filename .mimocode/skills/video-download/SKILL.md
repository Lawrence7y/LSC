---
name: video-download
description: 视频下载器自动化 - 抖音、B站等平台视频下载和处理
---

# 视频下载技能

自动化处理抖音、B站等平台的视频下载和处理工作流。

## 使用方法

```bash
# 下载单个视频
python -m mimocode skill run video-download --url "https://live.douyin.com/xxx"

# 批量下载
python -m mimocode skill run video-download --batch "urls.txt"

# 下载并处理
python -m mimocode skill run video-download --url "xxx" --process
```

## 支持平台

### 抖音
- 直播间录制
- 主页视频下载
- 单个视频下载
- Cookie认证支持

### B站
- 直播间录制
- 视频下载
- 弹幕下载
- Cookie认证支持

### 其他平台
- 通用流媒体下载
- M3U8流解析
- RTMP流录制

## 下载流程

1. **URL解析** - 解析视频URL，获取视频信息
2. **认证处理** - 处理Cookie认证
3. **流地址获取** - 获取实际流地址
4. **下载执行** - 执行下载任务
5. **文件处理** - 重命名、整理、转换
6. **元数据提取** - 提取视频元数据

## 输出格式

```markdown
## 下载报告

### 下载统计
- 总任务数: X个
- 成功: X个
- 失败: X个
- 进行中: X个

### 下载详情
| 任务 | 平台 | 状态 | 大小 | 耗时 |
|------|------|------|------|------|
| 视频1 | 抖音 | ✅ | 100MB | 2min |
| 视频2 | B站 | ❌ | - | - |

### 文件列表
- /path/to/video1.mp4
- /path/to/video2.mp4

### 错误信息
1. 任务名 - 错误描述 - 建议解决方案
```

## 注意事项

- 确保网络连接正常
- 大文件下载可能需要较长时间
- 遵守平台的使用条款
- 注意存储空间管理
- 敏感信息（Cookie）不要提交到版本控制