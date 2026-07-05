"""B站直播流Cookie认证支持。"""
import json
import logging
import os
import shutil
import sqlite3
import tempfile

_log = logging.getLogger(__name__)


def _query_cookie_db(db_path: str, domain: str) -> dict[str, str]:
    """Copy the browser cookie DB and query only the requested domain.

    Uses an exact-match query (``host_key IN (domain, .domain)``) instead
    of a wildcard LIKE to avoid scanning the whole table. The temporary
    copy is always removed in ``finally``.
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
            cursor.execute(
                "SELECT name, value FROM cookies WHERE host_key IN (?, ?)",
                (domain, f".{domain}"),
            )
            for name, value in cursor.fetchall():
                cookies[name] = value
        finally:
            conn.close()
    except Exception as exc:
        _log.debug("Failed to query cookie DB %s: %s", db_path, exc)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
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
        if content.strip().startswith("{"):
            data = json.loads(content)
            return data

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

    return cookies


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
            return json.loads(env_cookies)
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


def cookies_to_header(cookies: dict[str, str]) -> str:
    """将cookies字典转换为HTTP header字符串。"""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def save_cookies(cookies: dict[str, str], platform: str = "bilibili"):
    """保存cookies到配置文件。"""
    config_dir = os.path.expanduser("~/.lsc/cookies")
    os.makedirs(config_dir, exist_ok=True)

    cookie_file = os.path.join(config_dir, f"{platform}.json")
    with open(cookie_file, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2, ensure_ascii=False)

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
