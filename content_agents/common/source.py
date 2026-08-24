from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .http import get_text
from .models import Candidate


@dataclass(frozen=True)
class FeedEntry:
    title: str
    summary: str
    url: str
    published_at: str
    source: str


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in node if _local_name(child.tag) == name), None)


def parse_feed(xml_text: str, *, source: str) -> list[FeedEntry]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"invalid feed XML: {exc}") from exc
    entries: list[FeedEntry] = []
    nodes = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    for node in nodes:
        title = _text(_child(node, "title"))
        link_node = _child(node, "link")
        url = (link_node.text or "").strip() if link_node is not None else ""
        if link_node is not None and not url:
            url = link_node.attrib.get("href", "").strip()
        summary = _text(_child(node, "description")) or _text(_child(node, "summary"))
        published = _text(_child(node, "pubDate")) or _text(_child(node, "updated"))
        if title and url:
            entries.append(FeedEntry(title=title, summary=summary, url=url, published_at=published, source=source))
    return entries


def fetch_feed(url: str, *, source: str) -> list[Candidate]:
    return [
        Candidate(
            external_id=entry.url,
            title=entry.title,
            summary=entry.summary,
            url=entry.url,
            source=entry.source,
            published_at=entry.published_at,
        )
        for entry in parse_feed(get_text(url, headers={"User-Agent": "panghu-content-agent/0.1"}), source=source)
    ]
