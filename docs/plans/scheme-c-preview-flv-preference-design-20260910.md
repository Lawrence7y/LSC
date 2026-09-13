# 方案 C 实施细案与风险评估：预览优先 FLV（2026-09-10）

> 上游文档：`docs/plans/low-latency-preview-architecture-20260910.md`（方案 C）
> 目标：把预览流的协议从 HLS 切到 FLV，降低预览延迟约 2–4s，**同时不改动独立双流架构、不影响 per-room 画质分级**。

---

## 1. 现状分析：协议偏好"已声明但权重不足"

### 1.1 协议偏好确实参与打分，但权重极低

`lsc/platforms/probe.py::score_candidate`：

```python
if capabilities is not None:
    score += max(0, len(capabilities.preferred_protocols)
                    - capabilities.preferred_protocols.index(result.protocol)) * 15.0
```

即偏好列表第 0 位 `+30`、第 1 位 `+15`，**协议间最大差只有 15 分**。

而同一函数里其他项权重量级远大于此：

| 打分项 | 量级 |
|---|---|
| `requested_quality` 命中 | **+250** |
| `first_packet_ms` | 最高 **+100**（`100 − ms/100`） |
| `cdn_health` | **±100** |
| `quality_rank` | 最高 +50 |
| `has_audio` | +40 |
| **`preferred_protocols`** | **最高仅 +30** |

### 1.2 量化验证（已实测）

复刻打分公式，模拟三种网络情形：

| 情形 | 偏好 `(hls, flv)`（快手类） | 偏好 `(flv, hls)`（B站/虎牙类） |
|---|---|---|
| FLV 首包相近（300 vs 250ms） | **HLS 胜**（1152 vs 1168） | FLV 胜（1167 vs 1152） |
| FLV 首包略慢（800 vs 200ms） | **HLS 胜**（1147 vs 1168） | FLV 胜（1162 vs 1153） |
| FLV 首包慢（2000 vs 100ms） | **HLS 胜**（1135 vs 1169） | **HLS 胜**（1150 vs 1154） |

**两条关键结论**：

1. **HLS 优先平台（快手等）预览必然走 HLS**——即使 FLV 首包更快也不会被选中。
2. **即使 FLV 优先平台，FLV 首包稍慢时也会退回 HLS**（15 分差压不住首包分差）。

### 1.3 需要多大权重才能"稳定优先 FLV"

| 额外 bonus | FLV（首包 3000ms） vs HLS（首包 50ms） | 结果 |
|---|---|---|
| 0 | 1125 vs 1170 | HLS |
| 60 | 1185 vs 1170 | **FLV** |
| 100 | 1225 vs 1170 | FLV |
| 120 | 1245 vs 1170 | FLV |

**bonus ≥ 60 即可翻盘**；考虑 `first_packet_ms` 上限 +100 与 `cdn_health` ±100 的联合干扰，**建议取 120**（留安全边际）。

---

## 2. 实施细案

### 2.1 数据流与作用域隔离（关键）

```
预览路径  _handle_mse_preview → resolve_stream_v2(network_context={"prefer_protocol": "flv"})
                                → probe_candidates → select_stream_lease → score_candidate ✅ 加权

录制路径  start_recording     → resolve_stream_v2(不传提示)
                                → ...                                  → score_candidate ⬜ 不加权
```

**必须用 per-request 提示隔离**，理由是录制若从 FLV 切到 HLS 会影响：
- `-c copy` 直拷的容器与稳定性
- 录制链路的失败分类与重连策略（见 `platforms/failure.py`）
- 既有"虎牙 FLV 403"等已调优路径

`ResolveRequest` 已有 `network_context: Mapping[str, object]` 字段，**无需改数据结构**。

### 2.2 三处改动（均向后兼容）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `lsc/platforms/probe.py` `score_candidate` | 增参 `prefer_protocol: str = ""`；命中 `+ _PREFER_PROTOCOL_BONUS`，未命中 `- _PREFER_PROTOCOL_PENALTY` |
| 2 | `lsc/platforms/probe.py` `select_best_candidate` | 透传 `prefer_protocol` |
| 3 | `lsc/platforms/resolver.py` `select_stream_lease` | 从 `ResolveResult`/参数取 `prefer_protocol` 并透传 |

**兜底设计（不做硬过滤）**：

```python
# 只调权重，不剔除候选：
# - FLV 探测失败 → result.ok=False → 早已被 select 跳过（既有逻辑）
# - FLV 源不存在 → 候选表里根本没有 FLV → HLS 自然胜出
# 因此"强制优先"与"HLS 兜底"可同时成立。
```

### 2.3 常量建议

```python
_PREFER_PROTOCOL_BONUS = 120.0    # 压过 first_packet_ms(+100) 与 cdn_health(±100)
_PREFER_PROTOCOL_PENALTY = 60.0   # 轻惩罚，不足以剔除非偏好协议
```

---

## 3. 风险清单（逐项评估）

| # | 风险 | 严重度 | 分析 | 缓解 |
|---|---|---|---|---|
| **R1** | **录制被牵连**，误切 HLS 破坏 `-c copy`/重连 | 🔴 高 | 两路径共用 `resolve_stream_v2` | **per-request 提示**，录制路径不传（§2.1）；契约测试锁定 |
| **R2** | FLV 源不存在 → 无候可选 | 🟡 中 | 部分平台/房间只有 HLS | 只调权重**不硬过滤**；HLS 兜底（§2.2） |
| **R3** | FLV 首包很慢 → 起播变慢 | 🟡 中 | bonus 压过 `first_packet_ms` 后，慢首包 FLV 仍被选中 | 权衡：预览是长连接，**稳态延迟 > 首包时间**；可加"首包超阈值(<3s)才降权"的护栏（见 §5 待定项） |
| **R4** | 各房协议不同 → 房与房之间延迟差异变大 | 🟢 低 | per-room `recording_to_preview_delta` 仍逐房标定，轴换算正确 | 仅观感差异；文档说明 |
| **R5** | FLV CDN 的 Referer/headers 需求不同 → 403 | 🟡 中 | 项目已有 CDN 线路轮换与 403 快速失败 | 沿用同一 `headers_to_ffmpeg_input_args` 注入链路；观察失败分类 |
| **R6** | URL TTL/签名刷新频率变化 | 🟢 低 | FLV 与 HLS 的 `expected_ttl_seconds` 可能不同 | `LeaseManager` 已按能力 TTL 管理，自动适配 |
| **R7** | `MseStreamer` 对 FLV 的处理 | 🟢 低 | `-live_start_index -1` 仅在 `.m3u8` 时插入（已有判断） | 无需改动；冒烟验证 |
| **R8** | 预览画质变化 | 🟢 低 | 预览统一转码（`_PREVIEW_QUALITY_PRESETS`），源协议不影响输出画质 | 无需处理 |
| **R9** | 共享进样模式下的上游协议 | ⚪ 不适用 | 该模式默认关闭且已排除（见上游文档 §2.2） | — |
| **R10** | 探测开销变化 | 🟢 低 | 候选顺序变化，但 `limit_probe_candidates` 已限流（按 CDN 去重取前 N） | 无需处理 |
| **R11** | 既有测试假设被打破 | 🟡 中 | 262 个平台相关测试 | 已跑基线全绿；改动后需重跑 + 新增契约测试 |

---

## 4. 测试计划与已完成验证

### 4.1 已完成（基线）

| 项 | 结果 |
|---|---|
| `test_platform_probe_and_resolver.py` + `test_platform_adapter_contract.py` + `test_platform_credentials_and_resolver_models.py` | **71 passed** |
| `test_platform_acceptance.py` / `test_platform_adapters.py` / `test_platform_lease_manager.py` / `test_platform_recovery_policy.py` / `test_lease_refresh_runtime.py` / `test_huya_ingest_as_probe.py` / `test_platform_failure.py` / `test_platform_flags_and_redaction.py` | **191 passed** |
| 打分公式量化实验（§1.2 / §1.3） | bonus ≥ 60 可翻盘，建议 120 |

### 4.2 实施后需补的测试

1. **打分单测**：`prefer_protocol="flv"` 时，FLV 即使首包慢 3s 仍胜出；FLV 探测失败时 HLS 仍可选（兜底）
2. **契约测试**：预览路径携带 `prefer_protocol="flv"`，录制路径**不携带**（防止 R1 回归）
3. **回归**：§4.1 全部 262 测试重跑
4. **手动验收**：对同一房间分别强制 FLV / HLS，比较首帧时间与稳态延迟；覆盖至少一个 HLS 优先平台（如快手）

---

## 5. 结论与建议

### 可行 ✅

- 协议偏好机制**已存在**，方案 C 是"调权重 + 作用域隔离"，**不是架构改造**
- 改动集中且向后兼容（1 个常量 + 3 处签名透传）
- 兜底天然成立（只调权重、不做硬过滤）
- 262 个既有平台测试提供回归护栏

### 需你拍板的两个点

1. **bonus 取值**：建议 120（可配）。若担心"FLV 首包过慢"，可加护栏：
   `if result.first_packet_ms > 3000: prefer_bonus 减半`——**但会削弱低延迟目标**，倾向不加。
2. **生效范围**：建议**仅预览路径**（录制完全不碰）。若你希望录制也优先 FLV，需单独评估 CDN 403 历史问题（虎牙路径曾专门调优）。

### 建议实施顺序

1. 先加 `_PREFER_PROTOCOL_BONUS` 与 3 处透传（不影响任何现有行为，因为默认 `prefer_protocol=""`）
2. 预览路径接入提示
3. 补打分单测 + 契约测试
4. 重跑 262 回归 + 手动对比验收
