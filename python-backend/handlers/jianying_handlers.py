"""剪映草稿导出 WebSocket handlers。"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from persistence import load_analysis_results, load_finalization_job

from lsc.core.models import JianyingDraftOptions
from lsc.core.services.timeline_service import get_timeline_service
from lsc.exporter.jianying_draft import (
    ClipDraftSource,
    RoomDraftSource,
    _BROADCAST_VALID_END_BY,
    build_session_draft,
    clip_allowed_for_draft,
    detect_jianying_draft_dir,
    resolve_common_range,
)

_log = logging.getLogger(__name__)

# 由 register_jianying_handlers 注入的后端权威切片快照（room_handler 的
# _continuous_tasks / _analysis_jobs 中的 listed_clips）。草稿门禁必须以此为
# 准合并赛事审计字段，不能只信任前端薄投影。
_continuous_tasks: dict[str, dict[str, Any]] = {}
_analysis_jobs: dict[str, dict[str, Any]] = {}
# 任务态被 pop 后保留的房级终态权威快照（room_handler._last_authority_snapshots）。
# 收尾完成 → 用户导出之间任务态已消失，没有它就会回落到可能落后的 sidecar：
# 现场 2026-09-11 20:45，20:43:26 已定稿的 round-000105 因权威不可达被改回 pending 后跳过。
_authority_snapshots: dict[str, dict[str, Any]] = {}


def _find_authority_task_state(room_id: str) -> dict[str, Any] | None:
    """找到覆盖该房间的持续分析/同步分析任务状态（权威快照宿主）。

    活跃任务优先；任务已结束（pop）时回落到终态权威快照。
    """
    if not room_id:
        return None
    for task_state in _continuous_tasks.values():
        if room_id in (task_state.get("target_room_ids") or []):
            return task_state
    for job_state in _analysis_jobs.values():
        if room_id in (job_state.get("target_room_ids") or []):
            return job_state
    snapshot = _authority_snapshots.get(room_id)
    if isinstance(snapshot, dict):
        targets = snapshot.get("target_room_ids") or []
        if not targets or room_id in targets or snapshot.get("room_id") == room_id:
            return snapshot
    return None


def _load_recording_sidecars(room: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """读取房间当前录制对应的收尾 sidecar 与分析结果 sidecar（缺失返回 None）。"""
    rec_path = getattr(room, "record_output_path", "") or ""
    if not rec_path or not os.path.isfile(rec_path):
        return None, None
    try:
        finalization = load_finalization_job(rec_path)
    except Exception as exc:  # 防御：sidecar 读取失败不阻断草稿生成
        _log.debug("读取收尾 sidecar 失败: %s", exc)
        finalization = None
    try:
        analysis = load_analysis_results(rec_path)
    except Exception as exc:
        _log.debug("读取分析结果 sidecar 失败: %s", exc)
        analysis = None
    return finalization, analysis


def _sidecar_round_candidates(
    finalization: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    round_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """按 round_key 收集 sidecar 中的 accepted / rejected / pending 候选。"""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    if isinstance(finalization, dict):
        for bucket, target in (
            ("accepted_candidates", accepted),
            ("rejected_candidates", rejected),
            ("pending_candidates", pending),
        ):
            for item in finalization.get(bucket) or []:
                if isinstance(item, dict) and str(item.get("round_key") or "") == round_key:
                    target.append(item)
    if isinstance(analysis, dict):
        for item in analysis.get("highlights") or []:
            if (
                isinstance(item, dict)
                and str(item.get("round_key") or "") == round_key
                and str(item.get("broadcast_audit") or "").startswith("rejected")
            ):
                # 分析结果 sidecar 中的拒绝标记同样视为拒绝终态
                rejected.append(item)
    return accepted, rejected, pending


def _reconcile_clip_with_authority(
    clip: dict[str, Any],
    room: Any,
) -> tuple[dict[str, Any] | None, str]:
    """当前录制 epoch 权威校验（recording_id + round_key + 当前 sidecar）。

    返回 (clip, "") 表示放行（可能已被 sidecar 终态边界覆盖）；
    返回 (None, 原因) 表示该切片不属于当前权威集合，必须跳过。

    规则：
    - 无 round_key 的手动切片不走本校验（沿用既有门禁）；
    - 切片自带 recording_id 且与房间当前录制不一致 → 旧录制会话切片；
    - 任务 rejected_round_keys / sidecar rejected 命中 → 已被审计拒绝；
    - sidecar accepted 命中 → 边界与审计字段以 sidecar 终态为准；
    - 存在权威来源（任务/sidecar）但 round_key 不在其中 → 旧会话遗留切片。
    """
    round_key = str(clip.get("round_key") or "")
    if not round_key:
        return clip, ""
    # 1) recording_id 隔离：切片自带 id 时必须与房间当前录制一致
    clip_recording_id = str(clip.get("recording_id") or "")
    room_recording_id = str(getattr(room, "recording_id", "") or "")
    if clip_recording_id and room_recording_id and clip_recording_id != room_recording_id:
        return None, "旧录制会话切片"
    task_state = _find_authority_task_state(str(clip.get("room_id") or ""))
    finalization, analysis = _load_recording_sidecars(room)
    has_authority = task_state is not None or finalization is not None or analysis is not None
    if not has_authority:
        # 无权威来源（纯手动切片 / 历史会话已完成且无 sidecar）：沿用既有门禁
        return clip, ""
    # 2) 任务级拒绝 tombstone
    rejected_keys = (task_state or {}).get("rejected_round_keys")
    if isinstance(rejected_keys, dict) and round_key in rejected_keys:
        reason = str(rejected_keys[round_key].get("reason") or "rejected")
        return None, f"已拒绝({reason})"
    accepted, rejected, pending = _sidecar_round_candidates(finalization, analysis, round_key)
    if rejected:
        audit = str(rejected[0].get("broadcast_audit") or rejected[0].get("broadcast_start_gate") or "rejected")
        return None, f"已拒绝({audit})"
    if accepted:
        # 终态权威：边界与审计字段以 sidecar accepted 为准（覆盖旧 listed 版本）
        terminal = dict(accepted[0])
        merged = dict(clip)
        for key in (
            "start",
            "end",
            "broadcast_audit",
            "start_by",
            "end_by",
            "start_confidence",
            "end_confidence",
            "start_quality",
            "end_quality",
            "start_review_required",
            "end_review_required",
            "duration_anomaly",
            "boundary_source",
            "boundary_quality",
            "source_profile",
            "confirm_status",
        ):
            if terminal.get(key) is not None:
                merged[key] = terminal[key]
        # 录制轴别名必须同步回填：`resolve_common_range` 优先读
        # recording_start_sec / recording_end_sec（前端 payload 就带这两个），
        # 只改 start/end 会让精修后的出点被前端旧值顶掉 —— L3 实测 round-000065：
        # 权威出点 789.75 被前端 801.5 覆盖，草稿带进 12s 赛后内容且无任何告警。
        if terminal.get("start") is not None:
            merged["recording_start_sec"] = float(terminal["start"])
        if terminal.get("end") is not None:
            merged["recording_end_sec"] = float(terminal["end"])
        return merged, ""
    # 3) 当前会话在列（listed_clips）
    listed = (task_state or {}).get("listed_clips")
    if isinstance(listed, dict):
        for listed_key, item in listed.items():
            if (
                str(listed_key) == round_key
                or str(listed_key).endswith(f":{round_key}")
            ) and isinstance(item, dict):
                return dict(item), ""
    # 4) sidecar pending（含 open_tail）仍属当前会话，放行走 include_pending 门禁
    if pending:
        return clip, ""
    # 5) 收录文件定格后的分析结果（会话已结束、任务已清）仍是权威来源
    if isinstance(analysis, dict):
        for item in analysis.get("highlights") or []:
            if isinstance(item, dict) and str(item.get("round_key") or "") == round_key:
                merged = dict(clip)
                for key in ("start", "end", "broadcast_audit", "end_by", "confirm_status"):
                    if item.get(key) is not None:
                        merged[key] = item[key]
                return merged, ""
    # 6) 存在权威来源（活跃/收尾任务、sidecar、分析结果 JSON）但 round_key 不在
    # listed_clips / accepted / rejected / pending / analysis 任何集合中 → 该切片
    # 不属于当前录制 epoch 的权威集合，一律跳过（spec §权威集合归属）。
    # 不得再按 recording_id 兜底放行：新入列切片在 clip_queued 时即已写入
    # listed_clips（步骤 3 可命中）；未命中说明它属于旧会话/脏数据，放行只会让
    # 「recording_id 恰好正确却不在权威集合」的旧切片混入草稿。
    return None, "旧分析会话遗留切片"

_ERROR_CODES = (
    "draft_dir_missing",
    "no_rooms",
    "no_aligned_context",
    "library_missing",
    "write_failed",
    "invalid_state",
    "recording_not_finalized",
    "no_usable_clips",
)

# 跳过原因码：对外稳定标识（日志 / 响应 / 前端共用）。文案可改，码不可改。
SKIP_REASON_REJECTED = "REJECTED"
SKIP_REASON_NEVER_AUDITED = "NEVER_AUDITED"
SKIP_REASON_NO_EXCLUSION_EVIDENCE = "NO_EXCLUSION_EVIDENCE"
SKIP_REASON_END_NOT_FINAL = "END_NOT_FINAL"
SKIP_REASON_NOT_IN_AUTHORITY = "NOT_IN_AUTHORITY"
SKIP_REASON_INTERIOR_BOUNDARY = "INTERIOR_BOUNDARY"
SKIP_REASON_DUPLICATE_ROUND = "DUPLICATE_ROUND"
SKIP_REASON_MAPPING_FAILED = "MAPPING_FAILED"
SKIP_REASON_NO_RECORDING = "NO_RECORDING"
# 超长分裂碎片的两条专用码。现场（2026-09-14）：044-s1 / 070-s0 被报成
# NO_EXCLUSION_EVIDENCE，读起来像"审计没找到出点=逻辑缺口"，实际是同族的真实回合
# 已由兄弟碎片导出（044-s0 出点 557.25 / 070-s1 出点 930.75）。诊断文案盖掉真相，
# 会让人去修一个不存在的 bug（本次排查就差点如此），故单列。
SKIP_REASON_SIBLING_OWNS_ROUND = "SIBLING_OWNS_ROUND"
SKIP_REASON_SUPERSEDED_BY_SPLIT_MERGE = "SUPERSEDED_BY_SPLIT_MERGE"


def _split_family_base_key(round_key: object) -> str:
    """``round-000070-s1`` → ``round-000070``；非分裂子块返回空串。

    与 `lsc.analyzer.valorant_broadcast._split_family_base_key` 用**同一实现**
    （那边负责合并，这边只用于解释跳过原因），避免两处解析规则漂移。
    """
    try:
        from lsc.analyzer.valorant_broadcast import (
            _split_family_base_key as _analyzer_split_base_key,
        )
    except Exception:  # pragma: no cover - 分析器不可用时退化为不做家族解释
        return ""
    return _analyzer_split_base_key(round_key)


def _split_fragment_index(round_key: object) -> int | None:
    """``round-000070-s1`` → 1；非分裂子块返回 None。"""
    key = str(round_key or "").strip()
    base, sep, suffix = key.rpartition("-s")
    if not sep or not suffix.isdigit() or not base:
        return None
    return int(suffix)


def _sibling_owns_round(
    clip: dict[str, Any],
    lookup: Callable[[str], dict[str, Any] | None] | None,
) -> dict[str, Any] | None:
    """本碎片是否为「同族真实回合已由兄弟碎片覆盖」的残余（返回那个兄弟）。

    只用于**解释**跳过原因，不参与任何放行判据：仍然跳过，只是原因可辨。
    """
    if lookup is None:
        return None
    index = _split_fragment_index(clip.get("round_key"))
    base = _split_family_base_key(clip.get("round_key"))
    if index is None or not base:
        return None
    # 邻居含**父键本身**：2026-09-14 真实会话实测，同族的真实回合常常是用父键
    # （`round-000071`，711.0-802.2 vision_confirmed）定稿的，分裂碎片 s0 只是它的
    # 早期投影；只看 `-s{N±1}` 会漏掉父键，把残余碎片报成 NEVER_AUDITED。
    neighbour_keys = [f"{base}-s{index + 1}", base]
    if index > 0:
        neighbour_keys.insert(0, f"{base}-s{index - 1}")
    for neighbour_key in neighbour_keys:
        try:
            sibling = lookup(neighbour_key)
        except Exception:  # pragma: no cover - 解释性查询不得影响门禁
            return None
        if not isinstance(sibling, dict):
            continue
        if str(sibling.get("broadcast_audit") or "").strip().lower() != "passed":
            continue
        if str(sibling.get("end_by") or "").strip().lower() not in _BROADCAST_VALID_END_BY:
            continue
        return sibling
    return None


def _skip_reason_code(
    clip: dict[str, Any],
    reason: str = "",
    *,
    sibling_lookup: Callable[[str], dict[str, Any] | None] | None = None,
) -> str:
    """把「被跳过」归一成结构化原因码。

    现场（2026-09-11 20:45）：5 条跳过共用一句「未确认/近似定位/未通过赛事审计」，
    无法分辨是"从未审计""审计跑完但无排除证据""审计过了但出点没定稿"还是"不在权威集合"。
    判定顺序与 `_broadcast_gate_passed` 一致，最后一项兜底为出点未定稿。

    ``sibling_lookup``：可选的后端权威查询（round_key → listed 条目）。传了才能识别
    「同族兄弟已覆盖该回合」这一类（见 SKIP_REASON_SIBLING_OWNS_ROUND）；不传则本
    函数保持纯函数语义，既有调用方与测试不受影响。
    """
    audit = str(clip.get("broadcast_audit") or "").strip().lower()
    status = str(clip.get("confirm_status") or "").strip().lower()
    text = str(reason or "")
    if audit == "rejected_interior_boundary":
        # 区间跨回合（起点落在上一回合内部）：单独成码，避免与普通拒绝混为一谈
        return SKIP_REASON_INTERIOR_BOUNDARY
    if audit == "rejected_duplicate_round":
        # 同一真实回合被多条候选认领（L2 择一后并入他条）：原因码可辨
        return SKIP_REASON_DUPLICATE_ROUND
    if status == "rejected" or audit.startswith("rejected"):
        return SKIP_REASON_REJECTED
    # 权威校验阶段判定的拒绝：文案是「非当前录制权威切片：已拒绝(...)」，而切片 dict
    # 自身可能仍带前端陈旧的 pending_lookahead（L3 实测会被误判成 NEVER_AUDITED）。
    if "已拒绝" in text:
        return SKIP_REASON_REJECTED
    if "旧分析会话遗留" in text or "无权威来源" in text or "旧录制会话" in text:
        return SKIP_REASON_NOT_IN_AUTHORITY
    if "映射" in text or "坐标" in text or "过短" in text or "重叠" in text:
        return SKIP_REASON_MAPPING_FAILED
    if "录制文件" in text:
        return SKIP_REASON_NO_RECORDING
    # 分裂族解释：本碎片已把起点/内容并入兄弟（合并标记），或兄弟已拥有该回合的权威
    # 出点。两者都比"出点证据不足"更贴近真相，故排在 NO_EXCLUSION_EVIDENCE 之前。
    if str(clip.get("superseded_by_round_key") or ""):
        return SKIP_REASON_SUPERSEDED_BY_SPLIT_MERGE
    if _sibling_owns_round(clip, sibling_lookup) is not None:
        return SKIP_REASON_SIBLING_OWNS_ROUND
    if audit == "pending_no_exclusion":
        return SKIP_REASON_NO_EXCLUSION_EVIDENCE
    if audit == "pending_lookahead" or not audit:
        return SKIP_REASON_NEVER_AUDITED
    # audit=passed 或其它终态却被门禁拦下 ⇒ 出点未定稿（next_prep/coarse/需复核/时长异常）
    return SKIP_REASON_END_NOT_FINAL


def _resolve_draft_root(settings: dict) -> tuple[str | None, bool]:
    """返回 (path, auto_detected)。"""
    configured = (settings.get("jianying_draft_dir") or "").strip()
    if configured:
        return configured, False
    detected = detect_jianying_draft_dir()
    return detected, True


def _room_display_name(room, room_id: str) -> str:
    name = getattr(room, "streamer_name", None) or ""
    return name or room_id[:8]


def _derive_room_deltas_from_clips(clips: list[dict]) -> dict[str, float]:
    """ctx 缺失时从切片内联坐标反推每房 recording→common delta。

    切片前端携带 start/end（录制坐标）与 common_start/common_end（公共轴），
    差值即该房间 recording_to_common_delta（同一房间多切片取均值）。
    """
    deltas: dict[str, list[float]] = {}
    for c in clips:
        rid = c.get("room_id")
        try:
            start = float(c.get("start"))
            common = float(c.get("common_start"))
        except (TypeError, ValueError):
            continue
        if not rid or start < 0 or common < 0:
            continue
        deltas.setdefault(rid, []).append(common - start)
    out: dict[str, float] = {}
    for rid, vals in deltas.items():
        out[rid] = sum(vals) / len(vals)
    return out


_AUTHORITATIVE_AUDIT_FIELDS = (
    "source_profile",
    "broadcast_audit",
    "broadcast_audit_reason",
    "broadcast_excluded_reason",
    "broadcast_review_required",
    "start_quality",
    "end_quality",
    "start_review_required",
    "end_review_required",
    "broadcast_result_tail_sec",
    "duration_anomaly",
    "end_by",
    "start_by",
    "boundary_source",
    "broadcast_model_version",
    "broadcast_model_provider",
    "model_version",
    # 分裂族合并来源：由后端权威给出（前端不得伪造），导出侧据此解释跨度与原因码。
    "split_merged",
    "split_merged_from",
    "split_merged_original_start",
    "superseded_by_round_key",
)


def _lookup_continuous_clip(clip: dict) -> dict[str, Any] | None:
    """从后端权威快照（listed_clips）找回完整赛事审计元数据。

    后端优先于前端：前端可以覆盖 start/end/confirm_status（人工复核），
    但不得伪造 source_profile / broadcast_audit / duration_anomaly 等审计字段。
    """
    rid = str(clip.get("room_id") or "")
    rk = str(clip.get("round_key") or clip.get("clip_id") or "")
    if not rid or not rk:
        return None
    primary_key = f"{rid}:{rk}"

    def _match_in_listed(listed: Any) -> dict[str, Any] | None:
        if isinstance(listed, dict):
            item = listed.get(primary_key)
            if isinstance(item, dict):
                return item
            for value in listed.values():
                if not isinstance(value, dict):
                    continue
                if (
                    str(value.get("room_id") or "") == rid
                    and (
                        str(value.get("round_key") or "") == rk
                        or str(value.get("clip_id") or "") == rk
                    )
                ):
                    return value
            return None
        if isinstance(listed, list):
            for value in listed:
                if not isinstance(value, dict):
                    continue
                if (
                    str(value.get("room_id") or "") == rid
                    and (
                        str(value.get("round_key") or "") == rk
                        or str(value.get("clip_id") or "") == rk
                    )
                ):
                    return value
        return None

    for task in _continuous_tasks.values():
        found = _match_in_listed(task.get("listed_clips"))
        if found:
            return found
    for job in _analysis_jobs.values():
        found = _match_in_listed(job.get("listed_clips"))
        if found:
            return found
    # 任务已结束：回落到终态权威快照（C1），否则会拿前端/旧 sidecar 的陈旧字段。
    snapshot = _authority_snapshots.get(rid)
    if isinstance(snapshot, dict):
        found = _match_in_listed(snapshot.get("listed_clips"))
        if found:
            return found
    return None


def _merge_authoritative_clip(clip: dict) -> dict:
    """把后端权威审计字段合并进前端切片 dict（后端字段优先）。"""
    merged = dict(clip)
    auth = _lookup_continuous_clip(clip)
    if not auth:
        return merged
    for key in _AUTHORITATIVE_AUDIT_FIELDS:
        if key in auth and auth[key] is not None:
            merged[key] = auth[key]
    # 后端权威坐标/round_key 优先，避免前端改动边界后漏改审计关系
    for key in ("round_key", "clip_id"):
        if key in auth and auth[key]:
            merged[key] = auth[key]
    return merged


def _heal_rooms_in_progress_recording(manager: Any, room_ids: list[str]) -> list[str]:
    """草稿生成前补齐「已停写但仍叫 *_录制中.mp4」的录像，返回改名后路径。

    为什么必须放在这里：草稿守卫（本文件 ``_collect_draft_inputs`` 的 in_progress 检查）
    只按**文件名/房间路径**判"仍在录制"，而录制器异常退出（实测 2026-09-12：直播源
    broken pipe 让录制 ffmpeg 死在写盘中途）时收尾改名不会跑 ⇒ 录像停在 ``_录制中`` 名 ⇒
    生成草稿被 ``recording_not_finalized`` 永久拒绝，用户只能重启应用（启动自愈才改名）。

    这里复用同一个自愈函数 ``heal_stale_in_progress_recordings``（判定规则完全一致：
    不在 active_paths 且 mtime 已停写 >60s），并把房间的 ``record_output_path``
    同步刷新成新名——守卫与草稿都读这个字段，不刷新等于没修。
    """
    try:
        from lsc.core.recording_layout import heal_stale_in_progress_recordings
    except Exception:  # noqa: BLE001 - 自愈不可用时保持旧行为（守卫照旧拦截）
        return []

    rooms = []
    for rid in room_ids:
        room = manager.get_room(rid)
        if room is not None:
            rooms.append(room)
    roots: list[str] = []
    active: list[str] = []
    for room in rooms:
        current = str(getattr(room, "record_output_path", "") or "")
        if not current:
            continue
        if getattr(room, "is_recording", False):
            active.append(current)
            continue
        roots.append(os.path.dirname(current) or ".")
        for attr in ("output_bundle_dir", "reconnect_output_dir"):
            value = str(getattr(room, attr, "") or "")
            if value:
                roots.append(value)
    if not roots:
        return []
    try:
        healed = heal_stale_in_progress_recordings(roots, active_paths=active)
    except Exception as exc:  # noqa: BLE001 - 自愈失败不得拦住草稿路径（守卫会给出原因码）
        _log.warning("草稿前录像定稿自愈失败: %s", exc)
        return []
    if not healed:
        return []
    healed_by_prefix = {
        os.path.basename(path).split("_至_", 1)[0]: path for path in healed
    }
    refreshed: list[str] = []
    for room in rooms:
        current = str(getattr(room, "record_output_path", "") or "")
        if not current:
            continue
        prefix = os.path.basename(current).split("_录制中", 1)[0]
        new_path = healed_by_prefix.get(prefix)
        if new_path:
            room.record_output_path = new_path  # type: ignore[assignment]
            refreshed.append(new_path)
    if refreshed:
        _log.warning(
            "草稿前已补齐未定稿录像（原名含 '_录制中' 会挡住草稿导出）: %s",
            ", ".join(os.path.basename(p) for p in refreshed),
        )
    return refreshed


def _room_recording_dirs(room: Any, settings: dict[str, Any] | None) -> list[str]:
    """房间录像可能的目录（用于在内存路径丢失后从磁盘找回最近录像）。"""
    dirs: list[str] = []
    current = str(getattr(room, "record_output_path", "") or "")
    if current:
        dirs.append(os.path.dirname(current) or ".")
    for attr in ("output_bundle_dir", "reconnect_output_dir"):
        value = str(getattr(room, attr, "") or "")
        if value:
            dirs.append(value)
    # App 把录像放在 <output_dir>/<主播名>/ 子目录里（room.name 即该目录名）
    root = str((settings or {}).get("output_dir") or "")
    name = str(getattr(room, "name", "") or "")
    if root and name:
        dirs.append(os.path.join(root, name))
        dirs.append(root)
    out: list[str] = []
    for d in dirs:
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def _resolve_room_recording_path(room: Any, settings: dict[str, Any] | None) -> str:
    """内存里的 ``record_output_path`` 不可用时，从磁盘找回该房间「最近一次录像」。

    为什么需要：应用重启后房间状态来自 ``rooms.json``，``record_output_path`` 可能是
    空串（录制中不落盘）——此时草稿既拿不到录像（``no_rooms``）、也拿不到权威切片集合。
    这里只做"文件存在性"筛选，不做任何边界/归属推断；epoch 正确性仍由
    ``_reconcile_clip_with_authority`` 按 recording_id + round_key + 当前 sidecar 兜底。
    """
    from lsc.utils.helpers import resolve_real_video_path

    current = str(getattr(room, "record_output_path", "") or "")
    if current:
        real = resolve_real_video_path(current)
        if real and os.path.isfile(real):
            return real
    best: tuple[float, str] = (0.0, "")
    for directory in _room_recording_dirs(room, settings):
        for pattern in ("*.finalization.json", "*_至_*.mp4", "*.mp4"):
            try:
                entries = list(Path(directory).glob(pattern))
            except OSError:
                continue
            for entry in entries:
                if "_录制中" in entry.name or "_in_progress" in entry.name:
                    continue
                video = entry if entry.suffix == ".mp4" else entry.with_suffix(".mp4")
                if not video.is_file():
                    continue
                try:
                    mtime = video.stat().st_mtime
                except OSError:
                    continue
                if mtime > best[0]:
                    best = (mtime, str(video))
            if best[1]:
                break  # 优先用最强候选：收尾 sidecar > 定稿录像 > 任意录像
        if best[1]:
            break
    if best[1]:
        room.record_output_path = best[1]  # type: ignore[assignment]
        _log.warning(
            "房间录像路径已从磁盘找回（重启后内存态为空，否则草稿会 no_rooms）: %s",
            os.path.basename(best[1]),
        )
    return best[1]


def _sidecar_authoritative_clips(room_id: str, room: Any) -> list[dict[str, Any]]:
    """读取该房间当前录像旁的收尾 sidecar，返回其**已定稿（含 manual_review）候选**。

    只读 ``accepted_candidates``：``rejected_candidates`` 永不入稿（与内存权威口径一致）。
    """
    path = str(getattr(room, "record_output_path", "") or "")
    if not path:
        return []
    try:
        job = load_finalization_job(path)
    except Exception as exc:  # noqa: BLE001 - sidecar 不可读时退化为空（不影响其它补全）
        _log.debug("读取收尾 sidecar 失败: %s", exc)
        return []
    if not isinstance(job, dict):
        return []
    out: list[dict[str, Any]] = []
    for item in job.get("accepted_candidates") or []:
        if isinstance(item, dict):
            merged = dict(item)
            merged.setdefault("room_id", room_id)
            out.append(merged)
    return out


def _collect_draft_inputs(
    manager,
    data: dict[str, Any],
    *,
    skipped_details: list[dict[str, Any]] | None = None,
    load_settings: Callable[[], dict] | None = None,
) -> tuple[dict[str, Any] | None, list[RoomDraftSource], list[ClipDraftSource], JianyingDraftOptions, list[str], int]:
    """装配房间源、切片源与选项。成功时首项为 None，失败时返回 error payload。

    末项 ``requested_clip_count`` 为请求切片数口径的唯一来源（前端内联 clips 优先，
    否则用 clip_ids），由调用方复用，避免重复计算导致口径漂移。

    ``skipped_details`` 由调用方提供可变列表：每条被跳过的切片都会追加
    ``{round_key, label, start, end, reason_code, reason}``，供响应与前端逐条展示
    （现场只有一句聚合告警，5 条跳过分不清是哪道门禁）。
    """
    warnings: list[str] = []
    skip_log: list[dict[str, Any]] = skipped_details if skipped_details is not None else []

    def _record_skip(clip: dict[str, Any], reason: str) -> None:
        skip_log.append({
            "round_key": str(clip.get("round_key") or clip.get("clip_id") or ""),
            "label": str(clip.get("label") or ""),
            "start": clip.get("start"),
            "end": clip.get("end"),
            "confirm_status": clip.get("confirm_status"),
            "broadcast_audit": clip.get("broadcast_audit"),
            "end_by": clip.get("end_by"),
            "end_quality": clip.get("end_quality"),
            "reason_code": _skip_reason_code(clip, reason),
            "reason": reason,
        })
    timeline_svc = get_timeline_service()

    room_ids = list(data.get("room_ids") or [])
    clip_ids = list(data.get("clip_ids") or [])
    requested_clip_count = len(data.get("clips") or []) or len(data.get("clip_ids") or [])
    raw_opt = data.get("options") or {}
    include_pending = bool(data.get("include_pending", False))
    options = JianyingDraftOptions(
        include_recordings=bool(raw_opt.get("include_recordings", True)),
        include_clips=bool(raw_opt.get("include_clips", True)),
        text_labels=bool(raw_opt.get("text_labels", True)),
        vertical=bool(raw_opt.get("vertical", False)),
        draft_name=str(raw_opt.get("draft_name") or ""),
        non_main_volume_zero=bool(raw_opt.get("non_main_volume_zero", False)),
        include_pending=include_pending,
    )
    labels = data.get("labels") or {}
    allow_single_fallback = bool(data.get("allow_single_fallback", False))

    all_rooms = manager.list_rooms()
    room_map = {r.room_id: r for r in all_rooms}

    if not room_ids:
        room_ids_resolved = list(room_map.keys())
    else:
        room_ids_resolved = room_ids

    # 先补齐「录制已停但仍叫 *_录制中.mp4」的孤儿录像（改名 + 同步 sidecar + 刷新房间路径），
    # 再走 in_progress 守卫；否则这类录像永远导不出草稿（详见 helper 文档）。
    _healed_paths = _heal_rooms_in_progress_recording(manager, list(room_ids_resolved))
    for _healed in _healed_paths:
        warnings.append(
            f"录像已补齐定稿改名（原含 '_录制中' 会挡住导出）: {os.path.basename(_healed)}"
        )
    # 重启后房间内存态可能没有录像路径（rooms.json 不落盘「录制中」路径）⇒ 从磁盘找回，
    # 否则草稿会以 no_rooms 失败、权威切片也无处可查。
    for _rid in list(room_ids_resolved):
        _room_for_path = room_map.get(_rid)
        if _room_for_path is None:
            continue
        _settings_now = {}
        if load_settings is not None:
            try:
                _settings_now = load_settings() or {}
            except Exception:  # noqa: BLE001 - 设置读取失败不影响其余装配
                _settings_now = {}
        if not _resolve_room_recording_path(_room_for_path, _settings_now):
            continue
        warnings.append(
            f"房间录像路径已从磁盘找回: {os.path.basename(_room_for_path.record_output_path)}"
        )

    ctx = None
    for rid in room_ids_resolved:
        ctx = timeline_svc.get_active_timeline_for_room(rid)
        if ctx is not None:
            break

    main_id = ctx.reference_room_id if ctx is not None else (
        room_ids_resolved[0] if room_ids_resolved else None
    )

    # ctx 缺失时（预览重启/画质切换后未重新对齐），尝试从切片内联坐标
    # （start/end 录制坐标 + common_start/end 公共轴）反推每房 delta 兜底。
    inline_clips = list(data.get("clips") or [])
    derived_deltas: dict[str, float] = {}
    if ctx is None:
        derived_deltas = _derive_room_deltas_from_clips(inline_clips)

    sources: list[RoomDraftSource] = []
    if ctx is not None:
        for rid in room_ids_resolved:
            snap = ctx.room_snapshots.get(rid)
            if snap is None:
                warnings.append(f"房间 {rid} 对齐置信不足或不在对齐组，已跳过")
                continue
            room = room_map.get(rid) or manager.get_room(rid)
            if room is None:
                continue
            sources.append(
                RoomDraftSource(
                    room_id=rid,
                    name=_room_display_name(room, rid),
                    record_output_path=getattr(room, "record_output_path", "") or "",
                    record_manifest_path=getattr(room, "record_manifest_path", "") or "",
                    recording_to_common_delta=float(snap.recording_to_common_delta),
                    is_main=(rid == main_id),
                )
            )
    else:
        if len(room_ids_resolved) > 1 and not allow_single_fallback:
            if not derived_deltas:
                return (
                    {
                        "success": False,
                        "error": "多房草稿需先一键对齐",
                        "error_code": "no_aligned_context",
                        "warnings": warnings,
                    },
                    [],
                    [],
                    options,
                    warnings,
                    requested_clip_count,
                )
            # 无对齐上下文但切片自带坐标：按切片反推的 delta 构建房间源
            warnings.append("未对齐，已按切片坐标反推各房间偏移生成草稿")
            main_id = str(data.get("main_room_id") or room_ids_resolved[0])
            for rid in room_ids_resolved:
                delta = derived_deltas.get(rid)
                if delta is None:
                    warnings.append(f"房间 {rid} 无切片坐标，已跳过")
                    continue
                room = room_map.get(rid) or manager.get_room(rid)
                if room is None:
                    continue
                sources.append(
                    RoomDraftSource(
                        room_id=rid,
                        name=_room_display_name(room, rid),
                        record_output_path=getattr(room, "record_output_path", "") or "",
                        record_manifest_path=getattr(room, "record_manifest_path", "") or "",
                        recording_to_common_delta=float(delta),
                        is_main=(rid == main_id),
                    )
                )
            if not sources:
                return (
                    {
                        "success": False,
                        "error": "多房草稿需先一键对齐",
                        "error_code": "no_aligned_context",
                        "warnings": warnings,
                    },
                    [],
                    [],
                    options,
                    warnings,
                    requested_clip_count,
                )
        else:
            rid = main_id or (room_ids_resolved[0] if room_ids_resolved else None)
            if not rid:
                return (
                    {
                        "success": False,
                        "error": "没有可用房间",
                        "error_code": "no_rooms",
                    },
                    [],
                    [],
                    options,
                    warnings,
                    requested_clip_count,
                )
            room = room_map.get(rid) or manager.get_room(rid)
            if room is None:
                return (
                    {
                        "success": False,
                        "error": "房间不存在",
                        "error_code": "no_rooms",
                    },
                    [],
                    [],
                    options,
                    warnings,
                    requested_clip_count,
                )
            warnings.append("未对齐，已降级为主房单房草稿")
            sources.append(
                RoomDraftSource(
                    room_id=rid,
                    name=_room_display_name(room, rid),
                    record_output_path=getattr(room, "record_output_path", "") or "",
                    record_manifest_path=getattr(room, "record_manifest_path", "") or "",
                    recording_to_common_delta=0.0,
                    is_main=True,
                )
            )

    in_progress_sources = []
    for source in sources:
        room = manager.get_room(source.room_id)
        path_name = os.path.basename(source.record_output_path or "")
        if (
            bool(getattr(room, "is_recording", False))
            or "_录制中" in path_name
            or "_in_progress" in path_name
        ):
            in_progress_sources.append(source.name)
    if in_progress_sources:
        return (
            {
                "success": False,
                "error": "录制文件尚未完成封装，请等待停止录制后再生成草稿",
                "error_code": "recording_not_finalized",
                "rooms": in_progress_sources,
                "warnings": warnings,
            },
            [],
            [],
            options,
            warnings,
            requested_clip_count,
        )

    # 权威补齐开关：当前草稿仅有「会话级全集」入口，默认开启；前端若显式传
    # fill_authoritative=false（如未来的单条/子集导出）则只处理前端给定切片。
    fill_authoritative = bool(data.get("fill_authoritative", True))
    room_ids_set = set(room_ids_resolved)
    clip_sources: list[ClipDraftSource] = []
    # 前端已覆盖的 round_key（无论该条最终是否通过门禁）：权威补全时不再重复补入。
    covered_round_keys: set[str] = set()

    def _make_clip_source(
        raw_c: dict[str, Any], *, honor_clip_ids: bool
    ) -> ClipDraftSource | None:
        """把一条切片 dict 过全部门禁后构造 ClipDraftSource；不通过返回 None。"""
        # 后端权威审计字段优先（失败关闭门禁不能只信前端）。
        raw_cid = raw_c.get("clip_id") or raw_c.get("clip_snapshot_id")
        c = _merge_authoritative_clip(raw_c)
        cid = c.get("clip_id") or c.get("clip_snapshot_id")
        # clip_id 是按边界派生的：审计定稿会把 end 往前裁，权威侧 clip_id 随之变化
        # （现场 round-000065：请求 `…_6515_8015` → 权威 `…_6515_7898`）。
        # 只比权威 id 会把"刚精修好"的切片当成"不在请求清单里"静默丢弃
        # （L3 实测 requested−included=6 而明细只有 5 条，缺的正是 065）。
        if honor_clip_ids and clip_ids and cid not in clip_ids and raw_cid not in clip_ids:
            _record_skip(c, "不在请求的切片清单内（clip_id 已随边界精修变化）")
            return None
        # 当前录制 epoch 权威校验：recording_id + round_key + 当前 sidecar。
        # 旧会话遗留/已被审计拒绝/旧版本边界的切片不得进入草稿。
        _clip_room = room_map.get(c.get("room_id"))
        if _clip_room is not None:
            _reconciled, _skip_reason = _reconcile_clip_with_authority(c, _clip_room)
            if _reconciled is None:
                label = raw_c.get("label") or cid or "切片"
                warnings.append(
                    f"切片 {label} 非当前录制权威切片（{_skip_reason or '无权威来源'}），已跳过"
                )
                # 用合并后的 c（仍在手上）记原因码；_reconciled 为 None 不能传
                _record_skip(c, f"非当前录制权威切片：{_skip_reason or '无权威来源'}")
                return None
            c = _reconciled
        if not clip_allowed_for_draft(c, include_pending=include_pending):
            label = c.get("label") or cid or "切片"
            code = _skip_reason_code(c)
            warnings.append(f"切片 {label} 未确认/近似定位/未通过赛事审计，已跳过（{code}）")
            _record_skip(c, "未通过草稿门禁")
            return None
        if include_pending and c.get("confirm_status") in ("pending", "refining"):
            label = c.get("label") or cid or "切片"
            warnings.append(f"切片 {label} 的边界仍待确认，已按暂定出点加入草稿")
        if ctx is None and derived_deltas:
            # 无 ctx 兜底：缺 common 坐标时用反推 delta 补全（start+delta）
            rid = c.get("room_id")
            delta = derived_deltas.get(rid) if rid else None
            if delta is not None and c.get("common_start") is None and c.get("start") is not None:
                try:
                    s = float(c["start"])
                    e = float(c["end"])
                    c["common_start"] = s + delta
                    c["common_end"] = e + delta
                except (TypeError, ValueError):
                    pass
        resolved = resolve_common_range(c, ctx)
        if resolved is None:
            label = c.get("label") or cid or "切片"
            warnings.append(f"切片 {label} 无法映射到公共轴，已跳过")
            _record_skip(c, "无法映射到公共轴")
            return None
        cs, ce, prec = resolved
        return ClipDraftSource(
            clip_id=str(cid or ""),
            common_start=cs,
            common_end=ce,
            label=str(c.get("label") or labels.get(str(cid), "") or "回合"),
            precision=prec,
            confirm_status=c.get("confirm_status"),
            room_id=str(c.get("room_id") or ""),
            source_profile=c.get("source_profile"),
            broadcast_audit=c.get("broadcast_audit"),
            broadcast_review_required=bool(c.get("broadcast_review_required")),
            start_quality=c.get("start_quality"),
            end_quality=c.get("end_quality"),
            start_review_required=bool(c.get("start_review_required")),
            end_review_required=bool(c.get("end_review_required")),
            duration_anomaly=bool(c.get("duration_anomaly")),
            end_by=c.get("end_by"),
        )

    if inline_clips:
        for raw_c in inline_clips:
            _rk = str(raw_c.get("round_key") or "")
            if _rk:
                covered_round_keys.add(_rk)
            src = _make_clip_source(raw_c, honor_clip_ids=True)
            if src is not None:
                clip_sources.append(src)
    elif clip_ids:
        for cid in clip_ids:
            snap = timeline_svc.get_clip_snapshot(cid)
            if snap is None:
                warnings.append(f"切片 {cid} 不存在或已过期，已跳过")
                continue
            clip_sources.append(
                ClipDraftSource(
                    clip_id=snap.clip_id,
                    common_start=snap.common_start,
                    common_end=snap.common_end,
                    label=str(labels.get(cid) or "回合"),
                    precision="exact",
                    confirm_status="user_confirmed",
                    room_id=str(snap.room_id or ""),
                )
            )

    # 权威集合补全（spec §数据源优先级）：前端列表可能遗漏当前录制 epoch 的
    # 权威回合（断连重载、clip_queued 事件丢失）。后端 listed_clips 是当前会话
    # 实时权威快照，按其补建切片源，避免漏写已入列的回合。补入项同样过全部门禁
    # （rejected/pending 不会被复活），并只补本次请求覆盖的房间。
    #
    # 2026-09-12 起分两级：① 内存权威（实时会话，listed_clips）；② **磁盘权威**
    # （录像旁 {stem}.finalization.json）。② 的存在理由：应用一重启内存注册表全空，
    # 而前端切片列表只由 clip_queued 驱动、store 明确不消费 listed_clips，
    # 于是列表既补不出来也重建不了 ⇒ 草稿只能建出"整段录像、零切片"。
    # 两级都用同一个 `_make_clip_source`（同门禁 + 同 epoch 对账），不放宽任何判据。
    authoritative_filled = 0
    if options.include_clips and fill_authoritative:
        seen_listed_keys: set[str] = set()
        _memory_truth_sources: list[Any] = [
            *_continuous_tasks.values(), *_analysis_jobs.values(), *_authority_snapshots.values(),
        ]
        _has_live_authority = False
        for _task_state in _memory_truth_sources:
            _listed = (_task_state or {}).get("listed_clips")
            if isinstance(_listed, dict) and _listed:
                _has_live_authority = True
        for _task_state in _memory_truth_sources:
            _listed = (_task_state or {}).get("listed_clips")
            if not isinstance(_listed, dict):
                continue
            for _listed_key, _listed_item in _listed.items():
                _key = str(_listed_key)
                if _key in seen_listed_keys or not isinstance(_listed_item, dict):
                    continue
                seen_listed_keys.add(_key)
                _rk = str(_listed_item.get("round_key") or "")
                if not _rk or _rk in covered_round_keys:
                    continue
                if room_ids_set and str(_listed_item.get("room_id") or "") not in room_ids_set:
                    continue
                covered_round_keys.add(_rk)
                src = _make_clip_source(_listed_item, honor_clip_ids=False)
                if src is not None:
                    clip_sources.append(src)
                    warnings.append(
                        f"切片 {src.label} 由后端权威快照补入草稿（前端列表未覆盖）"
                    )
        # ② 磁盘权威：只在**内存权威完全为空**时启用（应用刚重启：任务态/快照都没了）。
        # 正常实时会话不读盘——避免旧 sidecar 的残留候选混进来，也避免重复劳动。
        for _rid in [] if _has_live_authority else list(room_ids_resolved):
            _room_for_sidecar = room_map.get(_rid)
            if _room_for_sidecar is None:
                continue
            for _disk_item in _sidecar_authoritative_clips(_rid, _room_for_sidecar):
                _rk = str(_disk_item.get("round_key") or "")
                if not _rk or _rk in covered_round_keys:
                    continue
                if room_ids_set and _rid not in room_ids_set:
                    continue
                covered_round_keys.add(_rk)
                authoritative_filled += 1
                src = _make_clip_source(_disk_item, honor_clip_ids=False)
                if src is not None:
                    clip_sources.append(src)
                    warnings.append(
                        f"切片 {src.label} 由收尾 sidecar 补入草稿（会话列表未覆盖）"
                    )
        if authoritative_filled:
            # 补入条数不改 requested_clip_count（该口径 = 调用方请求条数，前端对账依赖它
            # 满足 requested == included + skipped）；单独用告警说明"额外补入 N 条"。
            warnings.append(
                f"共 {authoritative_filled} 条切片由后端权威补入草稿"
                f"（内存快照/收尾 sidecar，前端列表未覆盖）"
            )

    if options.include_clips and requested_clip_count > 0 and not clip_sources:
        return (
            {
                "success": False,
                "error": "所有切片均未通过草稿门禁，未生成空切片草稿",
                "error_code": "no_usable_clips",
                "requested_clip_count": requested_clip_count,
                "included_clip_count": 0,
                "skipped_clip_count": requested_clip_count,
                "warnings": warnings,
            },
            [],
            [],
            options,
            warnings,
            requested_clip_count,
        )

    return None, sources, clip_sources, options, warnings, requested_clip_count


def register_jianying_handlers(
    server,
    *,
    bridge,
    manager,
    load_settings: Callable[[], dict],
    continuous_tasks: dict | None = None,
    analysis_jobs: dict | None = None,
    authority_snapshots: dict | None = None,
) -> None:
    """注册剪映相关 WS handlers。

    ⚠️ 三个权威注册表必须由 room_handler 注入：不注入时它们保持各自的空 dict，
    listed_clips 权威补全与终态快照都会静默失效（草稿只能靠 sidecar 回落）。
    """
    global _continuous_tasks, _analysis_jobs, _authority_snapshots
    if continuous_tasks is not None:
        _continuous_tasks = continuous_tasks
    if analysis_jobs is not None:
        _analysis_jobs = analysis_jobs
    if authority_snapshots is not None:
        _authority_snapshots = authority_snapshots

    @server.on("get_jianying_draft_dir")
    async def handle_get_jianying_draft_dir(data: dict[str, Any] | None):
        settings = load_settings()
        path, auto = _resolve_draft_root(settings)
        exists = bool(path and os.path.isdir(path))
        return {
            "success": True,
            "draft_dir": path or "",
            "auto_detected": auto and bool(path),
            "exists": exists,
        }

    @server.on("generate_jianying_draft")
    async def handle_generate_jianying_draft(data: dict[str, Any] | None):
        data = data or {}
        settings = load_settings()
        draft_root, _auto = _resolve_draft_root(settings)
        if not draft_root or not os.path.isdir(draft_root):
            return {
                "success": False,
                "error": "剪映草稿目录未找到，请到设置页配置",
                "error_code": "draft_dir_missing",
            }

        skipped_details: list[dict[str, Any]] = []
        err, sources, clip_sources, options, warnings, requested_clip_count = (
            _collect_draft_inputs(
                manager, data, skipped_details=skipped_details, load_settings=load_settings,
            )
        )
        if err is not None:
            # 失败响应也要带上逐条原因：no_usable_clips 时用户才知道是"全被拦"还是"全不在权威集合"
            err.setdefault("skipped", skipped_details)
            return err

        loop = asyncio.get_running_loop()

        def _run():
            return build_session_draft(
                rooms=sources,
                clips=clip_sources if options.include_clips else [],
                options=options,
                draft_root=draft_root,
            )

        result = await loop.run_in_executor(None, _run)
        # included_clip_count 必须按「实际写入切片轨」的数量统计：
        # gate 通过但被同轨重叠/越界丢弃的切片属于 skipped，而非 included。
        included_clip_count = (
            result.placed_clip_count if options.include_clips else 0
        )
        # 导出器内部筛掉的切片也要进明细：否则 requested−included 的差额无法逐条对账
        # （L3 实测 6 vs 5，缺的那条在导出器里被过滤且只留下一个本地计数）。
        for item in getattr(result, "excluded_clips", []) or []:
            skipped_details.append({
                "round_key": str(item.get("round_key") or item.get("clip_id") or ""),
                "label": item.get("label"),
                "start": item.get("start"),
                "end": item.get("end"),
                "confirm_status": item.get("confirm_status"),
                "broadcast_audit": item.get("broadcast_audit"),
                "end_by": item.get("end_by"),
                "end_quality": item.get("end_quality"),
                "reason_code": item.get("reason_code") or "EXCLUDED_BY_SOURCE_FILTER",
                "reason": item.get("reason") or "导出器过滤",
            })
        skipped_clip_count = max(0, requested_clip_count - included_clip_count)
        return {
            "success": result.success,
            "draft_name": result.draft_name,
            "draft_dir": result.draft_dir,
            "tracks": result.tracks,
            "segments": result.segments,
            "requested_clip_count": requested_clip_count,
            "included_clip_count": included_clip_count,
            "skipped_clip_count": skipped_clip_count,
            # 逐条跳过明细（round_key + 区间 + reason_code）：只有一句聚合告警时
            # 用户无法分辨是哪道门禁拦的（现场 5 条跳过、标签还重名）。
            "skipped": skipped_details,
            # 残差必须显式暴露：明细数 < 计数 = 还有没留痕的丢弃分支
            # （L3 的 065 就是靠它才被发现的）。恒等 0 才算对账干净。
            "skipped_unaccounted": max(0, skipped_clip_count - len(skipped_details)),
            "warnings": list(warnings) + list(result.warnings),
            "error": result.error,
            "error_code": result.error_code,
        }
