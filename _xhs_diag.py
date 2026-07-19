import urllib.request
import urllib.error
import ssl
import json
import subprocess

ROOM = "570367035144136880"
TIMEOUT = 8
FLV_READ = 32
ctx = ssl.create_default_context()

FF_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
JSON_HDRS = {
    "User-Agent": FF_UA,
    "Accept": "application/json",
    "Referer": f"https://www.redelight.cn/hina/livestream/{ROOM}",
}
IOS_HDRS = {
    "User-Agent": "ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))",
    "xy-common-params": "platform=iOS&sid=session.1722166379345546829388",
    "referer": "https://app.xhs.cn/",
}
PAGE_URL = (
    f"https://www.xiaohongshu.com/livestream/{ROOM}"
    "?track_id=&source=pc_search"
    "&xsec_token=ABxpVja1bW9hj2iPWlKpP48wO9pmvPNcTG9pMDrS8YnrrppWxvZbJ3u5GGThTFLyRv"
)
LIVE_PAGE = f"https://live.xiaohongshu.com/livestream/{ROOM}"


def probe(method, url, headers=None, read_bytes=None, flv=False):
    headers = headers or {}
    req = urllib.request.Request(url, method=method, headers=headers)
    print("=" * 72)
    print(f"{method} {url}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            status = resp.status
            ct = resp.headers.get("Content-Type", "")
            cl = resp.headers.get("Content-Length", "")
            print(f"  status={status} content-type={ct!r} content-length={cl!r}")
            if method == "HEAD":
                return
            data = resp.read(read_bytes if read_bytes else 400)
            if flv:
                hex16 = data[:16].hex()
                starts_flv = data[:3] == b"FLV"
                print(f"  first16_hex={hex16} starts_with_FLV={starts_flv} len_read={len(data)}")
            else:
                text = data.decode("utf-8", errors="replace")
                print(f"  first_400_chars:\n{text[:400]}")
    except urllib.error.HTTPError as e:
        body = e.read(400) if e.fp else b""
        ct = e.headers.get("Content-Type", "") if e.headers else ""
        cl = e.headers.get("Content-Length", "") if e.headers else ""
        print(f"  status={e.code} content-type={ct!r} content-length={cl!r}")
        print(f"  body_first_400:\n{body.decode('utf-8', errors='replace')[:400]}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")


def get_json(url, headers):
    print("=" * 72)
    print(f"GET {url}")
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            raw = resp.read(65536)
            ct = resp.headers.get("Content-Type", "")
            cl = resp.headers.get("Content-Length", "")
            print(f"  status={resp.status} content-type={ct!r} content-length={cl!r}")
            text = raw.decode("utf-8", errors="replace")
            print(f"  first_400_chars:\n{text[:400]}")
            try:
                obj = json.loads(text)
                data = obj.get("data") if isinstance(obj, dict) else None
                if isinstance(obj, dict):
                    print(f"  top_keys={list(obj.keys())}")
                    for k in ("code", "success", "msg", "message", "result"):
                        if k in obj:
                            print(f"  {k}={obj[k]!r}")
                if isinstance(data, dict):
                    print(f"  data_keys={list(data.keys())[:50]}")
                    for k in sorted(data.keys()):
                        kl = k.lower()
                        if (
                            kl in (
                                "status", "room_status", "live_status", "is_live",
                                "living", "room_id", "roomid", "title", "name",
                                "nickname", "host", "streamer", "anchor",
                                "play_url", "flv", "hls", "stream", "error",
                            )
                            or "status" in kl
                            or "live" in kl
                            or "url" in kl
                            or "flv" in kl
                            or "m3u8" in kl
                            or "play" in kl
                            or "room" in kl
                        ):
                            v = data[k]
                            s = json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, bool, type(None))) else repr(v)
                            if len(s) > 300:
                                s = s[:300] + "..."
                            print(f"  data.{k}={s}")
            except Exception as je:
                print(f"  JSON parse fail: {je}")
    except urllib.error.HTTPError as e:
        body = e.read(800)
        ct = e.headers.get("Content-Type", "") if e.headers else ""
        print(f"  status={e.code} content-type={ct!r}")
        print(f"  body_first_400:\n{body.decode('utf-8', errors='replace')[:400]}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")


# 1-3 FLV
for url in [
    f"http://live-source-play.xhscdn.com/live/{ROOM}.flv",
    f"http://live-play.xhscdn.com/live/{ROOM}.flv",
    f"https://live-source-play.xhscdn.com/live/{ROOM}.flv",
]:
    probe("HEAD", url, {"User-Agent": FF_UA})
    probe("GET", url, {"User-Agent": FF_UA}, read_bytes=FLV_READ, flv=True)

# 4 m3u8
url4 = f"https://live-play.xhscdn.com/live/{ROOM}.m3u8"
probe("HEAD", url4, {"User-Agent": FF_UA})
probe("GET", url4, {"User-Agent": FF_UA}, read_bytes=400)

# 5-6 APIs
get_json(
    f"https://www.redelight.cn/api/sns/red/live/app/v1/ecology/outside/share_info?room_id={ROOM}",
    JSON_HDRS,
)
get_json(
    f"https://www.xiaohongshu.com/api/sns/red/live/app/v1/ecology/outside/share_info?room_id={ROOM}",
    {
        **JSON_HDRS,
        "Referer": f"https://www.xiaohongshu.com/livestream/{ROOM}",
    },
)

# 7-8 pages
for url in [PAGE_URL, LIVE_PAGE]:
    probe("GET", url, IOS_HDRS, read_bytes=400)

# ffprobe
print("=" * 72)
print("ffprobe live-source-play FLV")
flv = f"http://live-source-play.xhscdn.com/live/{ROOM}.flv"
try:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name",
            "-of",
            "default=noprint_wrappers=1",
            "-user_agent",
            "Mozilla/5.0",
            "-headers",
            "Referer: https://www.xiaohongshu.com/\r\n",
            flv,
        ],
        capture_output=True,
        text=True,
        timeout=8,
    )
    print(f"  exit={r.returncode}")
    print(f"  stdout:\n{r.stdout[:500]}")
    print(f"  stderr:\n{r.stderr[:800]}")
except subprocess.TimeoutExpired as e:
    print("  TIMEOUT after 8s")
    out = e.stdout or ""
    err = e.stderr or ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", errors="replace")
    if isinstance(err, bytes):
        err = err.decode("utf-8", errors="replace")
    print(f"  stdout:\n{out[:500]}")
    print(f"  stderr:\n{err[:800]}")
except FileNotFoundError:
    print("  ffprobe not found on PATH")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print("=" * 72)
print("DONE")
