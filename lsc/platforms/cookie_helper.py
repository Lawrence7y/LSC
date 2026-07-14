"""B站/抖音直播流 Cookie 认证支持。"""
import json
import logging
import os
import shutil
import sqlite3
import tempfile

_log = logging.getLogger(__name__)


def _is_http_header_safe(value: str) -> bool:
    """HTTP 头（含 Cookie）必须可被 latin-1 编码；含 \\ufffd 的脏值一律拒绝。"""
    if not value or "\ufffd" in value:
        return False
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def _sanitize_cookie_map(cookies: dict[str, str]) -> dict[str, str]:
    """过滤无法写入 HTTP Cookie 头的键值，避免 urllib latin-1 编码崩溃。"""
    cleaned: dict[str, str] = {}
    dropped = 0
    for key, value in (cookies or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            dropped += 1
            continue
        if _is_http_header_safe(key) and _is_http_header_safe(value):
            cleaned[key] = value
        else:
            dropped += 1
    if dropped:
        _log.warning(
            "已丢弃 %d 个无效 Cookie（含解密失败产生的 \\ufffd 或非 latin-1 字符）",
            dropped,
        )
    return cleaned


def _decrypt_chrome_value(encrypted_value: bytes) -> str:
    """解密 Chrome/Edge 在 Windows 上加密的 Cookie 值。

    Chrome 80+ 使用 DPAPI / AES-GCM 加密 Cookie 值。
    前缀标识加密版本：v10 (Chrome 80-103)、v11 (Chrome 104-119)、
    v20 (Chrome 120+，含 App-Bound Encryption)。

    解密失败时必须返回空字符串，禁止把密文字节用 errors='replace'
    解码成含 \\ufffd 的伪明文——那样会污染 Cookie 头并触发
    ``latin-1 codec can't encode character '\\ufffd'``。
    """
    if not encrypted_value:
        return ""
    # 检测 Chrome 加密前缀 (v10/v11/v20 等 vNN 格式)
    if len(encrypted_value) > 3 and encrypted_value[:1] == b"v" and encrypted_value[1:3].isdigit():
        try:
            import win32crypt
            # 旧版 Chrome 可能整段走 DPAPI；新版 v20 通常会失败，交由上层回退到文件 Cookie
            decrypted = win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)
            if decrypted and decrypted[1]:
                text = decrypted[1].decode("utf-8")
                return text if _is_http_header_safe(text) else ""
        except Exception as exc:
            _log.debug("DPAPI 解密失败（可忽略，请改用导出的 Cookie 文件）: %s", exc)
        return ""
    # 无加密前缀：仅接受干净的 UTF-8/latin-1 明文，绝不 errors='replace'
    try:
        text = encrypted_value.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = encrypted_value.decode("latin-1")
        except Exception:
            return ""
    return text if _is_http_header_safe(text) else ""


def _query_cookie_db(db_path: str, domain: str) -> dict[str, str]:
    """Copy the browser cookie DB and query only the requested domain.

    Uses an exact-match query (``host_key IN (domain, .domain)``) instead
    of a wildcard LIKE to avoid scanning the whole table. The temporary
    copy is always removed in ``finally``.

    在 Windows 上会自动解密 Chrome/Edge 使用 DPAPI 加密的 Cookie 值。
    """
    cookies: dict[str, str] = {}
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
            shutil.copy2(db_path, tmp_path)

        conn = sqlite3.connect(tmp_path)
        try:
            cursor = conn.cursor()
            # 同时查询 value（明文）和 encrypted_value（DPAPI 加密）
            cursor.execute(
                "SELECT name, value, encrypted_value FROM cookies WHERE host_key IN (?, ?)",
                (domain, f".{domain}"),
            )
            for name, value, encrypted_value in cursor.fetchall():
                # 优先使用明文 value；若为空则尝试解密 encrypted_value
                candidate = ""
                if value:
                    candidate = value
                elif encrypted_value:
                    candidate = _decrypt_chrome_value(encrypted_value)
                if (
                    isinstance(name, str)
                    and isinstance(candidate, str)
                    and _is_http_header_safe(name)
                    and _is_http_header_safe(candidate)
                ):
                    cookies[name] = candidate
        finally:
            conn.close()
    except Exception as exc:
        _log.debug("Failed to query cookie DB %s: %s", db_path, exc)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception as exc:
                _log.debug("操作异常（已忽略）: %s", exc)
    return cookies


def get_chrome_cookies_for_domain(domain: str) -> dict[str, str]:
    """从Chrome浏览器获取指定域名的cookies。

    注意: 这需要Chrome已关闭，或者使用复制数据库的方式。
    """
    cookies = {}

    # Chrome cookie数据库路径
    chrome_paths = [
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data\Default\Cookies"),
        os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data\Default\Network\Cookies"),
    ]

    for chrome_path in chrome_paths:
        if not os.path.exists(chrome_path):
            continue
        cookies = _query_cookie_db(chrome_path, domain)
        if cookies:
            break

    return cookies


def get_edge_cookies_for_domain(domain: str) -> dict[str, str]:
    """从Edge浏览器获取指定域名的cookies。"""
    cookies = {}

    edge_paths = [
        os.path.expanduser(r"~\AppData\Local\Microsoft\Edge\User Data\Default\Cookies"),
        os.path.expanduser(r"~\AppData\Local\Microsoft\Edge\User Data\Default\Network\Cookies"),
    ]

    for edge_path in edge_paths:
        if not os.path.exists(edge_path):
            continue
        cookies = _query_cookie_db(edge_path, domain)
        if cookies:
            break

    return cookies


def load_cookies_from_file(cookie_file: str) -> dict[str, str]:
    """从cookie文件加载cookies。

    支持格式:
    - Netscape/Mozilla cookie jar格式
    - JSON格式
    """
    cookies = {}

    if not os.path.exists(cookie_file):
        return cookies

    try:
        with open(cookie_file, encoding="utf-8") as f:
            content = f.read()

        # 尝试JSON格式
        stripped = content.strip()
        if stripped.startswith("{"):
            data = json.loads(content)
            if isinstance(data, dict):
                return _sanitize_cookie_map(
                    {str(k): str(v) for k, v in data.items() if v is not None}
                )
            return {}
        # #37: JSON array format (browser extension export)
        if stripped.startswith("["):
            arr = json.loads(content)
            if isinstance(arr, list):
                result = {}
                for entry in arr:
                    if isinstance(entry, dict):
                        name = entry.get("name")
                        value = entry.get("value")
                        if name and value is not None:
                            result[str(name)] = str(value)
                _log.info("loaded %d cookies from JSON array", len(result))
                return _sanitize_cookie_map(result)
            return {}

        # 尝试Netscape格式
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                name = parts[5]
                value = parts[6]
                cookies[name] = value

    except Exception as e:
        _log.warning("读取cookie文件失败: %s", e)

    return _sanitize_cookie_map(cookies)


def get_bilibili_cookies() -> dict[str, str]:
    """获取B站的cookies。

    优先级:
    1. 环境变量 LSC_BILIBILI_COOKIES (JSON格式)
    2. 配置文件 ~/.lsc/cookies/bilibili.json
    3. 浏览器cookies (Chrome/Edge)
    """
    # 1. 环境变量
    env_cookies = os.environ.get("LSC_BILIBILI_COOKIES")
    if env_cookies:
        try:
            data = json.loads(env_cookies)
            if isinstance(data, dict):
                cleaned = _sanitize_cookie_map(
                    {str(k): str(v) for k, v in data.items() if v is not None}
                )
                if cleaned:
                    return cleaned
        except Exception as exc:
            _log.warning("LSC_BILIBILI_COOKIES 环境变量 JSON 解析失败: %s", exc)

    # 2. 配置文件
    config_dir = os.path.expanduser("~/.lsc/cookies")
    cookie_file = os.path.join(config_dir, "bilibili.json")
    if os.path.exists(cookie_file):
        cookies = load_cookies_from_file(cookie_file)
        if cookies:
            return cookies

    # 3. 浏览器cookies
    import sys
    cookies = get_chrome_cookies_for_domain("bilibili.com")
    if not cookies:
        cookies = get_edge_cookies_for_domain("bilibili.com")
    cookies = _sanitize_cookie_map(cookies)

    if sys.platform == "win32":
        if not cookies:
            _log.warning(
                "未找到有效的B站Cookie。在Windows平台下由于浏览器加密与文件共享锁定限制，"
                "自动提取可能失效。建议手动登录B站并使用浏览器插件导出Cookie文件到 ~/.lsc/cookies/bilibili.json"
            )
        elif not cookies.get("SESSDATA"):
            _log.warning(
                "检测到浏览器中的B站Cookie值（如 SESSDATA）为空（受Windows浏览器安全加密限制）。"
                "如需录制高画质流，请使用浏览器插件将Cookie导出为JSON，并保存到 ~/.lsc/cookies/bilibili.json"
            )

    return cookies


def get_douyin_cookies() -> dict[str, str]:
    """获取抖音的cookies。

    抖音反爬系统（验证中间页/CAPTCHA）会在请求没有登录态时拦截，
    返回验证页面而非直播间数据，导致SSR解析失败、所有房间显示"未开播"。

    优先级:
    1. 环境变量 LSC_DOUYIN_COOKIES (JSON格式)
    2. 配置文件 ~/.lsc/cookies/douyin.json
    3. 浏览器cookies (Chrome/Edge，域名 douyin.com / live.douyin.com)
    """
    # 1. 环境变量
    env_cookies = os.environ.get("LSC_DOUYIN_COOKIES")
    if env_cookies:
        try:
            data = json.loads(env_cookies)
            if isinstance(data, dict):
                cleaned = _sanitize_cookie_map(
                    {str(k): str(v) for k, v in data.items() if v is not None}
                )
                if cleaned:
                    return cleaned
        except Exception as exc:
            _log.warning("LSC_DOUYIN_COOKIES 环境变量 JSON 解析失败: %s", exc)

    # 2. 配置文件
    config_dir = os.path.expanduser("~/.lsc/cookies")
    cookie_file = os.path.join(config_dir, "douyin.json")
    if os.path.exists(cookie_file):
        cookies = load_cookies_from_file(cookie_file)
        if cookies:
            return cookies

    # 3. 浏览器cookies — 依次尝试 douyin.com 和 live.douyin.com 两个域名
    cookies = get_chrome_cookies_for_domain("douyin.com")
    if not cookies:
        cookies = get_chrome_cookies_for_domain("live.douyin.com")
    if not cookies:
        cookies = get_edge_cookies_for_domain("douyin.com")
    if not cookies:
        cookies = get_edge_cookies_for_domain("live.douyin.com")
    cookies = _sanitize_cookie_map(cookies)

    if not cookies:
        _log.warning(
            "未找到抖音Cookie。抖音反爬系统会在无登录态时返回验证页面（而非直播数据），"
            "导致所有房间显示\"未开播\"。建议：在浏览器登录抖音后，"
            "使用插件导出Cookie为JSON保存到 ~/.lsc/cookies/douyin.json，"
            "或设置环境变量 LSC_DOUYIN_COOKIES。"
        )

    return cookies


def parse_cookie_input(raw: str) -> dict[str, str]:
    """解析用户粘贴的 Cookie 文本。

    支持：
    - JSON 对象 ``{"ttwid":"..."}``
    - Cookie Editor / EditThisCookie 数组 ``[{"name":"ttwid","value":"..."}]``
    - 请求头字符串 ``ttwid=...; sessionid=...``
    """
    text = (raw or "").strip()
    if not text:
        return {}

    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        if isinstance(data, dict):
            # 兼容 {"cookies":[...]} 包装
            if "cookies" in data and isinstance(data["cookies"], list):
                data = data["cookies"]
            else:
                return _sanitize_cookie_map(
                    {str(k): str(v) for k, v in data.items() if v is not None}
                )
        if isinstance(data, list):
            out: dict[str, str] = {}
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("Name")
                value = item.get("value") if "value" in item else item.get("Value")
                if name is None or value is None:
                    continue
                out[str(name)] = str(value)
            return _sanitize_cookie_map(out)
        raise ValueError("Cookie JSON 格式无效，期望对象或数组")

    # Cookie 头：a=b; c=d
    out = {}
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            out[name] = value
    return _sanitize_cookie_map(out)


def get_douyin_cookie_status() -> dict[str, object]:
    """返回抖音 Cookie 状态，供设置页展示。"""
    cookies = get_douyin_cookies()
    config_dir = os.path.expanduser("~/.lsc/cookies")
    cookie_file = os.path.join(config_dir, "douyin.json")
    return {
        "configured": bool(cookies),
        "count": len(cookies),
        "keys": sorted(cookies.keys())[:20],
        "source_file": cookie_file if os.path.exists(cookie_file) else "",
        "has_env": bool(os.environ.get("LSC_DOUYIN_COOKIES")),
    }


def save_douyin_cookies_from_text(raw: str) -> dict[str, object]:
    """解析并保存抖音 Cookie，返回状态。"""
    cookies = parse_cookie_input(raw)
    if not cookies:
        raise ValueError("未解析到有效 Cookie，请确认格式（JSON 或 name=value; ...）")
    # 关键登录态字段提示（不强制，避免插件命名差异误杀）
    important = ("ttwid", "sessionid", "sessionid_ss", "sid_guard", "uid_tt")
    if not any(k in cookies for k in important):
        _log.warning(
            "抖音 Cookie 已保存，但未发现常见登录字段 %s，可能仍会被反爬拦截",
            "/".join(important),
        )
    save_cookies(cookies, platform="douyin")
    return get_douyin_cookie_status()


def cookies_to_header(cookies: dict[str, str]) -> str:
    """将cookies字典转换为HTTP header字符串。"""
    cleaned = _sanitize_cookie_map(cookies)
    return "; ".join(f"{k}={v}" for k, v in cleaned.items())


def save_cookies(cookies: dict[str, str], platform: str = "bilibili"):
    """保存cookies到配置文件（原子写入）。"""
    config_dir = os.path.expanduser("~/.lsc/cookies")
    os.makedirs(config_dir, exist_ok=True)

    cookie_file = os.path.join(config_dir, f"{platform}.json")
    tmp_file = cookie_file + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2, ensure_ascii=False)
    os.replace(tmp_file, cookie_file)

    _log.info("Cookies已保存到: %s", cookie_file)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    _log.info("获取B站cookies...")
    cookies = get_bilibili_cookies()
    _log.info("找到 %d 个cookies", len(cookies))
    if cookies:
        _log.info("Cookies: %s...", list(cookies.keys())[:5])
    else:
        _log.warning("未找到cookies，请手动设置:")
        _log.warning("1. 在浏览器中登录B站")
        _log.warning("2. 按F12打开开发者工具")
        _log.warning("3. 切换到Application/存储标签")
        _log.warning("4. 复制cookies并保存到 ~/.lsc/cookies/bilibili.json")
