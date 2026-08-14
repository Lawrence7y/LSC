# Huya Ingest-as-Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先止住虎牙 V2 签名重复消费造成的 FFmpeg 403/EOF 重启风暴，再让单连接签名平台按「上游即探测」工作：同一 `wsSecret` 在一次租约内只被真实上游打开一次。

**Architecture:** 阶段 0 用代码硬闸把虎牙踢出 V2 并关闭自动预览重连。阶段 1 增加 `probe_profile="ingest"`：解析后不打远端 Probe，共享上游首个 TS 即探测成功；403 作废签名族；录制/预览用独立有界队列；启动停止用 generation CAS。阶段 2 在自动化全绿后才解除硬闸。

**Tech Stack:** Python 3.10+、pytest、现有 `lsc/platforms` V2 模型、`SharedRoomIngest`、`IngestSupervisor`、Electron 运行时 `%APPDATA%/lsc-electron/lsc_config.json`。

**Spec:** [2026-08-13-huya-ingest-as-probe-design.md](../specs/2026-08-13-huya-ingest-as-probe-design.md)

---

## File map

| File | Responsibility |
|------|----------------|
| `lsc/platforms/models.py` | `preview_auto_reconnect`、`signature_family_id`、lease `consumed` |
| `lsc/platforms/capabilities.py` | 虎牙能力；`uses_ingest_probe()` |
| `lsc/config.py` | `_V2_PLATFORM_HARD_BLOCKLIST`；录制队列字节 |
| `lsc/platforms/signature_family.py` | **新建**：从 URL 提取签名族 id |
| `lsc/platforms/huya.py` | CDN 隔离仅线路故障；不再把 403 当换线 |
| `lsc/platforms/resolver.py` | 候选写入 family id；`select_ingest_lease()`；跳过远端 Probe |
| `lsc/platforms/recovery_policy.py` | 族失效 vs CDN 隔离；删除虎牙 SIGNATURE→CDN 改写 |
| `lsc/platforms/lease_manager.py` | `mark_consumed` / `is_consumed` |
| `lsc/core/orchestrator.py` | ingest 选路；禁止元数据 ffprobe 打 CDN |
| `lsc/core/services/shared_ingest.py` | `media_ready`、录制队列写线程、generation |
| `lsc/core/services/ingest_supervisor.py` | 恢复动作分派时尊重 family vs sink |
| `lsc/recorder/capture.py` | stop generation CAS |
| `python-backend/handlers/room_handler.py` | 自动重连门禁、禁止录制中强刷、重试预算 |
| `lsc/platforms/acceptance.py` | ingest 平台跳过远端 Probe |
| `tests/test_huya_ingest_as_probe.py` | **新建**：本规格主回归 |
| `tests/test_platform_recovery_policy.py` | 更新虎牙 refresh/recovery 期望 |
| `tests/test_platform_flags_and_redaction.py` | 硬闸：allowlist+shared_ingest 仍拒绝虎牙 |
| `tests/test_shared_ingest.py` | 无订阅者不得 `ok`；录制队列不堵上游 |
| `tests/test_recorder.py` | stop 不得关闭新 generation |
| `%APPDATA%/lsc-electron/lsc_config.json` | 运行时 allowlist 去掉 `huya`（阶段 0） |

**Out of scope:** 把虎牙标 `STABLE`、改 B 站/抖音 Probe、真 TCP 交接、为虎牙铸造第二套签名。

阶段 0（Task 1–4）可单独部署并重启后端。阶段 1（Task 5–14）在硬闸后面实现，测试直接调用新函数，不依赖运行时 allowlist。阶段 2（Task 15）只有第 13 节规格测试全绿才做。

---

### Task 1: 能力字段与 `uses_ingest_probe`

**Files:**
- Modify: `lsc/platforms/models.py`
- Modify: `lsc/platforms/capabilities.py`
- Modify: `tests/test_platform_v2_models.py`（若有 capabilities 构造测试）
- Create: `tests/test_huya_ingest_as_probe.py`（本任务只写能力相关用例）

- [ ] **Step 1: Write the failing test**

在 `tests/test_huya_ingest_as_probe.py`：

```python
from lsc.platforms.capabilities import get_platform_capabilities, uses_ingest_probe
from lsc.platforms.models import PlatformCapabilities


def test_huya_capabilities_are_ingest_probe_and_no_auto_reconnect():
    caps = get_platform_capabilities("huya")
    assert caps.probe_profile == "ingest"
    assert caps.preview_auto_reconnect is False
    assert caps.preview_refresh_when_recording is False
    assert caps.max_connect_concurrency == 1
    assert caps.signed_url is True
    assert uses_ingest_probe(caps) is True


def test_uses_ingest_probe_defaults_from_signed_single_connect():
    caps = PlatformCapabilities(
        platform="custom",
        signed_url=True,
        max_connect_concurrency=1,
        probe_profile="default",
    )
    assert uses_ingest_probe(caps) is True


def test_bilibili_keeps_remote_probe():
    caps = get_platform_capabilities("bilibili")
    assert caps.probe_profile in {"default", "ffprobe"}
    assert uses_ingest_probe(caps) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_huya_ingest_as_probe.py::test_huya_capabilities_are_ingest_probe_and_no_auto_reconnect tests/test_huya_ingest_as_probe.py::test_uses_ingest_probe_defaults_from_signed_single_connect -v`

Expected: FAIL (`uses_ingest_probe` 未定义，或虎牙仍是 `preview_refresh_when_recording=True`)

- [ ] **Step 3: Write minimal implementation**

`lsc/platforms/models.py` 的 `PlatformCapabilities` 增加字段（放在 `preview_refresh_when_recording` 旁）：

```python
preview_auto_reconnect: bool = True
```

已有 `probe_profile: str = "default"`，不要改默认值。

`lsc/platforms/capabilities.py`：

```python
def uses_ingest_probe(capabilities: PlatformCapabilities | None) -> bool:
    if capabilities is None:
        return False
    profile = str(capabilities.probe_profile or "").strip().lower()
    if profile == "ingest":
        return True
    return bool(capabilities.signed_url) and int(capabilities.max_connect_concurrency) <= 1
```

虎牙块改为：

```python
"huya": PlatformCapabilities(
    platform="huya",
    support_level="PREVIEW",
    auth_mode="signed",
    credential_kinds=(),
    preferred_protocols=("flv", "hls"),
    qualities=("原画", "高清", "标清", "流畅"),
    quality_mapping=_QUALITY_MAPPING,
    max_connect_concurrency=1,
    expected_ttl_seconds=120.0,
    multi_cdn=True,
    signed_url=True,
    preview_refresh_when_recording=False,
    preview_auto_reconnect=False,
    probe_profile="ingest",
    refresh_triggers=("SIGNATURE_EXPIRED",),
    max_probe_candidates=4,
    probe_timeout_sec=5.0,
),
```

`__all__` 加入 `uses_ingest_probe`。

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_huya_ingest_as_probe.py tests/test_platform_v2_models.py tests/test_platform_adapter_contract.py -q`

Expected: PASS（Task 2 才会改 recovery 测试；若 `test_platform_recovery_policy.py` 因本任务失败，先不要改能力，等 Task 2 一起改期望。）

本任务改了 `preview_refresh_when_recording` 后，`tests/test_platform_recovery_policy.py::test_platform_recovery_policy_is_capability_driven` **会红**。下一步立刻修期望，不要还原能力。

- [ ] **Step 5: Commit**

```bash
git add lsc/platforms/models.py lsc/platforms/capabilities.py tests/test_huya_ingest_as_probe.py
git commit -m "$(cat <<'EOF'
feat: mark Huya as ingest-probe with auto-reconnect off

EOF
)"
```

Windows PowerShell 没有 HEREDOC 时用：

```powershell
git add lsc/platforms/models.py lsc/platforms/capabilities.py tests/test_huya_ingest_as_probe.py
git commit -m "feat: mark Huya as ingest-probe with auto-reconnect off"
```

---

### Task 2: 更新 recovery 能力测试

**Files:**
- Modify: `tests/test_platform_recovery_policy.py`

- [ ] **Step 1: Change the failing assertions**

`test_platform_recovery_policy_is_capability_driven` 改为：

```python
assert should_force_refresh_when_recording(huya) is False
assert should_force_refresh_when_recording(bilibili) is False
assert should_force_recovery(huya, "ffmpeg abnormal exit code=0") is False
assert should_force_recovery(bilibili, "ffmpeg abnormal exit code=0") is False
```

`should_force_recovery` 目前依赖 `preview_refresh_when_recording`；关掉后预览编码器退出不得再强刷页面，这是规格第 8 节。

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_platform_recovery_policy.py tests/test_huya_ingest_as_probe.py -q`

Expected: PASS

- [ ] **Step 3: Commit**

```powershell
git add tests/test_platform_recovery_policy.py
git commit -m "test: stop expecting Huya preview refresh while recording"
```

---

### Task 3: V2 硬闸（配置绕不过）

**Files:**
- Modify: `lsc/config.py`
- Modify: `python-backend/handlers/room_handler.py`（`_shared_ingest_v2_enabled`）
- Modify: `tests/test_platform_flags_and_redaction.py`
- Modify: `tests/test_huya_ingest_as_probe.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_huya_ingest_as_probe.py`：

```python
from lsc.config import LscConfig, is_platform_pipeline_v2_enabled


def test_huya_v2_hard_blocklist_wins_over_allowlist_and_shared_ingest():
    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["bilibili", "huya"],
        shared_ingest_enabled=True,
        ingest_supervisor_v2=True,
    )
    assert is_platform_pipeline_v2_enabled("huya", cfg) is False
    assert is_platform_pipeline_v2_enabled("bilibili", cfg) is True
```

再在同文件写一个针对 `_shared_ingest_v2_enabled` 的测试：构造 `SimpleNamespace` 房间 `platform="huya"`，`manager.get_room` 返回它，`load_config` monkeypatch 为上面的 cfg，断言函数返回 `False`。

```python
from types import SimpleNamespace
from python_backend_import_helper import ...  # 不要这样
```

`_shared_ingest_v2_enabled` 定义在 `python-backend/handlers/room_handler.py`。测试里：

```python
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python-backend"))

from handlers.room_handler import _shared_ingest_v2_enabled


def test_shared_ingest_v2_gate_rejects_huya_even_when_global_shared_on(monkeypatch):
    from lsc.config import LscConfig
    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["huya"],
        shared_ingest_enabled=True,
    )
    monkeypatch.setattr("handlers.room_handler.load_config", lambda: cfg)
    room = SimpleNamespace(platform="huya", platform_name="huya", room_url="https://www.huya.com/1")
    manager = SimpleNamespace(get_room=lambda _rid: room)
    assert _shared_ingest_v2_enabled(manager, "room-1") is False
```

若 `handlers.room_handler` 导入路径与现有 `tests/test_room_handler_lifecycle.py` 不同，**复制该文件的导入方式**，不要发明新的 sys.path 技巧。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_huya_ingest_as_probe.py::test_huya_v2_hard_blocklist_wins_over_allowlist_and_shared_ingest -v`

Expected: FAIL（allowlist 含 huya 时当前返回 True）

- [ ] **Step 3: Implement the hard gate**

`lsc/config.py` 在 `is_platform_pipeline_v2_enabled` 之前：

```python
_V2_PLATFORM_HARD_BLOCKLIST = frozenset({"huya"})
```

函数开头、在 `normalized` 算出来之后立刻：

```python
    if normalized in _V2_PLATFORM_HARD_BLOCKLIST:
        return False
```

显示名匹配分支之前也要拦：如果 `normalized == "huya"` 已 return。若调用方传 `"虎牙"`，现有 display_name 回退仍可能把它映射进 allowlist。在 display_name 匹配成功后、return True 前再查：

```python
    if platform_id in _V2_PLATFORM_HARD_BLOCKLIST:
        return False
```

更干净的写法：allowlist 命中或 display_name 命中得到 `platform_id` 后：

```python
    if canonical_id in _V2_PLATFORM_HARD_BLOCKLIST:
        return False
```

实现时先把 canonical 定为 `normalized` 或匹配到的 `platform_id`，再查 blocklist。

`_shared_ingest_v2_enabled` 现有短路径：

```python
        if bool(getattr(cfg, "shared_ingest_enabled", False)):
            return True
```

改为先解析 platform，若 `is_platform_pipeline_v2_enabled(platform, cfg)` 为 False 且 platform canonical 在 blocklist 中，return False；然后再处理 `shared_ingest_enabled`。

不要把「shared_ingest_enabled=True 时所有平台进监督器」改坏：B 站不在 allowlist 但开了全局 shared ingest 时仍应 True。只排除 blocklist。

抽出小函数避免重复：

```python
def is_platform_v2_hard_blocked(platform: str) -> bool:
    key = str(platform or "").strip().lower()
    if key in _V2_PLATFORM_HARD_BLOCKLIST:
        return True
    try:
        from lsc.platforms.capabilities import all_platform_capabilities
        from lsc.platforms.registry import get_display_name
        return any(
            get_display_name(platform_id).strip().lower() == key
            and platform_id in _V2_PLATFORM_HARD_BLOCKLIST
            for platform_id in all_platform_capabilities()
        )
    except Exception:
        return False
```

`is_platform_pipeline_v2_enabled` 第一句：`if is_platform_v2_hard_blocked(platform): return False`

`_shared_ingest_v2_enabled`：解析 platform 后 `if is_platform_v2_hard_blocked(platform): return False`

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_huya_ingest_as_probe.py tests/test_platform_flags_and_redaction.py tests/test_room_url_validation.py tests/test_orchestrator.py -q`

Expected: PASS。`test_platform_flags_and_redaction.py` 里已有 `assert not is_platform_pipeline_v2_enabled("huya", cfg)`（allowlist 只有 bilibili），应仍然通过。

- [ ] **Step 5: Commit**

```powershell
git add lsc/config.py python-backend/handlers/room_handler.py tests/test_huya_ingest_as_probe.py
git commit -m "fix: hard-block Huya from V2 even when allowlisted"
```

---

### Task 4: 关闭虎牙自动预览重连 + 运行时配置

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Modify: `tests/test_huya_ingest_as_probe.py` 或 `tests/test_room_handler_lifecycle.py`
- Modify: `C:/Users/Administrator/AppData/Roaming/lsc-electron/lsc_config.json`（运行时，不进 git）

- [ ] **Step 1: Write a source/unit guard**

在 `tests/test_huya_ingest_as_probe.py` 增加对 handler 辅助函数的测试。先在 `room_handler.py` 顶部附近（`_should_refresh_failed_stream` 旁）加：

```python
def _preview_auto_reconnect_allowed(stream_info_or_platform: object) -> bool:
    platform = str(stream_info_or_platform or "")
    if not isinstance(stream_info_or_platform, str):
        platform = str(getattr(stream_info_or_platform, "platform", "") or "")
    from lsc.platforms.capabilities import get_platform_capabilities
    return bool(get_platform_capabilities(platform).preview_auto_reconnect)
```

测试：

```python
from handlers.room_handler import _preview_auto_reconnect_allowed  # 用现有测试导入风格
from lsc.platforms.base import StreamInfo

def test_huya_preview_auto_reconnect_is_disabled():
    info = StreamInfo(platform="huya", room_url="https://www.huya.com/1")
    assert _preview_auto_reconnect_allowed(info) is False
    assert _preview_auto_reconnect_allowed("bilibili") is True
```

Shared MSE 与 legacy MSE 两条 `on_error` 重连循环，在创建 `{attempts:0, running:True}` **之前**：

```python
            if not _preview_auto_reconnect_allowed(platform_or_info):
                await _finalize(current_error or "预览失败", mse_failure_reason, f"mse_no_auto_reconnect:{room_id}")
                return
```

需要先读到 platform。shared 路径在 `_on_shared_mse_error` 里用 `manager.get_room(room_id)`；legacy 路径同样。不要在 `attempts` 已经清零之后才判断。

同时删掉 / 短路这段虎牙强刷（约 5153–5174 行）：

```python
                # 虎牙签名 URL 有并发连接限制：若房间正在录制，预览强制刷新获取
                # 独立签名...
                force_refresh = False
                def _peek_room_state():
                    ...
                    return bool(
                        r.is_recording
                        and should_force_refresh_when_recording(r.stream_info)
                    )
```

`should_force_refresh_when_recording` 对虎牙已是 False，这条路径会自然失效。仍应删掉「独立签名」注释，改成「单连接平台预览挂同一上游，禁止为签名强刷」。保留 `force_refresh = False` 即可。

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_huya_ingest_as_probe.py tests/test_room_handler_lifecycle.py tests/test_frontend_stability_guards.py -q`

Expected: PASS

- [ ] **Step 3: Edit runtime config (not git)**

把

`C:/Users/Administrator/AppData/Roaming/lsc-electron/lsc_config.json`

的 allowlist 改成只有 `bilibili`：

```json
{
  "shared_ingest_enabled": false,
  "platform_pipeline_v2_enabled": true,
  "platform_pipeline_v2_allowlist": [
    "bilibili"
  ],
  "unified_resolver_v2": true,
  "media_probe_v2": true,
  "stream_lease_v2": true,
  "ingest_supervisor_v2": true,
  "runtime_events_v2": true,
  "segmented_recording_enabled": false,
  "segmented_recording_v2": false
}
```

不要提交这个文件。仓库根 `lsc_config.json` 本来就没有 huya allowlist，不要改。

- [ ] **Step 4: Restart backend**

阶段 0 代码进进程后必须重启 Python 后端，丢掉旧虎牙租约和监督器。不要杀整个 Electron，除非后端没法热退。重启后日志应有硬闸拒绝或虎牙走 legacy 的 INFO。

- [ ] **Step 5: Commit code only**

```powershell
git add python-backend/handlers/room_handler.py tests/test_huya_ingest_as_probe.py
git commit -m "fix: disable Huya preview auto-reconnect and signed URL force-refresh"
```

---

### Task 5: 签名族 id

**Files:**
- Create: `lsc/platforms/signature_family.py`
- Modify: `lsc/platforms/models.py`（`StreamCandidate.signature_family_id`）
- Modify: `lsc/platforms/resolver.py`（`_candidate_from_url`）
- Modify: `tests/test_huya_ingest_as_probe.py`

- [ ] **Step 1: Write the failing test**

```python
from lsc.platforms.signature_family import signature_family_id
from lsc.platforms.resolver import _candidate_from_url


def test_huya_tx_and_al_share_signature_family():
    secret = "abc123"
    ws_time = "68f0aa00"
    tx = f"https://tx.flv.huya.com/src/room.flv?wsSecret={secret}&wsTime={ws_time}&codec=264"
    al = f"https://al.flv.huya.com/src/room.flv?wsSecret={secret}&wsTime={ws_time}&codec=264"
    assert signature_family_id(tx) == signature_family_id(al)
    assert signature_family_id(tx)
    other = f"https://tx.flv.huya.com/src/room.flv?wsSecret=zzz&wsTime={ws_time}"
    assert signature_family_id(tx) != signature_family_id(other)


def test_candidate_from_url_sets_signature_family_id():
    url = "https://tx.flv.huya.com/src/room.flv?wsSecret=abc&wsTime=1"
    candidate = _candidate_from_url(
        platform="huya",
        quality="source",
        url=url,
        headers={},
        priority=0,
        raw={},
    )
    assert candidate is not None
    assert candidate.signature_family_id == signature_family_id(url)
    assert "abc" not in candidate.redacted().get("signature_family_id", "")
```

`redacted()` 只应输出 family id 短哈希，不应含 wsSecret。family id 本身已是 sha256 截断，断言它不等于原文即可。

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_huya_ingest_as_probe.py::test_huya_tx_and_al_share_signature_family -v`

Expected: FAIL（模块不存在）

- [ ] **Step 3: Implement**

`lsc/platforms/signature_family.py`：

```python
from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlparse

_FAMILY_KEYS = ("wsSecret", "wssecret", "wsTime", "wstime")


def signature_family_id(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        query = parse_qs(urlparse(text).query, keep_blank_values=False)
    except Exception:
        return ""
    secret = ""
    ws_time = ""
    for key, values in query.items():
        lowered = key.lower()
        if lowered == "wssecret" and values:
            secret = str(values[0])
        elif lowered == "wstime" and values:
            ws_time = str(values[0])
    if secret or ws_time:
        material = f"{secret}|{ws_time}"
    else:
        items = "&".join(
            f"{key}={values[0]}"
            for key, values in sorted(query.items())
            if key.lower() not in {"codec", "ctype", "fs"}
        )
        material = items
    if not material:
        return ""
    return hashlib.sha256(material.encode("utf-8", "ignore")).hexdigest()[:16]
```

`StreamCandidate` 增加 `signature_family_id: str = ""`，`redacted()` 加入该字段。

`_candidate_from_url` 构造时：

```python
        signature_family_id=signature_family_id(url),
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_huya_ingest_as_probe.py tests/test_platform_probe_and_resolver.py tests/test_platform_v2_models.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add lsc/platforms/signature_family.py lsc/platforms/models.py lsc/platforms/resolver.py tests/test_huya_ingest_as_probe.py
git commit -m "feat: fingerprint Huya CDN lines by shared signature family"
```

---

### Task 6: 恢复策略 — 作废签名族，而不是换 CDN

**Files:**
- Modify: `lsc/platforms/recovery_policy.py`
- Modify: `lsc/platforms/lease_manager.py`（可选：`revoke_family`）
- Modify: `tests/test_platform_recovery_policy.py`
- Modify: `tests/test_huya_ingest_as_probe.py`

- [ ] **Step 1: Write failing tests**

```python
from lsc.platforms.huya import _is_cdn_blacklisted, clear_cdn_blacklist
from lsc.platforms.recovery_policy import mark_failed_candidate, recovery_action
from lsc.platforms.base import StreamInfo
from lsc.platforms.failure import FailureKind


def test_huya_403_invalidates_family_and_does_not_quarantine_cdn():
    clear_cdn_blacklist()
    info = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/1",
        stream_url="https://tx.flv.huya.com/src/live.flv?wsSecret=abc&wsTime=1",
        raw={"v2": True, "candidate_id": "huya|source|0", "candidate_cdn_id": "tx"},
    )
    action = recovery_action(info, "Server returned 403 Forbidden", saw_first_ts=False)
    assert action == "invalidate_family"
    assert mark_failed_candidate(info, "Server returned 403 Forbidden") is False
    assert not _is_cdn_blacklisted("tx", room_key="https://www.huya.com/1")


def test_huya_connect_timeout_after_media_quarantines_cdn():
    clear_cdn_blacklist()
    info = StreamInfo(
        platform="huya",
        room_url="https://www.huya.com/1",
        stream_url="https://tx.flv.huya.com/src/live.flv?wsSecret=abc&wsTime=1",
    )
    action = recovery_action(
        info,
        "Connection to tcp://tx.flv.huya.com:443 failed: Error number -138 occurred",
        saw_first_ts=True,
    )
    assert action == "quarantine_cdn"
    assert mark_failed_candidate(
        info,
        "Connection to tcp://tx.flv.huya.com:443 failed: Error number -138 occurred",
        room_id="https://www.huya.com/1",
    ) is True
    assert _is_cdn_blacklisted("tx", room_key="https://www.huya.com/1")
    clear_cdn_blacklist()


def test_huya_eof_before_first_ts_invalidates_family():
    info = StreamInfo(platform="huya", stream_url="https://tx.flv.huya.com/src/live.flv")
    assert recovery_action(info, "End of file", saw_first_ts=False) == "invalidate_family"
    assert recovery_action(info, "preview encoder failed", saw_first_ts=True) == "restart_preview_sink"
```

现有 `test_huya_connect_timeout_quarantines_cdn` 不传 `saw_first_ts`。规格：尚无首包的超时仍视为族失效更安全；**已有首包后的超时才隔离 CDN**。把旧测试改成显式 `saw_first_ts=True` 的行为，或改 `mark_failed_candidate` 增加可选参数 `saw_first_ts: bool = False`。无首包默认不隔离 CDN。

因此旧测试 `test_huya_connect_timeout_quarantines_cdn` 在默认 `saw_first_ts=False` 时会失败。改为传入 `saw_first_ts=True`。

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_huya_ingest_as_probe.py::test_huya_403_invalidates_family_and_does_not_quarantine_cdn tests/test_platform_recovery_policy.py::test_huya_connect_timeout_quarantines_cdn -v`

Expected: FAIL（403 仍会 `mark_cdn_bad`；`recovery_action` 不存在）

- [ ] **Step 3: Implement**

删除 `recovery_policy.py` 中：

```python
    if platform == "huya" and kind in {
        FailureKind.AUTH_EXPIRED,
        FailureKind.SIGNATURE_EXPIRED,
    }:
        kind = FailureKind.CDN_FORBIDDEN
```

新增：

```python
def recovery_action(
    stream_info: object | None,
    error: object,
    *,
    saw_first_ts: bool = False,
) -> str:
    platform = str(getattr(stream_info, "platform", "") or "")
    capabilities = get_platform_capabilities(platform)
    kind = classify_failure(str(error or ""))
    ingest = uses_ingest_probe(capabilities)
    text = str(error or "")
    if kind is FailureKind.PREVIEW_ENCODER_FAILURE or "preview stdout stalled" in text.lower():
        return "restart_preview_sink"
    if kind is FailureKind.RECORDING_SINK_FAILURE:
        return "restart_recording_sink"
    if kind is FailureKind.OFFLINE:
        return "offline"
    family_kinds = {
        FailureKind.AUTH_EXPIRED,
        FailureKind.SIGNATURE_EXPIRED,
        FailureKind.CDN_FORBIDDEN,
    }
    if ingest and (
        kind in family_kinds
        or (not saw_first_ts and kind in {FailureKind.CONNECTION_RESET, FailureKind.NO_MEDIA, FailureKind.CONNECT_TIMEOUT})
    ):
        return "invalidate_family"
    if kind in {
        FailureKind.CONNECT_TIMEOUT,
        FailureKind.CONNECTION_RESET,
        FailureKind.DNS_FAILURE,
    }:
        return "quarantine_cdn"
    return "none"
```

`mark_failed_candidate` 开头调用 `recovery_action(...)`：只有 `quarantine_cdn` 才 `mark_cdn_bad`；`invalidate_family` 记录 candidate health 为 `SIGNATURE_EXPIRED` 但不标 CDN，返回 False（或 True 表示已处理但不隔离线路——测试按上面断言 403 时 `mark_failed_candidate is False` 且 CDN 未隔离）。

`should_force_recovery` 保持依赖 `preview_refresh_when_recording`（已 False）。

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_platform_recovery_policy.py tests/test_huya_ingest_as_probe.py tests/test_platform_failure.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add lsc/platforms/recovery_policy.py tests/test_platform_recovery_policy.py tests/test_huya_ingest_as_probe.py
git commit -m "fix: treat Huya 403 as signature-family expiry, not CDN hop"
```

---

### Task 7: `select_ingest_lease` — 跳过远端 Probe

**Files:**
- Modify: `lsc/platforms/resolver.py`
- Modify: `lsc/core/orchestrator.py`（`_resolve_v2_stream_info`）
- Modify: `lsc/platforms/__init__.py` / `registry.py` 导出
- Modify: `tests/test_huya_ingest_as_probe.py`
- Modify: `tests/test_platform_probe_and_resolver.py`（B 站路径不变）

- [ ] **Step 1: Write failing test**

```python
from unittest.mock import Mock
from lsc.platforms.capabilities import get_platform_capabilities
from lsc.platforms.models import ResolveResult, StreamCandidate
from lsc.platforms.resolver import probe_candidates, select_ingest_lease, select_stream_lease


def test_select_ingest_lease_does_not_require_probe_ok():
    caps = get_platform_capabilities("huya")
    tx = StreamCandidate(
        candidate_id="huya|source|0",
        url="https://tx.flv.huya.com/src/a.flv?wsSecret=abc&wsTime=1",
        quality_id="source",
        cdn_id="tx",
        protocol="flv",
        signature_family_id="fam1",
    )
    al = StreamCandidate(
        candidate_id="huya|al|1",
        url="https://al.flv.huya.com/src/a.flv?wsSecret=abc&wsTime=1",
        quality_id="al",
        cdn_id="al",
        protocol="flv",
        signature_family_id="fam1",
    )
    result = ResolveResult(
        platform="huya",
        room_url="https://www.huya.com/1",
        candidates=(tx, al),
        capabilities=caps,
        live_status="LIVE",
    )
    from lsc.platforms.lease_manager import LeaseManager
    lease = select_ingest_lease(result, room_id="r1", lease_manager=LeaseManager(), now=0.0)
    assert lease is not None
    assert lease.candidate.cdn_id == "tx"  # 非 al 优先，与现有虎牙启发式一致
    assert lease.probe_summary.get("mode") == "ingest"
    assert lease.consumed is False
```

再写一个测试：monkeypatch `ProbeService.probe_candidates` 为会 `raise` 的 Mock，调用 orchestrator 的 `_resolve_v2_stream_info` 或抽出来的纯函数，断言 ingest 平台不会调用它。

更可测的做法：在 `resolver.py` 增加：

```python
def resolve_playable_lease(result, *, room_id, lease_manager, probes=None, **kwargs):
    if uses_ingest_probe(result.capabilities):
        return select_ingest_lease(...)
    return select_stream_lease(result, probes, ...)
```

测试 `resolve_playable_lease` 在 huya capabilities 下 `probes=None` 仍返回 lease。

选路规则（规格第 5 节）：恰好 1 条。优先历史健康、跳过 `_is_cdn_blacklisted`、跳过 `cdn_id=="al"`（若还有别的）。不要 Probe。

- [ ] **Step 2: Run to verify fail**

Expected: FAIL（`select_ingest_lease` 不存在）

- [ ] **Step 3: Implement**

`select_ingest_lease` 放在 `resolver.py` 的 `select_stream_lease` 旁。用 `limit_probe_candidates` 限制条数后选 1 条。`lease.probe_summary = {"mode": "ingest", "has_video": False, "timestamp_ok": False}`。

`StreamLease` 增加 `consumed: bool = False`。`LeaseManager.mark_consumed(lease_id)` 置 True。

`_resolve_v2_stream_info` 中：

```python
        if uses_ingest_probe(capabilities):
            lease = select_ingest_lease(...)
            probes = {}
        else:
            probes = probe_candidates(...)
            lease = select_stream_lease(...)
```

`_on_connect_finished` 里：

```python
            if info.stream_url and not uses_ingest_probe(get_platform_capabilities(info.platform)):
                self._probe_metadata_async(room_id, info.stream_url)
```

ingest 平台不要对 CDN URL 做元数据 ffprobe。分辨率可在上游首包后从本地 TS 补，本任务允许暂时留空。

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_huya_ingest_as_probe.py tests/test_platform_probe_and_resolver.py tests/test_orchestrator.py tests/test_room_url_validation.py -q`

Expected: PASS。B 站仍走 Probe。

- [ ] **Step 5: Commit**

```powershell
git add lsc/platforms/resolver.py lsc/platforms/models.py lsc/platforms/lease_manager.py lsc/core/orchestrator.py lsc/platforms/__init__.py tests/test_huya_ingest_as_probe.py
git commit -m "feat: issue Huya leases without remote media probes"
```

---

### Task 8: `SharedIngestStartResult.media_ready` — 禁止假成功

**Files:**
- Modify: `lsc/core/services/shared_ingest.py`
- Modify: `tests/test_shared_ingest.py`
- Modify: `tests/test_huya_ingest_as_probe.py`
- Modify: 所有 `SharedIngestStartResult(ok=True)` 的生产路径（预览必须 `media_ready` 才 `ok`）

- [ ] **Step 1: Write failing test**

```python
def test_start_preview_without_subscribers_is_not_media_ready():
    ingest = SharedRoomIngest(room_id="r", url="https://example/live.flv")
    result = ingest.start_preview()
    assert result.accepted is True
    assert result.media_ready is False
    assert result.ok is False
```

现有 `test_shared_ingest.py` 里若断言无订阅者 `ok is True`，改为 `accepted is True` 且 `ok is False`。

再写：进程拉起但未喂 TS 时，`start_preview` 在有订阅者的情况下也不得 `media_ready`（可用 fake process：`poll()` 返回 None，stdout 不写数据）。若当前测试用 mock 启动，断言 `ok` 随 `media_ready`。

规格允许 UI 在媒体已接通时 LIVE，handler 成功响应可以用 `accepted`。`ok` 必须等于 `media_ready`。

- [ ] **Step 2: Run to verify fail**

Expected: FAIL（无订阅者现在 `ok=True`）

- [ ] **Step 3: Implement**

```python
@dataclass(frozen=True)
class SharedIngestStartResult:
    ok: bool
    use_legacy_fallback: bool = False
    error: str = ""
    accepted: bool = False
    media_ready: bool = False
```

增加工厂：

```python
def ingest_start_result(
    *,
    accepted: bool = False,
    media_ready: bool = False,
    error: str = "",
    use_legacy_fallback: bool = False,
) -> SharedIngestStartResult:
    return SharedIngestStartResult(
        ok=media_ready,
        accepted=accepted or media_ready,
        media_ready=media_ready,
        error=error,
        use_legacy_fallback=use_legacy_fallback,
    )
```

`start_preview`：

- 无订阅者：`return ingest_start_result(accepted=True)`
- 已有活着的 preview process：**仍不要** `ok=True`，除非 `_preview_has_init` 且 `_preview_has_media_segment`（加两个标志，在 fMP4 parser 回调里置位）
- 进程拉起、上游已有 TS、预览已发出 init+segment：`ingest_start_result(accepted=True, media_ready=True)`
- 进程拉起但还没有分片：`ingest_start_result(accepted=True, media_ready=False)` — **这会让现有 handler 以为失败**。

handler 必须改成看 `accepted` 而不是 `ok` 来返回「预览已受理」；看 `media_ready` 才标 LIVE；看稳住窗口才 `mse_reconnected`。本任务先改 ingest 结果；Task 9 改 handler 重试。本任务同步改 `_handle_mse_preview` 里 `getattr(result, 'ok')` 为：

```python
if not (getattr(result, "accepted", False) or getattr(result, "ok", False)):
    raise RuntimeError(...)
```

LIVE / reconnect 成功不要在这里清零 attempts（Task 9）。

`start_recording` 已有文件写入探测，可继续 `ok=True` 当 `media_ready=True`（录制 sink 已写入）。不要把录制 `ok` 语义弄乱。

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_shared_ingest.py tests/test_shared_ingest_integration_guards.py tests/test_ingest_supervisor.py tests/test_multi_room_manager.py tests/test_huya_ingest_as_probe.py tests/test_room_handler_lifecycle.py -q`

Expected: PASS。所有构造 `SharedIngestStartResult(ok=True)` 的测试仍合法，因为新字段有默认值；生产预览路径不再在无订阅者时 `ok=True`。

- [ ] **Step 5: Commit**

```powershell
git add lsc/core/services/shared_ingest.py python-backend/handlers/room_handler.py tests/test_shared_ingest.py tests/test_huya_ingest_as_probe.py
git commit -m "fix: require media bytes before treating shared preview as ready"
```

---

### Task 9: 重试预算与 30 秒稳住窗口

**Files:**
- Modify: `python-backend/handlers/room_handler.py`
- Modify: `lsc/core/services/shared_ingest.py`（可选：`durable_since` 时间戳）
- Modify: `tests/test_huya_ingest_as_probe.py`

- [ ] **Step 1: Write failing tests as a pure helper**

不要把整段 asyncio 重连循环塞进第一个测试。在 `room_handler.py` 抽出：

```python
DURABLE_SUCCESS_SEC = 30.0

def _reconnect_attempts_after_event(
    state: dict,
    *,
    event: str,
    durable_sec: float = DURABLE_SUCCESS_SEC,
    now: float,
) -> dict:
    """event: 'accepted' | 'media_ready' | 'durable' | 'exit' | 'user_stop'"""
```

规则：

- `accepted` / `media_ready`：不改 `attempts`，不删 state
- `durable`：`attempts=0`，可设 `durable=True`
- `exit` 且未 durable：`attempts += 1`
- `user_stop`：清空

测试：

```python
def test_reconnect_budget_does_not_reset_on_accepted_or_media_ready():
    now = 100.0
    state = {"attempts": 1, "running": True}
    state = _reconnect_attempts_after_event(state, event="accepted", now=now)
    state = _reconnect_attempts_after_event(state, event="media_ready", now=now)
    assert state["attempts"] == 1
    state = _reconnect_attempts_after_event(state, event="exit", now=now + 3)
    assert state["attempts"] == 2


def test_reconnect_budget_resets_only_after_durable_window():
    state = {"attempts": 2, "running": True, "media_ready_at": 100.0}
    state = _reconnect_attempts_after_event(
        state, event="durable", now=130.0, durable_sec=30.0,
    )
    assert state["attempts"] == 0
```

Shared/legacy 两条循环里：

- `result.success` / `result.ok` 时**不要** `_mse_reconnect_state.pop`
- 记录 `media_ready_at`
- 用 timer/watchdog：`now - media_ready_at >= durable_sec` 且进程仍在，才 pop 并 `mse_reconnected`
- 窗口内 `on_error`：不要把 attempts 重置为 0。当前代码在循环入口写 `_mse_reconnect_state[room_id] = {'attempts': 0, 'running': True}` —— **这是风暴核心**。改成：若已有 state 且未 durable，保留 attempts，只设 `running=True`

阶段 0 虎牙 `preview_auto_reconnect=False`，此循环对虎牙仍直接 finalize。该预算是给阶段 2 解闸后用的，B 站现在也受益（假成功不再清零）。**对所有平台应用 attempts 保留**，不要只写 huya。

- [ ] **Step 2–4:** 红 → 实现 → `pytest tests/test_huya_ingest_as_probe.py tests/test_room_handler_lifecycle.py -q` 绿

- [ ] **Step 5: Commit**

```powershell
git commit -m "fix: reset MSE reconnect budget only after durable media"
```

---

### Task 10: 录制有界队列，禁止堵住上游读取

**Files:**
- Modify: `lsc/core/services/shared_ingest.py`
- Modify: `lsc/config.py`（`shared_ingest_recording_queue_bytes`，默认 2*1024*1024）
- Modify: `tests/test_shared_ingest.py`
- Modify: `tests/test_huya_ingest_as_probe.py`

- [ ] **Step 1: Write failing test**

用假 recording stdin：`write()` 睡眠 2 秒或忽略数据但阻塞。上游 `_dispatch_ts_batch` 连续喂多个 188 字节包。断言在阻塞的 write 期间，预览队列仍增加（或 `_enqueue_preview_ts` 被调用）。更简单：

把 `_write_all` 换成会阻塞的 stub 挂在 recording process 上，在另一线程调 `_dispatch_ts_batch` 多次，主线程在 0.2s 内完成所有 dispatch（不会卡在 `_WRITE_TIMEOUT_SEC=10`）。

```python
def test_recording_stdin_backpressure_does_not_block_upstream_dispatch(monkeypatch):
    ingest = SharedRoomIngest(room_id="r", url="https://example/a.flv")
    # 构造 recording process with blocking stdin
    ...
    started = time.monotonic()
    ingest._dispatch_ts_batch(b"\x47" * 188 * 8)
    ingest._dispatch_ts_batch(b"\x47" * 188 * 8)
    assert time.monotonic() - started < 1.0
```

当前实现同步 `_write_all`，第二次会等到 timeout 或阻塞，测试会红。

- [ ] **Step 2: Implement**

照 `_enqueue_preview_ts` / `_write_preview_input_loop` 复制一套 recording：

- `_recording_ts_queue: deque[bytes]`
- `_recording_queued_bytes`
- `_recording_condition`
- `_enqueue_recording_ts`
- `_write_recording_input_loop`

`_dispatch_ts_batch` 改为：

```python
        self._enqueue_recording_ts(batch)
        self._enqueue_preview_ts(batch)
```

满队列：drop_oldest，累计 `recording_dropped_bytes`。连续满 5 秒：`_handle_recording_process_exit(..., RECORDING_SINK_FAILURE)`，不要停上游。

启动 recording 时启动写线程，带 `recording_generation`。

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_shared_ingest.py tests/test_huya_ingest_as_probe.py tests/test_ingest_supervisor.py -q`

Expected: PASS

- [ ] **Step 4: Commit**

```powershell
git commit -m "fix: isolate recording stdin writes from shared upstream reader"
```

---

### Task 11: `StreamCapture` generation CAS

**Files:**
- Modify: `lsc/recorder/capture.py`
- Modify: `tests/test_recorder.py`

- [ ] **Step 1: Write failing test**

```python
def test_stop_does_not_close_a_newer_generation(capture, monkeypatch):
    old = MagicMock()
    old.poll.return_value = None
    old.stdin = MagicMock()
    old.pid = 111
    new = MagicMock()
    new.poll.return_value = None
    new.stdin = MagicMock()
    new.pid = 222
    capture._status = CaptureStatus.STOPPING
    capture._process = old
    capture._generation = 1
    capture._output_path = "x.mp4"

    def fake_wait(timeout):
        capture._process = new
        capture._generation = 2
        capture._status = CaptureStatus.RECORDING
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)

    old.wait.side_effect = fake_wait
    capture.stop()
    assert capture._process is new
    new.stdin.close.assert_not_called()
```

把 `stop()` 里 5 秒 wait 通过 monkeypatch `_wait_with_deadline` 或把超时改成可注入会让测试更快。最小改动：测试里 monkeypatch `stop` 内部 wait。若难注入，把 wait 超时在 STOPPING 下用 0.01s 仅当 `LSC_TEST_CAPTURE_STOP_SEC` 环境变量设置。更干净：`stop()` 结束时比较 generation。

当前 bug 在：

```python
        with self._lock:
            proc = self._process
            self._process = None
            output_path = self._output_path
        self._close_proc_pipes(proc)
```

测试应在 wait 期间替换 `_process`，断言 stop 返回后 `_process is new` 且没有 `self._process = None`。

- [ ] **Step 2: Implement**

`__init__`: `self._generation = 0`

`start()` 在 Popen 成功后：`self._generation += 1`；`started_generation = self._generation`

`stop()` / `stop_async()` 快照 `generation = self._generation` 和 `proc`。

结束时：

```python
        with self._lock:
            if self._generation != generation:
                return CaptureResult(True, output_path)  # 旧代次已停，新代次保留
            if self._process is proc:
                self._process = None
            output_path = self._output_path
        self._close_proc_pipes(proc)
        if self._generation != generation:
            return ...
        self._set_status(CaptureStatus.STOPPED)
```

`stop_async` 把 generation 传进后台线程，调用 `stop(generation=...)`。

- [ ] **Step 3: Run** `pytest tests/test_recorder.py -q` → PASS

- [ ] **Step 4: Commit**

```powershell
git commit -m "fix: keep StreamCapture stop from closing a newer FFmpeg"
```

---

### Task 12: 租约 consumed + 假 CDN 只打开一次

**Files:**
- Modify: `lsc/platforms/lease_manager.py`
- Modify: `lsc/core/services/shared_ingest.py` 或 supervisor：上游 `-i` 前 `mark_consumed`
- Modify: `lsc/platforms/acceptance.py`
- Create tests in `tests/test_huya_ingest_as_probe.py` using `tests/test_fake_cdn_matrix.py` 的 HTTP 服务器模式

- [ ] **Step 1: Write failing test — GET count**

本地 HTTP 服务器：query 含 `wsSecret=abc`。第一次 GET 返回 `b"\x47"*188` 且 200；第二次同一 secret 返回 403。计数器按 secret 递增。

测试 A：`probe_profile=ingest` 路径只 `urlopen`/`ProbeService` 0 次，随后用一个最小「打开 URL 一次」的辅助（可直接 `urlopen` 模拟上游）。断言 count==1。

测试 B：错误路径先 `ProbeService.probe` 再 `urlopen`，断言第二次 403。这个测试锁住「为什么不能先 Probe」。

更贴近生产：调用 `probe_candidates` + 再 `urlopen(candidate.url)`，对 huya ingest 选择器断言 `probe_candidates` 未被调用（monkeypatch 计数）。

```python
def test_ingest_probe_path_does_not_call_probe_candidates(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        "lsc.platforms.resolver.probe_candidates",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or {},
    )
    # 构造 ResolveResult huya + select via resolve_playable_lease
    ...
    assert calls["n"] == 0
```

`mark_consumed`：第二次对同一 lease 启动上游应拒绝。

```python
def test_consumed_lease_cannot_open_again():
    manager = LeaseManager()
    lease = manager.issue(...)
    manager.mark_consumed(lease.lease_id)
    assert manager.is_consumed(lease.lease_id)
```

上游启动处：`if lease.consumed: raise`；成功拼好 `-i` 后立刻 `mark_consumed`。

acceptance.py：`if uses_ingest_probe(result.capabilities): probes={}; lease=select_ingest_lease(...)` else 旧路径。失败文案不要再说 “no candidate passed real media probe”。

- [ ] **Step 2–4:** 红 → 实现 → pytest 绿

- [ ] **Step 5: Commit**

```powershell
git commit -m "feat: consume signed leases on first upstream open"
```

---

### Task 13: SharedRoomIngest / supervisor generation 与预览编码器故障

**Files:**
- Modify: `lsc/core/services/shared_ingest.py`
- Modify: `lsc/core/services/ingest_supervisor.py`
- Modify: `python-backend/handlers/room_handler.py`（`_should_refresh_failed_stream`）
- Modify: `tests/test_supervised_recovery.py`
- Modify: `tests/test_huya_ingest_as_probe.py`

- [ ] **Step 1: Tests**

```python
def test_preview_encoder_failure_does_not_refresh_stream():
    assert _should_refresh_failed_stream("preview encoder failed") is False
    assert _should_refresh_failed_stream("shared preview stdout stalled (15s)") is False
    assert _should_refresh_failed_stream("Server returned 403 Forbidden") is True
```

ingest 平台 403 的 refresh 含义是重新解析新 family，不是复用缓存。`refresh_stream_url(force=True)` 仍可调用，但 Resolver 不得返回已 consumed 的 URL。Task 12 的 consumed 保证这一点。

supervisor 恢复：`PREVIEW_ENCODER_FAILURE` 只 `start_preview`，不 `switch_upstream`。看 `tests/test_supervised_recovery.py` 现有结构，补一条。

上游读写线程已有 generation 检查；录制写线程同样带 generation（Task 10）。本任务补预览错误分类与 supervisor 分派。

- [ ] **Step 2–4:** 实现与测试

- [ ] **Step 5: Commit**

```powershell
git commit -m "fix: restart Huya preview sink without refreshing signed URLs"
```

---

### Task 14: 定向回归与规格第 13 节清单

**Files:** 测试只读核对，缺什么补什么。

- [ ] **Step 1: Run the spec checklist**

```powershell
pytest tests/test_huya_ingest_as_probe.py tests/test_platform_recovery_policy.py tests/test_shared_ingest.py tests/test_recorder.py tests/test_platform_probe_and_resolver.py tests/test_orchestrator.py tests/test_fake_cdn_matrix.py tests/test_supervised_recovery.py tests/test_platform_flags_and_redaction.py tests/test_room_handler_lifecycle.py tests/test_platform_acceptance.py -q
```

对照规格 §13：

1. 签名只打开一次 — Task 12
2. 同源签名族 — Task 5–6
3. 线路故障才换 CDN — Task 6
4. 假成功 — Task 8–9
5. 稳住窗口 — Task 9
6. 预览编码器退出 — Task 13
7. 录制队列隔离 — Task 10
8. Capture CAS — Task 11
9. 硬闸 — Task 3
10. B 站回归 — 本步 pytest

缺哪条补哪条测试，不要改规格来迁就实现。

- [ ] **Step 2: Broader pytest**

```powershell
pytest tests/test_platform_*.py tests/test_shared_ingest.py tests/test_ingest_supervisor.py tests/test_core_recording_service.py tests/test_mse_streamer.py -q
```

Expected: PASS

- [ ] **Step 3: Commit any test-only fills**

---

### Task 15: 阶段 2 — 解除硬闸（仅当 Task 14 全绿）

**不要在 Task 14 失败时做本任务。**

**Files:**
- Modify: `lsc/config.py`（从 `_V2_PLATFORM_HARD_BLOCKLIST` 删除 `huya`）
- Modify: `tests/test_huya_ingest_as_probe.py`（硬闸测试改为：allowlist 含 huya 时 V2 **启用**，或删除该测试并改为 ingest 路径集成测试）
- Modify: 运行时 `lsc_config.json` allowlist 加回 `"huya"`（不进 git）
- Modify: `lsc/platforms/capabilities.py`：阶段 2 可将 `preview_auto_reconnect` 改回 `True`，**前提**是 Task 9 的稳住窗口已落地。若窗口未合入，保持 False。

- [ ] **Step 1: Flip tests first**

```python
def test_huya_v2_allowlist_enables_ingest_pipeline():
    cfg = LscConfig(
        platform_pipeline_v2_enabled=True,
        platform_pipeline_v2_allowlist=["huya"],
        shared_ingest_enabled=False,
    )
    assert is_platform_pipeline_v2_enabled("huya", cfg) is True
```

- [ ] **Step 2: Remove `huya` from `_V2_PLATFORM_HARD_BLOCKLIST`**

空 frozenset 或删除该常量及所有引用。

- [ ] **Step 3: Run Task 14 命令 + `tests/test_huya_ingest_as_probe.py`**

Expected: PASS

- [ ] **Step 4: Runtime allowlist add `huya`, restart backend**

- [ ] **Step 5: Commit**

```powershell
git commit -m "feat: re-enable Huya V2 on ingest-as-probe path"
```

真实验收（录制-only / 预览-only / 并行短测）仍是人工门禁。通过前 `support_level` 保持 `PREVIEW`。

---

## Self-review vs spec

| Spec section | Task |
|---|---|
| §3 阶段 0 止损 | 1–4 |
| §4 能力 / 硬闸 | 1, 3, 15 |
| §5 主链路 / 禁止远端 Probe | 7, 12 |
| §6 签名族 | 5 |
| §7 成功判定 / 重试 | 8, 9 |
| §8 恢复分类 | 6, 13 |
| §9 Windows 管道 | 10 |
| §10 Generation CAS | 11, 13 |
| §11 运行时配置与重启 | 4, 15 |
| §12 模块表 | File map |
| §13 测试 | 14 |
| §14 阶段 2 | 15 |
| §15 修正 8/9 Probe 条款 | 7 |

无 TBD。`ok` 与 `media_ready` 在 Task 8 钉死。阶段 2 不得提前于 Task 14。
