from __future__ import annotations

import argparse
import logging
import os
import re
from dataclasses import replace

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError, post_json
from content_agents.common.llm import generate_json
from content_agents.common.models import Candidate, ContentItem, SourceRef
from content_agents.common.review import assess
from content_agents.common.runner import run_agent
from content_agents.common.source import fetch_bilibili_hot, fetch_feed

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
DEFAULT_FEEDS = "Bilibili Hot Ranking|https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all"

# These are headline/news forms, not reusable jokes. They are intentionally
# conservative: a quiet run is preferable to publishing unrelated news.
NEWS_FORMS = (
    "表示", "称", "宣布", "发布", "通报", "确认", "回应", "发生", "正式", "获刑",
    "被捕", "遇难", "死亡", "事故", "地震", "台风", "洪水", "战争", "总统", "外交",
    "马拉松", "足球", "篮球", "赛事", "奖牌", "演唱会", "明星", "电影", "电视剧",
)
MEME_MARKERS = (
    "梗", "反转", "破防", "笑死", "绷不住", "离谱", "抽象", "逆天", "真香", "上大分",
    "空城计", "关中王", "牛来", "牛来了", "封神", "复活吧", "谐音", "戏称", "网友调侃",
)


def compact_title(title: str) -> str:
    return re.sub(r"\s+", "", title).strip("-_| ")


def agent_enabled(config: AgentConfig) -> bool:
    enabled = os.getenv("MEME_AGENT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    return enabled and bool(os.getenv("MEME_AGENT_SERVICE_URL", "").strip() or (config.llm_base_url and config.llm_api_key))


def meme_score(candidate: Candidate) -> tuple[int, list[str]]:
    title = compact_title(candidate.title)
    if not title or len(title) > int(os.getenv("MEME_MAX_TITLE_LENGTH", "12")):
        return -99, ["title-too-long"]
    if any(term in title for term in NEWS_FORMS):
        return -99, ["news-headline-form"]
    score = 0
    reasons: list[str] = []
    if any(marker in title for marker in MEME_MARKERS):
        score += 4
        reasons.append("meme-marker")
    if re.search(r"[\u2018\u2019\u201c\u201d\"].{1,12}[\u2018\u2019\u201c\u201d\"]", title):
        score += 3
        reasons.append("quoted-phrase")
    if 3 <= len(title) <= 8:
        score += 3
        reasons.append("short-phrase")
    elif 2 <= len(title) <= 4:
        score += 5
        reasons.append("compact-idiom-like-phrase")
    if any(mark in title for mark in ("？", "！", "…", "（", "）", "(", ")")):
        score += 2
        reasons.append("meme-template")
    if candidate.metadata.get("view_count"):
        score += 1
    return score, reasons


def agent_judge(candidate: Candidate, config: AgentConfig) -> tuple[Candidate | None, str]:
    """Ask the configured content agent to extract a reusable meme phrase."""
    prompt = f"""你是网络梗研究员。请判断下面的 Bilibili 热门视频是否包含网友传播的事件型梗。
梗必须是类似“恒大空城计”“是关中王来了”“牛来”的短句、谐音、典故或模板，能脱离原新闻标题独立传播。
普通新闻、完整事件标题、单纯明星/赛事/搬运视频都不是梗。
只返回 JSON：{{"is_meme":true/false,"phrase":"不超过12个汉字的梗短句","context":"一句话说明事件背景","joke":"一句话说明网友调侃的错位点","confidence":0到1}}
标题：{candidate.title}
简介：{candidate.summary}
来源：{candidate.url}"""
    try:
        service_url = os.getenv("MEME_AGENT_SERVICE_URL", "").rstrip("/")
        if service_url:
            result = post_json(f"{service_url}/v1/meme/judge", {"title": candidate.title, "summary": candidate.summary, "url": candidate.url}, timeout=90)
        else:
            result = generate_json(prompt, config)
    except (HttpClientError, RuntimeError) as exc:
        logging.getLogger(__name__).warning(
            "Meme agent judge failed; fallback applied (attempts=%s, status=%s): %s",
            getattr(exc, "attempts", 1),
            getattr(exc, "status_code", "n/a"),
            exc,
        )
        return candidate, "llm-fallback"
    if not isinstance(result, dict) or not result.get("is_meme"):
        return None, "agent-rejected"
    phrase = compact_title(str(result.get("phrase") or candidate.title))
    if not phrase or len(phrase) > 12:
        return None, "agent-invalid-phrase"
    metadata = dict(candidate.metadata)
    metadata["agent_confidence"] = result.get("confidence", 0)
    metadata["agent_judgement"] = "approved"
    summary = "；".join(filter(None, [str(result.get("context") or "").strip(), str(result.get("joke") or "").strip()]))
    return replace(candidate, title=phrase, summary=summary, metadata=metadata), "agent-approved"


def apply_agent_result(candidate: Candidate, result: object) -> tuple[Candidate | None, str]:
    if not isinstance(result, dict) or not result.get("is_meme"):
        return None, "agent-rejected"
    phrase = compact_title(str(result.get("phrase") or candidate.title))
    if not phrase or len(phrase) > 12:
        return None, "agent-invalid-phrase"
    metadata = dict(candidate.metadata)
    metadata["agent_confidence"] = result.get("confidence", 0)
    metadata["agent_judgement"] = "approved"
    summary = " ".join(filter(None, [str(result.get("context") or "").strip(), str(result.get("joke") or "").strip()]))
    return replace(candidate, title=phrase, summary=summary, metadata=metadata), "agent-approved"


def agent_judge_batch(candidates: list[Candidate], config: AgentConfig) -> list[Candidate | None]:
    service_url = os.getenv("MEME_AGENT_SERVICE_URL", "").rstrip("/")
    if not service_url:
        return [agent_judge(candidate, config)[0] for candidate in candidates]
    try:
        result = post_json(
            f"{service_url}/v1/meme/judge-batch",
            {"candidates": [{"title": c.title, "summary": c.summary, "url": c.url} for c in candidates]},
            timeout=180,
        )
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list) or len(items) != len(candidates):
            raise RuntimeError("invalid batch judgement response")
        return [apply_agent_result(candidate, item)[0] for candidate, item in zip(candidates, items)]
    except (HttpClientError, RuntimeError) as exc:
        logging.getLogger(__name__).warning(
            "Meme batch agent failed; fallback applied (attempts=%s, status=%s): %s",
            getattr(exc, "attempts", 1),
            getattr(exc, "status_code", "n/a"),
            exc,
        )
        return [candidate for candidate in candidates]


def collect(config: AgentConfig, *, sample: bool = False) -> list[Candidate]:
    if sample:
        return [Candidate(external_id="sample-meme-001", title="是关中王来了", summary="", url="https://example.com/meme", source="Sample", metadata={"meme_score": 7})]
    candidates: list[Candidate] = []
    for value in os.getenv("MEME_FEEDS", DEFAULT_FEEDS).split("||"):
        if "|" not in value:
            continue
        source, url = (part.strip() for part in value.split("|", 1))
        try:
            if "bilibili" in url.lower() or "bilibili" in source.lower():
                candidates.extend(fetch_bilibili_hot(url, source=source, limit=50))
            else:
                candidates.extend(fetch_feed(url, source=source))
        except (HttpClientError, ValueError) as exc:
            logging.getLogger(__name__).warning("Meme feed failed source=%s error=%s", source, exc)
    scored: list[tuple[int, Candidate]] = []
    minimum = max(1, int(os.getenv("MEME_MIN_SCORE", "6")))
    unique_candidates = list({row.url: row for row in candidates if row.url}.values())
    judged_candidates = agent_judge_batch(unique_candidates, config) if agent_enabled(config) else unique_candidates
    for candidate in judged_candidates:
        if candidate is None:
            continue
        score, reasons = meme_score(candidate)
        if agent_enabled(config):
            score, local_reasons = meme_score(candidate)
            reasons.extend(local_reasons)
            reasons.append("agent-approved")
            score = max(score, minimum)
        if score >= minimum:
            candidate.metadata.update(meme_score=score, meme_reasons=reasons)
            scored.append((score, candidate))
    scored.sort(key=lambda row: (-row[0], int(row[1].metadata.get("rank") or 9999)))
    return [candidate for _, candidate in scored]


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    title = compact_title(candidate.title)
    summary = " ".join((candidate.summary or "").split()).strip()
    body = (summary or "这个梗的背景请以原视频为准。") + f"\n\n原视频：{candidate.url}"
    risk, status, notes = assess(body, default_risk="medium")
    return ContentItem.create(
        bot_name=config.bot_name, bot_version=config.bot_version, prompt_version=config.prompt_version,
        title=f"今日热梗：{title}", body=body, summary=summary, language="zh-CN",
        source_refs=[SourceRef(name=candidate.source, url=candidate.url, external_id=candidate.external_id, published_at=candidate.published_at, excerpt=summary)],
        tags=["热梗", "短句梗", candidate.source], topics=["internet-culture", "event-joke"],
        risk_level=risk, review_status=status, review_notes=notes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect short event-based internet jokes")
    parser.add_argument("--sample", action="store_true")
    args = parser.parse_args()
    config = AgentConfig.from_env("meme-collector")
    run_agent(config, lambda: collect(config, sample=args.sample), render)


if __name__ == "__main__":
    main()
