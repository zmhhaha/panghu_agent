"""
通用游戏试玩 — Playwright 浏览器生命周期管理。

职责：
- 启动/关闭浏览器（headless 由 GAME_HEADLESS 控制，本地调试可开窗口）
- 注入认证 cookie（从环境变量解析，支持单 cookie 与 JSON 多域列表）
- 提供 page 对象给通用试玩工具使用

认证策略：
- 生产环境受 oauth2-proxy（Casdoor OIDC）保护的游戏，需要浏览器持有
  登录后的会话 cookie（默认名 `_oauth2_proxy`）。
- 通过环境变量注入，K8s 里由 Secret `game-auth` 提供：
    GAME_AUTH_COOKIE="name=_oauth2_proxy;value=...;domain=.panghuer.top;path=/;secure"
    GAME_AUTH_COOKIES_JSON='[{"name":"_oauth2_proxy","value":"...","domain":".panghuer.top","path":"/","secure":true}]'
- 未配置 cookie 时访问受保护页面会跳到登录页，由调用方检测并给出清晰报错，
  不在这里做交互式登录（避免维护 OAuth 密码的脆弱流程）。
"""
import json
import os

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


def _parse_cookies() -> list[dict]:
    """从环境变量解析要注入的 cookie 列表。"""
    cookies: list[dict] = []

    single = os.getenv("GAME_AUTH_COOKIE", "").strip()
    if single:
        fields = {}
        for part in single.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                fields[k.strip().lower()] = v.strip()
        name = fields.get("name", "_oauth2_proxy")
        value = fields.get("value", "")
        if value:
            cookie = {"name": name, "value": value, "domain": fields.get("domain", ".panghuer.top")}
            if fields.get("path"):
                cookie["path"] = fields["path"]
            if fields.get("secure", "").lower() in ("1", "true", "yes"):
                cookie["secure"] = True
            cookies.append(cookie)

    multi = os.getenv("GAME_AUTH_COOKIES_JSON", "").strip()
    if multi:
        try:
            parsed = json.loads(multi)
            if isinstance(parsed, list):
                cookies.extend(parsed)
        except json.JSONDecodeError:
            pass  # 配置错误不阻塞启动，交给后续登录检测报错

    return cookies


class GameBrowser:
    """Playwright 浏览器生命周期管理器。"""

    def __init__(self, headless: bool | None = None):
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        # GAME_HEADLESS=0 时开窗口（本地调试）；默认 headless
        if headless is None:
            headless = os.getenv("GAME_HEADLESS", "1") != "0"
        self.headless = headless

    def start(self) -> "GameBrowser":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        # 注入认证 cookie（如有）
        cookies = _parse_cookies()
        if cookies:
            try:
                self._context.add_cookies(cookies)
            except Exception:
                pass
        self._context.set_default_timeout(10000)
        return self

    @property
    def page(self) -> Page:
        if self._context is None:
            raise RuntimeError("GameBrowser 尚未启动，请先调用 start()")
        return self._context.pages[0] if self._context.pages else self._context.new_page()

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        if self._pw:
            self._pw.stop()
        self._context = None
        self._browser = None
        self._pw = None


def detect_login_redirect(page: Page) -> str | None:
    """检测当前页面是否被重定向到登录页（oauth2-proxy / 常见登录页）。

    返回描述字符串，非登录页返回 None。
    """
    url = page.url.lower()
    if "/oauth2/sign_in" in url or "/oauth2/start" in url:
        return f"被重定向到 oauth2-proxy 登录页: {page.url}"
    if "/login" in url or "/signin" in url or "/sign_in" in url:
        # 排除常见登录后页面误判
        if "logout" not in url:
            return f"被重定向到登录页: {page.url}"
    return None
