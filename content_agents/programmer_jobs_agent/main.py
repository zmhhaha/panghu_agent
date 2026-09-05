from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import random
import re
import time
from html.parser import HTMLParser
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError, get_json, get_text, post_json
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
    "JDWatch Daily|https://www.jdwatch.work/blog",
))
MAX_JOBS = 80
JDWATCH_JOB_PATH = re.compile(r"^/jobs/([A-Za-z0-9_-]+)/?$")
DAILY_REPORT_TITLE = re.compile(r"^程序员招聘日报（(\d{4}-\d{2}-\d{2})）$")
WEEKLY_REPORT_CONTENT_LIMIT = 6000
TECH_TERMS = (
    "python", "java", "golang", "go developer", "c++", "c#", "rust", "ruby", "php", "scala",
    "javascript", "typescript", "react", "vue", "angular", "node", "frontend", "front-end",
    "backend", "back-end", "full stack", "fullstack", "software", "engineer", "developer",
    "devops", "sre", "platform", "cloud", "kubernetes", "docker", "terraform", "linux",
    "data engineer", "data scientist", "machine learning", "ai", "llm", "rag", "algorithm",
    "区块链", "智能合约", "开发", "工程师", "后端", "前端", "全栈", "运维", "测试开发",
    "数据工程", "数据科学", "机器学习", "人工智能", "算法", "云原生", "嵌入式", "移动端",
)


class _AnchorParser(HTMLParser):
    """Collect visible anchor labels without executing the source page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href") or ""
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        self.anchors.append((self._href, " ".join(self._text)))
        self._href = ""
        self._text = []


def parse_anchors(document: str) -> list[tuple[str, str]]:
    parser = _AnchorParser()
    parser.feed(document)
    parser.close()
    return parser.anchors


class _JsonLdParser(HTMLParser):
    """Collect JSON-LD script blocks without executing page JavaScript."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._capturing = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        script_type = (dict(attrs).get("type") or "").split(";", 1)[0].strip().lower()
        if script_type == "application/ld+json":
            self._capturing = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._capturing:
            return
        block = "".join(self._buffer).strip()
        if block:
            self.blocks.append(block)
        self._capturing = False
        self._buffer = []


def parse_json_ld(document: str) -> list[dict[str, Any]]:
    """Return object records from JSON-LD, including nested @graph entries."""
    parser = _JsonLdParser()
    parser.feed(document)
    parser.close()
    records: list[dict[str, Any]] = []

    def add(value: object) -> None:
        if isinstance(value, dict):
            graph = value.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    add(item)
            else:
                records.append(value)
        elif isinstance(value, list):
            for item in value:
                add(item)

    for block in parser.blocks:
        try:
            add(json.loads(block))
        except json.JSONDecodeError:
            logger.debug("JDWatch detail page contained invalid JSON-LD")
    return records


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


def _jdwatch_daily_page(index_url: str, lookback_days: int) -> tuple[str, str] | None:
    document = get_text(index_url, headers={"User-Agent": "panghu-programmer-jobs-agent/0.3"}, timeout=30)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    reports: list[tuple[date, str]] = []
    for href, _ in parse_anchors(document):
        match = re.search(r"/blog/(\d{4}-\d{2}-\d{2})-daily/?(?:$|[?#])", href)
        if not match:
            continue
        try:
            report_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if report_date <= today and (today - report_date).days <= max(lookback_days, 1):
            reports.append((report_date, urljoin(index_url, href)))
    if not reports:
        return None
    report_date, report_url = max(reports, key=lambda value: value[0])
    return report_url, report_date.isoformat()


def _json_ld_job_record(document: str) -> dict[str, Any] | None:
    records = parse_json_ld(document)
    for record in records:
        job_type = record.get("@type")
        types = job_type if isinstance(job_type, list) else [job_type]
        if any(clean(item).lower().rsplit("/", 1)[-1] == "jobposting" for item in types):
            return record
    return None


def _json_ld_location(value: object) -> str:
    locations = value if isinstance(value, list) else [value]
    for location in locations:
        if not isinstance(location, dict):
            text = clean(location, limit=160)
            if text:
                return text
            continue
        address = location.get("address", location)
        if isinstance(address, dict):
            parts = [
                clean(address.get(key), limit=80)
                for key in ("addressLocality", "addressRegion", "addressCountry")
            ]
            text = "，".join(dict.fromkeys(part for part in parts if part))
        else:
            text = clean(address, limit=160)
        if text:
            return text
    return ""


def _json_ld_skills(value: object) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,，;/|、\n]+", value)
        return list(dict.fromkeys(clean(part, limit=120) for part in parts if clean(part, limit=120)))[:30]
    return values(value)[:30]


def _json_ld_salary(value: object) -> str:
    if not isinstance(value, dict):
        return clean(value, limit=160)
    currency = clean(value.get("currency"), limit=20)
    amount = value.get("value")
    if isinstance(amount, dict):
        lower = clean(amount.get("minValue"), limit=40)
        upper = clean(amount.get("maxValue"), limit=40)
        amount_text = "-".join(part for part in (lower, upper) if part)
    else:
        amount_text = clean(amount, limit=80)
    return " ".join(part for part in (amount_text, currency) if part)


def _jdwatch_detail_fields(document: str) -> dict[str, object]:
    record = _json_ld_job_record(document)
    if record is None:
        return {}
    organization = record.get("hiringOrganization")
    return {
        "title": record.get("title"),
        "company": organization,
        "city": _json_ld_location(record.get("jobLocation")),
        "salary": _json_ld_salary(record.get("baseSalary")),
        "experience": record.get("experienceRequirements"),
        "education": record.get("educationRequirements"),
        "skills": _json_ld_skills(record.get("skills")),
        "description": record.get("description") or record.get("responsibilities"),
        "published_at": record.get("datePosted") or record.get("dateCreated"),
    }


def fetch_jdwatch(name: str, url: str, lookback_days: int) -> list[Candidate]:
    """Read the latest public report and politely fetch its job detail pages."""
    selected = _jdwatch_daily_page(url, lookback_days)
    if selected is None:
        logger.warning("JDWatch has no recent public daily report url=%s", url)
        return []
    report_url, report_date = selected
    document = get_text(report_url, headers={"User-Agent": "panghu-programmer-jobs-agent/0.3"}, timeout=30)
    try:
        detail_limit = max(1, int(os.getenv("PROGRAMMER_JOB_MAX_PER_SOURCE", "20")))
    except ValueError:
        detail_limit = 20
        logger.warning("Invalid PROGRAMMER_JOB_MAX_PER_SOURCE; using %d", detail_limit)

    entries: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for href, title in parse_anchors(document):
        absolute_url = urljoin(report_url, href)
        parsed = urlparse(absolute_url)
        match = JDWATCH_JOB_PATH.fullmatch(parsed.path)
        if not match or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        entries.append((match.group(1), absolute_url, clean(title, limit=200)))
        if len(entries) >= detail_limit:
            break

    jobs: list[Candidate] = []
    detail_pages = 0
    rate_limited = False
    for index, (job_id, detail_url, list_title) in enumerate(entries):
        if index:
            delay = random.uniform(3.0, 10.0)
            logger.debug("Waiting %.1fs before JDWatch detail url=%s", delay, detail_url)
            time.sleep(delay)
        try:
            detail_document = get_text(
                detail_url,
                headers={"User-Agent": "panghu-programmer-jobs-agent/0.4"},
                timeout=30,
            )
        except HttpClientError as exc:
            if exc.status_code in (403, 429):
                logger.warning(
                    "JDWatch detail access stopped after HTTP %s url=%s; remaining details skipped",
                    exc.status_code,
                    detail_url,
                )
                rate_limited = True
                break
            logger.warning("JDWatch detail failed url=%s error=%s; using report entry", detail_url, exc)
            detail_document = ""
        detail_pages += 1
        fields = _jdwatch_detail_fields(detail_document)
        job = make_job(
            source=name,
            source_id=job_id,
            title=fields.get("title") or list_title,
            url=detail_url,
            company=fields.get("company", ""),
            city=fields.get("city", ""),
            salary=fields.get("salary", ""),
            experience=fields.get("experience", ""),
            education=fields.get("education", ""),
            skills=fields.get("skills", ()),
            description=fields.get("description") or f"JDWatch 公开岗位日报 {report_date}",
            published_at=fields.get("published_at") or f"{report_date}T00:00:00+08:00",
        )
        if job:
            jobs.append(job)
    logger.info(
        "JDWatch daily report=%s detail_pages=%d technical_jobs=%d rate_limited=%s",
        report_url,
        detail_pages,
        len(jobs),
        rate_limited,
    )
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
    if "jdwatch" in lowered:
        return fetch_jdwatch(name, url, lookback_days)
    logger.warning("Unsupported programmer jobs source=%s", name)
    return []


def collect_jobs() -> tuple[list[Candidate], list[tuple[str, str]]]:
    lookback_days = max(1, int(os.getenv("PROGRAMMER_JOB_LOOKBACK_DAYS", "7")))
    per_source_limit = max(1, int(os.getenv("PROGRAMMER_JOB_MAX_PER_SOURCE", "20")))
    sources: list[tuple[str, str]] = []
    source_jobs: list[list[Candidate]] = []
    for name, url in feed_specs():
        try:
            candidates = fetch_source(name, url, lookback_days)
        except (HttpClientError, ValueError) as exc:
            logger.warning("Programmer jobs source failed source=%s error=%s", name, exc)
            continue
        if candidates:
            sources.append((name, url))
        source_jobs.append(candidates[:per_source_limit])

    # Round-robin keeps one large source from crowding all other public feeds
    # out of the single batch sent to the LLM.
    jobs: list[Candidate] = []
    seen: set[str] = set()
    while source_jobs and len(jobs) < MAX_JOBS:
        next_round: list[list[Candidate]] = []
        for candidates in source_jobs:
            if not candidates:
                continue
            job = candidates.pop(0)
            if job.external_id not in seen:
                seen.add(job.external_id)
                jobs.append(job)
                if len(jobs) >= MAX_JOBS:
                    break
            if candidates:
                next_round.append(candidates)
        source_jobs = next_round
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


def fetch_daily_reports(config: AgentConfig, *, limit: int = 7) -> list[dict[str, str]]:
    """Read this bot's public daily reports from Hublog for weekly synthesis."""
    if not config.hublog_service_token:
        logger.error("HUBLOG_SERVICE_TOKENS has no programmer-jobs entry; weekly report skipped")
        return []
    reports: list[dict[str, str]] = []
    cursor: str | None = None
    seen_dates: set[str] = set()
    for _ in range(4):
        query = {"limit": "50"}
        if cursor:
            query["cursor"] = cursor
        endpoint = f"{config.hublog_base_url}/api/v1/me/posts?{urlencode(query)}"
        try:
            page = get_json(
                endpoint,
                headers={"Authorization": f"Bearer {config.hublog_service_token}"},
                timeout=30,
            )
        except HttpClientError as exc:
            logger.warning("Hublog daily reports could not be loaded: %s", exc)
            return []
        if not isinstance(page, dict):
            logger.warning("Hublog daily reports returned an invalid page")
            return []
        items = page.get("items", [])
        if not isinstance(items, list):
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            title = clean(item.get("title"), limit=300)
            match = DAILY_REPORT_TITLE.fullmatch(title)
            if not match or match.group(1) in seen_dates:
                continue
            seen_dates.add(match.group(1))
            reports.append({
                "title": title,
                "content": clean(item.get("content"), limit=WEEKLY_REPORT_CONTENT_LIMIT),
                "published_at": clean(item.get("created_at"), limit=100),
            })
            if len(reports) >= limit:
                break
        if len(reports) >= limit:
            break
        cursor = page.get("next_cursor") if isinstance(page.get("next_cursor"), str) else None
        if not cursor:
            break
    reports.sort(key=lambda item: item["title"], reverse=True)
    return reports[:limit]


def summarize_weekly(config: AgentConfig, reports: list[dict[str, str]]) -> dict[str, Any] | None:
    service_url = os.getenv("CONTENT_LLM_SERVICE_URL", "").rstrip("/")
    if not service_url:
        logger.error("CONTENT_LLM_SERVICE_URL is required for programmer-jobs weekly report")
        return None
    try:
        result = post_json(
            f"{service_url}/v1/jobs/programmer-weekly-summary",
            {"reports": reports},
            timeout=180,
        )
    except HttpClientError as exc:
        logger.warning(
            "Programming jobs weekly batch summary failed; publication skipped (attempts=%s, status=%s): %s",
            getattr(exc, "attempts", 1), getattr(exc, "status_code", "n/a"), exc,
        )
        return None
    if not isinstance(result, dict) or not clean(result.get("overview")):
        logger.warning("Programming jobs weekly batch summary returned an invalid result")
        return None
    return result


def report_candidate(jobs: list[Candidate], sources: list[tuple[str, str]], summary: dict[str, Any]) -> Candidate:
    dates: list[date] = []
    for job in jobs:
        if not job.published_at:
            continue
        try:
            dates.append(datetime.fromisoformat(job.published_at.replace("Z", "+00:00")).date())
        except ValueError:
            try:
                dates.append(parsedate_to_datetime(job.published_at).date())
            except (TypeError, ValueError):
                continue
    day = max(dates).isoformat() if dates else datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    return Candidate(external_id=f"programmer-jobs-daily:{day}", title=f"程序员招聘日报（{day}）",
                     summary=clean(summary.get("overview")), url=sources[0][1], source="技术招聘公开源",
                     metadata={"jobs": jobs, "sources": sources, "llm_summary": summary})


def weekly_report_candidate(reports: list[dict[str, str]], summary: dict[str, Any]) -> Candidate:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    year, week, _ = now.isocalendar()
    return Candidate(
        external_id=f"programmer-jobs-weekly:{year}-W{week:02d}",
        title=f"程序员招聘周报（{year}-W{week:02d}）",
        summary=clean(summary.get("overview")),
        url="https://hublog.panghuer.top/",
        source="Hublog 程序员招聘日报",
        metadata={"reports": reports, "llm_summary": summary},
    )


def collect(*, sample: bool = False) -> list[Candidate]:
    jobs, sources = sample_jobs() if sample else collect_jobs()
    if not jobs:
        logger.warning("No valid programming jobs collected; skip daily publication")
        return []
    summary = summarize(jobs)
    return [report_candidate(jobs, sources, summary)] if summary else []


def collect_weekly(config: AgentConfig) -> list[Candidate]:
    reports = fetch_daily_reports(config)
    if not reports:
        logger.warning("No Hublog daily programmer-job reports found; skip weekly publication")
        return []
    summary = summarize_weekly(config, reports)
    return [weekly_report_candidate(reports, summary)] if summary else []


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


def job_sample_lines(jobs: list[Candidate], limit: int = 12) -> str:
    lines: list[str] = []
    for job in jobs[:limit]:
        company = clean(job.metadata.get("company"), limit=100)
        city = clean(job.metadata.get("city"), limit=80)
        context = "；".join(part for part in [company, city] if part)
        suffix = f"（{context}）" if context else ""
        lines.append(f"- [{job.title}]({job.url}){suffix}")
    return "\n".join(lines)


def weekly_bullet_lines(value: object, *, limit: int = 6) -> str:
    lines: list[str] = []
    if isinstance(value, list):
        for item in value[:limit]:
            if isinstance(item, dict):
                detail = "：".join(filter(None, [clean(item.get("direction"), limit=160), clean(item.get("demand"), limit=280)]))
                skills = "、".join(values(item.get("skills"))[:10])
                if skills:
                    detail += f"（常见技能：{skills}）"
            else:
                detail = clean(item, limit=300)
            if detail:
                lines.append(f"- {detail}")
    return "\n".join(lines)


def render_daily(candidate: Candidate, config: AgentConfig) -> ContentItem:
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
    sample_lines = job_sample_lines(jobs) or "- 本次没有可展示的代表性岗位链接。"
    report_day = candidate.title.removeprefix("程序员招聘日报（").removesuffix("）")
    body = (
        f"样本日期：{report_day}；从 {len(sources)} 个公开技术招聘源整理 {len(jobs)} 条近期岗位信息。\n\n"
        f"市场概览：{clean(summary.get('overview'))}\n\n需求方向\n{directions}\n\n"
        f"高频技术技能：{skills}\n\n招聘信号\n{signals or '- 样本中的相关字段不足。'}\n\n"
        f"求职建议：{clean(summary.get('advice')) or '将岗位技能与自己的项目经历逐项对应，并以职位详情的最新要求为准。'}\n\n"
        f"代表性岗位（仅列部分，点击链接查看最新详情）\n{sample_lines}\n\n"
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


def render_weekly(candidate: Candidate, config: AgentConfig) -> ContentItem:
    summary = candidate.metadata["llm_summary"]
    reports: list[dict[str, str]] = candidate.metadata["reports"]
    directions = weekly_bullet_lines(summary.get("directions")) or "- 周报样本不足以稳定归纳方向。"
    skills = "、".join(values(summary.get("top_skills"))[:20]) or "以日报中出现的岗位要求为准"
    companies = weekly_bullet_lines(summary.get("company_signals")) or "- 样本不足以比较公司变化。"
    body = (
        f"本周基于 {len(reports)} 篇程序员招聘日报进行汇总，不重复抓取岗位明细。\n\n"
        f"市场概览：{clean(summary.get('overview'))}\n\n"
        f"一周需求方向\n{directions}\n\n"
        f"高频技术技能：{skills}\n\n"
        f"公司与岗位变化\n{companies}\n\n"
        f"求职建议：{clean(summary.get('advice')) or '结合一周趋势检查自己的项目经历和技能匹配度。'}\n\n"
        "数据来源：本机器人已发布的程序员招聘日报（Hublog）。岗位状态和招聘要求请以原始职位页为准。"
    )
    risk, status, notes = assess(body, default_risk="medium")
    return ContentItem.create(
        bot_name=config.bot_name, bot_version=config.bot_version, prompt_version=config.prompt_version,
        title=candidate.title, body=body, summary=candidate.summary, language="zh-CN",
        source_refs=[SourceRef(name="Hublog 程序员招聘日报", url="https://hublog.panghuer.top/", external_id=report["title"], excerpt="已发布的程序员招聘日报") for report in reports],
        tags=["招聘", "程序员", "求职", "技术趋势", "周报"], topics=["jobs", "technology-careers"],
        risk_level=risk, review_status=status, review_notes=notes,
    )


# Keep the original renderer name available for local integrations.
render = render_daily


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily and weekly programming-jobs market summaries")
    parser.add_argument("--sample", action="store_true", help="use offline sample jobs")
    parser.add_argument("--weekly", action="store_true", help="summarize the latest Hublog daily reports")
    args = parser.parse_args()
    config = AgentConfig.from_env("programmer-jobs")
    if args.weekly:
        run_agent(config, lambda: collect_weekly(config), render_weekly)
    else:
        run_agent(config, lambda: collect(sample=args.sample), render_daily)


if __name__ == "__main__":
    main()
