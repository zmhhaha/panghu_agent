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

DEFAULT_FEEDS = (
    "China News International|https://www.chinanews.com.cn/rss/world.xml",
)


def feed_specs() -> list[tuple[str, str]]:
    raw = os.getenv("NEWS_FEEDS", "||".join(DEFAULT_FEEDS))
    specs: list[tuple[str, str]] = []
    for value in raw.split("||"):
        if "|" in value:
            name, url = value.split("|", 1)
            if name.strip() and url.strip():
                specs.append((name.strip(), url.strip()))
    return specs


def collect(*, sample: bool = False) -> list[Candidate]:
    if sample:
        return [
            Candidate(
                external_id="sample-news-001",
                title="Sample international event",
                summary="This sample is used to validate the pipeline without network access.",
                url="https://example.com/news/sample",
                source="Sample source",
                published_at="2026-08-24T00:00:00Z",
            )
        ]

    candidates: list[Candidate] = []
    seen: set[str] = set()
    for source_name, url in feed_specs():
        try:
            rows = fetch_feed(url, source=source_name)
        except (HttpClientError, ValueError) as exc:
            logging.getLogger(__name__).warning("News feed failed source=%s error=%s", source_name, exc)
            continue
        for row in rows:
            if row.url not in seen:
                seen.add(row.url)
                candidates.append(row)
    return candidates


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    body = (
        f"\u5df2\u786e\u8ba4\u4fe1\u606f\uff1a{candidate.title}\n\n"
        f"\u6765\u6e90\u6458\u8981\uff1a{candidate.summary or '\u6765\u6e90\u672a\u63d0\u4f9b\u6458\u8981\uff0c\u8bf7\u6253\u5f00\u539f\u6587\u6838\u9a8c\u3002'}\n\n"
        "\u5f53\u524d\u72b6\u6001\uff1a\u8fd9\u662f\u4e00\u6761\u57fa\u4e8e\u516c\u5f00\u6765\u6e90\u7684\u4e8b\u4ef6\u7ebf\u7d22\uff0c\u5b98\u65b9\u8bf4\u6cd5\u3001\u5a92\u4f53\u62a5\u9053\u548c\u63a8\u6d4b\u9700\u8981\u533a\u5206\u3002"
        "\u5728\u81f3\u5c11\u4e24\u4e2a\u72ec\u7acb\u6765\u6e90\u4ea4\u53c9\u6838\u9a8c\u524d\uff0c\u4e0d\u5e94\u628a\u672a\u786e\u8ba4\u7ec6\u8282\u5f53\u4f5c\u4e8b\u5b9e\u3002\n\n"
        f"\u539f\u6587\uff1a{candidate.url}\n\n"
        "\u672c\u6587\u4e3a\u516c\u5f00\u6765\u6e90\u6458\u8981\uff0c\u4e0d\u6784\u6210\u4e8b\u5b9e\u88c1\u51b3\u6216\u6295\u8d44\u5efa\u8bae\u3002"
    )
    risk, status, notes = assess(body, default_risk="high")
    return ContentItem.create(
        bot_name=config.bot_name,
        bot_version=config.bot_version,
        prompt_version=config.prompt_version,
        title=f"\u56fd\u9645\u5c40\u52bf\u7b80\u62a5\uff1a{candidate.title}",
        body=body,
        summary=candidate.summary,
        language="zh-CN",
        source_refs=[
            SourceRef(
                name=candidate.source,
                url=candidate.url,
                external_id=candidate.external_id,
                published_at=candidate.published_at,
                excerpt=candidate.summary,
            )
        ],
        tags=["\u56fd\u9645\u65b0\u95fb", candidate.source],
        topics=["international-affairs"],
        risk_level=risk,
        review_status=status,
        review_notes=notes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and review international news briefs")
    parser.add_argument("--sample", action="store_true", help="use an offline sample candidate")
    args = parser.parse_args()
    config = AgentConfig.from_env("international-news")
    run_agent(config, lambda: collect(sample=args.sample), render)


if __name__ == "__main__":
    main()
