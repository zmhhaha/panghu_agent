"""PDF download strategies and one collection round."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .models import DownloadResult, Paper


def safe_filename(value: str, suffix: str = ".pdf") -> str:
    name = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", value.strip())
    name = name.strip("._")[:120] or "untitled"
    return name + suffix


def download_pdf(url: str, target_path: str | Path, config: Settings = settings) -> dict[str, Any]:
    """Download a URL to a temporary file, accepting only PDF-like content."""
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".part")
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "PanghuLiteratureDownloader/1.0",
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.2",
            },
        )
        with urllib.request.urlopen(request, timeout=config.download_timeout) as response:
            content_type = str(response.headers.get("Content-Type", "")).lower()
            length = int(response.headers.get("Content-Length", "0") or 0)
            if length > config.max_pdf_bytes:
                return {"ok": False, "path": str(target), "size": length, "error": "PDF exceeds configured size limit"}
            data = response.read(config.max_pdf_bytes + 1)
        if len(data) > config.max_pdf_bytes:
            return {"ok": False, "path": str(target), "size": len(data), "error": "PDF exceeds configured size limit"}
        if len(data) < 5 or not data.startswith(b"%PDF-"):
            detail = f"Content-Type: {content_type}" if content_type else "missing PDF signature"
            return {"ok": False, "path": str(target), "size": len(data), "error": f"Not a PDF ({detail})"}
        temp.write_bytes(data)
        temp.replace(target)
        return {"ok": True, "path": str(target), "size": len(data), "error": ""}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        return {"ok": False, "path": str(target), "size": 0, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _arxiv_id(paper: Paper) -> str:
    value = paper.arxiv_id or paper.identifiers.get("arxiv", "")
    match = re.search(r"(\d{4}\.\d{4,}(?:v\d+)?)", value)
    return match.group(1) if match else value.strip("/").split("/")[-1]


def _semantic_id(paper: Paper) -> str:
    return paper.identifiers.get("semantic_scholar") or (f"DOI:{paper.doi}" if paper.doi else paper.title)


def _unpaywall_url(doi: str, config: Settings) -> str:
    if not doi or not config.contact_email:
        return ""
    try:
        request = urllib.request.Request(
            f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(config.contact_email)}",
            headers={"User-Agent": "PanghuLiteratureDownloader/1.0"},
        )
        with urllib.request.urlopen(request, timeout=config.download_timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        location = data.get("best_oa_location") or ((data.get("oa_locations") or [None])[0]) or {}
        return str(location.get("url_for_pdf") or location.get("url") or "")
    except Exception:
        return ""


def _semantic_pdf_url(paper: Paper, config: Settings) -> str:
    paper_id = _semantic_id(paper)
    if not paper_id:
        return ""
    try:
        query = urllib.parse.urlencode({"fields": "openAccessPdf"})
        request = urllib.request.Request(
            f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(paper_id, safe=':')}?{query}",
            headers={"User-Agent": "PanghuLiteratureDownloader/1.0", **({"x-api-key": config.semantic_scholar_api_key} if config.semantic_scholar_api_key else {})},
        )
        with urllib.request.urlopen(request, timeout=config.download_timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        return str((data.get("openAccessPdf") or {}).get("url") or "")
    except Exception:
        return ""


def collect_paper(paper: Paper, target_dir: str | Path, config: Settings = settings) -> DownloadResult:
    """Try arXiv -> DOI/OA -> Semantic Scholar -> metadata PDF URL."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def attempt_download(source: str, url: str) -> DownloadResult | None:
        if not url or url in seen_urls:
            return None
        seen_urls.add(url)
        filename_prefix = f"{paper.local_id or 'paper'}-{safe_filename(paper.title)}"
        target = target_dir / filename_prefix
        started = time.perf_counter()
        result = download_pdf(url, target, config)
        attempt = {
            "source": source,
            "url": url,
            "ok": bool(result.get("ok")),
            "size": int(result.get("size") or 0),
            "error": str(result.get("error") or ""),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
        attempts.append(attempt)
        if result.get("ok"):
            return DownloadResult(True, str(result["path"]), int(result["size"]), source, "", attempts)
        return None

    arxiv_id = _arxiv_id(paper)
    if arxiv_id:
        success = attempt_download("arXiv", f"https://arxiv.org/pdf/{arxiv_id}.pdf")
        if success:
            return success

    if paper.doi:
        success = attempt_download("DOI", f"https://doi.org/{paper.doi}")
        if success:
            return success
        oa_url = _unpaywall_url(paper.doi, config)
        if oa_url:
            success = attempt_download("Unpaywall", oa_url)
            if success:
                return success

    ss_url = _semantic_pdf_url(paper, config)
    if ss_url:
        success = attempt_download("Semantic Scholar", ss_url)
        if success:
            return success

    if paper.pdf_url:
        success = attempt_download("metadata_pdf", paper.pdf_url)
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
