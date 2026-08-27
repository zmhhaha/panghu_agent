from __future__ import annotations

import argparse
import hashlib
import html
import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError, get_json, post_json
from content_agents.common.models import Candidate, ContentItem, SourceRef
from content_agents.common.review import assess
from content_agents.common.runner import run_agent
from content_agents.common.source import fetch_feed

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

DEFAULT_FEEDS = "||".join((
    "RemoteJobsCN|https://remotejobscn.com/rss.xml",
    "Remotive|https://remotive.com/api/remote-jobs",
    "Remote OK|https://remoteok.com/api",
    "AI Dev Jobs|https://aidevboard.com/api/v1/jobs?tags=python,ai&posted_within_days=7",
))
MAX_JOBS = 80
TECH_TERMS = (
    "python", "java", "golang", "go developer", "c++", "c#", "rust", "ruby", "php", "scala",
    "javascript", "typescript", "react", "vue", "angular", "node", "frontend", "front-end",
    "backend", "back-end", "full stack", "fullstack", "software", "engineer", "developer",
    "devops", "sre", "platform", "cloud", "kubernetes", "docker", "terraform", "linux",
    "data engineer", "data scientist", "machine learning", "ai", "llm", "rag", "algorithm",
    "区块链", "智能合约", "开发", "工程师", "后端", "前端", "全栈", "运维", "测试开发",
    "数据工程", "数据科学", "机器学习", "人工智能", "算法", "云原生", "嵌入式", "移动端",
)


def feed_specs() -> list[tuple[str, str]]:
    raw = os.getenv("PROGRAMMER_JOB_FEEDS", DEFAULT_FEEDS)
    return [
        (name.strip(), url.strip())
        for value in raw.split("||")
        if "|" in value
        for name, url in [value.split("|", 1)]
        if name.strip() and url.strip()
    ]


def clean(value: object, *, limit: int = 4000) -> str:
    if isinstance(value, str):
        return " ".join(html.unescape(value).split())[:limit]
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = clean(row.get(key))
        if value:
            return value
    return ""


def values(value: object) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                result.extend(clean(item.get(key), limit=120) for key in ("name", "label", "value", "title"))
            else:
                result.append(clean(item, limit=120))
        return list(dict.fromkeys(item for item in result if item))
    value_text = clean(value, limit=120)
    return [value_text] if value_text else []


def company_name(value: object) -> str:
    return first(value, "name", "company_name", "title") if isinstance(value, dict) else clean(value, limit=200)


def is_technical(*parts: object) -> bool:
    searchable = " ".join(
        item.lower() if isinstance(item, str) else " ".join(values(item)).lower()
        for item in parts if item
    )
    return any(term in searchable for term in TECH_TERMS)


def published_recent(value: str, lookback_days: int) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed >= datetime.now(timezone.utc) - timedelta(days=lookback_days)


def make_job(
    *, source: str, source_id: object, title: object, url: object, company: object = "", city: object = "",
    salary: object = "", experience: object = "", education: object = "", skills: object = (),
    description: object = "", published_at: object = "",
) -> Candidate | None:
    job_title = clean(title, limit=200)
    job_url = clean(url, limit=2000)
    job_company = company_name(company)
    job_skills = values(skills)[:30]
    # A concise public excerpt is enough for skill classification and keeps
    # the once-per-run LLM batch within a predictable context size.
    job_description = clean(description, limit=1000)
    published = clean(published_at, limit=100)
    if not job_title or not job_url or not is_technical(job_title, job_skills, job_description):
        return None
    external = clean(source_id, limit=300) or hashlib.sha256(
        f"{source}|{job_title}|{job_company}|{job_url}".encode("utf-8")
    ).hexdigest()
    return Candidate(
        external_id=f"{source}:{external}", title=job_title,
        summary="；".join(part for part in [job_company, clean(city, limit=120), clean(salary, limit=120)] if part),
        url=job_url, source=source, published_at=published,
        metadata={
            "company": job_company, "city": clean(city, limit=120), "salary": clean(salary, limit=120),
            "experience": clean(experience, limit=120), "education": clean(education, limit=120),
            "skills": job_skills, "description": job_description,
        },
    )


def fetch_remotejobscn(name: str, url: str, lookback_days: int) -> list[Candidate]:
    jobs: list[Candidate] = []
    for row in fetch_feed(url, source=name):
        if published_recent(row.published_at, lookback_days):
            job = make_job(source=name, source_id=row.external_id, title=row.title, url=row.url,
                           description=row.summary, published_at=row.published_at)
            if job:
                jobs.append(job)
    return jobs


def fetch_remotive(name: str, url: str, lookback_days: int) -> list[Candidate]:
    document = get_json(url, headers={"User-Agent": "panghu-programmer-jobs-agent/0.2"})
    rows = document.get("jobs", []) if isinstance(document, dict) else []
    jobs: list[Candidate] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not published_recent(clean(row.get("publication_date")), lookback_days):
            continue
        job = make_job(source=name, source_id=row.get("id"), title=row.get("title"), url=row.get("url"),
                       company=row.get("company_name"), city=row.get("candidate_required_location"),
                       salary=row.get("salary"), skills=row.get("tags"), description=row.get("description"),
                       published_at=row.get("publication_date"))
        if job:
            jobs.append(job)
    return jobs


def fetch_remoteok(name: str, url: str, lookback_days: int) -> list[Candidate]:
    document = get_json(url, headers={"User-Agent": "panghu-programmer-jobs-agent/0.2"})
    jobs: list[Candidate] = []
    for row in document if isinstance(document, list) else []:
        if not isinstance(row, dict) or not row.get("position") or not published_recent(clean(row.get("date")), lookback_days):
            continue
        salary = "-".join(part for part in (clean(row.get("salary_min")), clean(row.get("salary_max"))) if part)
        job = make_job(source=name, source_id=row.get("id"), title=row.get("position"), url=row.get("url"),
                       company=row.get("company"), city=row.get("location"), salary=salary, skills=row.get("tags"),
                       description=row.get("description"), published_at=row.get("date"))
        if job:
            jobs.append(job)
    return jobs


def fetch_ai_dev_jobs(name: str, url: str, lookback_days: int) -> list[Candidate]:
    document = get_json(url, headers={"User-Agent": "panghu-programmer-jobs-agent/0.2"}, timeout=45)
    rows = document.get("jobs", []) if isinstance(document, dict) else []
    jobs: list[Candidate] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        published = first(row, "posted_at", "published_at", "created_at", "date")
        if not published_recent(published, lookback_days):
            continue
        job = make_job(
            source=name, source_id=row.get("id") or row.get("slug"), title=row.get("title") or row.get("name"),
            url=row.get("url") or row.get("apply_url"), company=row.get("company") or row.get("company_name"),
            city=row.get("location") or row.get("candidate_required_location"),
            salary=row.get("salary") or row.get("salary_range"), experience=row.get("level") or row.get("experience"),
            education=row.get("education"), skills=row.get("tags") or row.get("skills"),
            description=row.get("description") or row.get("summary"), published_at=published,
        )
        if job:
            jobs.append(job)
    return jobs


def fetch_source(name: str, url: str, lookback_days: int) -> list[Candidate]:
    lowered = name.lower()
    if "remotejobscn" in lowered:
        return fetch_remotejobscn(name, url, lookback_days)
    if "remotive" in lowered:
        return fetch_remotive(name, url, lookback_days)
    if "remote ok" in lowered:
        return fetch_remoteok(name, url, lookback_days)
    if "ai dev jobs" in lowered:
        return fetch_ai_dev_jobs(name, url, lookback_days)
    logger.warning("Unsupported programmer jobs source=%s", name)
    return []


def collect_jobs() -> tuple[list[Candidate], list[tuple[str, str]]]:
    lookback_days = max(1, int(os.getenv("PROGRAMMER_JOB_LOOKBACK_DAYS", "7")))
    jobs: list[Candidate] = []
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, url in feed_specs():
        try:
            candidates = fetch_source(name, url, lookback_days)
        except (HttpClientError, ValueError) as exc:
            logger.warning("Programmer jobs source failed source=%s error=%s", name, exc)
            continue
        if candidates:
            sources.append((name, url))
        for job in candidates:
            if job.external_id not in seen:
                seen.add(job.external_id)
                jobs.append(job)
                if len(jobs) >= MAX_JOBS:
                    return jobs, sources
    return jobs, sources


def sample_jobs() -> tuple[list[Candidate], list[tuple[str, str]]]:
    sources = [("Remotive", "https://remotive.com/api/remote-jobs"), ("Remote OK", "https://remoteok.com/api")]
    jobs = [
        make_job(source="Remotive", source_id="sample-python", title="Python Backend Engineer", url="https://example.com/python", company="Example Cloud", city="Remote", salary="25-45K", experience="3-5 years", education="Bachelor", skills=["Python", "FastAPI", "PostgreSQL", "Redis"], description="Build backend services and cloud APIs."),
        make_job(source="Remote OK", source_id="sample-java", title="Java Platform Engineer", url="https://example.com/java", company="Example Platform", city="Remote", salary="30-50K", experience="5-10 years", education="Bachelor", skills=["Java", "Spring Boot", "Kafka", "Kubernetes"], description="Operate a distributed developer platform."),
    ]
    return [job for job in jobs if job], sources


def summarize(jobs: list[Candidate]) -> dict[str, Any] | None:
    service_url = os.getenv("CONTENT_LLM_SERVICE_URL", "").rstrip("/")
    if not service_url:
        logger.error("CONTENT_LLM_SERVICE_URL is required for programmer-jobs-agent")
        return None
    payload = {"jobs": [{
        "title": job.title, "company": job.metadata.get("company", ""), "city": job.metadata.get("city", ""),
        "salary": job.metadata.get("salary", ""), "experience": job.metadata.get("experience", ""),
        "education": job.metadata.get("education", ""), "skills": job.metadata.get("skills", []),
        "description": job.metadata.get("description", ""), "source": job.source,
    } for job in jobs]}
    try:
        result = post_json(f"{service_url}/v1/jobs/programmer-summary", payload, timeout=180)
    except HttpClientError as exc:
        logger.warning(
            "Programming jobs batch summary failed; publication skipped (attempts=%s, status=%s): %s",
            getattr(exc, "attempts", 1),
            getattr(exc, "status_code", "n/a"),
            exc,
        )
        return None
    if not isinstance(result, dict) or not clean(result.get("overview")):
        logger.warning("Programming jobs batch summary returned an invalid result")
        return None
    return result


def report_candidate(jobs: list[Candidate], sources: list[tuple[str, str]], summary: dict[str, Any]) -> Candidate:
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    return Candidate(external_id=f"programmer-jobs-daily:{day}", title=f"程序员招聘日报（{day}）",
                     summary=clean(summary.get("overview")), url=sources[0][1], source="技术招聘公开源",
                     metadata={"jobs": jobs, "sources": sources, "llm_summary": summary})


def collect(*, sample: bool = False) -> list[Candidate]:
    jobs, sources = sample_jobs() if sample else collect_jobs()
    if not jobs:
        logger.warning("No valid programming jobs collected; skip daily publication")
        return []
    summary = summarize(jobs)
    return [report_candidate(jobs, sources, summary)] if summary else []


def bullet_lines(value: object) -> str:
    lines: list[str] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        direction = clean(item.get("direction"), limit=180)
        demand = clean(item.get("demand"), limit=300)
        skills = "、".join(values(item.get("skills"))[:10])
        detail = "：".join(part for part in [direction, demand] if part)
        if skills:
            detail += f"（常见技能：{skills}）"
        if detail:
            lines.append(f"- {detail}")
    return "\n".join(lines)


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    summary = candidate.metadata["llm_summary"]
    jobs: list[Candidate] = candidate.metadata["jobs"]
    sources: list[tuple[str, str]] = candidate.metadata["sources"]
    directions = bullet_lines(summary.get("directions")) or "- 本次样本不足以稳定归纳方向。"
    skills = "、".join(values(summary.get("top_skills"))[:20]) or "以岗位原文为准"
    signals = "\n".join(filter(None, [
        f"- 经验要求：{clean(summary.get('experience_signal'))}" if clean(summary.get("experience_signal")) else "",
        f"- 学历要求：{clean(summary.get('education_signal'))}" if clean(summary.get("education_signal")) else "",
        f"- 薪资信号：{clean(summary.get('salary_signal'))}" if clean(summary.get("salary_signal")) else "",
    ]))
    source_lines = "\n".join(f"- {name}：{url}" for name, url in sources)
    body = (
        f"今日样本：从 {len(sources)} 个公开技术招聘源整理 {len(jobs)} 条近期岗位信息。\n\n"
        f"市场概览：{clean(summary.get('overview'))}\n\n需求方向\n{directions}\n\n"
        f"高频技术技能：{skills}\n\n招聘信号\n{signals or '- 样本中的相关字段不足。'}\n\n"
        f"求职建议：{clean(summary.get('advice')) or '将岗位技能与自己的项目经历逐项对应，并以职位详情的最新要求为准。'}\n\n"
        f"来源（公开职位页/API，岗位状态会实时变化）\n{source_lines}\n\n"
        "本日报基于公开技术岗位样本和模型归纳，不代表完整招聘市场，也不构成就业承诺。"
    )
    risk, status, notes = assess(body, default_risk="medium")
    return ContentItem.create(
        bot_name=config.bot_name, bot_version=config.bot_version, prompt_version=config.prompt_version,
        title=candidate.title, body=body, summary=candidate.summary, language="zh-CN",
        source_refs=[SourceRef(name=name, url=url, external_id=f"{candidate.external_id}:{index}", excerpt="公开技术招聘岗位源") for index, (name, url) in enumerate(sources)],
        tags=["招聘", "程序员", "求职", "技术岗位", "远程招聘"], topics=["jobs", "technology-careers"],
        risk_level=risk, review_status=status, review_notes=notes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily programming-jobs market summary")
    parser.add_argument("--sample", action="store_true", help="use offline sample jobs")
    args = parser.parse_args()
    config = AgentConfig.from_env("programmer-jobs")
    run_agent(config, lambda: collect(sample=args.sample), render)


if __name__ == "__main__":
    main()
