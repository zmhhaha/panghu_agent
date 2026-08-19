"""Small serializable models used by the downloader pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Paper:
    title: str
    authors: str = ""
    date: str = ""
    doi: str = ""
    arxiv_id: str = ""
    pmid: str = ""
    provider: str = ""
    providers: list[str] = field(default_factory=list)
    abstract: str = ""
    url: str = ""
    pdf_url: str = ""
    venue: str = ""
    cited_by_count: int = 0
    open_access: bool = False
    identifiers: dict[str, str] = field(default_factory=dict)
    pdf_path: str = ""
    pdf_status: str = "pending_download"
    verification_status: str = ""
    relevance_score: float = 0.0
    local_id: int | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Paper":
        identifiers = dict(record.get("identifiers") or {})
        arxiv_id = str(record.get("arxiv_id") or identifiers.get("arxiv") or "")
        pmid = str(record.get("pmid") or identifiers.get("pmid") or "")
        providers = list(record.get("providers") or [])
        provider = str(record.get("provider") or (providers[0] if providers else ""))
        if provider and provider not in providers:
            providers.insert(0, provider)
        return cls(
            title=str(record.get("title") or "").strip(),
            authors=str(record.get("authors") or "").strip(),
            date=str(record.get("date") or "")[:32],
            doi=str(record.get("doi") or "").strip().lower(),
            arxiv_id=arxiv_id,
            pmid=pmid,
            provider=provider,
            providers=providers,
            abstract=str(record.get("abstract") or ""),
            url=str(record.get("url") or ""),
            pdf_url=str(record.get("pdf_url") or ""),
            venue=str(record.get("venue") or ""),
            cited_by_count=int(record.get("cited_by_count") or 0),
            open_access=bool(record.get("open_access")),
            identifiers=identifiers,
            pdf_path=str(record.get("pdf_path") or ""),
            pdf_status=str(record.get("pdf_status") or "pending_download"),
            verification_status=str(record.get("verification_status") or ""),
            relevance_score=float(record.get("relevance_score") or 0),
            local_id=record.get("local_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DownloadResult:
    ok: bool
    path: str = ""
    size: int = 0
    source: str = ""
    error: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationResult:
    title: str
    verdict: str
    path: str = ""
    size: int = 0
    text_chars: int = 0
    reason: str = ""
    notes: str = ""
    verifier: str = "LiteratureVerifier"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
