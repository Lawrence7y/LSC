#!/usr/bin/env python3
"""下载 B 站二路/直播回放素材（供标记支路扩样用）。

工具选择（实测结论，别再走回头路）
---------------------------------
- **yt-dlp + 用户提供的登录 cookie → 1920×1080**，可用；
- 不带 cookie 时 yt-dlp 对 B 站 playurl/metadata 端点持续 **HTTP 412**（反爬）；
- 免登录的 **you-get** 可用但只有 **480P**（无 cookie 时的兜底）。

策略
----
每个 BV 只取**一个分 P**：自动挑"时长最长且名字不含赛前/BP/采访"的那一段
（二路 VOD 通常是「p1 赛前分析 + 每图一个 p」），再按 `--max-seconds` 截取开头
若干分钟——挖掘按固定间隔抽帧，20 分钟足够覆盖该转播方 UI 并采到若干回放转场
（实测本地 7–15 分钟一段就有 25–80 个标记正样本）。

**只取视频轨**（`-f bv*[height<=1080]`）：标记挖掘不需要音轨，省一半体积与时间。

用法
----
    python scripts/valorant_vision/fetch_bilibili_vods.py \
        --bvid BV1JqYu6yEaS BV1sCb46fEAv ... \
        --out-dir D:/valorant_vods \
        --cookies C:/lsc_tmp/bili_cookies_netscape.txt
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SKIP_KEYWORDS = ("赛前", "预测", "采访", "颁奖", "合影", "打卡", "抽奖")
API = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
VIDEO_FORMAT = "bv*[height<=1080]"


def fetch_meta(bvid: str) -> dict:
    request = urllib.request.Request(
        API.format(bvid=bvid),
        headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - 固定 https 端点
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"{bvid}: meta code={payload.get('code')} {payload.get('message')}")
    return payload["data"]


def pick_page(data: dict) -> dict:
    """挑一个"实际比赛"的分 P：时长最长、且名字不含跳过关键词。"""
    pages = data.get("pages") or [{"page": 1, "part": "", "duration": data.get("duration", 0)}]
    if len(pages) == 1:
        return pages[0]
    candidates = [p for p in pages if not any(k in str(p.get("part") or "") for k in SKIP_KEYWORDS)]
    return max(candidates or pages, key=lambda p: int(p.get("duration") or 0))


def download(
    bvid: str,
    page: int,
    out_dir: Path,
    log: Path,
    cookies: Path | None,
    max_seconds: int | None,
    fmt: str,
) -> tuple[bool, str]:
    cmd = [
        "yt-dlp", "--no-progress", "--newline", "--no-warnings",
        "--user-agent", UA, "--referer", "https://www.bilibili.com/",
        "--playlist-items", str(page),
        "-f", fmt,
        "-o", str(out_dir / "%(title).110B.%(ext)s"),
        "--print", "after_move:filepath",
    ]
    if cookies is not None:
        cmd += ["--cookies", str(cookies)]
    if max_seconds:
        cmd += ["--download-sections", f"*0-{max_seconds}"]
    cmd.append(f"https://www.bilibili.com/video/{bvid}/")
    with log.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"\n=== {time.strftime('%H:%M:%S')} {bvid} p{page}\n")
        handle.flush()
        result = subprocess.run(cmd, capture_output=True, timeout=7200, check=False)
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        handle.write(stdout + "\n" + stderr[-2000:] + "\n")
    produced = [
        line.strip() for line in stdout.splitlines()
        if line.strip() and Path(line.strip()).suffix == ".mp4"
    ]
    return result.returncode == 0, (produced[-1] if produced else stderr[-400:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bvid", nargs="+", required=True,
                        help="BV 号（直接给 BVxxxx；链接里的 ?p= 会被忽略，自动挑分 P）")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--cookies", type=Path, default=None, help="Netscape 格式 cookie 文件")
    parser.add_argument("--page", type=int, default=None, help="强制指定分 P")
    parser.add_argument("--max-seconds", type=int, default=1200,
                        help="每段只取开头多少秒（0=不限）")
    parser.add_argument("--format", default=VIDEO_FORMAT)
    args = parser.parse_args(argv)

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "_download.log"
    cookies = args.cookies.expanduser().resolve() if args.cookies else None
    if cookies is not None and not cookies.is_file():
        print(f"!! cookie 文件不存在: {cookies}", file=sys.stderr)
        return 2

    report = {"videos": []}
    for raw in args.bvid:
        bvid = raw.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        try:
            data = fetch_meta(bvid)
        except Exception as exc:  # noqa: BLE001 - 单个失败不该中断整批
            print(f"!! {bvid} 元数据失败: {exc}", file=sys.stderr, flush=True)
            report["videos"].append({"bvid": bvid, "ok": False, "error": str(exc)})
            continue
        page = args.page or pick_page(data)["page"]
        part = {p["page"]: str(p.get("part") or "") for p in (data.get("pages") or [])}.get(page, "")
        ok, output = download(bvid, page, out_dir, log, cookies, args.max_seconds or None, args.format)
        entry = {
            "bvid": bvid,
            "ok": ok,
            "title": data.get("title"),
            "owner": (data.get("owner") or {}).get("name"),
            "page": page,
            "part": part,
            "output": output,
        }
        report["videos"].append(entry)
        print(f"  {bvid} p{page} [{part}] {entry['owner']} | ok={ok} | {output}",
              file=sys.stderr, flush=True)
    (out_dir / "_download_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
