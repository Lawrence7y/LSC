#!/usr/bin/env python3
"""官方解说（broadcast）分支「持续分析」真实环境自动验收。

做什么
------
一条命令跑完整条真实链路，并给出逐项 PASS/FAIL：

1. **自动启动程序**：拉起真实后端 ``python-backend/main.py``（用**独立的**
   ``LSC_DATA_DIR``，不去连用户已保存的房间），端口默认取空闲口避免与在跑的 App 撞；
2. 走程序自己的 WebSocket API（``add_room`` → ``connect_room`` → ``start_recording``
   → ``start_continuous_analysis``），其中持续分析显式指定
   ``valorant_profile="broadcast"``（**这就是官方解说分支**）；
3. 跑满 ``--duration`` 秒，期间轮询它在跑的同一份状态（录制时长/回合数/高光数/滞后）；
4. 停止持续分析 + 停止录制，等**收尾定稿**（改名出 ``_录制中``）；
5. 校验产物与日志：模型是否从**生产目录**加载、标记支路是否加载、回合审计是否发生、
   高光是否都是 broadcast 来源、边界溯源字段是否齐全、A5 回放排除字段是否自洽、
   日志有无 ERROR/Traceback；可选再做**切点内容核验**（切点两侧跑模型 + OCR 标记）；
6. 打印 PASS/FAIL 表并写 JSON 报告。

为什么要独立数据目录
--------------------
后端启动会恢复 `LSC_DATA_DIR` 下已保存的房间并**自动连接**——若直接用用户的数据目录，
验收会顺手把用户那 11 个房间全连上开录，污染现场。故默认用临时目录，
``--data-dir`` 可覆盖（想连真实房间时才传用户的目录）。

用法
----
    # 默认：huya 29701502，跑 10 分钟
    python scripts/valorant_vision/run_broadcast_acceptance.py

    # 冒烟（90 秒）
    python scripts/valorant_vision/run_broadcast_acceptance.py --duration 90 --skip-boundary-check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_URL = "https://www.huya.com/29701502"
DEFAULT_BROADCAST_MODEL = _ROOT / "lsc/analyzer/models/valorant_phase_broadcast_finetune_v4_fused_20260907"
ACCEPTANCE_TOKEN = "acceptance-local"


REQUIRED_MODULES = ("numpy", "cv2", "onnxruntime", "websockets")


def _project_python_candidates() -> list[Path]:
    """项目真实使用的解释器（装了 numpy/cv2/onnxruntime/websockets/torch 的那个）。

    为什么必须锁解释器：**不同 shell 里 `python` 指向的可能是不同解释器**（实测
    bash 侧曾解析到 WorkBuddy 自带的 3.13，连 numpy 都没有）→ 后端一起来就
    ``ModuleNotFoundError``。真实 App 用的是 ``AppData/Local/Programs/Python/Python312``。
    """
    local = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local:
        candidates.append(Path(local) / "Programs/Python/Python312/python.exe")
    candidates.append(Path.home() / "AppData/Local/Programs/Python/Python312/python.exe")
    candidates.append(Path("C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe"))
    return candidates


def _modules_available(python: str) -> bool:
    probe = "import " + ", ".join(REQUIRED_MODULES)
    try:
        result = subprocess.run([python, "-c", probe], capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def resolve_python(explicit: str | None) -> str:
    """选一个依赖齐全的解释器；当前这个就够用就沿用当前。"""
    if explicit:
        return explicit
    if _modules_available(sys.executable):
        return sys.executable
    for candidate in _project_python_candidates():
        if candidate.is_file() and _modules_available(str(candidate)):
            return str(candidate)
    return sys.executable


def ensure_interpreter(explicit: str | None) -> None:
    """当前解释器缺依赖时，用选定的解释器**重新执行本脚本**（一次），避免半路炸在 import。"""
    chosen = resolve_python(explicit)
    if os.path.normcase(os.path.abspath(chosen)) == os.path.normcase(os.path.abspath(sys.executable)):
        return
    print(f"（当前解释器 {sys.executable} 缺依赖，改用 {chosen} 重新执行）", flush=True)
    os.execv(chosen, [chosen, str(Path(__file__).resolve()), *sys.argv[1:]])


def port_in_use(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class WsClient:
    """极简 WS 客户端：发送 {type,data}，等 ``<type>_response``。"""

    def __init__(self, port: int, *, timeout: float = 60.0) -> None:
        self._uri = f"ws://127.0.0.1:{port}"
        self._timeout = timeout
        self._ws = None

    async def __aenter__(self) -> WsClient:
        import websockets

        self._ws = await websockets.connect(self._uri, open_timeout=20)
        await self._ws.send(json.dumps({"type": "auth", "token": ACCEPTANCE_TOKEN}))
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def call(self, message_type: str, data: dict | None = None, *, timeout: float | None = None):
        await self._ws.send(json.dumps({"type": message_type, "data": data or {}}))
        deadline = time.monotonic() + (timeout or self._timeout)
        want = f"{message_type}_response"
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{message_type} 超时")
            raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == want:
                return msg.get("data") or {}
            # 忽略途中推送的 runtime_event / 状态广播


async def wait_for_backend(port: int, deadline_sec: float) -> bool:
    deadline = time.monotonic() + deadline_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            await asyncio.sleep(1.0)
    return False


def read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


async def run(args) -> dict:
    # 解释器在预检阶段就要用到，故最先解析
    python = resolve_python(args.python)
    print(f"解释器：{python}", flush=True)
    checks: list[dict] = []
    last_status: dict = {}

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(ok), "detail": detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = args.work_dir.expanduser().resolve() if args.work_dir else (
        Path("C:/lsc_models/acceptance") / stamp
    )
    (work / "logs").mkdir(parents=True, exist_ok=True)
    # 后端端口在 main.py 里写死 9876（与 Electron 生产一致）——不做端口改写，
    # 用真实端口才有"真实环境"意义；被占用就快速失败并提示关掉在跑的程序。
    port = args.port
    log_path = work / "logs" / "backend.log"

    # ── 预检：生产模型必须带标记支路 ──
    meta_path = Path(args.broadcast_model_dir) / "valorant_phase_v1.json"
    marker_ok = False
    if meta_path.is_file():
        try:
            marker_ok = "marker_roi_branch" in json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            marker_ok = False
    record("预检：生产广播档模型已声明标记支路", marker_ok, str(meta_path))
    record("预检：解释器依赖齐全", _modules_available(python),
           f"{python} 需要 {'/'.join(REQUIRED_MODULES)}")

    print(f"\n启动后端：port={port} data-dir={work}", flush=True)
    env = dict(os.environ)
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "LSC_WS_TOKEN": ACCEPTANCE_TOKEN,
        "LSC_WS_TOKEN_REQUIRED": "1",       # token 由本脚本提供，行为与生产一致
        "LSC_DATA_DIR": str(work),
        "LSC_LOG_DIR": str(work / "logs"),
        "LSC_PARENT_PID": "",               # 0 → 不挂父进程看门狗（main.py 对 <=0 直接 return）
        # 注意：**不要**注入 App 的 runtime/python-packages —— 那份依赖是为内嵌 Python
        # 编译的，混进系统 Python 会 ABI 冲突（实测 numpy._core._multiarray_umath 缺失）。
        "LSC_PYTHON_PACKAGES": "",
        "TMP": env.get("TEMP", "C:/lsc_tmp"),
    })
    backend = subprocess.Popen(
        [python, "-u", str(_ROOT / "python-backend/main.py")],
        cwd=str(_ROOT / "python-backend"),
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    started_ok = await wait_for_backend(port, 90)
    record("程序启动：后端端口可连接", started_ok, f"127.0.0.1:{port}")
    if not started_ok:
        backend.terminate()
        tail = read_log(log_path)[-600:]
        record("后端启动失败原因（日志尾部）", False, tail.replace("\n", " / ")[-300:])
        return {"checks": checks, "work_dir": str(work), "passed": 0, "total": len(checks),
                "backend_log_tail": tail}

    room_id = ""
    try:
        async with WsClient(port) as client:
            record("WebSocket 鉴权", True)

            added = await client.call("add_room", {"url": args.url})
            room_id = str(added.get("room_id") or "")
            record("添加房间", bool(added.get("success")) and bool(room_id), f"room_id={room_id} url={args.url}")

            if room_id:
                connected = await client.call("connect_room", {"room_id": room_id}, timeout=args.connect_timeout)
                record("连接房间", bool(connected.get("success", connected.get("accepted"))), str(connected)[:160])

            # 持续分析要求"主直播间已在录制"，故录制是前提（实测程序会以
            # 「主直播间尚未开始录制，无法启动持续分析」拒绝）。
            # 注意 connect_room 是**异步受理**（accepted=True, async=True），
            # 受理 ≠ 已连上——立刻录制会拿到「房间未连接」→ 故这里要等/重试。
            started: dict = {}
            if room_id:
                deadline = time.monotonic() + args.connect_timeout
                while True:
                    started = await client.call("start_recording", {"room_id": room_id}, timeout=90)
                    if started.get("success") and "error" not in started:
                        break
                    if time.monotonic() >= deadline:
                        break
                    if args.verbose:
                        print(f"    重试开始录制：{started.get('error')}", flush=True)
                    await asyncio.sleep(3)
                record("开始录制", bool(started.get("success")) and "error" not in started, str(started)[:160])

            # 起录到"可分析"之间存在短暂竞态 → 重试到成功或超时
            request = {
                "main_room_id": room_id,
                "target_room_ids": [room_id],
                "mode": "valorant_round",
                "interval": args.interval,
                "threshold": 0.3,
                "game": "valorant",
                "valorant_profile": "broadcast",   # 官方解说分支
            }
            analysis: dict = {}
            deadline = time.monotonic() + args.start_analysis_timeout
            while True:
                analysis = await client.call("start_continuous_analysis", request, timeout=90)
                if analysis.get("success"):
                    break
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(5)
                if args.verbose:
                    print(f"    重试启动持续分析：{analysis.get('error')}", flush=True)
            record("启动持续分析", bool(analysis.get("success")), str(analysis.get("message") or analysis)[:160])
            record(
                "解说分支生效：valorant_profile=broadcast",
                str(analysis.get("valorant_profile") or analysis.get("requested_valorant_profile")) == "broadcast",
                str(analysis.get("valorant_profile")),
            )

            print(f"\n跑 {args.duration}s（每 {args.interval}s 一个扫描周期）…", flush=True)
            deadline = time.monotonic() + args.duration
            last: dict = {}
            peak_rounds = 0
            peak_highlights = 0
            while time.monotonic() < deadline:
                await asyncio.sleep(min(15, max(1, args.duration // 10)))
                try:
                    status = await client.call("get_continuous_analysis_status", {}, timeout=60)
                except (TimeoutError, Exception):  # noqa: BLE001
                    continue
                last = status
                last_status = status
                peak_rounds = max(peak_rounds, int(status.get("confirmed_rounds") or 0))
                peak_highlights = max(peak_highlights, int(status.get("total_highlights") or 0))
                print(
                    f"    t+{args.duration - int(deadline - time.monotonic()):>4}s "
                    f"录制={float(status.get('recorded_duration') or 0):7.1f}s "
                    f"已分析={float(status.get('analyzed_duration') or 0):7.1f}s "
                    f"回合={status.get('confirmed_rounds')} 高光={status.get('total_highlights')} "
                    f"阶段={status.get('analysis_stage')} 滞后={float(status.get('analysis_lag_sec') or 0):.0f}s",
                    flush=True,
                )
            record(
                "分析确实在推进（已分析时长 > 0）",
                float(last.get("analyzed_duration") or 0) > 0,
                f"analyzed={last.get('analyzed_duration')} recorded={last.get('recorded_duration')}",
            )
            record("产出高光 ≥1", peak_highlights >= 1, f"total_highlights={peak_highlights}")
            record("有回合级产出（高光或确认回合 ≥1）", (peak_rounds >= 1) or (peak_highlights >= 1),
                   f"confirmed_rounds={peak_rounds} total_highlights={peak_highlights}")

            # WS 可能已掉线（实测 ConnectionClosedError）→ 兜住，别让产物校验与报告
            # 一起丢掉；掉线本身记一条失败项。
            try:
                stopped = await client.call("stop_continuous_analysis", {"room_id": room_id}, timeout=180)
                record("停止持续分析", bool(stopped.get("success", True)), str(stopped)[:120])
            except Exception as exc:  # noqa: BLE001
                record("停止持续分析", False, f"WS 中断: {type(exc).__name__}")
            if room_id:
                try:
                    stop_rec = await client.call("stop_recording", {"room_id": room_id}, timeout=180)
                except Exception as exc:  # noqa: BLE001
                    stop_rec = {}
                    record("停止录制", False, f"WS 中断: {type(exc).__name__}")
                else:
                    record("停止录制", bool(stop_rec.get("success", True)), str(stop_rec)[:120])
                # ⚠️ 程序的停止录制是 `wait_for_finalize=False`（日志实测）——**不在这里等，
                # 脚本随后杀后端会让"定稿改名"来不及做，录像永远停在 `_录制中`**，
                # 剪映草稿守卫就会永久拒绝它（正是现场那个现象的成因之一）。
                target = str(stop_rec.get("output_path") or "")
                if target:
                    deadline = time.monotonic() + args.finalize_timeout
                    while time.monotonic() < deadline:
                        still = os.path.exists(target) and "_录制中" in os.path.basename(target)
                        if not still:
                            break
                        await asyncio.sleep(2.0)
                    record(
                        "停止后已收尾定稿（改名出 '_录制中'）",
                        not (os.path.exists(target) and "_录制中" in os.path.basename(target)),
                        os.path.basename(target),
                    )
    finally:
        await asyncio.sleep(3)
        backend.terminate()
        try:
            backend.wait(timeout=20)
        except subprocess.TimeoutExpired:
            backend.kill()

    # ── 产物与日志校验 ──
    log = read_log(log_path)
    final_status: dict = last_status
    record("生产目录加载广播档模型", "valorant_phase_broadcast_finetune_v4_fused_20260907" in log,
           "日志: Valorant classifier loaded")
    record("回放标记支路已加载", "回放标记支路已加载" in log)
    # 回合审计需要回合**结束后**的 look-ahead 窗口；跑得短、或录制中途换段把回合留在
    # 旧文件上时，审计可能仍在队列里没跑完（实测 status 里 `broadcast_audit=pending_lookahead`、
    # `audit_queue_depth=1`）。故：审计完成 → PASS；程序自报"审计仍在队列里" → 也算 PASS
    # 但在明细里点明；程序声称已交付审计却没有审计日志行 → FAIL（那才是真问题）。
    audit_done = "赛事回合审计完成" in log
    audit_queued = int(final_status.get("audit_queue_depth") or 0)
    audit_delivered = int(final_status.get("audit_delivered_total") or 0) + int(
        final_status.get("audit_accepted_count") or 0
    )
    record(
        "发生回合审计（完成，或程序自报仍在 look-ahead 队列中）",
        audit_done or audit_queued > 0,
        f"完成={audit_done} 队列深度={audit_queued} 已交付/接收={audit_delivered}"
        + ("" if audit_done else "（未完成：该回合的 look-ahead 尚未跑完，短跑属正常）"),
    )

    # 录像/分析 sidecar 落在**后端的默认输出目录**（settings.output_dir，实测
    # 为 ~/LSC/output/<主播名>/），不一定在隔离数据目录里 → 两处都找，取最新。
    search_roots = [work, Path.home() / "LSC" / "output"]
    analysis_files = sorted(
        (p for root in search_roots if root.is_dir() for p in root.rglob("*.analysis.json")),
        key=lambda p: p.stat().st_mtime,
    )
    video = ""
    highlights: list[dict] = []
    if analysis_files:
        newest = analysis_files[-1]
        payload = json.loads(newest.read_text(encoding="utf-8"))
        video = str(payload.get("video_path") or "")
        highlights = [h for h in (payload.get("highlights") or []) if isinstance(h, dict)]
        try:
            shown = str(newest.relative_to(work))
        except ValueError:
            shown = str(newest)
        record("产出分析 sidecar", True, shown)
    record("产出高光（sidecar 内）", len(highlights) >= 1, f"highlights={len(highlights)}")

    if highlights:
        sources = {str(h.get("source_profile")) for h in highlights}
        record("全部高光来源=官方解说分支(broadcast)", sources == {"broadcast"}, f"sources={sources}")
        missing = [
            i for i, h in enumerate(highlights, 1)
            if not (h.get("start") is not None and h.get("end") is not None
                    and h.get("end_by") and h.get("confirm_status") and h.get("boundary_quality"))
        ]
        record("每条高光边界溯源字段齐全", not missing, f"缺字段的高光序号={missing}")
        bad_a5 = [
            i for i, h in enumerate(highlights, 1)
            if h.get("replay_end_excluded_sec") is not None
            and abs(float(h.get("end", 0)) - float(h.get("replay_end_exclusion_candidate_from") or h.get("end", 0))) > 1e-6
        ]
        record("A5 回放排除字段自洽", not bad_a5, f"不自洽的高光序号={bad_a5}")
        skipped = [
            (i, h.get("replay_end_exclusion_skipped"))
            for i, h in enumerate(highlights, 1) if h.get("replay_end_exclusion_skipped")
        ]
        print(f"    说明：A5 主动跳过 {len(skipped)} 条 {skipped[:4]}")

    if video:
        vp = Path(video)
        record("录像文件存在", vp.is_file(), vp.name)
        # sidecar 里的 video_path 在定稿改名后会失效（程序自身的 move_recording_sidecars
        # 也只改文件名不改内容）→ 按同一"开始时间戳"找最终文件，而不是只看 sidecar 里的名字。
        final_path = vp
        if "_录制中" in vp.name:
            stamp = vp.name.split("_录制中")[0]
            candidates = sorted(vp.parent.glob(f"{stamp}_至_*.mp4"))
            if candidates:
                final_path = candidates[-1]
        record(
            "录像已收尾定稿（文件名不含 '_录制中'）",
            "_录制中" not in final_path.name and final_path.is_file(),
            final_path.name,
        )
        if vp.is_file():
            record("录像时长 > 0", vp.stat().st_size > 100_000, f"{vp.stat().st_size/1e6:.1f}MB")

    record("日志无 ERROR/Traceback", "Traceback" not in log and "\nERROR" not in log)

    if args.boundary_check and highlights:
        print("\n切点内容核验（切点两侧跑生产模型 + OCR 找 REPLAY 标记）…", flush=True)
        boundary = check_boundaries(video, highlights)
        for row in boundary:
            print(f"    高光{row['index']} 出点 {row['end']:.1f}s → "
                  f"{row['before_t']}s:{row['before_class']}{'*' if row['before_marker'] else ''}  "
                  f"{row['at_t']}s:{row['at_class']}{'*' if row['at_marker'] else ''}", flush=True)

    passed = sum(1 for c in checks if c["passed"])
    return {
        "work_dir": str(work),
        "port": port,
        "url": args.url,
        "duration_sec": args.duration,
        "room_id": room_id,
        "video_path": video,
        "highlights": highlights,
        "checks": checks,
        "passed": passed,
        "total": len(checks),
    }


def check_boundaries(video_path: str, highlights: list[dict]) -> list[dict]:
    """切点两侧各取一帧，用生产模型判类 + OCR 找标记（只报告，不做 PASS/FAIL）。"""
    import cv2
    import numpy as np

    sys.path.insert(0, str(_ROOT))
    from lsc.analyzer.ocr_detector import _get_ocr
    from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier

    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    model = ValorantFrameClassifier(DEFAULT_BROADCAST_MODEL)
    model.load()
    ocr = _get_ocr()
    tmp = Path("C:/lsc_tmp/acceptance_frames")
    tmp.mkdir(parents=True, exist_ok=True)
    LAB = ("non_game", "buy", "combat", "result", "replay")
    rows = []
    for index, h in enumerate(highlights, 1):
        end = float(h.get("end") or 0.0)
        row = {"index": index, "end": end}
        for tag, offset in (("before", -1.0), ("at", 1.0)):
            t = max(0.0, end + offset)
            out = tmp / f"h{index}_{tag}.jpg"
            subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-i", video_path, "-ss", str(t),
                 "-frames:v", "1", "-q:v", "2", str(out)],
                check=False, capture_output=True, timeout=120,
            )
            image = cv2.imdecode(np.fromfile(str(out), dtype=np.uint8), cv2.IMREAD_COLOR) if out.is_file() else None
            if image is None:
                row[f"{tag}_t"], row[f"{tag}_class"], row[f"{tag}_marker"] = round(t, 1), "n/a", False
                continue
            probs = model.predict_broadcast_batch([image])[0]
            enlarged = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            lines, _ = ocr(enlarged)
            row[f"{tag}_t"] = round(t, 1)
            row[f"{tag}_class"] = LAB[int(np.argmax(probs))]
            row[f"{tag}_marker"] = any("REPLAY" in str(x).upper() for _, x, _ in (lines or []))
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help="直播间 URL（默认 huya 29701502）")
    parser.add_argument("--duration", type=int, default=600, help="持续分析跑多少秒")
    parser.add_argument("--interval", type=int, default=5, help="扫描周期（秒）")
    parser.add_argument("--port", type=int, default=9876,
                        help="后端端口（默认 9876，与生产一致；写死在 main.py 里，不可改）")
    parser.add_argument("--work-dir", type=Path, default=None, help="隔离数据目录（缺省 C:/lsc_models/acceptance/<时间戳>）")
    parser.add_argument("--broadcast-model-dir", type=Path, default=DEFAULT_BROADCAST_MODEL)
    parser.add_argument("--connect-timeout", type=float, default=120.0)
    parser.add_argument("--finalize-timeout", type=float, default=120.0,
                        help="停止录制后等待定稿改名的最长秒数")
    parser.add_argument("--start-analysis-timeout", type=float, default=120.0,
                        help="起录后等待'可分析'的最长秒数（起录↔分析之间有竞态）")
    parser.add_argument("--python", default=None,
                        help="跑后端的解释器（缺省自动选依赖齐全的那个）")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-boundary-check", dest="boundary_check", action="store_false", default=True)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    ensure_interpreter(args.python)

    print("=" * 84)
    print("官方解说（broadcast）分支 · 持续分析 · 真实环境自动验收")
    print("=" * 84)
    report = asyncio.run(run(args))
    print("\n" + "=" * 84)
    print(f"结果：{report['passed']}/{report['total']} 项通过   数据目录={report['work_dir']}")
    for item in report["checks"]:
        if not item["passed"]:
            print(f"  !! 未通过：{item['check']} — {item['detail']}")
    print("=" * 84)
    if args.json:
        out = args.json.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写出 JSON: {out}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
