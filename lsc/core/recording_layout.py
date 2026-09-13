"""录制与切片的磁盘目录约定。

单房间：``{output_dir}/{主播名}/``，多次开录复用，不因目录已存在而加 ``_1``。
对齐成功后：``{output_dir}/{主播A}+{主播B}/{主播名}/``，切片立即写入；
录像在 FFmpeg 停录后再改名为「开始至结束」并搬进该目录。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_log = logging.getLogger(__name__)

_ILLEGAL_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_IN_PROGRESS_SUFFIX = "_录制中.mp4"

# 与录像同名的 sidecar 后缀。录像定稿改名（``*_录制中.mp4`` → ``*_至_*.mp4``）时
# 必须同步改名，否则读取方按最终录像名查 ``{stem}.analysis.json`` /
# ``{stem}.finalization.json`` 会落空——剪映导出的权威对账依赖分析 sidecar，
# 落空会让「被拒切片」重新混入草稿。
#
# ⚠️ 本列表是 sidecar 后缀的单一事实来源，必须与
# ``python-backend/persistence.py`` 的 ``_analysis_json_path`` /
# ``_finalization_json_path`` 保持一致；由
# ``tests/test_recording_layout_wiring.py`` 守卫（不一致会导致改名漏项）。
SIDECAR_SUFFIXES: tuple[str, ...] = (
    ".analysis.json",
    ".finalization.json",
    ".finalization.json.bak",
)


def move_recording_sidecars(src_path: str, dest_path: str) -> list[str]:
    """把与录像同名的 sidecar 一起改名，维持 ``{stem}.*`` 契约。

    只搬**实际存在**的 sidecar；单项失败仅告警，绝不影响录像本身的定稿结果
    （sidecar 缺失可由后续收尾扫描重建，录像名不一致则会破坏读取契约）。
    返回成功改名后的新路径列表。
    """
    if not src_path or not dest_path:
        return []
    src_stem = os.path.splitext(src_path)[0]
    dest_stem = os.path.splitext(dest_path)[0]
    if src_stem == dest_stem:
        return []
    moved: list[str] = []
    for suffix in SIDECAR_SUFFIXES:
        old = src_stem + suffix
        if not os.path.isfile(old):
            continue
        new = dest_stem + suffix
        try:
            os.replace(old, new)
        except OSError as exc:
            _log.warning(
                "sidecar 随录像改名失败 old=%s: %s", os.path.basename(old), exc
            )
            continue
        moved.append(new)
    if moved:
        _log.info(
            "sidecar 已随录像定稿改名 %d 项: %s",
            len(moved),
            ", ".join(os.path.basename(p) for p in moved),
        )
    return moved


# 录制镜像（本地回看数据源）随主录像改名的后缀。镜像名 = 主录像去扩展名 + 该后缀，
# 所以定稿改名（*_录制中.mp4 -> *_至_*.mp4）时镜像必须同步改名，否则前端按新的
# record_output_path 推导出的 .dvr.mp4 路径会落空（方案 A「录制中回看」失效）。
DVR_MIRROR_SUFFIX = ".dvr.mp4"


def dvr_mirror_path(record_path: str) -> str:
    """由录像路径推导录制镜像路径：<录像路径去扩展名>.dvr.mp4。"""
    if not record_path:
        return ""
    stem, _ext = os.path.splitext(record_path)
    if not stem:
        return ""
    return f"{stem}{DVR_MIRROR_SUFFIX}"


def move_dvr_mirror(src_path: str, dest_path: str) -> str:
    """把录制镜像随主录像一起改名，返回新镜像路径（缺失/失败返回空串）。

    镜像只是回看数据源，改名失败只告警、绝不影响录像本身的定稿结果（回看可回退到
    直接播主录像文件）。
    """
    src_mirror = dvr_mirror_path(src_path)
    dest_mirror = dvr_mirror_path(dest_path)
    if not src_mirror or not dest_mirror or src_mirror == dest_mirror:
        return ""
    if not os.path.isfile(src_mirror):
        return ""
    try:
        os.replace(src_mirror, dest_mirror)
    except OSError as exc:
        _log.warning(
            "录制镜像随录像改名失败 old=%s: %s", os.path.basename(src_mirror), exc
        )
        return ""
    _log.info(
        "录制镜像已随录像定稿改名: %s -> %s",
        os.path.basename(src_mirror), os.path.basename(dest_mirror),
    )
    return dest_mirror

def sanitize_folder_name(name: str, *, fallback: str = "room", max_len: int = 40) -> str:
    text = _ILLEGAL_FS.sub("_", (name or "").strip())
    text = re.sub(r"_+", "_", text).strip(" ._")
    if not text:
        return fallback
    return text[:max_len]


def streamer_folder_name(
    *,
    streamer_name: str = "",
    stream_title: str = "",
    room_id: str = "",
) -> str:
    if streamer_name.strip():
        return sanitize_folder_name(streamer_name)
    if stream_title.strip():
        return sanitize_folder_name(stream_title)
    short = (room_id or "room")[-6:]
    return sanitize_folder_name(short, fallback="room")


def bundle_folder_name(streamer_names: list[str]) -> str:
    seen: list[str] = []
    for raw in streamer_names:
        name = sanitize_folder_name(raw) if raw else ""
        if not name or name in seen:
            continue
        seen.append(name)
    if not seen:
        return "sync"
    return "+".join(seen)


def room_recording_dir(
    base_dir: str,
    *,
    streamer_name: str = "",
    stream_title: str = "",
    room_id: str = "",
    bundle_dir: str = "",
) -> str:
    leaf = streamer_folder_name(
        streamer_name=streamer_name,
        stream_title=stream_title,
        room_id=room_id,
    )
    parent = bundle_dir if bundle_dir else base_dir
    return os.path.join(parent, leaf)


def recording_in_progress_filename(started_at: datetime) -> str:
    return started_at.strftime("%Y-%m-%d_%H-%M-%S") + _IN_PROGRESS_SUFFIX


def recording_in_progress_path(output_dir: str, started_at: datetime) -> str:
    os.makedirs(output_dir, exist_ok=True)
    return ensure_unique_path(os.path.join(output_dir, recording_in_progress_filename(started_at)))


# 「孤儿录制」：录制已结束、但收尾改名失败留下的 `*_录制中.mp4`。
# 成因：`_finalize_and_commit_recording` 的 P1 契约规定"源被占用时宁可没改名也绝不
# 留重复副本" —— 于是改名失败就**保留 `_录制中` 名并告警**。本身没错，但下游
# （剪映草稿的 `_录制中` 守卫，见 python-backend/handlers/jianying_handlers.py）
# 只按**文件名**判"仍在录制"，于是这段录像**永远导不出草稿**，且因为
# `is_recording` 已是 False，用户也无法靠"停止录制"再触发一次。
# 2026-09-11 实测现场：录制 07:41:32→07:52:47（history 已写结束时间），文件仍是
# `_录制中`，两次生成草稿都被拒（error_code=recording_not_finalized）。
_STALE_MIN_IDLE_SEC = 60.0
_IN_PROGRESS_STEM_FORMAT = "%Y-%m-%d_%H-%M-%S"


def stale_in_progress_recordings(
    dirs: Iterable[str],
    *,
    active_paths: Iterable[str] = (),
    now: datetime | None = None,
    min_idle_sec: float = _STALE_MIN_IDLE_SEC,
) -> list[tuple[Path, datetime, datetime]]:
    """找出「已停写但仍是 `_录制中` 名」的录像。

    返回 ``[(path, started_at, ended_at)]``：
    - ``started_at`` 取文件名前缀里的 ``%Y-%m-%d_%H-%M-%S``（程序命名依据）；
    - ``ended_at`` 取**文件 mtime**——录制器停写的那一刻，比"现在"更接近真实结束时间。

    判定为"已停写"：不在 ``active_paths``（仍在录的房间路径）里，且 mtime 距 ``now``
    已超过 ``min_idle_sec``（默认 60s，避免把正在写的文件误判成孤儿）。
    """
    moment = now or datetime.now()
    active = {os.path.normcase(os.path.abspath(str(p))) for p in active_paths if p}
    found: list[tuple[Path, datetime, datetime]] = []
    seen_dirs: set[str] = set()
    for raw in dirs:
        if not raw:
            continue
        directory = os.path.abspath(str(raw))
        key = os.path.normcase(directory)
        if key in seen_dirs or not os.path.isdir(directory):
            continue
        seen_dirs.add(key)
        # **必须递归**：App 把录像放在 `<root>/<主播名>/` 子目录里（实测孤儿就在
        # `~/LSC/output/EDG夺冠回顾/` 下），只扫顶层会永远找不到 → 自愈形同虚设。
        # 限深 4 层，避免误扫到用户自己嵌套很深的目录。
        for path in sorted(Path(directory).rglob(f"*{_IN_PROGRESS_SUFFIX}")):
            try:
                if len(path.relative_to(directory).parts) > 4:
                    continue
            except ValueError:
                continue
            if os.path.normcase(str(path.resolve())) in active:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            ended_at = datetime.fromtimestamp(stat.st_mtime)
            if (moment - ended_at).total_seconds() < min_idle_sec:
                continue
            started_at = _in_progress_started_at(path, ended_at)
            found.append((path, started_at, ended_at))
    return found


def _in_progress_started_at(path: Path, fallback: datetime) -> datetime:
    """从 ``*_录制中.mp4`` 的文件名前缀取开始时间，失败则回退。"""
    try:
        return datetime.strptime(path.name[: -len(_IN_PROGRESS_SUFFIX)], _IN_PROGRESS_STEM_FORMAT)
    except ValueError:
        return fallback


def finalize_in_progress_recording(
    path: str | os.PathLike[str],
    *,
    min_idle_sec: float = 3.0,
    now: datetime | None = None,
) -> str | None:
    """把**单个**仍在 ``_录制中`` 名的录像定稿（改名 + sidecar）；不适合则返回 None。

    与 ``heal_stale_in_progress_recordings`` 的区别：那个是**启动期**批量兜底，
    这个是**切换点**即时收尾。用于录制 epoch 轮转（重连/换段）——实测该路径会直接
    开新文件而把旧文件一直留在 ``_录制中``：
    - 剪映草稿守卫按文件名判"仍在录制" → 旧录像**永远导不出草稿**；
    - 持续分析每 2s 刷一条"跳过旧文件空扫描结果（文件已切换）"的告警。
    原先只能靠"下次启动自愈"事后补救。

    ``min_idle_sec`` 内仍被写入（mtime 很新）的文件视为**正在录**，不动它——避免把
    并发写同一文件的进程剪断。
    """
    if not path:
        return None
    target = Path(path)
    if not target.is_file() or not target.name.endswith(_IN_PROGRESS_SUFFIX):
        return None
    try:
        ended_at = datetime.fromtimestamp(target.stat().st_mtime)
    except OSError:
        return None
    if ((now or datetime.now()) - ended_at).total_seconds() < min_idle_sec:
        return None
    started_at = _in_progress_started_at(target, ended_at)
    dest = finalize_recording_file(
        str(target), started_at=started_at, ended_at=ended_at, dest_dir=str(target.parent)
    )
    move_recording_sidecars(str(target), dest)
    _log.info(
        "换段定稿: %s -> %s（起 %s / 止 %s）",
        target.name, Path(dest).name,
        started_at.strftime(_IN_PROGRESS_STEM_FORMAT), ended_at.strftime(_IN_PROGRESS_STEM_FORMAT),
    )
    return dest


def heal_stale_in_progress_recordings(
    dirs: Iterable[str],
    *,
    active_paths: Iterable[str] = (),
    now: datetime | None = None,
    min_idle_sec: float = _STALE_MIN_IDLE_SEC,
) -> list[str]:
    """把「孤儿录制」补齐收尾（改名 + 同步 sidecar），返回新的录像路径。

    单项失败只告警、不影响其它项——这是启动期自愈，**绝不能因此拦住启动**。
    """
    healed: list[str] = []
    for path, started_at, ended_at in stale_in_progress_recordings(
        dirs, active_paths=active_paths, now=now, min_idle_sec=min_idle_sec
    ):
        try:
            dest = finalize_recording_file(
                str(path),
                started_at=started_at,
                ended_at=ended_at,
                dest_dir=str(path.parent),
            )
            move_recording_sidecars(str(path), dest)
            healed.append(dest)
            _log.info(
                "孤儿录制已补齐收尾: %s -> %s（起 %s / 止 %s）",
                path.name, Path(dest).name,
                started_at.strftime(_IN_PROGRESS_STEM_FORMAT),
                ended_at.strftime(_IN_PROGRESS_STEM_FORMAT),
            )
        except (OSError, ValueError) as exc:
            _log.warning("孤儿录制收尾失败（保留原名，稍后可重试）: %s (%s)", path, exc)
    return healed


def finalize_room_recording(room: Any, source_path: str, *, ended_at: datetime | None = None) -> str:
    started_at = getattr(room, "record_started_at", None) or datetime.now()
    ended = ended_at or datetime.now()
    bundle = str(getattr(room, "output_bundle_dir", "") or "")
    if bundle:
        dest_dir = recording_dest_dir(room, bundle)
    else:
        dest_dir = os.path.dirname(source_path) or "."
    return finalize_recording_file(
        source_path,
        started_at=started_at,
        ended_at=ended,
        dest_dir=dest_dir,
    )


def recording_final_filename(started_at: datetime, ended_at: datetime) -> str:
    return (
        f"{started_at.strftime('%Y-%m-%d_%H-%M-%S')}"
        f"_至_"
        f"{ended_at.strftime('%Y-%m-%d_%H-%M-%S')}.mp4"
    )


def ensure_unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    suffix = 1
    candidate = f"{stem}_{suffix}{ext}"
    while os.path.exists(candidate):
        suffix += 1
        candidate = f"{stem}_{suffix}{ext}"
    return candidate


def resolve_clip_output_dir(room: Any, base_dir: str) -> str:
    bundle = str(getattr(room, "output_bundle_dir", "") or "")
    dest = room_recording_dir(
        base_dir,
        streamer_name=getattr(room, "streamer_name", "") or "",
        stream_title=getattr(room, "stream_title", "") or "",
        room_id=getattr(room, "room_id", "") or "",
        bundle_dir=bundle,
    )
    os.makedirs(dest, exist_ok=True)
    return dest


def bind_rooms_to_bundle(rooms: list[Any], base_dir: str) -> str:
    names = [
        streamer_folder_name(
            streamer_name=getattr(room, "streamer_name", "") or "",
            stream_title=getattr(room, "stream_title", "") or "",
            room_id=getattr(room, "room_id", "") or "",
        )
        for room in rooms
    ]
    bundle_dir = os.path.join(base_dir, bundle_folder_name(names))
    os.makedirs(bundle_dir, exist_ok=True)
    for room in rooms:
        room.output_bundle_dir = bundle_dir
        os.makedirs(
            room_recording_dir(
                base_dir,
                streamer_name=getattr(room, "streamer_name", "") or "",
                stream_title=getattr(room, "stream_title", "") or "",
                room_id=getattr(room, "room_id", "") or "",
                bundle_dir=bundle_dir,
            ),
            exist_ok=True,
        )
    return bundle_dir


def recording_dest_dir(room: Any, base_dir: str) -> str:
    bundle = str(getattr(room, "output_bundle_dir", "") or "")
    dest = room_recording_dir(
        base_dir,
        streamer_name=getattr(room, "streamer_name", "") or "",
        stream_title=getattr(room, "stream_title", "") or "",
        room_id=getattr(room, "room_id", "") or "",
        bundle_dir=bundle,
    )
    os.makedirs(dest, exist_ok=True)
    return dest


def _is_cross_device(exc: OSError) -> bool:
    """判断 OSError 是否为「跨盘」错误（只有这种情况才必须复制+删源）。"""
    import errno

    if exc.errno == errno.EXDEV:
        return True
    # Windows: ERROR_NOT_SAME_DEVICE
    return getattr(exc, "winerror", None) == 17


def finalize_recording_file(
    source_path: str,
    *,
    started_at: datetime,
    ended_at: datetime,
    dest_dir: str,
) -> str:
    """把「录制中」文件改名为「起_至_止」定稿名。

    ⚠️ 幂等性契约（P1 修复）：**源被占用时绝不回退为复制**。
    原实现直接用 ``shutil.move``：Windows 上若源被其他进程占用（录制 FFmpeg 尚未
    释放句柄，或并发分析/预览正读该录像），``os.rename`` 抛 ``PermissionError`` →
    ``shutil`` 回退 ``copy2``（复制成功）+ ``unlink``（源仍被占用→失败）→ 抛异常。
    调用方 ``orchestrator._finalize_and_commit_recording`` 有 3 次重试，且每次都取
    ``datetime.now()`` 作结束时刻（名字各不相同），于是**每重试一次就多留下一份完整
    副本**——实测一次退出留下 3 份 1045MB 的同内容录像。故此处改为：

    1. 优先 ``os.replace``（原子改名；同盘不会在失败时留下任何副本）；
    2. 仅在**确属跨盘**时才复制+删源，且删源失败必须回滚已复制的目标；
    3. 其余 OSError（源被占用等）直接抛出，交由调用方在句柄释放后重试。
    """
    if not source_path:
        return source_path
    os.makedirs(dest_dir, exist_ok=True)
    dest = ensure_unique_path(
        os.path.join(dest_dir, recording_final_filename(started_at, ended_at))
    )
    if os.path.abspath(source_path) == os.path.abspath(dest):
        return dest
    if not os.path.isfile(source_path):
        return source_path
    try:
        os.replace(source_path, dest)
        move_recording_sidecars(source_path, dest)
        move_dvr_mirror(source_path, dest)
        return dest
    except OSError as exc:
        if not _is_cross_device(exc):
            raise
    # 跨盘：只能复制 + 删源。
    shutil.copy2(source_path, dest)
    try:
        os.unlink(source_path)
    except OSError:
        # 删源失败必须回滚目标，否则调用方重试会累积出多份完整副本。
        try:
            os.unlink(dest)
        except OSError as cleanup_exc:  # noqa: BLE001
            _log.warning("回滚未完成的定稿副本失败 path=%s: %s", dest, cleanup_exc)
        raise
    move_recording_sidecars(source_path, dest)
    move_dvr_mirror(source_path, dest)
    return dest
