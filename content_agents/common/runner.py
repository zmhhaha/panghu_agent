from __future__ import annotations

import logging
import os
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from .channel import ChannelAdapter, build_channels
from .config import AgentConfig
from .models import Candidate, ContentItem, PublicationResult
from .storage import JsonStore

logger = logging.getLogger("panghu.content_agents")


def run_agent(
    config: AgentConfig,
    collector: Callable[[], Iterable[Candidate]],
    renderer: Callable[[Candidate, AgentConfig], ContentItem],
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    store = JsonStore(config.data_dir / config.bot_name)
    channels = build_channels(config, store)
    candidates = list(collector())[: config.max_items]
    generated = 0
    duplicates = 0
    retries = 0
    publication_rows: list[dict[str, Any]] = []
    attempted_ids: set[str] = set()

    auto_approve = os.getenv("CONTENT_AUTO_APPROVE", "false").strip().lower() in {"1", "true", "yes", "on"}

    def publish_item(item: ContentItem, *, retry: bool) -> None:
        nonlocal retries
        attempted_ids.add(item.content_id)
        for channel in channels:
            if store.is_published(item.content_id, channel.name):
                continue
            # JSON is the review ledger. RSS and Hublog are public channels and
            # only receive approved content when draft mode is disabled.
            if item.review_status == "blocked":
                result = PublicationResult(channel=channel.name, status="blocked", error="content blocked by review")
            elif channel.name in {"rss", "hublog"} and (
                config.draft_only or item.review_status != "approved"
            ):
                reason = "BOT_DRAFT_ONLY=true" if config.draft_only else "content requires review"
                result = PublicationResult(channel=channel.name, status="draft", error=reason)
            else:
                result = channel.publish(item)
            if retry:
                retries += 1
            store.save_publication(result, item.content_id)
            publication_rows.append({"content_id": item.content_id, **result.__dict__})

    for candidate in candidates:
        item = renderer(candidate, config)
        existing_item = store.find_content(item.content_hash)
        if existing_item is not None:
            duplicates += 1
            item = existing_item
            if auto_approve and item.review_status == "needs_review":
                item.review_status = "approved"
                item.review_notes = ["auto-approved by CONTENT_AUTO_APPROVE=true (legacy item)"]
                store.save_content_revision(item)
        else:
            store.save_content(item)
            generated += 1
        publish_item(item, retry=existing_item is not None)

    # A source item may fall out of the current top-N candidates after a failed
    # request. Replay approved items with failed/draft/missing channel records
    # so transient Hublog or RSS outages do not strand content in the ledger.
    for item in store.iter_content():
        if item.content_id in attempted_ids:
            continue
        if auto_approve and item.review_status == "needs_review":
            item.review_status = "approved"
            item.review_notes = ["auto-approved by CONTENT_AUTO_APPROVE=true (legacy item)"]
            store.save_content_revision(item)
        if item.review_status != "approved":
            continue
        pending = any(not store.is_published(item.content_id, channel.name) for channel in channels)
        if pending:
            publish_item(item, retry=True)
    record = {
        "run_id": run_id,
        "bot_name": config.bot_name,
        "candidate_count": len(candidates),
        "generated_count": generated,
        "duplicate_count": duplicates,
        "retry_count": retries,
        "draft_only": config.draft_only,
        "publications": publication_rows,
        "publication_counts": dict(Counter(row["status"] for row in publication_rows)),
    }
    store.save_run(record)
    logger.info(
        "run_id=%s bot_name=%s candidates=%d generated=%d duplicates=%d retries=%d publication_counts=%s",
        run_id,
        config.bot_name,
        len(candidates),
        generated,
        duplicates,
        retries,
        record["publication_counts"],
    )
    return record
