from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SourceRef:
    name: str
    url: str
    external_id: str = ""
    published_at: str = ""
    excerpt: str = ""


@dataclass(frozen=True)
class Candidate:
    external_id: str
    title: str
    summary: str
    url: str
    source: str
    published_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContentItem:
    content_id: str
    bot_name: str
    bot_version: str
    prompt_version: str
    title: str
    body: str
    summary: str
    language: str
    source_refs: list[SourceRef]
    tags: list[str]
    topics: list[str]
    risk_level: str
    review_status: str
    review_notes: list[str]
    created_at: str
    valid_until: str | None
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        bot_name: str,
        bot_version: str,
        prompt_version: str,
        title: str,
        body: str,
        summary: str,
        language: str,
        source_refs: list[SourceRef],
        tags: list[str],
        topics: list[str],
        risk_level: str,
        review_status: str,
        review_notes: list[str] | None = None,
        valid_until: str | None = None,
    ) -> "ContentItem":
        canonical = {
            "bot_name": bot_name,
            "title": title.strip(),
            "body": body.strip(),
            "source_refs": [asdict(ref) for ref in source_refs],
        }
        content_hash = hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            content_id=str(uuid.uuid4()),
            bot_name=bot_name,
            bot_version=bot_version,
            prompt_version=prompt_version,
            title=title.strip(),
            body=body.strip(),
            summary=summary.strip(),
            language=language,
            source_refs=source_refs,
            tags=list(dict.fromkeys(tags)),
            topics=list(dict.fromkeys(topics)),
            risk_level=risk_level,
            review_status=review_status,
            review_notes=review_notes or [],
            created_at=utc_now(),
            valid_until=valid_until,
            content_hash=content_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PublicationResult:
    channel: str
    status: str
    external_id: str = ""
    error: str = ""

