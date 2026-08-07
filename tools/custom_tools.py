import ipaddress
import os
import re
import socket
import time
import urllib.parse
from pathlib import Path
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


def _resolve_agent_path(file_path: str) -> Path:
    """Resolve a path inside the configured agent file root."""
    root = Path(os.getenv("AGENT_FILE_ROOT") or Path.cwd()).resolve()
    candidate = Path(file_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"文件路径超出允许目录: {root}") from exc
    return resolved


def _validate_public_url(url: str) -> str:
    """Reject non-HTTP URLs and hosts that resolve to non-public addresses."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("仅允许 http 或 https URL")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL 主机无效或包含不允许的凭据")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("不允许访问本机或内部域名")

    try:
        direct_ip = ipaddress.ip_address(hostname.split("%", 1)[0])
        addresses = {direct_ip}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0].split("%", 1)[0])
                for item in socket.getaddrinfo(
                    hostname,
                    parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法解析 URL 主机: {hostname}") from exc

    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("不允许访问本机、私网、保留或链路本地地址")
    return urllib.parse.urlunparse(parsed)


def _max_web_fetch_bytes() -> int:
    try:
        configured = int(os.getenv("WEB_FETCH_MAX_BYTES", "5242880"))
    except ValueError:
        configured = 5 * 1024 * 1024
    return min(max(configured, 64 * 1024), 20 * 1024 * 1024)


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
            path = _resolve_agent_path(file_path)
            with path.open("r", encoding="utf-8") as f:
                content = f.read()
            return f"文件内容 ({path}):\n{content}"
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
            path = _resolve_agent_path(file_path)
            with path.open("w", encoding="utf-8") as f:
                f.write(content)
            return f"文件已写入: {path}"
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

        try:
            safe_url = _validate_public_url(url)
        except ValueError as exc:
            return f"获取网页失败 ({url}): URL 被安全策略拒绝: {exc}"

        def _do_fetch():
            current_url = safe_url
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            for _ in range(6):
                current_url = _validate_public_url(current_url)
                resp = requests.get(
                    current_url,
                    timeout=30,
                    headers=headers,
                    allow_redirects=False,
                    stream=True,
                )
                if resp.status_code in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("Location")
                    resp.close()
                    if not location:
                        raise ValueError("重定向响应缺少 Location")
                    current_url = urllib.parse.urljoin(current_url, location)
                    continue

                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "").lower()
                allowed_types = ("text/", "application/json", "application/xml", "application/xhtml+xml")
                if content_type and not content_type.startswith(allowed_types):
                    resp.close()
                    raise ValueError(f"不支持的网页内容类型: {content_type.split(';', 1)[0]}")

                max_bytes = _max_web_fetch_bytes()
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    resp.close()
                    raise ValueError(f"网页内容超过 {max_bytes} 字节限制")
                chunks = []
                size = 0
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        resp.close()
                        raise ValueError(f"网页内容超过 {max_bytes} 字节限制")
                    chunks.append(chunk)
                encoding = resp.encoding or "utf-8"
                body = b"".join(chunks).decode(encoding, errors="replace")
                resp.close()
                return current_url, body
            raise ValueError("网页重定向次数过多")

        try:
            final_url, response_text = _retry(_do_fetch, attempts=2, delay=1.0)
        except Exception as e:
            error_detail = (
                f"URL 被安全策略拒绝: {e}"
                if isinstance(e, ValueError)
                else f"{type(e).__name__}: {e}"
            )
            return (
                f"获取网页失败 ({url}): {error_detail}\n"
                f"建议：检查 URL 是否正确，或尝试搜索其他来源。"
            )

        try:
            # 尝试用 BeautifulSoup 提取正文
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(response_text, "html.parser")

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
                text = re.sub(r"<style[^>]*>.*?</style>", "", response_text, flags=re.DOTALL | re.IGNORECASE)
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

            return f"## 网页内容: {final_url}\n\n{text}"

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
