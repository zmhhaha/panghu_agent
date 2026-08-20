"""Academic provider adapters returning the shared PaperRecord schema."""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from .http_client import request_json, request_text
from .models import PaperRecord, make_paper, normalize_doi


def _strip_markup(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def search_openalex(query: str, limit: int, *, email: str = "") -> list[PaperRecord]:
    params: dict[str, Any] = {
        "search": query,
        "per-page": min(max(1, limit), 50),
        "sort": "relevance_score:desc",
    }
    if email:
        params["mailto"] = email
    data = request_json("https://api.openalex.org/works", params=params)
    records: list[PaperRecord] = []
    for item in data.get("results", []):
        title = _strip_markup(item.get("display_name"))
        if not title:
            continue
        location = item.get("primary_location") or {}
        best_location = item.get("best_oa_location") or {}
        locations = item.get("locations") or []
        location_candidates = [best_location, location, *locations]
        landing_url = ""
        pdf_url = ""
        pdf_urls: list[str] = []
        for candidate in location_candidates:
            if not isinstance(candidate, dict):
                continue
            landing_url = landing_url or str(candidate.get("landing_page_url") or "")
            candidate_pdf = str(candidate.get("pdf_url") or "").strip()
            if candidate_pdf and candidate_pdf not in pdf_urls:
                pdf_urls.append(candidate_pdf)
            pdf_url = pdf_url or candidate_pdf
        source = location.get("source") or {}
        inverted = item.get("abstract_inverted_index") or {}
        positions = sorted(
            (position, word)
            for word, indexes in inverted.items()
            if isinstance(indexes, list)
            for position in indexes
            if isinstance(position, int)
        )
        abstract = " ".join(str(word) for position, word in positions)
        doi = normalize_doi(item.get("doi"))
        records.append(make_paper(
            "OpenAlex",
            title=title,
            date=item.get("publication_date"),
            url=landing_url or item.get("id") or (f"https://doi.org/{doi}" if doi else ""),
            doi=doi,
            authors=", ".join(
                str(authorship.get("author", {}).get("display_name") or "")
                for authorship in item.get("authorships", [])[:8]
                if authorship.get("author", {}).get("display_name")
            ),
            venue=source.get("display_name"),
            cited_by_count=item.get("cited_by_count"),
            abstract=abstract,
            pdf_url=pdf_url,
            open_access=bool((item.get("open_access") or {}).get("is_oa")),
            identifiers={
                "openalex": str(item.get("id") or ""),
                "openalex_pdf_urls": pdf_urls,
            },
        ))
    return records


def search_crossref(query: str, limit: int, *, email: str = "") -> list[PaperRecord]:
    params: dict[str, Any] = {"query.bibliographic": query, "rows": min(max(1, limit), 50)}
    if email:
        params["mailto"] = email
    data = request_json(
        "https://api.crossref.org/works",
        params=params,
        headers={"User-Agent": f"PanghuAcademic/1.0 (mailto:{email or 'none'})"},
    )
    records: list[PaperRecord] = []
    for item in data.get("message", {}).get("items", []):
        title_values = item.get("title") or []
        title = _strip_markup(title_values[0] if title_values else "")
        if not title:
            continue
        date_parts = (
            (item.get("published-print") or {}).get("date-parts")
            or (item.get("published-online") or {}).get("date-parts")
            or (item.get("created") or {}).get("date-parts")
            or []
        )
        date = ""
        if date_parts and date_parts[0]:
            date = "-".join(str(part).zfill(2) for part in date_parts[0][:3])
        doi = normalize_doi(item.get("DOI"))
        pdf_url = ""
        pdf_urls: list[str] = []
        for link in item.get("link") or []:
            if not isinstance(link, dict):
                continue
            content_type = str(link.get("content-type") or link.get("content_type") or "").lower()
            candidate_url = str(link.get("URL") or link.get("url") or "")
            if "pdf" in content_type or candidate_url.lower().split("?", 1)[0].endswith(".pdf"):
                if candidate_url and candidate_url not in pdf_urls:
                    pdf_urls.append(candidate_url)
                pdf_url = pdf_url or candidate_url
        records.append(make_paper(
            "Crossref",
            type=item.get("type") or "paper",
            title=title,
            date=date,
            url=item.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
            doi=doi,
            authors=", ".join(
                " ".join(filter(None, (author.get("given"), author.get("family"))))
                for author in item.get("author", [])[:8]
            ),
            venue=((item.get("container-title") or [""])[0]),
            cited_by_count=item.get("is-referenced-by-count"),
            abstract=_strip_markup(item.get("abstract")),
            pdf_url=pdf_url,
            open_access=bool(pdf_url),
            identifiers={"crossref_pdf_urls": pdf_urls},
        ))
    return records


def search_semantic_scholar(
    query: str,
    limit: int,
    *,
    api_key: str = "",
) -> list[PaperRecord]:
    params = {
        "query": query,
        "limit": min(max(1, limit), 50),
        "fields": (
            "paperId,title,year,publicationDate,abstract,url,externalIds,authors,"
            "venue,citationCount,isOpenAccess,openAccessPdf"
        ),
    }
    headers = {"x-api-key": api_key} if api_key else {}
    data = request_json(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params=params,
        headers=headers,
    )
    records: list[PaperRecord] = []
    for item in data.get("data", []):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        identifiers = item.get("externalIds") or {}
        doi = normalize_doi(identifiers.get("DOI"))
        pdf_url = str((item.get("openAccessPdf") or {}).get("url") or "")
        records.append(make_paper(
            "Semantic Scholar",
            title=title,
            date=item.get("publicationDate") or item.get("year"),
            url=item.get("url") or pdf_url or (f"https://doi.org/{doi}" if doi else ""),
            doi=doi,
            authors=", ".join(str(author.get("name") or "") for author in item.get("authors", [])[:8]),
            venue=item.get("venue"),
            cited_by_count=item.get("citationCount"),
            abstract=item.get("abstract"),
            pdf_url=pdf_url,
            open_access=item.get("isOpenAccess"),
            identifiers={
                "semantic_scholar": str(item.get("paperId") or ""),
                **{str(k).lower(): str(v) for k, v in identifiers.items() if v},
            },
        ))
    return records


def _arxiv_expression(query: str) -> str:
    if re.search(r"\b(?:all|ti|au|abs|co|jr|cat|rn|id):", query, flags=re.I):
        return query
    tokens = re.findall(r"[A-Za-z0-9+.-]+|[\u4e00-\u9fff]+", query)
    return " AND ".join(f'all:"{token}"' for token in tokens[:8]) or f'all:"{query}"'


def search_arxiv(
    query: str,
    limit: int,
    *,
    sort_by: str = "relevance",
) -> list[PaperRecord]:
    raw = request_text(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": _arxiv_expression(query),
            "start": 0,
            "max_results": min(max(1, limit), 25),
            "sortBy": sort_by if sort_by in {"relevance", "lastUpdatedDate", "submittedDate"} else "relevance",
            "sortOrder": "descending",
        },
    )
    root = ET.fromstring(raw)
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    records: list[PaperRecord] = []
    for entry in root.findall("atom:entry", ns):
        title = _strip_markup(entry.findtext("atom:title", default="", namespaces=ns))
        if not title:
            continue
        entry_url = entry.findtext("atom:id", default="", namespaces=ns) or ""
        arxiv_id = entry_url.rsplit("/", 1)[-1]
        doi = normalize_doi(entry.findtext("arxiv:doi", default="", namespaces=ns))
        pdf_url = ""
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = str(link.get("href") or "")
                break
        category = entry.find("arxiv:primary_category", ns)
        records.append(make_paper(
            "arXiv",
            title=title,
            date=entry.findtext("atom:published", default="", namespaces=ns),
            url=entry_url,
            doi=doi,
            authors=", ".join(
                str(author.findtext("atom:name", default="", namespaces=ns) or "").strip()
                for author in entry.findall("atom:author", ns)[:8]
            ),
            venue=f"arXiv:{category.get('term', '')}" if category is not None else "arXiv",
            abstract=_strip_markup(entry.findtext("atom:summary", default="", namespaces=ns)),
            pdf_url=pdf_url,
            open_access=True,
            identifiers={"arxiv": arxiv_id},
        ))
    return records


def _element_text(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def search_pubmed(
    query: str,
    limit: int,
    *,
    date_range: str = "",
    api_key: str = "",
    email: str = "",
) -> list[PaperRecord]:
    term = query
    if date_range:
        term = f"({query}) AND ({date_range}[pdat])"
    common: dict[str, Any] = {"db": "pubmed", "api_key": api_key or None, "email": email or None}
    common = {key: value for key, value in common.items() if value}
    search_data = request_json(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={**common, "term": term, "retmax": min(max(1, limit), 50), "retmode": "json", "sort": "relevance"},
    )
    identifiers = search_data.get("esearchresult", {}).get("idlist", [])
    if not identifiers:
        return []
    if not api_key:
        time.sleep(0.35)
    raw = request_text(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params={**common, "id": ",".join(identifiers), "retmode": "xml"},
    )
    root = ET.fromstring(raw)
    records: list[PaperRecord] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID", default="")
        title = _element_text(article.find(".//ArticleTitle"))
        if not title:
            continue
        abstract_parts = []
        for section in article.findall(".//Abstract/AbstractText"):
            text = _element_text(section)
            label = section.get("Label", "")
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)
        authors = []
        for author in article.findall(".//Author")[:8]:
            collective = author.findtext("CollectiveName", default="")
            name = " ".join(filter(None, (
                author.findtext("ForeName", default=""),
                author.findtext("LastName", default=""),
            )))
            if collective or name:
                authors.append(collective or name)
        date_element = article.find(".//JournalIssue/PubDate")
        date = ""
        if date_element is not None:
            date = "-".join(filter(None, (
                date_element.findtext("Year", default=""),
                date_element.findtext("Month", default=""),
                date_element.findtext("Day", default=""),
            ))) or date_element.findtext("MedlineDate", default="")
        doi = ""
        extra_ids: dict[str, str] = {"pmid": str(pmid)}
        for article_id in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
            id_type = str(article_id.get("IdType") or "").lower()
            value = str(article_id.text or "")
            if id_type and value:
                extra_ids[id_type] = value
            if id_type == "doi":
                doi = value
        records.append(make_paper(
            "PubMed",
            title=title,
            date=date,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            doi=doi,
            authors=", ".join(authors),
            venue=_element_text(article.find(".//Journal/Title")),
            abstract="\n".join(abstract_parts),
            identifiers=extra_ids,
        ))
    return records


PROVIDER_SEARCHERS = {
    "openalex": search_openalex,
    "crossref": search_crossref,
    "semantic_scholar": search_semantic_scholar,
    "arxiv": search_arxiv,
    "pubmed": search_pubmed,
}
