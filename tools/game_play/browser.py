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
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


T = TypeVar("T")


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


class GameBrowserSession:
    """Own a sync Playwright browser on one dedicated thread.

    CrewAI may execute synchronous tools through an asyncio executor. A
    Playwright ``Page`` cannot cross that thread boundary because its greenlet
    is bound to the thread where ``sync_playwright().start()`` ran. This
    session keeps browser creation, every page operation, and shutdown on one
    worker while allowing tools to be called from arbitrary threads.
    """

    def __init__(
        self,
        headless: bool | None = None,
        browser_factory: Callable[..., GameBrowser] = GameBrowser,
    ):
        self._headless = headless
        self._browser_factory = browser_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="game-browser")
        self._browser: GameBrowser | None = None
        self._owner_thread_id: int | None = None
        self._closed = False
        self._state_lock = threading.Lock()

    def start(self) -> "GameBrowserSession":
        with self._state_lock:
            if self._closed:
                raise RuntimeError("GameBrowserSession 已关闭，不能重新启动")
            if self._browser is not None:
                return self
            self._executor.submit(self._start_on_owner_thread).result()
        return self

    def _start_on_owner_thread(self) -> None:
        self._owner_thread_id = threading.get_ident()
        browser = self._browser_factory(headless=self._headless)
        try:
            self._browser = browser.start()
        except Exception:
            try:
                browser.close()
            except Exception:
                pass
            self._owner_thread_id = None
            raise

    def run(self, operation: Callable[[Page], T]) -> T:
        """Run one complete page operation on the Playwright owner thread."""
        on_owner_thread = threading.get_ident() == self._owner_thread_id
        with self._state_lock:
            if self._closed:
                raise RuntimeError("GameBrowserSession 已关闭")
            if self._browser is None:
                raise RuntimeError("GameBrowserSession 尚未启动，请先调用 start()")
            future = None if on_owner_thread else self._executor.submit(
                self._run_on_owner_thread, operation
            )

        if on_owner_thread:
            return operation(self._get_page_on_owner_thread())
        return future.result()

    def _run_on_owner_thread(self, operation: Callable[[Page], T]) -> T:
        return operation(self._get_page_on_owner_thread())

    def _get_page_on_owner_thread(self) -> Page:
        if self._browser is None:
            raise RuntimeError("GameBrowserSession 尚未启动")
        return self._browser.page

    def close(self) -> None:
        on_owner_thread = threading.get_ident() == self._owner_thread_id
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            close_future = None
            if self._browser is not None and not on_owner_thread:
                close_future = self._executor.submit(self._close_on_owner_thread)

        try:
            if self._browser is not None:
                if on_owner_thread:
                    self._close_on_owner_thread()
                elif close_future is not None:
                    close_future.result()
        finally:
            self._executor.shutdown(wait=not on_owner_thread)

    def _close_on_owner_thread(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None


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
