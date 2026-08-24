from __future__ import annotations

import argparse
import logging
import os

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError
from content_agents.common.models import Candidate, ContentItem, SourceRef
from content_agents.common.review import assess
from content_agents.common.runner import run_agent
from content_agents.common.source import fetch_baidu_hot, fetch_feed

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

DEFAULT_FEEDS = "Baidu Hot Search|https://top.baidu.com/board?tab=realtime"


def collect(*, sample: bool = False) -> list[Candidate]:
    if sample:
        return [
            Candidate(
                external_id="sample-meme-001",
                title="Sample internet phrase",
                summary="A sample trend used for offline validation.",
                url="https://example.com/trends/sample",
                source="Sample trend source",
            )
        ]

    candidates: list[Candidate] = []
    for value in os.getenv("MEME_FEEDS", DEFAULT_FEEDS).split("||"):
        if "|" not in value:
            continue
        source, url = value.split("|", 1)
        try:
            source = source.strip()
            url = url.strip()
            if "top.baidu.com/board" in url:
                candidates.extend(fetch_baidu_hot(url, source=source, limit=50))
            else:
                candidates.extend(fetch_feed(url, source=source))
        except (HttpClientError, ValueError) as exc:
            logging.getLogger(__name__).warning("Meme feed failed source=%s error=%s", source, exc)
    unique: dict[str, Candidate] = {row.url: row for row in candidates if row.url}
    return list(unique.values())


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    body = (
        f"{candidate.title}\n\n"
        f"\u542b\u4e49\u4e0e\u8bed\u5883\uff1a{candidate.summary or '\u5f53\u524d\u4ec5\u53d1\u73b0\u8d8b\u52bf\u6807\u9898\uff0c\u4f7f\u7528\u524d\u8bf7\u7ed3\u5408\u539f\u59cb\u8bed\u5883\u6838\u9a8c\u3002'}\n\n"
        "\u4f20\u64ad\u89c2\u5bdf\uff1a\u5b83\u53ef\u80fd\u6765\u81ea\u7279\u5b9a\u5e73\u53f0\u6216\u793e\u533a\uff0c\u8de8\u5e73\u53f0\u4f7f\u7528\u65f6\u8981\u6ce8\u610f\u8bed\u6c14\u3001\u5bf9\u8c61\u548c\u65f6\u6548\u6027\u3002"
        "\u672c\u6587\u4e0d\u8f6c\u8f7d\u539f\u59cb\u56fe\u7247\u3001\u89c6\u9891\u3001\u97f3\u4e50\u6216\u5927\u6bb5\u7528\u6237\u5185\u5bb9\uff0c\u4ec5\u4fdd\u7559\u5fc5\u8981\u6587\u5b57\u8bf4\u660e\u548c\u539f\u94fe\u63a5\u3002\n\n"
        f"\u539f\u59cb\u6765\u6e90\uff1a{candidate.url}"
    )
    risk, status, notes = assess(body, default_risk="medium")
    return ContentItem.create(
        bot_name=config.bot_name,
        bot_version=config.bot_version,
        prompt_version=config.prompt_version,
        title=f"\u4eca\u65e5\u70ed\u6897\uff1a{candidate.title}",
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
        tags=["\u70ed\u6897", candidate.source],
        topics=["internet-culture"],
        risk_level=risk,
        review_status=status,
        review_notes=notes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and explain internet memes and phrases")
    parser.add_argument("--sample", action="store_true", help="use an offline sample candidate")
    args = parser.parse_args()
    config = AgentConfig.from_env("meme-collector")
    run_agent(config, lambda: collect(sample=args.sample), render)


if __name__ == "__main__":
    main()
