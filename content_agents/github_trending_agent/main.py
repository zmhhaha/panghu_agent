from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError, get_json
from content_agents.common.models import Candidate, ContentItem, SourceRef
from content_agents.common.review import assess
from content_agents.common.runner import run_agent

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


def collect(*, lookback_hours: int, sample: bool = False) -> list[Candidate]:
    if sample:
        return [
            Candidate(
                external_id="sample/panghu-content-agent",
                title="Panghu content agent",
                summary="A sample repository for offline validation.",
                url="https://github.com/example/panghu-content-agent",
                source="GitHub",
                metadata={"stars": 128, "forks": 12, "language": "Python", "license": "MIT"},
            )
        ]

    since = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).date().isoformat()
    query = quote(f"pushed:>={since} stars:>5", safe="")
    headers = {"User-Agent": "panghu-github-trending-agent/0.1", "Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        payload = get_json(
            f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page=20",
            headers=headers,
        )
    except HttpClientError as exc:
        logging.getLogger(__name__).warning("GitHub collection failed: %s", exc)
        return []

    return [
        Candidate(
            external_id=str(row.get("full_name", row.get("id", ""))),
            title=str(row.get("full_name", "Untitled repository")),
            summary=str(row.get("description") or "No repository description."),
            url=str(row.get("html_url", "")),
            source="GitHub",
            metadata={
                "stars": row.get("stargazers_count", 0),
                "forks": row.get("forks_count", 0),
                "open_issues": row.get("open_issues_count", 0),
                "language": row.get("language") or "unknown",
                "license": (row.get("license") or {}).get("spdx_id") or "unknown",
                "updated_at": row.get("updated_at", ""),
            },
        )
        for row in payload.get("items", [])
        if row.get("html_url")
    ]


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    meta = candidate.metadata
    title = f"GitHub \u4eca\u65e5\u70ed\u95e8\uff1a{candidate.title}"
    body = (
        f"{candidate.title}\uff1a{candidate.summary}\n\n"
        f"\u4e3a\u4ec0\u4e48\u503c\u5f97\u5173\u6ce8\uff1a\u8fd1\u671f\u6d3b\u8dc3\u5ea6\u8f83\u9ad8\uff0c\u5f53\u524d\u7ea6 {meta.get('stars', 0)} stars\u3001{meta.get('forks', 0)} forks\u3002\n"
        f"\u6280\u672f\u6808\uff1a{meta.get('language', 'unknown')}\uff1b\u8bb8\u53ef\u8bc1\uff1a{meta.get('license', 'unknown')}\u3002\n"
        "\u4f7f\u7528\u524d\u8bf7\u81ea\u884c\u68c0\u67e5 README\u3001\u8bb8\u53ef\u8bc1\u3001\u4f9d\u8d56\u548c\u5b89\u88c5\u811a\u672c\u3002\n\n"
        f"\u539f\u4ed3\u5e93\uff1a{candidate.url}"
    )
    risk, status, notes = assess(
        body,
        default_risk="low" if meta.get("license") not in (None, "unknown") else "medium",
    )
    language = str(meta.get("language", "unknown"))
    return ContentItem.create(
        bot_name=config.bot_name,
        bot_version=config.bot_version,
        prompt_version=config.prompt_version,
        title=title,
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
        tags=["GitHub", "\u5f00\u6e90", language],
        topics=["developer-tools"],
        risk_level=risk,
        review_status=status,
        review_notes=notes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and summarize active GitHub repositories")
    parser.add_argument("--sample", action="store_true", help="use an offline sample candidate")
    args = parser.parse_args()
    config = AgentConfig.from_env("github-trending")
    run_agent(config, lambda: collect(lookback_hours=config.lookback_hours, sample=args.sample), render)


if __name__ == "__main__":
    main()
