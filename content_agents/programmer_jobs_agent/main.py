from __future__ import annotations

import argparse
import hashlib
import html
import logging
import os
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError, get_json, post_json
from content_agents.common.models import Candidate, ContentItem, SourceRef
from content_agents.common.review import assess
from content_agents.common.runner import run_agent

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

DEFAULT_SEARCHES = "开发工程师|https://www.zhipin.com/web/geek/job?query=%E5%BC%80%E5%8F%91%E5%B7%A5%E7%A8%8B%E5%B8%88"
MAX_SOURCE_JOBS = 80


def search_specs() -> list[tuple[str, str]]:
    raw = os.getenv("BOSS_JOB_SEARCHES", DEFAULT_SEARCHES)
    return [
        (name.strip(), url.strip())
        for value in raw.split("||")
        if "|" in value
        for name, url in [value.split("|", 1)]
        if name.strip() and url.strip()
    ]


def text(value: object) -> str:
    if isinstance(value, str):
        return " ".join(html.unescape(value).split())
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def list_text(value: object) -> list[str]:
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            if isinstance(item, dict):
                values.extend(text(item.get(key)) for key in ("name", "value", "label", "text"))
            else:
                values.append(text(item))
        return list(dict.fromkeys(item for item in values if item))
    return [text(value)] if text(value) else []


def first(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = text(row.get(key))
        if value:
            return value
    return ""


def boss_api_url(search_url: str) -> str:
    """Build the read-only job-list URL used by Boss's public search page."""
    parts = urlsplit(search_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("city", "100010000")
    query.setdefault("scene", "1")
    query.setdefault("ka", "page-0")
    return urlunsplit((parts.scheme, parts.netloc, "/wapi/zpgeek/search/joblist.json", urlencode(query), ""))


def boss_headers(search_url: str) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (compatible; PanghuJobMarketBot/1.0; +https://hublog.panghuer.top/)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": search_url,
    }


def normalize_job(row: dict[str, Any], *, search_name: str, search_url: str) -> Candidate | None:
    title = first(row, "jobName", "job_name", "positionName", "jobTitle")
    company = first(row, "brandName", "companyName", "brand_name", "company")
    salary = first(row, "salaryDesc", "salary", "salaryText")
    city = first(row, "cityName", "city", "locationName")
    experience = first(row, "experienceName", "experience", "workingExp")
    education = first(row, "degreeName", "degree", "education")
    skills = list_text(row.get("jobLabels") or row.get("skills") or row.get("skillList") or row.get("jobSkills"))
    if not title or not company:
        return None
    if len(title) > 100 or len(company) > 100:
        return None
    source_id = first(row, "encryptJobId", "jobId", "job_id", "id")
    external_id = source_id or hashlib.sha256(
        f"{search_url}|{title}|{company}|{salary}|{city}".encode("utf-8")
    ).hexdigest()[:24]
    detail_url = first(row, "jobUrl", "url", "detailUrl") or search_url
    return Candidate(
        external_id=f"{search_name}:{external_id}",
        title=title,
        summary="；".join(part for part in [company, city, salary, experience, education] if part),
        url=detail_url,
        source="Boss直聘",
        metadata={
            "company": company,
            "city": city,
            "salary": salary,
            "experience": experience,
            "education": education,
            "skills": skills[:20],
            "search_name": search_name,
            "search_url": search_url,
        },
    )


def fetch_boss_jobs() -> tuple[list[Candidate], list[tuple[str, str]]]:
    jobs: list[Candidate] = []
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for search_name, search_url in search_specs():
        try:
            document = get_json(boss_api_url(search_url), headers=boss_headers(search_url), timeout=20)
        except HttpClientError as exc:
            logger.warning("Boss job-list request failed search=%s error=%s", search_name, exc)
            continue
        if not isinstance(document, dict):
            logger.warning("Boss job-list returned an invalid document search=%s", search_name)
            continue
        if document.get("code") not in (0, "0"):
            logger.warning(
                "Boss job-list rejected search=%s code=%s message=%s; no bypass is attempted",
                search_name,
                document.get("code"),
                text(document.get("message")) or "unknown",
            )
            continue
        data = document.get("zpData")
        rows = data.get("jobList", []) if isinstance(data, dict) else []
        if not isinstance(rows, list):
            logger.warning("Boss job-list did not contain jobs search=%s", search_name)
            continue
        sources.append((search_name, search_url))
        for row in rows:
            if not isinstance(row, dict):
                continue
            job = normalize_job(row, search_name=search_name, search_url=search_url)
            if job and job.external_id not in seen:
                seen.add(job.external_id)
                jobs.append(job)
                if len(jobs) >= MAX_SOURCE_JOBS:
                    return jobs, sources
    return jobs, sources


def sample_jobs() -> tuple[list[Candidate], list[tuple[str, str]]]:
    source = ("示例检索", "https://www.zhipin.com/web/geek/job?query=" + quote("开发工程师"))
    jobs = [
        Candidate("sample-python", "Python 后端工程师", "示例科技；北京；25-45K；3-5年；本科", source[1], "Boss直聘", metadata={"company": "示例科技", "city": "北京", "salary": "25-45K", "experience": "3-5年", "education": "本科", "skills": ["Python", "FastAPI", "PostgreSQL", "Redis"]}),
        Candidate("sample-java", "Java 平台工程师", "样例云服务；上海；30-50K；5-10年；本科", source[1], "Boss直聘", metadata={"company": "样例云服务", "city": "上海", "salary": "30-50K", "experience": "5-10年", "education": "本科", "skills": ["Java", "Spring Boot", "Kafka", "Kubernetes"]}),
    ]
    return jobs, [source]


def summarize(jobs: list[Candidate], config: AgentConfig) -> dict[str, Any] | None:
    service_url = os.getenv("CONTENT_LLM_SERVICE_URL", "").rstrip("/")
    if not service_url:
        logger.error("CONTENT_LLM_SERVICE_URL is required for programmer-jobs-agent")
        return None
    payload = {"jobs": [
        {
            "title": job.title,
            "company": job.metadata.get("company", ""),
            "city": job.metadata.get("city", ""),
            "salary": job.metadata.get("salary", ""),
            "experience": job.metadata.get("experience", ""),
            "education": job.metadata.get("education", ""),
            "skills": job.metadata.get("skills", []),
        }
        for job in jobs
    ]}
    try:
        result = post_json(f"{service_url}/v1/jobs/programmer-summary", payload, timeout=180)
    except HttpClientError as exc:
        logger.warning("Programming jobs batch summary failed: %s", exc)
        return None
    if not isinstance(result, dict) or not text(result.get("overview")):
        logger.warning("Programming jobs batch summary returned an invalid result")
        return None
    return result


def report_candidate(jobs: list[Candidate], sources: list[tuple[str, str]], summary: dict[str, Any]) -> Candidate:
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    source_names = ", ".join(name for name, _ in sources)
    overview = text(summary.get("overview"))
    return Candidate(
        external_id=f"programmer-jobs-daily:{day}",
        title=f"程序员招聘日报（{day}）",
        summary=overview,
        url=sources[0][1],
        source="Boss直聘",
        metadata={"jobs": jobs, "sources": sources, "llm_summary": summary, "source_names": source_names},
    )


def collect(config: AgentConfig, *, sample: bool = False) -> list[Candidate]:
    jobs, sources = sample_jobs() if sample else fetch_boss_jobs()
    if not jobs:
        logger.warning("No valid Boss jobs collected; skip daily publication")
        return []
    summary = summarize(jobs, config)
    if summary is None:
        return []
    return [report_candidate(jobs, sources, summary)]


def bullet_lines(value: object, *, prefix: str = "- ") -> str:
    if not isinstance(value, list):
        return ""
    lines: list[str] = []
    for item in value[:8]:
        if isinstance(item, dict):
            direction = text(item.get("direction"))
            demand = text(item.get("demand"))
            skills = "、".join(list_text(item.get("skills"))[:10])
            detail = "：".join(part for part in [direction, demand] if part)
            if skills:
                detail += f"（常见技能：{skills}）"
            if detail:
                lines.append(prefix + detail)
        else:
            item_text = text(item)
            if item_text:
                lines.append(prefix + item_text)
    return "\n".join(lines)


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    summary = candidate.metadata["llm_summary"]
    jobs: list[Candidate] = candidate.metadata["jobs"]
    sources: list[tuple[str, str]] = candidate.metadata["sources"]
    directions = bullet_lines(summary.get("directions")) or "- 本次样本不足以稳定归纳方向。"
    skills = "、".join(list_text(summary.get("top_skills"))[:20]) or "以岗位原文为准"
    signals = "\n".join(filter(None, [
        f"- 经验要求：{text(summary.get('experience_signal'))}" if text(summary.get("experience_signal")) else "",
        f"- 学历要求：{text(summary.get('education_signal'))}" if text(summary.get("education_signal")) else "",
        f"- 薪资信号：{text(summary.get('salary_signal'))}" if text(summary.get("salary_signal")) else "",
    ]))
    source_lines = "\n".join(f"- {name}：{url}" for name, url in sources[:5])
    body = (
        f"今日样本：从 Boss 直聘公开岗位检索页整理 {len(jobs)} 条程序员岗位信息。\n\n"
        f"市场概览：{text(summary.get('overview'))}\n\n"
        f"需求方向\n{directions}\n\n"
        f"高频技术技能：{skills}\n\n"
        f"招聘信号\n{signals or '- 样本中的相关字段不足。'}\n\n"
        f"求职建议：{text(summary.get('advice')) or '将岗位技能与自己的项目经历逐项对应，并以职位详情的最新要求为准。'}\n\n"
        f"来源（公开搜索页，职位会实时变化）\n{source_lines}\n\n"
        "本日报基于公开检索结果和模型归纳，不代表平台完整岗位库，也不构成就业承诺。"
    )
    risk, status, notes = assess(body, default_risk="medium")
    return ContentItem.create(
        bot_name=config.bot_name,
        bot_version=config.bot_version,
        prompt_version=config.prompt_version,
        title=candidate.title,
        body=body,
        summary=candidate.summary,
        language="zh-CN",
        source_refs=[
            SourceRef(name=f"Boss直聘：{name}", url=url, external_id=f"{candidate.external_id}:{index}", excerpt="公开程序员岗位检索页")
            for index, (name, url) in enumerate(sources)
        ],
        tags=["招聘", "程序员", "求职", "Boss直聘"],
        topics=["jobs", "technology-careers"],
        risk_level=risk,
        review_status=status,
        review_notes=notes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Boss programming-jobs market summary")
    parser.add_argument("--sample", action="store_true", help="use offline sample jobs")
    args = parser.parse_args()
    config = AgentConfig.from_env("programmer-jobs")
    run_agent(config, lambda: collect(config, sample=args.sample), render)


if __name__ == "__main__":
    main()
