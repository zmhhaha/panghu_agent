from __future__ import annotations

import argparse
import logging
import os

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError
from content_agents.common.models import Candidate, ContentItem, SourceRef
from content_agents.common.review import assess
from content_agents.common.runner import run_agent
from content_agents.common.source import fetch_feed

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
DEFAULT_FEEDS = ("China News International|https://www.chinanews.com.cn/rss/world.xml",)
LOW_RELEVANCE_TERMS = ("马拉松", "足球", "篮球", "网球", "赛事", "奖牌", "运动员", "球队", "演唱会", "电影", "电视剧", "明星", "综艺", "游戏", "旅游", "美食")


def feed_specs() -> list[tuple[str, str]]:
    raw = os.getenv("NEWS_FEEDS", "||".join(DEFAULT_FEEDS))
    return [(name.strip(), url.strip()) for value in raw.split("||") if "|" in value for name, url in [value.split("|", 1)] if name.strip() and url.strip()]


def collect(*, sample: bool = False) -> list[Candidate]:
    if sample:
        return [Candidate(external_id="sample-news-001", title="Sample international event", summary="This sample is used to validate the pipeline without network access.", url="https://example.com/news/sample", source="Sample source", published_at="2026-08-24T00:00:00Z")]
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for source_name, url in feed_specs():
        try:
            rows = fetch_feed(url, source=source_name)
        except (HttpClientError, ValueError) as exc:
            logging.getLogger(__name__).warning("News feed failed source=%s error=%s", source_name, exc)
            continue
        for row in rows:
            if any(term in f"{row.title} {row.summary}" for term in LOW_RELEVANCE_TERMS):
                continue
            if row.url not in seen:
                seen.add(row.url)
                candidates.append(row)
    return candidates


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    summary = " ".join((candidate.summary or "").split()).strip()
    body = (summary or "来源未提供摘要，请打开原文核验。") + f"\n\n原文：{candidate.url}"
    risk, status, notes = assess(body, default_risk="high")
    return ContentItem.create(bot_name=config.bot_name, bot_version=config.bot_version, prompt_version=config.prompt_version, title=f"国际局势：{candidate.title}", body=body, summary=candidate.summary, language="zh-CN", source_refs=[SourceRef(name=candidate.source, url=candidate.url, external_id=candidate.external_id, published_at=candidate.published_at, excerpt=candidate.summary)], tags=["国际新闻", candidate.source], topics=["international-affairs"], risk_level=risk, review_status=status, review_notes=notes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and review international news briefs")
    parser.add_argument("--sample", action="store_true")
    args = parser.parse_args()
    config = AgentConfig.from_env("international-news")
    run_agent(config, lambda: collect(sample=args.sample), render)


if __name__ == "__main__":
    main()
