from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .models import ContentItem, PublicationResult, utc_now


class JsonStore:
    """Small append-only store for CronJob output and offline review."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._index_path = root / "content-index.json"
        try:
            self._index = set(json.loads(self._index_path.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError):
            self._index: set[str] = set()

    def seen(self, content_hash: str) -> bool:
        return content_hash in self._index

    def save_content(self, item: ContentItem) -> bool:
        with self._lock:
            if item.content_hash in self._index:
                return False
            self._append("content-items.jsonl", item.to_dict())
            self._index.add(item.content_hash)
            self._index_path.write_text(json.dumps(sorted(self._index), ensure_ascii=False), encoding="utf-8")
            return True

    def save_content_revision(self, item: ContentItem) -> None:
        """Append an updated moderation state without changing the content hash index."""
        with self._lock:
            self._append("content-items.jsonl", item.to_dict())

    def find_content(self, content_hash: str) -> ContentItem | None:
        """Load an existing item so failed channel publications can be retried."""
        path = self.root / "content-items.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("content_hash") == content_hash:
                try:
                    return ContentItem.from_dict(value)
                except (TypeError, KeyError):
                    continue
        return None

    def iter_content(self) -> list[ContentItem]:
        path = self.root / "content-items.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        items: list[ContentItem] = []
        for line in lines:
            try:
                value = json.loads(line)
                items.append(ContentItem.from_dict(value))
            except (TypeError, KeyError, json.JSONDecodeError):
                continue
        return items

    def latest_publication(self, content_id: str, channel: str) -> PublicationResult | None:
        path = self.root / "channel-publications.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("content_id") == content_id and value.get("channel") == channel:
                return PublicationResult(
                    channel=channel,
                    status=str(value.get("status", "")),
                    external_id=str(value.get("external_id", "")),
                    error=str(value.get("error", "")),
                )
        return None

    def is_published(self, content_id: str, channel: str) -> bool:
        result = self.latest_publication(content_id, channel)
        return bool(result and result.status == "published" and result.external_id)

    def save_publication(self, result: PublicationResult, content_id: str) -> None:
        self._append("channel-publications.jsonl", {"content_id": content_id, "created_at": utc_now(), **result.__dict__})

    def save_run(self, record: dict[str, Any]) -> None:
        self._append("bot-runs.jsonl", {"created_at": utc_now(), **record})

    def _append(self, name: str, value: dict[str, Any]) -> None:
        with (self.root / name).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
