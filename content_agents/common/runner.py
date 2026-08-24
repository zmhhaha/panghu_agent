from __future__ import annotations

import logging
import uuid
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
    publication_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        item = renderer(candidate, config)
        if not store.save_content(item):
            duplicates += 1
            continue
        generated += 1
        for channel in channels:
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
            store.save_publication(result, item.content_id)
            publication_rows.append({"content_id": item.content_id, **result.__dict__})
    record = {
        "run_id": run_id,
        "bot_name": config.bot_name,
        "candidate_count": len(candidates),
        "generated_count": generated,
        "duplicate_count": duplicates,
        "draft_only": config.draft_only,
        "publications": publication_rows,
    }
    store.save_run(record)
    logger.info("run_id=%s bot_name=%s candidates=%d generated=%d duplicates=%d", run_id, config.bot_name, len(candidates), generated, duplicates)
    return record
