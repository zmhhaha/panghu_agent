import re
import time
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type


# ============================================================
#  通用工具函数
# ============================================================

def _retry(func, attempts=3, delay=2.0, backoff=2.0):
    """带退避的重试器"""
    last_err = None
    for i in range(attempts):
        try:
            return func()
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                wait = delay * (backoff ** i)
                time.sleep(wait)
    raise last_err


# ============================================================
#  文件读写工具
# ============================================================

class FileReadInput(BaseModel):
    """Input for reading a file."""
    file_path: str = Field(..., description="要读取的文件路径")


class FileReadTool(BaseTool):
    name: str = "file_read"
    description: str = "读取本地文件内容"
    args_schema: Type[BaseModel] = FileReadInput

    def _run(self, file_path: str) -> str:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            return f"文件内容 ({file_path}):\n{content}"
        except Exception as e:
            return f"读取文件失败: {e}"


class FileWriteInput(BaseModel):
    """Input for writing a file."""
    file_path: str = Field(..., description="要写入的文件路径")
    content: str = Field(..., description="要写入的内容")


class FileWriteTool(BaseTool):
    name: str = "file_write"
    description: str = "将内容写入本地文件"
    args_schema: Type[BaseModel] = FileWriteInput

    def _run(self, file_path: str, content: str) -> str:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"文件已写入: {file_path}"
        except Exception as e:
            return f"写入文件失败: {e}"


# ============================================================
#  网页搜索工具 — 基于 DDGS (DuckDuckGo)，免费无需 API Key
# ============================================================

class WebSearchInput(BaseModel):
    """Input for web search."""
    query: str = Field(..., description="搜索关键词（支持中英文）")
    max_results: int = Field(5, description="最大返回结果数，默认 5，范围 3-10", ge=3, le=10)


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = (
        "使用 DuckDuckGo 搜索引擎搜索网页，免费无需 API Key。"
        "返回结果包含标题、URL 和摘要。用于查找最新信息、官方文档、新闻报道等。"
        "如果搜索失败，可以尝试用更简短的关键词，或者直接用 web_fetch 抓取已知 URL。"
    )
    args_schema: Type[BaseModel] = WebSearchInput

    def _run(self, query: str, max_results: int = 5) -> str:
        # 优先尝试 ddgs（新版包名）
        ddgs_imported = False
        for package_name in ("ddgs", "duckduckgo_search"):
            try:
                if package_name == "ddgs":
                    from ddgs import DDGS  # type: ignore
                else:
                    from duckduckgo_search import DDGS  # type: ignore
                ddgs_imported = True
                break
            except ImportError:
                continue

        if not ddgs_imported:
            return (
                "错误：未安装搜索库，请运行: pip install ddgs\n"
                "（如果 pip install ddgs 失败，也可以尝试: pip install duckduckgo_search）"
            )

        def _do_search():
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append(r)
            return results

        try:
            results = _retry(_do_search, attempts=2, delay=1.5)
        except Exception as e:
            err_msg = str(e)[:300]
            return (
                f"搜索失败 ({query}): {type(e).__name__}: {err_msg}\n\n"
                f"建议：\n"
                f"1. 尝试用更简短的关键词重新搜索（如只保留 2-3 个核心词）\n"
                f"2. 如果你知道具体网站的 URL，用 web_fetch 工具直接抓取\n"
                f"3. 尝试用英文关键词搜索"
            )

        if not results:
            return (
                f"未找到与 '{query}' 相关的结果。\n"
                f"建议：尝试更换关键词，或用英文搜索。"
            )

        output = f"## 搜索结果: {query}\n\n"
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            href = r.get("href", "")
            body = r.get("body", "无摘要")
            output += f"**{i}. [{title}]({href})**\n"
            output += f"   {body}\n\n"

        return output


# ============================================================
#  网页抓取工具 — 直接抓取 URL 内容
# ============================================================

class WebFetchInput(BaseModel):
    """Input for fetching a web page and extracting its text content."""
    url: str = Field(..., description="要获取内容的网页 URL（完整地址，如 https://example.com/article）")


class WebFetchTool(BaseTool):
    name: str = "web_fetch"
    description: str = (
        "获取指定 URL 的网页内容并提取纯文本。用于阅读搜索结果中的具体文章、"
        "官方文档、新闻报道等。注意：每次调用只能获取一个 URL，请优先选择最相关的链接。"
    )
    args_schema: Type[BaseModel] = WebFetchInput

    def _run(self, url: str) -> str:
        try:
            import requests
        except ImportError:
            return "错误：未安装 requests 库，请运行: pip install requests"

        def _do_fetch():
            resp = requests.get(
                url,
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            resp.raise_for_status()
            return resp

        try:
            resp = _retry(_do_fetch, attempts=2, delay=1.0)
        except Exception as e:
            return (
                f"获取网页失败 ({url}): {type(e).__name__}: {e}\n"
                f"建议：检查 URL 是否正确，或尝试搜索其他来源。"
            )

        try:
            # 尝试用 BeautifulSoup 提取正文
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(resp.text, "html.parser")

                # 移除无关标签
                for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                    tag.decompose()

                # 优先提取 <article> / <main> / <body> 中的内容
                container = soup.find("article") or soup.find("main") or soup.find("body")
                if container:
                    text = container.get_text(separator="\n", strip=True)
                else:
                    text = soup.get_text(separator="\n", strip=True)

                # 压缩多余空行
                text = re.sub(r"\n{3,}", "\n\n", text)
                text = text.strip()

            except ImportError:
                # 纯正则回退：去掉 HTML 标签
                text = re.sub(r"<style[^>]*>.*?</style>", "", resp.text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"&nbsp;", " ", text)
                text = re.sub(r"&amp;", "&", text)
                text = re.sub(r"&lt;", "<", text)
                text = re.sub(r"&gt;", ">", text)
                text = re.sub(r"\s+", " ", text).strip()

            # 限制输出长度，避免上下文爆炸
            max_chars = 10000
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n...(内容过长，已截断，建议抓取更具体的子页面)"

            return f"## 网页内容: {url}\n\n{text}"

        except Exception as e:
            return f"解析网页失败 ({url}): {type(e).__name__}: {e}"


# ============================================================
#  多源交叉验证工具
# ============================================================

class MultiFetchInput(BaseModel):
    """Input for fetching multiple URLs in one call."""
    urls: str = Field(..., description="要同时获取的多个 URL，用换行或逗号分隔")


class MultiFetchTool(BaseTool):
    name: str = "multi_fetch"
    description: str = (
        "一次获取多个 URL 的内容并汇总。用于交叉验证同一事实在不同来源中的描述，"
        "或同时查看多个相关页面的信息。URL 之间用换行或逗号分隔，建议不超过 5 个。"
    )
    args_schema: Type[BaseModel] = MultiFetchInput

    def _run(self, urls: str) -> str:
        # 解析 URL 列表
        url_list = re.split(r"[\n,;]+", urls)
        url_list = [u.strip() for u in url_list if u.strip()]

        if not url_list:
            return "错误：未提供有效的 URL"

        if len(url_list) > 5:
            url_list = url_list[:5]

        fetcher = WebFetchTool()
        results = []
        for i, url in enumerate(url_list, 1):
            results.append(f"--- 来源 {i}/{len(url_list)} ---")
            results.append(fetcher._run(url))
            results.append("")

        return "\n".join(results)
