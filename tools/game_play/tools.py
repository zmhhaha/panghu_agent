"""
通用游戏试玩 — CrewAI 浏览器工具集。

设计原则（通用化，不绑定任何具体游戏）：
- 页面感知类工具把"当前 DOM"结构化成文本 + 带索引的可交互元素列表，
  这是喂给试玩员 LLM 的"眼睛"。
- 操作类工具按索引/选择器/文本把 LLM 的决策翻译成真实点击/输入。
- 每次 page_scan 重采快照；操作执行前也会 re-scan 确认元素仍存在，
  避免 DOM 变化后按旧索引错点。
"""
import html
import re
import time
from collections.abc import Callable

from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from tools.game_play.browser import GameBrowserSession

# 常见被忽略的控件（避免把隐藏/装饰元素当可交互）
_IGNORED_TAGS = {"script", "style", "noscript", "template", "head", "meta", "link"}

_INTERACTIVE_SELECTOR = ", ".join((
    "button",
    "a",
    "input",
    "select",
    "textarea",
    "summary",
    "[data-action]",
    '[role="button"]',
    '[role="link"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="tab"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="switch"]',
    '[role="textbox"]',
    '[contenteditable]:not([contenteditable="false"])',
))

MAX_TEXT_CHARS = 6000      # 可见文本截断上限
MAX_ELEMENTS = 60          # 可交互元素列表上限


PageTarget = Page | GameBrowserSession


def _on_page(target: PageTarget, operation: Callable[[Page], str]) -> str:
    """Run a whole tool operation without exposing Page across threads."""
    if isinstance(target, GameBrowserSession):
        return target.run(operation)
    return operation(target)


def _visible_el(el) -> bool:
    """Playwright 元素可见性判断。"""
    try:
        return el.is_visible()
    except Exception:
        return False


def _el_text(el) -> str:
    try:
        txt = (el.inner_text() or "").strip()
    except Exception:
        txt = ""
    if txt:
        return " ".join(txt.split())[:80]
    try:
        attr = (el.get_attribute("value") or "").strip()
        if attr:
            return f"value={attr[:40]}"
    except Exception:
        pass
    for placeholder_attr in ("placeholder", "aria-placeholder", "data-placeholder"):
        try:
            placeholder = (el.get_attribute(placeholder_attr) or "").strip()
            if placeholder:
                return f"{placeholder_attr}={placeholder[:40]}"
        except Exception:
            pass
    return ""


def _element_label(el) -> str:
    """Return the exact label used by both scanning and later index lookup."""
    text = _el_text(el)
    if text:
        return text
    for attr in ("data-action", "title", "aria-label", "data-tab", "name"):
        try:
            value = el.get_attribute(attr)
        except Exception:
            value = None
        if value:
            return f"{attr}={value}"
    try:
        associated_label = el.evaluate(
            "(e) => Array.from(e.labels || []).map((label) => label.innerText.trim()).filter(Boolean).join(' ')"
        )
        if associated_label:
            return str(associated_label)[:80]
    except Exception:
        pass
    try:
        tag = el.evaluate("(e) => e.tagName.toLowerCase()")
        if tag == "textarea":
            return "textarea"
        if tag == "input":
            return f"input[type={el.get_attribute('type') or 'text'}]"
        if el.get_attribute("contenteditable") not in (None, "false"):
            return "contenteditable"
    except Exception:
        pass
    return ""


def _indexed_elements(page: Page) -> list[tuple[object, str, str]]:
    """Build the canonical visible-element list used by scan and actions."""
    indexed = []
    locator = page.locator(_INTERACTIVE_SELECTOR)
    count = min(locator.count(), MAX_ELEMENTS + 20)
    for i in range(count):
        try:
            el = locator.nth(i)
            if not _visible_el(el):
                continue
            tag = el.evaluate("(e) => e.tagName.toLowerCase()")
            if tag in _IGNORED_TAGS:
                continue
            label = _element_label(el)
            if not label:
                continue
        except Exception:
            # React/Vue may replace a node while the list is being inspected.
            continue
        indexed.append((el, tag, label))
        if len(indexed) >= MAX_ELEMENTS:
            break
    return indexed


def _canvas_regions(page: Page) -> list[str]:
    """Describe visible canvas bounds for games that need coordinate input."""
    regions = []
    try:
        canvases = page.locator("canvas")
        count = min(canvases.count(), 10)
        for i in range(count):
            canvas = canvases.nth(i)
            if not _visible_el(canvas):
                continue
            box = canvas.bounding_box()
            if not box:
                continue
            label = _element_label(canvas) or "未命名画布"
            regions.append(
                f"[canvas:{i}] {label} "
                f"x={round(box['x'])}, y={round(box['y'])}, "
                f"width={round(box['width'])}, height={round(box['height'])}"
            )
    except Exception:
        pass
    return regions


def _safe_selector(s: str) -> str | None:
    """把用户给的任意字符串转成安全的文本选择器片段，防注入。"""
    if not s:
        return None
    return re.sub(r'["\\\']', "", s)[:60]


class PageScanInput(BaseModel):
    """Input for scanning the current page."""
    max_text_chars: int = Field(MAX_TEXT_CHARS, description="可见文本最大截断长度", ge=500, le=20000)


class PageScanTool(BaseTool):
    name: str = "page_scan"
    description: str = (
        "扫描当前页面，返回可见文本和所有可交互元素（按钮/链接/输入框/下拉，带稳定索引）。"
        "【每次决策前必须调用】。返回的 elements 里的 idx 用于 page_click / page_type 等操作。"
        "页面是有状态且动态变化的，任何操作完成后都应重新 page_scan 获取最新快照。"
    )
    args_schema: Type[BaseModel] = PageScanInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, max_text_chars: int = MAX_TEXT_CHARS) -> str:
        return _on_page(self._page, lambda page: _scan(page, max_text_chars))


def _scan(page: Page, max_text_chars: int = MAX_TEXT_CHARS) -> str:
    """扫描页面 → 结构化文本快照。"""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    # 1) 收集可交互元素
    elements = []
    try:
        for idx, (el, tag, label) in enumerate(_indexed_elements(page)):
            elements.append({"idx": idx, "tag": tag, "text": label, "attrs": _quick_attrs(el)})
    except Exception:
        pass

    # 2) 提取可见正文文本（去重、截断）
    body_text = ""
    try:
        body_text = page.inner_text("body")
    except Exception:
        try:
            body_text = page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            body_text = ""

    # 压缩空行
    body_text = re.sub(r"\n{3,}", "\n\n", body_text)
    if len(body_text) > max_text_chars:
        body_text = body_text[:max_text_chars] + "\n...(文本过长，已截断)"

    # 3) 组装
    lines = []
    lines.append(f"URL: {page.url}")
    lines.append(f"Title: {page.title() if page.title() else '(无标题)'}")
    lines.append("")
    lines.append("===== 可见文本 =====")
    lines.append(body_text or "(空)")
    lines.append("")
    lines.append("===== 可交互元素 (用 idx 操作) =====")
    if elements:
        for el in elements:
            attrs = f" {el['attrs']}" if el["attrs"] else ""
            lines.append(f"[{el['idx']}] <{el['tag']}> {el['text']}{attrs}")
    else:
        lines.append("(未找到可交互元素，可能需要等待页面加载)")
    lines.append("")
    lines.append(f"(共 {len(elements)} 个可交互元素)")

    canvas_regions = _canvas_regions(page)
    if canvas_regions:
        lines.append("")
        lines.append("===== Canvas / 坐标交互区域 =====")
        lines.extend(canvas_regions)
        lines.append("需要操作画布时使用 page_click_xy / page_drag；坐标必须位于上述边界内。")

    return "\n".join(lines)


def _quick_attrs(el) -> str:
    """提取少量关键属性，帮助 LLM 理解元素。"""
    parts = []
    for attr in ("data-action", "data-campaign", "data-mode", "data-testid", "type", "name"):
        v = el.get_attribute(attr)
        if v:
            parts.append(f"{attr}={v}")
    return " ".join(parts)


class PageTextInput(BaseModel):
    """Input for reading a specific element's text."""
    idx: int = Field(..., description="元素索引（来自最近一次 page_scan）")
    max_chars: int = Field(2000, description="最大返回字符数", ge=100, le=10000)


class PageTextTool(BaseTool):
    name: str = "page_text"
    description: str = "读取某个可交互元素（或其附近）的完整文本，用于看清长内容。"
    args_schema: Type[BaseModel] = PageTextInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, idx: int, max_chars: int = 2000) -> str:
        def read(page: Page) -> str:
            el = _get_element_by_idx(page, idx)
            if el is None:
                return f"错误：找不到索引 {idx} 的元素，请先 page_scan 获取最新快照"
            try:
                txt = el.inner_text() or ""
            except Exception as e:
                return f"读取失败: {e}"
            txt = re.sub(r"\n{3,}", "\n\n", txt)
            if len(txt) > max_chars:
                txt = txt[:max_chars] + "\n...(已截断)"
            return f"[{idx}] 内容:\n{txt}"

        return _on_page(self._page, read)


def _get_element_by_idx(page: Page, idx: int):
    """按 page_scan 的索引定位元素（重新扫描，索引以最新扫描为准）。"""
    try:
        indexed = _indexed_elements(page)
        if 0 <= idx < len(indexed):
            return indexed[idx][0]
    except Exception:
        pass
    return None


def _normalized_label(value: str) -> str:
    value = re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE).lower()
    for suffix in ("button", "link", "按钮", "链接"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return value


class PageClickInput(BaseModel):
    """Input for clicking an element."""
    idx: int = Field(..., description="元素索引（来自最近一次 page_scan）")
    hint: str = Field(..., min_length=1, description="从 page_scan 复制的目标可见文本，用于防止页面变化后错点")


class PageClickTool(BaseTool):
    name: str = "page_click"
    description: str = (
        "点击页面上某个可交互元素。idx 和 hint 必须来自最近一次 page_scan。"
        "若点击后页面跳转/更新，请再次 page_scan。"
    )
    args_schema: Type[BaseModel] = PageClickInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, idx: int, hint: str) -> str:
        def click(page: Page) -> str:
            el = _get_element_by_idx(page, idx)
            if el is None:
                return f"错误：找不到索引 {idx} 的元素（页面可能已变化），请重新 page_scan"
            actual_label = _element_label(el)
            expected = _normalized_label(hint)
            actual = _normalized_label(actual_label)
            if expected and actual and expected not in actual and actual not in expected:
                return (
                    f"错误：索引 {idx} 当前是“{actual_label}”，与目标“{hint}”不符。"
                    "页面可能已变化，请重新 page_scan，禁止继续猜索引。"
                )
            try:
                el.scroll_into_view_if_needed(timeout=5000)
                el.click(timeout=8000)
            except Exception as e:
                return f"点击失败: {e}"
            time.sleep(0.8)  # 等渲染
            return f"已点击 [{idx}] {actual_label}"

        return _on_page(self._page, click)


class PageTypeInput(BaseModel):
    """Input for typing into an input field."""
    idx: int = Field(..., description="输入框元素索引（来自最近一次 page_scan）")
    text: str = Field(..., description="要输入的文本")
    submit: bool = Field(True, description="输入后是否按 Enter 提交（表单）")


class PageTypeTool(BaseTool):
    name: str = "page_type"
    description: str = "在输入框/文本域中输入文本，可选择提交（Enter）。用于提问、填表、搜索等。"
    args_schema: Type[BaseModel] = PageTypeInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, idx: int, text: str, submit: bool = True) -> str:
        def type_text(page: Page) -> str:
            el = _get_element_by_idx(page, idx)
            if el is None:
                return f"错误：找不到索引 {idx} 的元素（页面可能已变化），请重新 page_scan"
            try:
                el.fill("", timeout=5000)
                el.fill(text, timeout=8000)
                if submit:
                    el.press("Enter", timeout=3000)
            except Exception as e:
                return f"输入失败: {e}"
            time.sleep(0.8)
            return f"已输入 '{text[:50]}' {'并提交' if submit else ''}"

        return _on_page(self._page, type_text)


class PageSelectInput(BaseModel):
    """Input for selecting an option in a dropdown."""
    idx: int = Field(..., description="下拉框元素索引（来自最近一次 page_scan）")
    value: str = Field(..., description="要选的值或选项文本")


class PageSelectTool(BaseTool):
    name: str = "page_select"
    description: str = "在下拉框（select）中选择一个选项。value 可以是选项的值或可见文本。"
    args_schema: Type[BaseModel] = PageSelectInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, idx: int, value: str) -> str:
        def select(page: Page) -> str:
            el = _get_element_by_idx(page, idx)
            if el is None:
                return f"错误：找不到索引 {idx} 的元素（页面可能已变化），请重新 page_scan"
            try:
                el.select_option(value, timeout=5000)
            except Exception:
                # 尝试按 label 选
                try:
                    el.select_option(label=value, timeout=5000)
                except Exception as e:
                    return f"下拉选择失败: {e}"
            time.sleep(0.5)
            return f"已选择 '{value}'"

        return _on_page(self._page, select)


class PagePressInput(BaseModel):
    """Input for pressing a key."""
    key: str = Field(..., description="要按的键：Enter / Escape / Tab / ArrowUp / ArrowDown 等")


class PagePressTool(BaseTool):
    name: str = "page_press"
    description: str = "按一个键盘键（Enter/Escape/Tab/方向键等），用于提交、关闭弹窗、切换选项。"
    args_schema: Type[BaseModel] = PagePressInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, key: str) -> str:
        def press(page: Page) -> str:
            try:
                page.keyboard.press(key)
            except Exception as e:
                return f"按键失败: {e}"
            time.sleep(0.5)
            return f"已按键 {key}"

        return _on_page(self._page, press)


class PageScrollInput(BaseModel):
    """Input for scrolling the page or a hovered game surface."""
    delta_y: int = Field(700, description="垂直滚动量；正数向下、负数向上", ge=-5000, le=5000)
    delta_x: int = Field(0, description="水平滚动量；正数向右、负数向左", ge=-5000, le=5000)


class PageScrollTool(BaseTool):
    name: str = "page_scroll"
    description: str = "滚动当前页面。长页面、隐藏在视口外的控件或滚轮类游戏操作可使用。滚动后必须重新 page_scan。"
    args_schema: Type[BaseModel] = PageScrollInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, delta_y: int = 700, delta_x: int = 0) -> str:
        def scroll(page: Page) -> str:
            try:
                page.mouse.wheel(delta_x, delta_y)
            except Exception as e:
                return f"滚动失败: {e}"
            time.sleep(0.5)
            return f"已滚动: delta_x={delta_x}, delta_y={delta_y}"

        return _on_page(self._page, scroll)


class PageClickXYInput(BaseModel):
    """Input for coordinate clicks on canvas or visual game surfaces."""
    x: float = Field(..., description="视口横坐标", ge=0, le=10000)
    y: float = Field(..., description="视口纵坐标", ge=0, le=10000)
    description: str = Field(..., min_length=1, description="依据画布边界判断的目标说明")


class PageClickXYTool(BaseTool):
    name: str = "page_click_xy"
    description: str = (
        "按视口坐标点击，供 canvas、棋盘、地图等没有 DOM 控件的游戏使用。"
        "只能使用 page_scan 返回的 canvas 边界内坐标；普通按钮仍用 page_click。"
    )
    args_schema: Type[BaseModel] = PageClickXYInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, x: float, y: float, description: str) -> str:
        def click_xy(page: Page) -> str:
            try:
                page.mouse.click(x, y)
            except Exception as e:
                return f"坐标点击失败: {e}"
            time.sleep(0.5)
            return f"已点击坐标 ({x}, {y}): {description}"

        return _on_page(self._page, click_xy)


class PageDragInput(BaseModel):
    """Input for drag gestures."""
    from_x: float = Field(..., description="起点横坐标", ge=0, le=10000)
    from_y: float = Field(..., description="起点纵坐标", ge=0, le=10000)
    to_x: float = Field(..., description="终点横坐标", ge=0, le=10000)
    to_y: float = Field(..., description="终点纵坐标", ge=0, le=10000)
    description: str = Field(..., min_length=1, description="拖拽目标与目的")


class PageDragTool(BaseTool):
    name: str = "page_drag"
    description: str = "执行鼠标拖拽，适用于拼图、棋子、滑块、地图和 canvas。操作后必须重新 page_scan。"
    args_schema: Type[BaseModel] = PageDragInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        description: str,
    ) -> str:
        def drag(page: Page) -> str:
            mouse_is_down = False
            try:
                page.mouse.move(from_x, from_y)
                page.mouse.down()
                mouse_is_down = True
                page.mouse.move(to_x, to_y, steps=12)
                page.mouse.up()
                mouse_is_down = False
            except Exception as e:
                if mouse_is_down:
                    try:
                        page.mouse.up()
                    except Exception:
                        pass
                return f"拖拽失败: {e}"
            time.sleep(0.5)
            return f"已拖拽 ({from_x}, {from_y}) -> ({to_x}, {to_y}): {description}"

        return _on_page(self._page, drag)


class PageWaitInput(BaseModel):
    """Input for waiting."""
    text: str = Field(default="", description="要等待出现的文本（空则只等固定时长）")
    seconds: float = Field(2.0, description="最大等待秒数", ge=0.5, le=30.0)


class PageWaitTool(BaseTool):
    name: str = "page_wait"
    description: str = "等待页面加载或某个文本出现。游戏加载/动画时需要。"
    args_schema: Type[BaseModel] = PageWaitInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, text: str = "", seconds: float = 2.0) -> str:
        def wait(page: Page) -> str:
            if text:
                try:
                    page.wait_for_selector(f"text={_safe_selector(text)}", timeout=int(seconds * 1000))
                    return f"已等到文本出现: {text}"
                except Exception:
                    return f"等待文本超时（{seconds}s）: {text}"
            time.sleep(seconds)
            return f"已等待 {seconds}s"

        return _on_page(self._page, wait)


class PageScreenshotInput(BaseModel):
    """Input for taking a screenshot."""
    name: str = Field(default="step", description="截图文件名前缀")


class PageScreenshotTool(BaseTool):
    name: str = "page_screenshot"
    description: str = "对当前页面截图并保存到输出目录，返回截图路径。用于保存游戏证据。"
    args_schema: Type[BaseModel] = PageScreenshotInput

    def __init__(self, page: PageTarget, out_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._page = page
        self._out_dir = out_dir

    def _run(self, name: str = "step") -> str:
        import os
        os.makedirs(self._out_dir, exist_ok=True)
        safe = re.sub(r"[^\w\-]", "_", name)[:40] or "step"
        path = os.path.join(self._out_dir, f"{safe}.png")

        def screenshot(page: Page) -> str:
            try:
                page.screenshot(path=path, full_page=False)
            except Exception as e:
                return f"截图失败: {e}"
            return f"截图已保存: {path}"

        return _on_page(self._page, screenshot)


class PageGoInput(BaseModel):
    """Input for navigating."""
    url: str = Field(..., description="要访问的完整 URL")


class PageGoTool(BaseTool):
    name: str = "page_go"
    description: str = "跳转到指定 URL。通常只在开局时使用一次。"
    args_schema: Type[BaseModel] = PageGoInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, url: str) -> str:
        def go(page: Page) -> str:
            try:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
            except PlaywrightTimeoutError:
                try:
                    body_text = page.inner_text("body").strip()
                except Exception:
                    body_text = ""
                if body_text:
                    return f"已打开页面（部分资源仍在加载）: {page.url}"
                return f"跳转超时且页面为空: {url}"
            except Exception as e:
                return f"跳转失败: {e}"
            time.sleep(1.0)
            return f"已跳转: {url}"

        return _on_page(self._page, go)


class PageBackInput(BaseModel):
    """Input for browser back navigation."""
    wait_seconds: float = Field(1.0, description="返回后等待页面稳定的秒数", ge=0, le=10)


class PageBackTool(BaseTool):
    name: str = "page_back"
    description: str = "返回上一页，用于技能探索或误入非游戏页面后的恢复。返回后必须重新 page_scan。"
    args_schema: Type[BaseModel] = PageBackInput

    def __init__(self, page: PageTarget, **kwargs):
        super().__init__(**kwargs)
        self._page = page

    def _run(self, wait_seconds: float = 1.0) -> str:
        def go_back(page: Page) -> str:
            previous_url = page.url
            try:
                response = page.go_back(timeout=20000, wait_until="domcontentloaded")
            except PlaywrightTimeoutError:
                response = None
            except Exception as e:
                return f"返回失败: {e}"
            time.sleep(wait_seconds)
            if response is None and page.url == previous_url:
                return "返回失败: 没有可返回的上一页"
            return f"已返回: {page.url}"

        return _on_page(self._page, go_back)


def make_game_tools(page: PageTarget, out_dir: str) -> list[BaseTool]:
    """创建通用游戏工具集（无游戏假设）。"""
    return [
        PageScanTool(page=page),
        PageTextTool(page=page),
        PageClickTool(page=page),
        PageTypeTool(page=page),
        PageSelectTool(page=page),
        PagePressTool(page=page),
        PageScrollTool(page=page),
        PageClickXYTool(page=page),
        PageDragTool(page=page),
        PageWaitTool(page=page),
        PageScreenshotTool(page=page, out_dir=out_dir),
        PageGoTool(page=page),
        PageBackTool(page=page),
    ]
