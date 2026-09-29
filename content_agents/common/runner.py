from __future__ import annotations

import logging
import os
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any
from datetime import datetime, timedelta, timezone

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
    replay_limit = max(0, int(os.getenv("BOT_REPLAY_MAX_ITEMS", "2")))
    replay_cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0, int(os.getenv("BOT_REPLAY_MAX_AGE_HOURS", "24"))))
    replayed = 0

    def fresh(item: ContentItem) -> bool:
        try:
            dates = [datetime.fromisoformat(item.created_at.replace('Z', '+00:00'))]
            dates += [datetime.fromisoformat(ref.published_at.replace('Z', '+00:00'))
                      for ref in item.source_refs if ref.published_at]
            now = datetime.now(timezone.utc)
            return all(d.replace(tzinfo=d.tzinfo or timezone.utc) >= replay_cutoff for d in dates) and (
                not item.valid_until or datetime.fromisoformat(item.valid_until.replace('Z', '+00:00')) > now)
        except (ValueError, TypeError):
            return False

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
                try:
                    result = channel.publish(item)
                except Exception as exc:
                    logger.exception("channel publication failed bot=%s channel=%s content_id=%s",
                                     config.bot_name, channel.name, item.content_id)
                    result = PublicationResult(channel=channel.name, status="failed", error=f"{type(exc).__name__}: {exc}")
            if retry:
                retries += 1
            store.save_publication(result, item.content_id)
            publication_rows.append({"content_id": item.content_id, **result.__dict__})

    for candidate in candidates:
        item = renderer(candidate, config)
        existing_item = store.find_content(item.content_hash)
        if existing_item is None:
            # Prefer the source identity when available. Feeds can revise a
            # summary without changing the underlying article/event ID.
            for source_ref in item.source_refs:
                existing_item = store.find_content_by_source(
                    bot_name=config.bot_name,
                    source=source_ref.name,
                    external_id=source_ref.external_id,
                )
                if existing_item is not None:
                    break
        if existing_item is not None:
            duplicates += 1
            item = existing_item
            if not fresh(item) or replayed >= replay_limit:
                continue
            if not any(not store.is_published(item.content_id, c.name) for c in channels):
                continue
            replayed += 1
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
    for item in sorted(store.iter_content(), key=lambda row: row.created_at, reverse=True):
        if replayed >= replay_limit:
            break
        if item.content_id in attempted_ids:
            continue
        if not fresh(item):
            continue
        if auto_approve and item.review_status == "needs_review":
            item.review_status = "approved"
            item.review_notes = ["auto-approved by CONTENT_AUTO_APPROVE=true (legacy item)"]
            store.save_content_revision(item)
        if item.review_status != "approved":
            continue
        pending = any(not store.is_published(item.content_id, channel.name) for channel in channels)
        if pending:
            replayed += 1
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
    recent = store.root / "run-health.json"
    import json
    from .rss_cache import atomic_write
    try:
        previous = json.loads(recent.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        previous = {}
    failures = [row for row in publication_rows if row['status'] in {'failed', 'skipped'}]
    streak = int(previous.get('consecutive_failures', 0)) + 1 if failures else 0
    atomic_write(recent, json.dumps({'run_id': run_id, 'consecutive_failures': streak,
        'errors': failures, 'updated_at': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False).encode('utf-8'))
    if streak >= 2:
        logger.error('CONTENT_AGENT_ALERT bot=%s consecutive_failures=%d errors=%s', config.bot_name, streak, failures)
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
