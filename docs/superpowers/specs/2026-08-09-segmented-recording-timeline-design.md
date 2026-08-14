# 分段录制、产物清单与时间轴设计规格

> 状态：提案  
> 日期：2026-08-09  
> 上游规格：[房间级进样监督器设计规格](2026-08-09-ingest-supervisor-design.md)  
> 主要代码范围：lsc/core、python-backend 的录制、切片、OCR、对齐与导出

---

## 1. 目的

将单个长文件录制改造为可恢复的短分段与清单模型，保证断流、换线、进程崩溃或应用退出后，已录内容仍可校验、恢复并进入后处理。

---

## 2. 产物布局

每次录制创建独立 recording_session_id 和目录：

    recording-session/
      manifest.json
      segments/
        000001.partial.mkv
        000001.mkv
        000002.mkv
      diagnostics/
        session-summary.json

文件名只是建议，实现必须确保排序稳定、路径不可越界、不同会话不冲突。

---

## 3. 分段策略

默认策略：

- 容器优先使用 MKV；确认平台和后处理链兼容时可使用 MPEG-TS。
- 目标时长 60 秒。
- 遇到租约切换、时间戳不连续、编码参数变化或上游恢复时立即切段。
- 超过最大字节数或最大时长必须切段。
- 正在写入的文件使用 partial 状态；完成关闭并校验后原子标记为 complete。

不得以 MP4 容器尾部未写入为由丢弃整个录制会话。

---

## 4. 清单模型

manifest.json 至少包含：

- schema_version。
- recording_session_id 和 room_session_id。
- platform_id、canonical_room_id。
- started_at、ended_at。
- requested_quality 与实际质量变化。
- recording_state。
- timeline_origin。
- segments 数组。
- gaps 数组。
- recovery_history。
- aggregate_duration_ms。
- content_offset 兼容信息。

每个 segment 包含：

| 字段 | 说明 |
|---|---|
| sequence | 单调递增序号 |
| generation | 对应进样租约代次 |
| path | 会话目录内相对路径 |
| state | WRITING、COMPLETE、RECOVERED、CORRUPT、MISSING |
| started_at | 墙上时钟开始时间 |
| ended_at | 墙上时钟结束时间 |
| media_start_ms | 媒体时间轴起点 |
| media_end_ms | 媒体时间轴终点 |
| duration_ms | 校验后的时长 |
| size_bytes | 文件大小 |
| codecs | 音视频编码摘要 |
| discontinuity_before | 前方是否存在断点 |
| checksum | 可选完整性摘要 |
| validation | ffprobe 校验摘要 |

清单更新必须使用临时文件加原子替换，不能原地写出半截 JSON。

---

## 5. 时间轴语义

同时维护三个概念：

1. wall_clock：真实发生时间，用于跨断流关联事件。
2. media_time：每段媒体自身时间戳。
3. content_time：对用户可见的逻辑内容时间轴，保留现有 content_offset 语义。

规则：

- 分段切换不能导致 content_time 倒退。
- 真实断流写入 gap，不伪造连续媒体。
- 同一代次内的异常时间戳跳变必须触发切段或修正记录。
- OCR、切片、对齐和导出统一通过 TimelineMapper 转换时间。
- 旧接口需要单一偏移时，由 TimelineMapper 生成兼容视图。

---

## 6. 写入与关闭

RecordingSink 写入流程：

1. 创建 partial 文件并在清单登记 WRITING。
2. 写入媒体数据并周期更新内存进度。
3. 正常切段时 flush、关闭并运行快速校验。
4. 校验通过后重命名为最终文件，更新 COMPLETE。
5. 校验失败时标记 CORRUPT，保留原文件用于诊断，不伪装成功。

应用正常退出必须关闭当前分段。达到关闭 deadline 后允许终止写入，但清单需保留 unclean_shutdown。

---

## 7. 崩溃恢复

启动或打开项目时扫描未完成会话：

- 对 partial 文件运行 ffprobe 和可读性检查。
- 可恢复时关闭或重封装为 RECOVERED 分段。
- 无法恢复时标记 CORRUPT，不自动删除。
- 修复缺失的 duration、轨道和时间戳摘要。
- 根据相邻段与事件日志重建 gap。
- 清单损坏时优先从分段文件和诊断摘要重建新版本，并保留旧文件副本。

恢复过程必须幂等；重复执行不会改变已确认的内容时间轴或重复添加分段。

---

## 8. 后处理消费

所有后处理改为接收 RecordingAsset：

- manifest 路径。
- 可消费分段列表。
- TimelineMapper。
- 缺口与不完整状态。

兼容期内提供逻辑单文件适配：

- 按清单顺序生成 concat 描述或只读虚拟列表。
- 不要求先无损合并全部文件。
- 导出可以按需跨分段读取。
- OCR 和音频对齐需要显式处理 gap 与 discontinuity。

后处理不得通过扫描目录和猜测文件名来确定有效分段。

---

## 9. 校验等级

快速校验：

- 文件存在且大小达到最低阈值。
- ffprobe 可读取容器。
- 必要视频轨存在。
- duration 和时间戳合理。

完整校验：

- 解码首、中、尾采样窗口。
- 音视频轨可解码。
- 时间戳单调性和间隔符合阈值。
- 需要音频的业务确认音轨存在且非持续静音异常。

实时录制只执行快速校验；后台和发布验收执行完整校验。

---

## 10. 存储与清理

- 录制前检查目标目录、剩余空间和写权限。
- 低空间阈值触发告警并阻止新会话，现有会话按策略安全收尾。
- 清理只能处理已确认可删除的会话，不删除 CORRUPT 或恢复中的证据。
- 所有路径必须限定在配置的录制根目录内。
- 临时文件和诊断文件有明确保留周期。

---

## 11. 测试要求

1. 在任意分段写入时强制终止进程，重启后可恢复已写内容。
2. 模拟上游换线、时间戳归零、编码变化和长时间断流。
3. 验证清单原子更新和损坏重建。
4. 验证 TimelineMapper 在跨段、跨 gap 和 content_offset 场景下不倒退。
5. 验证 OCR、切片、音频对齐和导出能消费多分段资产。
6. 验证 partial、corrupt、missing 状态不会被误报为完整录制。
7. 验证磁盘满、权限不足和路径异常时安全停止。
8. 验证旧单文件入口在兼容期内结果一致。

---

## 12. 验收标准

- 任意单次断流或进程崩溃最多损失当前未关闭分段，不损坏已完成分段。
- 每个完成分段均有可读取轨道、时长和时间轴记录。
- 后处理不依赖目录猜测，全部经清单读取。
- 时间轴跨恢复和换线保持可解释、可映射且不倒退。
- 系统不会把只有文件名和大小、但不可解码的产物标记为成功。
