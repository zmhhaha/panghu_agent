from __future__ import annotations

import argparse
import logging
import os
import re

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError
from content_agents.common.models import Candidate, ContentItem, SourceRef
from content_agents.common.review import assess
from content_agents.common.runner import run_agent
from content_agents.common.source import fetch_bilibili_hot, fetch_baidu_hot, fetch_feed

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
DEFAULT_FEEDS = "Baidu Hot Search|https://top.baidu.com/board?tab=realtime||Bilibili 热门|https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all"
NEWS_EXCLUDE_TERMS = ("地震", "台风", "洪水", "火灾", "事故", "遇难", "死亡", "受贿", "判刑", "犯罪", "猥亵", "诈骗", "袭击", "战争", "军事", "政治局", "会议", "通报", "辟谣", "疫情", "外交", "总统")
MEME_SIGNAL_PATTERNS = (r"[‘’'\"“”].{1,32}[’'\"“”]", r"(?:梗|反转|破防|笑死|绷不住|离谱|抽象|逆天|真香|上大分|网友调侃)", r"(?:空城计|关中王|牛来了|封神)", r"(?:竟然|居然|没想到|原来是|被称为|戏称|谐音|双关)")


def meme_score(candidate: Candidate) -> tuple[int, list[str]]:
    text = f"{candidate.title} {candidate.summary}".strip()
    if any(term in text for term in NEWS_EXCLUDE_TERMS):
        return -99, ["ordinary-news-or-sensitive-event"]
    score = 0
    reasons: list[str] = []
    for pattern in MEME_SIGNAL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score += 2
            reasons.append(pattern)
    if candidate.metadata.get("hot_score") or candidate.metadata.get("view_count"):
        score += 1
    if 2 <= len(candidate.title) <= 22:
        score += 1
    if any(mark in candidate.title for mark in ("？", "！", "…", "，")):
        score += 1
    return score, reasons


def collect(*, sample: bool = False) -> list[Candidate]:
    if sample:
        return [Candidate(external_id="sample-meme-001", title="是关中王来了", summary="网友用三国典故调侃某个意外登场的场面。", url="https://example.com/meme", source="Sample meme source", metadata={"meme_score": 5})]
    candidates: list[Candidate] = []
    for value in os.getenv("MEME_FEEDS", DEFAULT_FEEDS).split("||"):
        if "|" not in value:
            continue
        source, url = (part.strip() for part in value.split("|", 1))
        try:
            if "top.baidu.com/board" in url:
                candidates.extend(fetch_baidu_hot(url, source=source, limit=50))
            elif "bilibili.com" in url or "bilibili" in source.lower():
                candidates.extend(fetch_bilibili_hot(url, source=source, limit=50))
            else:
                candidates.extend(fetch_feed(url, source=source))
        except (HttpClientError, ValueError) as exc:
            logging.getLogger(__name__).warning("Meme feed failed source=%s error=%s", source, exc)
    unique = {row.url: row for row in candidates if row.url}
    minimum = max(1, int(os.getenv("MEME_MIN_SCORE", "3")))
    scored = []
    for candidate in unique.values():
        score, reasons = meme_score(candidate)
        if score >= minimum:
            candidate.metadata.update(meme_score=score, meme_reasons=reasons)
            scored.append((score, candidate))
    scored.sort(key=lambda row: (-row[0], int(row[1].metadata.get("rank") or 9999)))
    return [candidate for _, candidate in scored]


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    score = int(candidate.metadata.get("meme_score", 0))
    body = (f"{candidate.title}\n\n发生了什么：{candidate.summary or '具体背景请打开原始来源核对。'}\n\n"
            "网友怎么调侃：网友把这个事件重新包装成戏称、谐音或典故，用来形容其中的反差感。这里记录公开语境中的玩笑，不把未经证实的传闻当事实。\n\n"
            f"为什么好笑：熟悉的说法被套进了意外场景，形成错位感（候选评分 {score}）。\n\n原始来源：{candidate.url}")
    risk, status, notes = assess(body, default_risk="medium")
    return ContentItem.create(bot_name=config.bot_name, bot_version=config.bot_version, prompt_version=config.prompt_version, title=f"今日热梗：{candidate.title}", body=body, summary=candidate.summary, language="zh-CN", source_refs=[SourceRef(name=candidate.source, url=candidate.url, external_id=candidate.external_id, published_at=candidate.published_at, excerpt=candidate.summary)], tags=["热梗", "网友调侃", candidate.source], topics=["internet-culture", "event-joke"], risk_level=risk, review_status=status, review_notes=notes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect event-based internet jokes")
    parser.add_argument("--sample", action="store_true")
    args = parser.parse_args()
    config = AgentConfig.from_env("meme-collector")
    run_agent(config, lambda: collect(sample=args.sample), render)


if __name__ == "__main__":
    main()
