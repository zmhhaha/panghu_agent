"""PDF download strategies and one collection round."""

from __future__ import annotations

import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .models import DownloadResult, Paper


_TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MAX_REQUESTS_PER_PAPER = 30
_HOST_REQUEST_TIMES: dict[str, float] = {}
_HOST_REQUEST_LOCK = threading.Lock()
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)


class _PdfLinkParser(HTMLParser):
    """Extract standard scholarly PDF metadata without site-specific rules."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).lower(): str(value or "").strip() for key, value in attrs}
        if tag.lower() == "meta":
            name = (values.get("name") or values.get("property") or values.get("itemprop") or "").lower()
            if name in {
                "citation_pdf_url",
                "eprints.document_url",
                "wkhealth_pdf_url",
                "pdf_url",
            }:
                self._add(values.get("content", ""))
        elif tag.lower() == "link":
            content_type = values.get("type", "").lower()
            href = values.get("href", "")
            if "application/pdf" in content_type or _looks_like_pdf_url(href):
                self._add(href)
        elif tag.lower() == "a":
            href = values.get("href", "")
            if _looks_like_pdf_url(href):
                self._add(href)

    def _add(self, value: str) -> None:
        if value and value not in self.urls:
            self.urls.append(value)


def _looks_like_pdf_url(value: str) -> bool:
    path = urllib.parse.urlsplit(str(value or "")).path.lower()
    return path.endswith((".pdf", "/pdf", "/pdf/"))


def _extract_pdf_urls(data: bytes, base_url: str, link_header: str = "") -> list[str]:
    urls: list[str] = []
    parser = _PdfLinkParser()
    try:
        parser.feed(data[:2 * 1024 * 1024].decode("utf-8", errors="replace"))
    except Exception:
        pass
    candidates = list(parser.urls)
    for match in re.finditer(r"<([^>]+)>\s*;[^,]*\btype\s*=\s*[\"']?application/pdf", link_header, re.I):
        candidates.append(match.group(1))
    for candidate in candidates:
        resolved = urllib.parse.urljoin(base_url, candidate.strip())
        parsed = urllib.parse.urlsplit(resolved)
        if parsed.scheme in {"http", "https"} and resolved != base_url and resolved not in urls:
            urls.append(resolved)
        if len(urls) >= 12:
            break
    return urls


def _wait_for_host(url: str, interval_ms: int) -> None:
    if interval_ms <= 0:
        return
    host = urllib.parse.urlsplit(url).netloc.lower()
    if not host:
        return
    interval = interval_ms / 1000.0
    with _HOST_REQUEST_LOCK:
        now = time.monotonic()
        scheduled = max(now, _HOST_REQUEST_TIMES.get(host, 0.0) + interval)
        _HOST_REQUEST_TIMES[host] = scheduled
    delay = scheduled - now
    if delay > 0:
        time.sleep(delay)


def _request_headers(config: Settings, referer: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/pdf,application/octet-stream;q=0.9,text/html;q=0.5,*/*;q=0.2",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.7",
        "Cache-Control": "no-cache",
    }
    if config.contact_email:
        headers["From"] = config.contact_email
    if referer.startswith(("http://", "https://")):
        headers["Referer"] = referer
    return headers


def safe_filename(value: str, suffix: str = ".pdf") -> str:
    name = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", value.strip())
    name = name.strip("._")[:120] or "untitled"
    return name + suffix


def download_pdf(
    url: str,
    target_path: str | Path,
    config: Settings = settings,
    *,
    referer: str = "",
    opener: Any = None,
) -> dict[str, Any]:
    """Download a URL and expose PDF links found on scholarly landing pages."""
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    try:
        total_attempts = config.download_retries + 1
        for request_attempt in range(1, total_attempts + 1):
            _wait_for_host(url, config.download_request_interval_ms)
            request = urllib.request.Request(url, headers=_request_headers(config, referer))
            try:
                open_url = opener.open if opener is not None else urllib.request.urlopen
                with open_url(request, timeout=config.download_timeout) as response:
                    content_type = str(response.headers.get("Content-Type", "")).lower()
                    length = int(response.headers.get("Content-Length", "0") or 0)
                    final_url = str(response.geturl() or url)
                    status_code = int(getattr(response, "status", 200) or 200)
                    link_header = str(response.headers.get("Link", "") or "")
                    if length > config.max_pdf_bytes:
                        return {
                            "ok": False,
                            "path": str(target),
                            "size": length,
                            "error": "PDF exceeds configured size limit",
                            "status_code": status_code,
                            "final_url": final_url,
                            "request_attempts": request_attempt,
                            "pdf_urls": [],
                        }
                    data = response.read(config.max_pdf_bytes + 1)
                if len(data) > config.max_pdf_bytes:
                    return {
                        "ok": False,
                        "path": str(target),
                        "size": len(data),
                        "error": "PDF exceeds configured size limit",
                        "status_code": status_code,
                        "final_url": final_url,
                        "request_attempts": request_attempt,
                        "pdf_urls": [],
                    }
                if len(data) >= 5 and data.startswith(b"%PDF-"):
                    temp.write_bytes(data)
                    temp.replace(target)
                    return {
                        "ok": True,
                        "path": str(target),
                        "size": len(data),
                        "error": "",
                        "status_code": status_code,
                        "final_url": final_url,
                        "request_attempts": request_attempt,
                        "pdf_urls": [],
                    }
                detail = f"Content-Type: {content_type}" if content_type else "missing PDF signature"
                return {
                    "ok": False,
                    "path": str(target),
                    "size": len(data),
                    "error": f"Not a PDF ({detail})",
                    "status_code": status_code,
                    "final_url": final_url,
                    "request_attempts": request_attempt,
                    "pdf_urls": _extract_pdf_urls(data, final_url, link_header),
                }
            except urllib.error.HTTPError as exc:
                status_code = int(exc.code or 0)
                if status_code in _TRANSIENT_HTTP_STATUS and request_attempt < total_attempts:
                    retry_after = str(exc.headers.get("Retry-After", "") or "") if exc.headers else ""
                    delay = min(float(retry_after), 10.0) if retry_after.isdigit() else (
                        config.download_retry_backoff_ms / 1000.0 * (2 ** (request_attempt - 1))
                    )
                    time.sleep(delay)
                    continue
                return {
                    "ok": False,
                    "path": str(target),
                    "size": 0,
                    "error": f"HTTPError: {exc}",
                    "status_code": status_code,
                    "final_url": str(exc.geturl() or url),
                    "request_attempts": request_attempt,
                    "pdf_urls": [],
                }
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if request_attempt < total_attempts:
                    time.sleep(config.download_retry_backoff_ms / 1000.0 * (2 ** (request_attempt - 1)))
                    continue
                return {
                    "ok": False,
                    "path": str(target),
                    "size": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                    "status_code": 0,
                    "final_url": url,
                    "request_attempts": request_attempt,
                    "pdf_urls": [],
                }
            except ValueError as exc:
                return {
                    "ok": False,
                    "path": str(target),
                    "size": 0,
                    "error": f"ValueError: {exc}",
                    "status_code": 0,
                    "final_url": url,
                    "request_attempts": request_attempt,
                    "pdf_urls": [],
                }
        raise RuntimeError("download retry loop exited unexpectedly")
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _arxiv_id(paper: Paper) -> str:
    value = paper.arxiv_id or paper.identifiers.get("arxiv", "")
    match = re.search(r"(\d{4}\.\d{4,}(?:v\d+)?)", value)
    return match.group(1) if match else value.strip("/").split("/")[-1]


def _semantic_id(paper: Paper) -> str:
    # A title lookup requires a separate Semantic Scholar search for every
    # paper and made large collections serially spend most of their time in
    # rate-limited metadata calls. Search results already carry a paper ID or
    # DOI when that route is meaningful.
    return paper.identifiers.get("semantic_scholar") or (f"DOI:{paper.doi}" if paper.doi else "")


def _request_json(url: str, config: Settings, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = _request_headers(config)
    request_headers["Accept"] = "application/json"
    request_headers.update(headers or {})
    total_attempts = config.download_retries + 1
    for request_attempt in range(1, total_attempts + 1):
        _wait_for_host(url, config.download_request_interval_ms)
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=config.download_timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return payload if isinstance(payload, dict) else {}
        except urllib.error.HTTPError as exc:
            if int(exc.code or 0) not in _TRANSIENT_HTTP_STATUS or request_attempt >= total_attempts:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if request_attempt >= total_attempts:
                raise
        time.sleep(config.download_retry_backoff_ms / 1000.0 * (2 ** (request_attempt - 1)))
    return {}


def _unpaywall_urls(doi: str, config: Settings) -> list[str]:
    if not doi or not config.contact_email:
        return []
    try:
        data = _request_json(
            f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(config.contact_email)}",
            config,
        )
        urls: list[str] = []
        locations = []
        best = data.get("best_oa_location")
        if isinstance(best, dict):
            locations.append(best)
        locations.extend(item for item in (data.get("oa_locations") or []) if isinstance(item, dict))
        for location in locations:
            for key in ("url_for_pdf", "url_for_landing_page", "url"):
                value = str(location.get(key) or "").strip()
                if value and value not in urls:
                    urls.append(value)
        return urls[:12]
    except Exception:
        return []


def _openalex_oa_urls(paper: Paper, config: Settings) -> list[str]:
    """Refresh OA locations for records that did not originate in OpenAlex."""
    if not paper.doi or (paper.identifiers or {}).get("openalex"):
        return []
    try:
        work_id = urllib.parse.quote(f"https://doi.org/{paper.doi}", safe="")
        query = urllib.parse.urlencode({"mailto": config.contact_email}) if config.contact_email else ""
        data = _request_json(f"https://api.openalex.org/works/{work_id}{'?' + query if query else ''}", config)
        candidates = [data.get("best_oa_location"), data.get("primary_location"), *(data.get("locations") or [])]
        urls: list[str] = []
        for location in candidates:
            if not isinstance(location, dict):
                continue
            for key in ("pdf_url", "landing_page_url"):
                value = str(location.get(key) or "").strip()
                if value and value not in urls:
                    urls.append(value)
        return urls[:12]
    except Exception:
        return []


def _unpaywall_url(doi: str, config: Settings) -> str:
    """Compatibility wrapper for callers that only need the best URL."""
    return (_unpaywall_urls(doi, config) or [""])[0]


def _semantic_pdf_url(paper: Paper, config: Settings) -> str:
    if not config.semantic_scholar_api_key and not paper.identifiers.get("semantic_scholar"):
        return ""
    paper_id = _semantic_id(paper)
    if not paper_id:
        return ""
    try:
        query = urllib.parse.urlencode({"fields": "openAccessPdf"})
        data = _request_json(
            f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(paper_id, safe=':')}?{query}",
            config,
            {"x-api-key": config.semantic_scholar_api_key} if config.semantic_scholar_api_key else {},
        )
        return str((data.get("openAccessPdf") or {}).get("url") or "")
    except Exception:
        return ""


def collect_paper(paper: Paper, target_dir: str | Path, config: Settings = settings) -> DownloadResult:
    """Try direct and discovered legal OA routes before metadata fallbacks."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    seen_requests: set[tuple[str, bool]] = set()
    session_opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def attempt_download(source: str, url: str, *, referer: str = "") -> DownloadResult | None:
        request_key = (url, bool(referer))
        if not url or request_key in seen_requests or len(seen_requests) >= _MAX_REQUESTS_PER_PAPER:
            return None
        seen_requests.add(request_key)
        filename_prefix = f"{paper.local_id or 'paper'}-{safe_filename(paper.title)}"
        target = target_dir / filename_prefix
        started = time.perf_counter()
        result = download_pdf(url, target, config, referer=referer, opener=session_opener)
        attempt = {
            "source": source,
            "url": url,
            "ok": bool(result.get("ok")),
            "size": int(result.get("size") or 0),
            "error": str(result.get("error") or ""),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "status_code": int(result.get("status_code") or 0),
            "final_url": str(result.get("final_url") or url),
            "request_attempts": int(result.get("request_attempts") or 1),
        }
        attempts.append(attempt)
        if result.get("ok"):
            return DownloadResult(True, str(result["path"]), int(result["size"]), source, "", attempts)
        landing_url = str(result.get("final_url") or url)
        for discovered_url in result.get("pdf_urls") or []:
            success = attempt_download(f"{source}:html_pdf", str(discovered_url), referer=landing_url)
            if success:
                return success
        return None

    arxiv_id = _arxiv_id(paper)
    if arxiv_id:
        success = attempt_download("arXiv", f"https://arxiv.org/pdf/{arxiv_id}.pdf")
        if success:
            return success

    # OpenAlex and Crossref can return several locations. A publisher PDF
    # link may be blocked while an institutional repository link is usable.
    identifiers = paper.identifiers or {}
    alternate_urls: list[str] = []
    for key in ("openalex_pdf_urls", "crossref_pdf_urls", "pdf_urls"):
        values = identifiers.get(key) or []
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            alternate_urls.extend(str(value).strip() for value in values if str(value).strip())
    metadata_urls = [paper.pdf_url, *alternate_urls]
    for metadata_url in metadata_urls:
        success = attempt_download("metadata_pdf", metadata_url)
        if success:
            return success

    landing_urls: list[str] = []
    for key in ("openalex_landing_urls", "landing_urls"):
        values = identifiers.get(key) or []
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            landing_urls.extend(str(value).strip() for value in values if str(value).strip())
    if paper.doi:
        for oa_url in _unpaywall_urls(paper.doi, config):
            success = attempt_download("Unpaywall", oa_url)
            if success:
                return success

    for landing_url in landing_urls[:8]:
        success = attempt_download("metadata_landing", landing_url)
        if success:
            return success

    if paper.doi:
        for oa_url in _openalex_oa_urls(paper, config):
            success = attempt_download("OpenAlex OA", oa_url)
            if success:
                return success
        success = attempt_download("DOI", f"https://doi.org/{paper.doi}")
        if success:
            return success

    ss_url = _semantic_pdf_url(paper, config)
    if ss_url:
        success = attempt_download("Semantic Scholar", ss_url)
        if success:
            return success

    pmcid = str(paper.identifiers.get("pmcid") or paper.identifiers.get("pmc") or "").strip()
    if pmcid:
        pmcid = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
        success = attempt_download("PubMed Central", f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/")
        if success:
            return success

    # The search report's source URL may itself resolve to an openly hosted PDF.
    # Keep it as the last fallback; HTML landing pages are rejected by the PDF
    # signature check and remain visible in the collection report.
    if paper.url:
        success = attempt_download("metadata_url", paper.url)
        if success:
            return success

    error = attempts[-1]["error"] if attempts else "No downloadable PDF URL found"
    return DownloadResult(False, "", 0, "", error, attempts)
