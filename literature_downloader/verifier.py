"""Deterministic PDF verification checks."""

from __future__ import annotations

import re
from pathlib import Path

from .config import Settings, settings
from .models import Paper, VerificationResult


def _extract_text(path: Path) -> tuple[str, str]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text, ""
    except Exception as exc:
        return "", f"text extraction failed: {type(exc).__name__}: {exc}"


def _title_match(title: str, text: str) -> bool:
    words = [word.lower() for word in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{3,}", title)]
    if not words:
        return True
    haystack = text[:20_000].lower()
    matches = sum(1 for word in words if word in haystack)
    return matches >= max(1, min(3, len(words) // 3))


def verify_pdf(paper: Paper, config: Settings = settings) -> VerificationResult:
    path = Path(paper.pdf_path)
    if not path.exists():
        return VerificationResult(paper.title, "fail", str(path), reason="PDF file does not exist")
    size = path.stat().st_size
    if size < config.min_pdf_bytes:
        return VerificationResult(paper.title, "fail", str(path), size=size, reason=f"PDF is too small ({size} bytes)")
    try:
        with path.open("rb") as stream:
            header = stream.read(5)
        if header != b"%PDF-":
            return VerificationResult(paper.title, "fail", str(path), size=size, reason="invalid PDF signature")
    except OSError as exc:
        return VerificationResult(paper.title, "fail", str(path), size=size, reason=f"cannot read PDF: {exc}")

    text, extraction_error = _extract_text(path)
    chars = len(re.sub(r"\s+", "", text))
    if extraction_error:
        return VerificationResult(paper.title, "uncertain", str(path), size, chars, notes=extraction_error)
    if chars < config.min_text_chars:
        return VerificationResult(paper.title, "uncertain", str(path), size, chars, notes=f"text is short ({chars} characters), possibly scanned PDF")
    if not _title_match(paper.title, text):
        return VerificationResult(paper.title, "uncertain", str(path), size, chars, notes="PDF text does not clearly match the metadata title")
    return VerificationResult(paper.title, "pass", str(path), size, chars, notes=f"readable text extracted ({chars} characters)")
