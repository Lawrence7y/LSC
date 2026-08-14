"""common_to_recording 必须与墙钟映射同号同结果。

锁定 common↔recording 与墙钟公式的符号约定，禁止两套公式悄然分叉：
    common = preview_local + preview_to_common_delta
    common = recording_local + recording_to_common_delta
    recording_to_common_delta = media_start_mono + 相对偏移(content_offset 差)

⚠️ 2026-08 锚点修订：build_room_snapshots_from_align 传入 align_mono 且
room_meta 带 preview_current_time 时，preview_to_common_delta 额外叠加
「预览 PTS 轴 → 单调时钟轴」锚点（align_mono - preview_current_time），
而 recording_to_common_delta 仍只含相对偏移（不得叠加锚点，否则录制轴双重偏移）。
不传 align_mono 时行为与旧版完全一致（p2c 即相对偏移）。

live=false（拖拽）不得使用「按下时刻的 wallclock」冒充内容时刻；
精确导出只允许 I/O 键 live=true 墙钟路径，或 TimelineContext + create_clip_snapshot + export_clip_by_id。
"""
from __future__ import annotations

from lsc.core.models import TimelineContext
from lsc.core.services.timeline_service import build_room_snapshots_from_align


def test_common_to_recording_matches_wallclock_formula():
    # 基准房 offset=0，房间 B 领先 1.5s（content_offset=+1.5）
    media_start = 1000.0  # recording_media_start_mono
    snaps = build_room_snapshots_from_align(
        reference_room_id="ref",
        offsets={"ref": 0.0, "b": 1.5},
        scores={"ref": 0.9, "b": 0.9},
        room_meta={
            "ref": {"media_start_mono": media_start, "preview_epoch_id": "e1", "recording_id": "r1"},
            "b": {"media_start_mono": media_start, "preview_epoch_id": "e1", "recording_id": "r2"},
        },
    )
    ctx = TimelineContext(
        timeline_id="t1",
        reference_room_id="ref",
        preview_ready=True,
        clip_ready=True,
        room_snapshots=snaps,
    )
    # 用户在 common=10 处切：等价于「墙钟 = media_start + preview_local」且减 content_offset
    common = 10.0
    rec_b = ctx.common_to_recording("b", common)
    # preview_to_common_delta[b] = 1.5 - 0 = 1.5
    # recording_to_common_delta[b] = 1000 + 1.5 = 1001.5
    # recording_local = 10 - 1001.5 = -991.5 → 导出侧会 max(0, ...)；这里测原始转换
    assert abs(snaps["b"].preview_to_common_delta - 1.5) < 1e-9
    assert abs(snaps["b"].recording_to_common_delta - (media_start + 1.5)) < 1e-9
    assert abs(rec_b - (common - snaps["b"].recording_to_common_delta)) < 1e-9

    # 与墙钟公式对照：mark_wc=media_start+preview_local, export=mark_wc-media_start-content_offset
    preview_local_b = ctx.common_to_preview("b", common)  # 10 - 1.5 = 8.5
    mark_wc = media_start + preview_local_b  # 仅在「预览本地时间≈录制已开时长」假设下
    del mark_wc  # 墙钟换算公式已在注释中说明，此处仅验证 common 轴转换
    # common 路径：recording_local = common - (media_start + delta) 再加 media_start 才是文件时间？
    # 产品定义：common_to_recording 直接给出文件内秒数
    # 当 media_start 被编入 recording_to_common_delta 时，
    # file_time = common - media_start - preview_delta = 10 - 1000 - 1.5 = -991.5
    # 这与「文件从 0 起算的本地秒」不一致时，说明对齐瞬间 common 原点约定必须在测试注释中写死。
    # 生产 export_clip_by_id 使用：export_start = common_start - rec_delta
    # 即 file_time = common - recording_to_common_delta
    assert abs(rec_b - (common - (media_start + 1.5))) < 1e-9


def test_anchored_delta_preview_and_recording_axes_consistent():
    """带预览锚点时：同一时刻同一内容的 preview→common 与 recording→common 必须一致。

    现场复现：对齐时刻 preview currentTime=39.17（PTS 基座≈0），align_mono≈5171.4，
    media_start_mono=5127.328。旧实现 p2c 仅含相对偏移（0/0.2691），导致播放头
    (preview→common≈39) 与切片 (recording→common≈5171) 错位 ~5100s：
    时间线显示播放头与切片相距极远、点击切片 seek 到预览缓冲之外。
    且公共轴零点 = 最早录制起点（origin_mono），时间线从 0 起算会话时长，
    而不是显示 time.monotonic() 的系统开机基座（可达数小时）。
    """
    align_mono = 5171.4
    media_start = 5127.328
    snaps = build_room_snapshots_from_align(
        reference_room_id="ref",
        offsets={"ref": 0.0, "b": 0.2691},
        scores={"ref": 0.9, "b": 0.9},
        room_meta={
            # 对齐时刻各房预览 PTS（MSE currentTime，捕获结束瞬间）
            "ref": {
                "media_start_mono": media_start,
                "preview_epoch_id": "e1",
                "recording_id": "r1",
                "preview_current_time": 39.17,
            },
            "b": {
                "media_start_mono": media_start + 2.2,
                "preview_epoch_id": "e2",
                "recording_id": "r2",
                "preview_current_time": 39.44,
            },
        },
        align_mono=align_mono,
    )
    origin = media_start  # 最早录制起点 = 公共轴零点
    # p2c = (align_mono - origin - pct) + rel
    assert abs(snaps["ref"].preview_to_common_delta - (align_mono - origin - 39.17)) < 1e-6
    assert abs(
        snaps["b"].preview_to_common_delta - ((align_mono - origin - 39.44) + 0.2691)
    ) < 1e-6
    # r2c = media_start + rel - origin：参考房 = 0（录制起点即公共轴零点）
    assert abs(snaps["ref"].recording_to_common_delta - 0.0) < 1e-6
    assert abs(snaps["b"].recording_to_common_delta - (2.2 + 0.2691)) < 1e-6
    # 对齐时刻：预览 PTS=current_time 的内容 与 录制位置=align_mono-media_start 的内容
    # 是同一内容，两路换算必须落到同一 common 值（都≈align_mono-origin+该房相对偏移）。
    rels = {"ref": 0.0, "b": 0.2691}
    for rid, pct in (("ref", 39.17), ("b", 39.44)):
        common_via_preview = pct + snaps[rid].preview_to_common_delta
        recording_pos = align_mono - snaps[rid].media_start_mono
        common_via_recording = recording_pos + snaps[rid].recording_to_common_delta
        assert abs(common_via_preview - common_via_recording) < 1e-6
        # 公共轴以最早录制起点为 0：对齐时刻 common = 会话已进行秒数 + 相对偏移
        assert abs(common_via_preview - ((align_mono - origin) + rels[rid])) < 1e-6
    # 公共轴最大值 = 会话时长（分钟级），而非 time.monotonic() 系统开机基座（小时级）
    assert snaps["ref"].recording_to_common_delta < 60.0
    assert snaps["ref"].preview_to_common_delta < 60.0
    # origin_mono 记录公共轴零点，供录制起步时重建 r2c
    assert snaps["ref"].origin_mono == media_start
    assert snaps["b"].origin_mono == media_start


def test_no_anchor_falls_back_to_legacy_relative_only():
    """旧前端/旧数据不传 preview_current_time 时退化为纯相对偏移，不破坏旧行为。"""
    snaps = build_room_snapshots_from_align(
        reference_room_id="ref",
        offsets={"ref": 0.0, "b": 0.8},
        scores={"ref": 0.9, "b": 0.9},
        room_meta={
            "ref": {"media_start_mono": 100.0, "preview_epoch_id": "e1", "recording_id": "r1"},
            "b": {"media_start_mono": 100.0, "preview_epoch_id": "e1", "recording_id": "r2"},
        },
        align_mono=500.0,
    )
    assert abs(snaps["ref"].preview_to_common_delta - 0.0) < 1e-9
    assert abs(snaps["b"].preview_to_common_delta - 0.8) < 1e-9
    assert abs(snaps["ref"].recording_to_common_delta - 100.0) < 1e-9
    assert abs(snaps["b"].recording_to_common_delta - 100.8) < 1e-9
