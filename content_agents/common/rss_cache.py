"""Shared CephFS RSS cache: advisory locking, backups and atomic replacement."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


@contextmanager
def rss_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.rss.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_write(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def valid_row(row) -> bool:
    return (isinstance(row, dict)
            and all(isinstance(row.get(k), str) for k in
                    ('content_id', 'content_hash', 'title', 'body', 'created_at'))
            and isinstance(row.get('source_refs', []), list)
            and all(isinstance(ref, dict) and isinstance(ref.get('url'), str)
                    for ref in row.get('source_refs', [])))


def load_entries(root: Path) -> list[dict]:
    path = root / 'rss-items.json'
    try:
        rows = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(rows, list) or not all(valid_row(r) for r in rows):
            raise ValueError('invalid RSS cache schema')
        return rows
    except FileNotFoundError:
        return []
    except (UnicodeError, ValueError) as exc:
        backup = path.with_name(f'{path.name}.corrupt-{uuid.uuid4().hex}')
        shutil.copy2(path, backup)
        logger.error('RSS cache damaged; backup=%s reason=%s', backup, exc)
        # Rebuild only entries previously delivered to RSS, not unpublished drafts.
        rows = {}
        for ledger in root.glob('*/channel-publications.jsonl'):
            published = set()
            for line in ledger.read_text(encoding='utf-8').splitlines():
                try:
                    entry = json.loads(line)
                    if entry.get('channel') == 'rss' and entry.get('status') == 'published':
                        published.add(entry['content_id'])
                except (ValueError, KeyError):
                    continue
            content = ledger.parent / 'content-items.jsonl'
            if not content.exists():
                continue
            for line in content.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(line)
                    if valid_row(row) and row['content_id'] in published:
                        rows[row['content_id']] = row
                except ValueError:
                    continue
        return sorted(rows.values(), key=lambda r: r['created_at'], reverse=True)[:100]


def write_entries(root: Path, entries: list[dict]) -> None:
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    for key, value in [('title', 'Panghu Content Agents'),
                       ('link', 'https://hublog.panghuer.top/'),
                       ('description', 'Generated content from Panghu agents')]:
        ET.SubElement(channel, key).text = value
    for row in entries:
        node = ET.SubElement(channel, 'item')
        refs = row.get('source_refs', [])
        for key, value in [('guid', row['content_id']), ('title', row['title']),
                           ('description', row['body']), ('pubDate', row['created_at']),
                           ('link', refs[0]['url'] if refs else '')]:
            ET.SubElement(node, key).text = value
    atomic_write(root / 'rss-items.json', json.dumps(entries, ensure_ascii=False).encode('utf-8'))
    atomic_write(root / 'feed.xml', ET.tostring(rss, encoding='utf-8', xml_declaration=True))


if __name__ == '__main__':
    root = Path(os.getenv('CONTENT_DATA_DIR', '/data/content-agents'))
    with rss_lock(root):
        entries = load_entries(root)
        write_entries(root, entries)
        print(f'RSS cache repaired: {len(entries)} entries; no Hublog posts created')
