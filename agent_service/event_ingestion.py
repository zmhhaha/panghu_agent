"""Persist only bounded Worker metadata, with an attempt-scoped replay cursor.

Native events are never trusted as lifecycle authority. The store commits each
sanitized page and its cursor in the same transaction; a control restart resumes
that cursor instead of appending the same engine events a second time.
"""
from __future__ import annotations

from .contracts import ExecutionEnvelope, ServiceError


MAX_PAGE_EVENTS = 100
MAX_WORKER_EVENTS = 500
WORKER_EVENT_KINDS = frozenset({'pi_event', 'runtime_finished', 'engine_started', 'engine_progress'})


def _invalid():
    return ServiceError('worker_events_invalid', 503)


def _checked_page(page, envelope: ExecutionEnvelope, after: int, limit: int):
    """Do not commit a prefix of a malformed, misbound or overflowing page."""
    if (type(page) is not dict
            or set(page) != {'attempt_id', 'request_sha256', 'items', 'next_cursor', 'has_more'}
            or page['attempt_id'] != envelope.attempt_id
            or page['request_sha256'] != envelope.request_sha256
            or type(page['items']) is not list or len(page['items']) > limit
            or type(page['next_cursor']) is not int or type(page['has_more']) is not bool):
        raise _invalid()
    items = page['items']
    next_cursor = after + len(items)
    if (page['next_cursor'] != next_cursor or next_cursor > MAX_WORKER_EVENTS
            or page['has_more'] and (not items or next_cursor == MAX_WORKER_EVENTS)):
        raise _invalid()
    checked = []
    for sequence, item in enumerate(items, start=after + 1):
        if (type(item) is not dict or set(item) != {'seq', 'type', 'payload'}
                or type(item['seq']) is not int or item['seq'] != sequence
                or item['type'] != 'engine.progress'
                or type(item['payload']) is not dict or set(item['payload']) != {'event'}
                or type(item['payload']['event']) is not str
                or item['payload']['event'] not in WORKER_EVENT_KINDS):
            raise _invalid()
        # Construct new data rather than forwarding an untrusted nested object.
        checked.append({'seq': sequence, 'type': 'engine.progress',
                        'payload': {'event': item['payload']['event']}})
    return checked, next_cursor, page['has_more']


def sync_worker_events(store, adapter, envelope: ExecutionEnvelope, *, page_limit=MAX_PAGE_EVENTS,
                       max_pages=5):
    """Consume a bounded number of pages before the controller commits terminal.

    The normal Worker has at most 500 events, so the defaults can drain it in one
    poll. ``has_more`` must be honored if a caller chooses a smaller work budget.
    Older trusted adapter fixtures without an events method have no event stream.
    """
    if (type(page_limit) is not int or not 1 <= page_limit <= MAX_PAGE_EVENTS
            or type(max_pages) is not int or not 1 <= max_pages <= 5):
        raise ValueError('Invalid bounded Worker event page budget')
    after = store.worker_event_cursor(envelope.job_id, envelope.attempt_id, envelope.fence)
    if type(after) is not int or not 0 <= after <= MAX_WORKER_EVENTS:
        raise _invalid()
    fetch = getattr(adapter, 'events', None)
    if not callable(fetch):
        return {'cursor': after, 'has_more': False, 'pages': 0}
    for count in range(1, max_pages + 1):
        page = fetch(envelope, after, limit=page_limit)
        checked, next_cursor, has_more = _checked_page(page, envelope, after, page_limit)
        committed = store.ingest_worker_events(envelope.job_id, envelope.attempt_id, envelope.fence,
                                                after, checked, next_cursor)
        if type(committed) is not int or not next_cursor <= committed <= MAX_WORKER_EVENTS:
            raise _invalid()
        after = committed
        if not has_more:
            return {'cursor': after, 'has_more': False, 'pages': count}
    return {'cursor': after, 'has_more': True, 'pages': max_pages}
