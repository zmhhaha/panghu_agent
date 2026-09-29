from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from .config import AgentConfig
from .http import HttpClientError, post_json
from .models import ContentItem, PublicationResult
from .storage import JsonStore
from .rss_cache import rss_lock, load_entries, write_entries


class ChannelAdapter(ABC):
    name: str

    @abstractmethod
    def publish(self, item: ContentItem) -> PublicationResult:
        raise NotImplementedError


class JsonChannel(ChannelAdapter):
    name = "json"

    def __init__(self, store: JsonStore):
        self.store = store

    def publish(self, item: ContentItem) -> PublicationResult:
        self.store._append("published-content.jsonl", item.to_dict())
        return PublicationResult(channel=self.name, status="published", external_id=item.content_id)


class RssChannel(ChannelAdapter):
    name = "rss"

    def __init__(self, root: Path):
        self.path = root / "feed.xml"
        self.items_path = root / "rss-items.json"

    def publish(self, item: ContentItem) -> PublicationResult:
        with rss_lock(self.path.parent):
            old = load_entries(self.path.parent)
            entries = [item.to_dict(), *[row for row in old if row.get("content_hash") != item.content_hash]][:100]
            write_entries(self.path.parent, entries)
        return PublicationResult(channel=self.name, status="published", external_id=item.content_id)


class HublogChannel(ChannelAdapter):
    name = "hublog"

    def __init__(self, config: AgentConfig):
        self.config = config

    def publish(self, item: ContentItem) -> PublicationResult:
        if not self.config.hublog_service_token:
            return PublicationResult(
                channel=self.name,
                status="skipped",
                error="HUBLOG_SERVICE_TOKENS has no entry for this bot",
            )
        payload = {
            "post_type": "article" if len(item.body) > 500 else "short",
            "visibility": "public",
            "title": item.title,
            "content": item.body,
            "tags": item.tags,
        }
        try:
            response = post_json(
                f"{self.config.hublog_base_url}/api/v1/posts",
                payload,
                headers={
                    "Authorization": f"Bearer {self.config.hublog_service_token}",
                    "Idempotency-Key": f"{item.bot_name}:{item.content_hash}",
                },
                timeout=30,
            )
        except HttpClientError as exc:
            return PublicationResult(channel=self.name, status="failed", error=str(exc))
        external_id = str(response.get("id", "")) if isinstance(response, dict) else ""
        return PublicationResult(channel=self.name, status="published", external_id=external_id)


def build_channels(config: AgentConfig, store: JsonStore) -> list[ChannelAdapter]:
    channels: list[ChannelAdapter] = []
    for name in config.channels:
        if name == "json":
            channels.append(JsonChannel(store))
        elif name == "rss":
            channels.append(RssChannel(config.data_dir))
        elif name == "hublog":
            channels.append(HublogChannel(config))
        else:
            raise ValueError(f"unsupported channel: {name}")
    return channels
