from __future__ import annotations

import argparse
import logging
import os

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError
from content_agents.common.models import Candidate, ContentItem, SourceRef
from content_agents.common.review import assess
from content_agents.common.runner import run_agent
from content_agents.common.source import fetch_cls_telegraph, fetch_feed

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

DEFAULT_FEEDS = ("财联社电报|https://www.cls.cn/api/cache?rn=20&name=telegraph",)


def feed_specs() -> list[tuple[str, str]]:
    raw = os.getenv("FINANCE_NEWS_FEEDS", "||".join(DEFAULT_FEEDS))
    return [
        (name.strip(), url.strip())
        for value in raw.split("||")
        if "|" in value
        for name, url in [value.split("|", 1)]
        if name.strip() and url.strip()
    ]


def collect(*, sample: bool = False) -> list[Candidate]:
    if sample:
        return [Candidate(
            external_id="sample-finance-001",
            title="示例：央行发布公开市场业务消息",
            summary="这是一条用于离线验证财经资讯流水线的示例。",
            url="https://example.com/finance/sample",
            source="示例财经来源",
            published_at="2026-08-26T00:00:00Z",
        )]

    candidates: list[Candidate] = []
    seen: set[str] = set()
    for source_name, url in feed_specs():
        try:
            if "cls.cn/api/cache" in url.lower():
                rows = fetch_cls_telegraph(url, source=source_name, limit=20)
            else:
                rows = fetch_feed(url, source=source_name)
        except (HttpClientError, ValueError) as exc:
            logging.getLogger(__name__).warning("Finance feed failed source=%s error=%s", source_name, exc)
            continue
        for row in rows:
            if row.external_id not in seen:
                seen.add(row.external_id)
                candidates.append(row)
    return candidates


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    summary = " ".join((candidate.summary or "").split()).strip()
    body = (
        f"已确认信息：{candidate.title}\n\n"
        f"来源摘要：{summary or '来源未提供摘要，请打开原文核验。'}\n\n"
        f"原文：{candidate.url}\n"
        "本文为公开来源摘要，不构成投资建议。"
    )
    risk, status, notes = assess(body, default_risk="high")
    return ContentItem.create(
        bot_name=config.bot_name,
        bot_version=config.bot_version,
        prompt_version=config.prompt_version,
        title=f"财经快讯：{candidate.title}",
        body=body,
        summary=summary,
        language="zh-CN",
        source_refs=[SourceRef(
            name=candidate.source,
            url=candidate.url,
            external_id=candidate.external_id,
            published_at=candidate.published_at,
            excerpt=summary,
        )],
        tags=["财经", "财联社" if "财联社" in candidate.source else candidate.source],
        topics=["finance", "markets"],
        risk_level=risk,
        review_status=status,
        review_notes=notes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and publish finance news briefs")
    parser.add_argument("--sample", action="store_true", help="use an offline sample candidate")
    args = parser.parse_args()
    config = AgentConfig.from_env("finance-news")
    run_agent(config, lambda: collect(sample=args.sample), render)


if __name__ == "__main__":
    main()
