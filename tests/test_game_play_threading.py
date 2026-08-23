import asyncio
import threading
import unittest
from unittest.mock import patch

from tools.game_play.browser import GameBrowserSession, _merge_cookies, _parse_cookie_header
from tools.game_play.tools import PageClickTool, PageGoTool, _get_element_by_idx, _scan, make_game_tools


class _ThreadCheckingPage:
    def __init__(self):
        self.owner_thread_id = threading.get_ident()
        self.goto_thread_ids = []

    def goto(self, url, timeout, wait_until):
        current_thread_id = threading.get_ident()
        if current_thread_id != self.owner_thread_id:
            raise RuntimeError("page used outside its owner thread")
        self.goto_thread_ids.append(current_thread_id)


class _ThreadCheckingBrowser:
    def __init__(self, headless=None):
        self._page = None
        self.closed_thread_id = None

    def start(self):
        self._page = _ThreadCheckingPage()
        return self

    @property
    def page(self):
        if threading.get_ident() != self._page.owner_thread_id:
            raise RuntimeError("page requested outside its owner thread")
        return self._page

    def close(self):
        self.closed_thread_id = threading.get_ident()
        if self.closed_thread_id != self._page.owner_thread_id:
            raise RuntimeError("browser closed outside its owner thread")


class _IndexedElement:
    def __init__(self, text="", title=""):
        self.text = text
        self.attrs = {"title": title, "type": "button"}
        self.clicked = False

    def is_visible(self):
        return True

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def evaluate(self, _script):
        return "button"

    def scroll_into_view_if_needed(self, timeout):
        return None

    def click(self, timeout):
        self.clicked = True


class _IndexedLocator:
    def __init__(self, elements):
        self.elements = elements

    def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]


class _IndexedPage:
    url = "https://example.com"

    def __init__(self, elements):
        self.elements = elements

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def locator(self, _selector):
        return _IndexedLocator(self.elements)

    def inner_text(self, selector):
        return "test body" if selector == "body" else ""

    def title(self):
        return "test"


class GameBrowserSessionTests(unittest.TestCase):
    def test_forwarded_sso_cookie_is_scoped_and_whitelisted(self):
        with patch.dict("os.environ", {"GAME_AUTH_COOKIE_NAMES": "_oauth2_proxy,game_session"}):
            cookies = _parse_cookie_header(
                "_oauth2_proxy=opaque-token; _oauth2_proxy_1=chunk; unrelated=do-not-forward; game_session=game-token",
                ".panghuer.top",
            )

        self.assertEqual(
            [cookie["name"] for cookie in cookies],
            ["_oauth2_proxy", "_oauth2_proxy_1", "game_session"],
        )
        self.assertTrue(all(cookie["domain"] == ".panghuer.top" for cookie in cookies))
        self.assertTrue(all(cookie["path"] == "/" and cookie["secure"] for cookie in cookies))

    def test_request_cookie_overrides_fallback_cookie_without_duplicate(self):
        fallback = [{"name": "_oauth2_proxy", "value": "stale", "domain": ".panghuer.top", "path": "/"}]
        forwarded = [{"name": "_oauth2_proxy", "value": "current", "domain": ".panghuer.top", "path": "/"}]

        merged = _merge_cookies(fallback, forwarded)

        self.assertEqual(merged, [forwarded[0]])

    def test_sso_header_reaches_browser_factory(self):
        browser = _ThreadCheckingBrowser()
        received = {}

        def factory(**kwargs):
            received.update(kwargs)
            return browser

        session = GameBrowserSession(
            browser_factory=factory,
            auth_cookie_header="_oauth2_proxy=current-token",
            auth_cookie_domain=".panghuer.top",
        ).start()
        try:
            self.assertEqual(received["auth_cookie_header"], "_oauth2_proxy=current-token")
            self.assertEqual(received["auth_cookie_domain"], ".panghuer.top")
        finally:
            session.close()

    def test_generic_game_tools_are_exposed(self):
        session = GameBrowserSession()
        try:
            names = {tool.name for tool in make_game_tools(session, "trial_output")}
        finally:
            session.close()

        self.assertEqual(
            names,
            {
                "page_scan",
                "page_text",
                "page_click",
                "page_type",
                "page_select",
                "page_press",
                "page_scroll",
                "page_click_xy",
                "page_drag",
                "page_wait",
                "page_screenshot",
                "page_go",
                "page_back",
            },
        )

    @patch("tools.game_play.tools.time.sleep", return_value=None)
    def test_tool_calls_are_dispatched_to_playwright_owner_thread(self, _sleep):
        browser = _ThreadCheckingBrowser()
        session = GameBrowserSession(browser_factory=lambda **_: browser).start()
        tool = PageGoTool(page=session)
        structured_tool = tool.to_structured_tool()

        async def invoke_from_crewai_executor():
            return await asyncio.gather(
                structured_tool.ainvoke({"url": "https://example.com/0"}),
                structured_tool.ainvoke({"url": "https://example.com/1"}),
            )

        try:
            results = asyncio.run(invoke_from_crewai_executor())

            self.assertTrue(all(result.startswith("已跳转:") for result in results))
            self.assertEqual(browser._page.goto_thread_ids, [browser._page.owner_thread_id] * 2)
            self.assertNotEqual(browser._page.owner_thread_id, threading.get_ident())
        finally:
            session.close()

        self.assertEqual(browser.closed_thread_id, browser._page.owner_thread_id)

    @patch("tools.game_play.tools.time.sleep", return_value=None)
    def test_scan_and_click_use_identical_indexes(self, _sleep):
        title_only = _IndexedElement(title="播放")
        start_button = _IndexedElement(text="撕开案卷")
        page = _IndexedPage([title_only, start_button])

        snapshot = _scan(page)
        self.assertIn("[0] <button> title=播放", snapshot)
        self.assertIn("[1] <button> 撕开案卷", snapshot)
        self.assertIs(_get_element_by_idx(page, 0), title_only)
        self.assertIs(_get_element_by_idx(page, 1), start_button)

        mismatch = PageClickTool(page=page)._run(0, hint="撕开案卷")
        self.assertIn("与目标“撕开案卷”不符", mismatch)
        self.assertFalse(title_only.clicked)

        result = PageClickTool(page=page)._run(1, hint="撕开案卷")
        self.assertEqual(result, "已点击 [1] 撕开案卷")
        self.assertFalse(title_only.clicked)
        self.assertTrue(start_button.clicked)


if __name__ == "__main__":
    unittest.main()
