from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


def fetch_cls_telegraph(url: str, *, source: str, limit: int = 20) -> list[Candidate]:
    """Read 财联社's public rolling-news JSON endpoint.

    The endpoint expects ``lastTime`` to be a Unix timestamp.  Supplying the
    current time asks for the newest items and avoids baking a moving timestamp
    into the ConfigMap.
    """
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("rn", str(limit))
    query["lastTime"] = str(int(datetime.now(timezone.utc).timestamp()))
    request_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    document = json.loads(get_text(request_url, headers={
        "User-Agent": "panghu-content-agent/0.1",
        "Accept": "application/json",
        "Referer": "https://www.cls.cn/telegraph",
    }))
    if not isinstance(document, dict) or document.get("errno") not in (0, "0"):
        raise ValueError("财联社电报接口返回错误")
    data = document.get("data")
    rows = data.get("roll_data", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        raise ValueError("财联社电报接口返回数据格式错误")
    candidates: list[Candidate] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        summary = str(row.get("brief") or row.get("content") or "").strip()
        if not item_id or not title:
            continue
        published_at = ""
        if row.get("ctime"):
            try:
                published_at = datetime.fromtimestamp(int(row["ctime"]), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                published_at = str(row["ctime"])
        candidates.append(Candidate(
            external_id=item_id,
            title=title,
            summary=summary,
            url=f"https://www.cls.cn/detail/{item_id}",
            source=source,
            published_at=published_at,
            metadata={"reading_num": row.get("reading_num"), "comment_num": row.get("comment_num")},
        ))
    return candidates


def fetch_baidu_hot(url: str, *, source: str, limit: int = 20) -> list[Candidate]:
    """Extract Baidu's embedded hot-list JSON from the public board page."""
    html = get_text(url, headers={"User-Agent": "panghu-content-agent/0.1"})
    marker = "<!--s-data:"
    start = html.find(marker)
    if start < 0:
        raise ValueError("Baidu hot board did not contain embedded data")
    payload_start = start + len(marker)
    try:
        document, _ = json.JSONDecoder().raw_decode(html[payload_start:])
    except json.JSONDecodeError as exc:
        raise ValueError("Baidu hot board embedded data is invalid JSON") from exc

    candidates: list[Candidate] = []
    cards = document.get("data", {}).get("cards", []) if isinstance(document, dict) else []
    for card in cards:
        if not isinstance(card, dict) or card.get("component") != "hotList":
            continue
        for row in card.get("content", []):
            if not isinstance(row, dict):
                continue
            title = str(row.get("word") or row.get("query") or "").strip()
            link = str(row.get("rawUrl") or row.get("url") or "").strip()
            if not title or not link:
                continue
            candidates.append(
                Candidate(
                    external_id=link,
                    title=title,
                    summary=str(row.get("desc") or "").strip(),
                    url=link,
                    source=source,
                    metadata={
                        "rank": row.get("index"),
                        "hot_score": row.get("hotScore"),
                    },
                )
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


def fetch_bilibili_hot(url: str, *, source: str, limit: int = 20) -> list[Candidate]:
    """Read Bilibili's public ranking JSON without scraping video pages."""
    document = json.loads(get_text(url, headers={"User-Agent": "panghu-content-agent/0.1", "Accept": "application/json"}))
    rows = document.get("data", {}).get("list", []) if isinstance(document, dict) else []
    candidates: list[Candidate] = []
    for rank, row in enumerate(rows[:limit], start=1):
        if not isinstance(row, dict):
            continue
        title = re.sub(r"<[^>]+>", "", str(row.get("title") or "")).strip()
        bvid = str(row.get("bvid") or "").strip()
        if not title or not bvid:
            continue
        candidates.append(Candidate(
            external_id=bvid,
            title=title,
            summary=re.sub(r"\s+", " ", str(row.get("desc") or "")).strip(),
            url=f"https://www.bilibili.com/video/{bvid}",
            source=source,
            metadata={"rank": rank, "view_count": row.get("stat", {}).get("view")},
        ))
    return candidates
