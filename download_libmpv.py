"""使用 playwright 绕过 Cloudflare 下载 libmpv 包。

策略：
1. 用非 headless Edge 浏览器访问 sourceforge 下载页
2. 等待 Cloudflare 验证通过
3. 从页面中提取 meta refresh 下载链接
4. 用浏览器的 cookies 直接下载文件
"""
import os
import re
import sys
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIBMPV_DIR = ROOT / ".runtime" / "libmpv"
TMP_7Z = Path(os.environ.get("TEMP", "/tmp")) / "mpv-dev.7z"
TMP_EXTRACT = Path(os.environ.get("TEMP", "/tmp")) / "mpv-dev-extracted"

DOWNLOAD_URL = "https://sourceforge.net/projects/mpv-player-windows/files/libmpv/mpv-dev-x86_64-20260607-git-71ebd08.7z/download"


def main():
    from playwright.sync_api import sync_playwright

    print("Launching Edge browser (non-headless)...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context = browser.new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )

        # 隐藏 webdriver 标志
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
        """)

        page = context.new_page()

        print(f"Navigating to: {DOWNLOAD_URL}")
        page.goto(DOWNLOAD_URL, timeout=90000, wait_until="domcontentloaded")

        # 等待 Cloudflare 验证完成
        print("Waiting for Cloudflare check...")
        for i in range(120):
            time.sleep(1)
            title = page.title()
            # Cloudflare 验证页标题通常是 "Just a moment..." 或 "请稍候…"
            if "Just a moment" not in title and "moment" not in title.lower() and "请稍候" not in title and "Loading" not in title:
                print(f"  Cloudflare passed! Title: {title}")
                break
            if i % 10 == 9:
                print(f"  Still waiting... ({i+1}s) Title: {title}")
        else:
            print("  WARNING: Cloudflare check may not have completed")

        # 持续检查 meta refresh，最多等待 30 秒
        print("Waiting for download page to load...")
        download_url = None
        for i in range(30):
            time.sleep(1)
            try:
                content = page.content()
            except Exception:
                continue
            meta_match = re.search(
                r'<meta[^>]+refresh[^>]+url=([^"\'>]+)',
                content,
                re.IGNORECASE,
            )
            if meta_match:
                download_url = meta_match.group(1).replace("&amp;", "&").strip()
                print(f"  Found meta refresh URL!")
                break
            # 也检查直接下载链接
            links = page.query_selector_all("a[href*='.7z']")
            if links:
                href = links[0].get_attribute("href")
                if href:
                    download_url = href
                    print(f"  Found direct download link!")
                    break
            if i % 5 == 4:
                print(f"  Still waiting... ({i+1}s) Title: {page.title() if page.is_closed() is False else 'closed'}")

        if not download_url:
            print("No download URL found in page")
            try:
                print(f"Page title: {page.title()}")
                print(f"Page URL: {page.url}")
                # 保存页面内容用于调试
                debug_path = Path(os.environ["TEMP"]) / "sf_debug.html"
                debug_path.write_text(page.content(), encoding="utf-8")
                print(f"Page content saved to: {debug_path}")
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        print(f"Download URL: {download_url[:100]}...")

        # 方法 1: 尝试 expect_download
        download_ok = False
        try:
            print("Attempting download via browser navigation...")
            with page.expect_download(timeout=30000) as download_info:
                page.goto(download_url)
            download = download_info.value
            download_path = Path(os.environ["TEMP"]) / "mpv-download" / download.suggested_filename
            download_path.parent.mkdir(parents=True, exist_ok=True)
            download.save_as(str(download_path))
            if download_path.stat().st_size > 1000000:
                shutil.copy2(download_path, TMP_7Z)
                download_ok = True
                print(f"Downloaded: {download_path.stat().st_size} bytes")
        except Exception as e:
            print(f"Browser download failed: {e}")

        # 方法 2: 用浏览器 cookies 通过 API 下载
        if not download_ok:
            print("Trying download via browser request API...")
            try:
                resp = page.request.get(download_url, timeout=120000)
                if resp.ok:
                    body = resp.body()
                    if len(body) > 1000000 and b"<html" not in body[:200].lower():
                        TMP_7Z.write_bytes(body)
                        download_ok = True
                        print(f"Downloaded via request: {len(body)} bytes")
                    else:
                        print(f"Response too small or HTML: {len(body)} bytes")
                else:
                    print(f"Request failed: {resp.status}")
            except Exception as e:
                print(f"Request API failed: {e}")

        # 方法 3: 提取 cookies，用 requests 下载
        if not download_ok:
            print("Trying download with browser cookies via requests...")
            try:
                import requests
                cookies = {}
                for cookie in context.cookies():
                    cookies[cookie["name"]] = cookie["value"]

                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                    "Referer": page.url,
                }

                resp = requests.get(download_url, cookies=cookies, headers=headers, timeout=120, stream=True)
                print(f"  Status: {resp.status_code}, Content-Type: {resp.headers.get('Content-Type')}")

                if resp.status_code == 200 and "html" not in resp.headers.get("Content-Type", "").lower():
                    total = 0
                    with open(TMP_7Z, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)
                                total += len(chunk)
                    if total > 1000000:
                        download_ok = True
                        print(f"Downloaded: {total} bytes")
                    else:
                        print(f"File too small: {total} bytes")
                else:
                    print(f"Download failed: {resp.status_code}")
            except Exception as e:
                print(f"Cookies download failed: {e}")

        browser.close()

    if not download_ok:
        print("\nERROR: All download methods failed")
        sys.exit(1)

    # 检查下载的文件
    size = TMP_7Z.stat().st_size
    print(f"\nDownloaded file size: {size} bytes")

    if size < 1000000:
        print("ERROR: File too small")
        with open(TMP_7Z, "rb") as f:
            head = f.read(200)
        if b"<html" in head.lower():
            print("ERROR: Got HTML instead of 7z file")
            sys.exit(1)

    # 解压
    print("\nExtracting...")
    try:
        import py7zr
    except ImportError:
        os.system(f"{sys.executable} -m pip install py7zr")
        import py7zr

    if TMP_EXTRACT.exists():
        shutil.rmtree(TMP_EXTRACT)
    TMP_EXTRACT.mkdir(parents=True)

    with py7zr.SevenZipFile(TMP_7Z, mode="r") as z:
        z.extractall(path=TMP_EXTRACT)
    print(f"Extracted to: {TMP_EXTRACT}")

    # 列出所有文件
    print("\nExtracted files:")
    for p in sorted(TMP_EXTRACT.rglob("*")):
        if p.is_file():
            rel = p.relative_to(TMP_EXTRACT)
            print(f"  {rel} ({p.stat().st_size} bytes)")

    # 复制 DLL 到 .runtime/libmpv/
    print(f"\nCopying DLLs to {LIBMPV_DIR}...")
    LIBMPV_DIR.mkdir(parents=True, exist_ok=True)

    dll_files = list(TMP_EXTRACT.rglob("*.dll"))
    print(f"Found {len(dll_files)} DLL files")

    for src in dll_files:
        dst = LIBMPV_DIR / src.name
        shutil.copy2(src, dst)
        print(f"  {src.name}: {src.stat().st_size} bytes")

    # 确保 mpv-2.dll 存在
    mpv2 = LIBMPV_DIR / "mpv-2.dll"
    libmpv2 = LIBMPV_DIR / "libmpv-2.dll"

    # 清理 0 字节文件
    for f in [mpv2, libmpv2]:
        if f.exists() and f.stat().st_size == 0:
            f.unlink()
            print(f"  Removed empty: {f.name}")

    # 如果 mpv-2.dll 不存在但 libmpv-2.dll 存在，复制
    if not mpv2.exists() or mpv2.stat().st_size == 0:
        if libmpv2.exists() and libmpv2.stat().st_size > 0:
            shutil.copy2(libmpv2, mpv2)
            print(f"  Copied libmpv-2.dll -> mpv-2.dll")
        else:
            for p in TMP_EXTRACT.rglob("mpv-2.dll"):
                shutil.copy2(p, mpv2)
                print(f"  Found and copied mpv-2.dll from archive")
                break

    # 最终状态
    print(f"\nFinal .runtime/libmpv/ contents:")
    for f in sorted(LIBMPV_DIR.iterdir()):
        if f.is_file():
            print(f"  {f.name}: {f.stat().st_size} bytes")

    # 清理
    TMP_7Z.unlink(missing_ok=True)
    shutil.rmtree(TMP_EXTRACT, ignore_errors=True)

    print("\nDONE: libmpv installed successfully")


if __name__ == "__main__":
    main()
