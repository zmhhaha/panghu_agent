"""
学术搜索工具 — 提供 arXiv、PubMed、Semantic Scholar、Crossref 的免费搜索和获取功能。
所有 API 均为免费，无需 API Key。

工具列表:
  ArxivSearchTool          - 搜索 arXiv 预印论文
  ArxivFetchTool           - 获取 arXiv 论文详情
  PubMedSearchTool         - 搜索 PubMed 生物医学论文
  PubMedFetchTool          - 获取 PubMed 论文详情
  SemanticScholarSearchTool - 搜索 Semantic Scholar（全学科 + 引用数据）
  SemanticScholarFetchTool  - 获取论文详情（引用数、参考文献）
  CrossrefLookupTool       - 通过 DOI 查找论文元数据
"""
import re
import time
import json
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type


# ============================================================
#  通用工具函数
# ============================================================

def _retry(func, attempts=3, delay=2.0, backoff=2.0):
    """带退避的重试器"""
    last_err = None
    for i in range(attempts):
        try:
            return func()
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                wait = delay * (backoff ** i)
                time.sleep(wait)
    raise last_err


def _safe_import_requests():
    """安全导入 requests"""
    try:
        import requests as r
        return r
    except ImportError:
        raise ImportError("未安装 requests 库，请运行: pip install requests")


# ============================================================
#  arXiv 搜索工具
# ============================================================

class ArxivSearchInput(BaseModel):
    """arXiv 搜索参数"""
    query: str = Field(..., description="搜索关键词（支持 arXiv API 查询语法，如: ti:transformer AND au:vaswani）")
    max_results: int = Field(10, description="最大返回结果数（3-20）", ge=3, le=20)
    sort_by: str = Field("relevance", description="排序方式: relevance（相关度）或 lastUpdatedDate（最近更新）")


class ArxivSearchTool(BaseTool):
    name: str = "arxiv_search"
    description: str = (
        "搜索 arXiv 论文数据库。覆盖物理学、计算机科学、数学、统计学等领域。"
        "返回论文标题、作者、摘要、arXiv ID、分类、发布日期和 PDF 链接。"
        "最适合查找预印本、最新技术进展和深度技术论文。"
        "查询示例: 'ti:transformer AND cat:cs.CL'（搜索标题含 transformer 且分类为计算语言学的论文）"
    )
    args_schema: Type[BaseModel] = ArxivSearchInput

    def _run(self, query: str, max_results: int = 10, sort_by: str = "relevance") -> str:
        import urllib.request
        import urllib.parse
        import xml.etree.ElementTree as ET

        def _do_search():
            q = query.replace(" ", "+")
            url = (
                f"http://export.arxiv.org/api/query"
                f"?search_query=all:{urllib.parse.quote(query)}"
                f"&start=0"
                f"&max_results={max_results}"
                f"&sortBy={sort_by}"
                f"&sortOrder={'descending' if sort_by == 'lastUpdatedDate' else 'descending'}"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "ScientificAgent/1.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.read().decode("utf-8")

        try:
            xml_data = _retry(_do_search, attempts=3, delay=1.5)
        except Exception as e:
            return f"arXiv 搜索失败: {type(e).__name__}: {str(e)[:300]}"

        try:
            root = ET.fromstring(xml_data)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "arxiv": "http://arxiv.org/schemas/atom",
            }

            entries = root.findall("atom:entry", ns)
            if not entries:
                return f"arXiv 未找到与 '{query}' 相关的结果。\n建议：尝试更换关键词，或用英文搜索。"

            total = root.findtext("atom:totalResults", namespaces=ns) or str(len(entries))
            output = f"## arXiv 搜索结果: {query}\n"
            output += f"共找到 {total} 篇论文，返回 {len(entries)} 篇：\n\n"

            for i, entry in enumerate(entries, 1):
                title = entry.findtext("atom:title", default="无标题", namespaces=ns).strip().replace("\n", " ")
                authors = [a.findtext("atom:name", default="", namespaces=ns).strip()
                          for a in entry.findall("atom:author", ns)]
                authors_str = ", ".join(authors[:5]) + (" 等" if len(authors) > 5 else "")
                summary = entry.findtext("atom:summary", default="无摘要", namespaces=ns).strip().replace("\n", " ")[:500]
                arxiv_id = entry.findtext("atom:id", default="", namespaces=ns).split("/abs/")[-1]
                published = entry.findtext("atom:published", default="未知日期", namespaces=ns)[:10]
                category = entry.findtext("arxiv:primary_category", default="", namespaces=ns)
                if isinstance(category, str) and hasattr(ET, 'tostring'):
                    pass  # category is already a string
                cat_attr = entry.find("arxiv:primary_category", ns)
                cat_name = cat_attr.get("term", "") if cat_attr is not None else ""
                pdf_link = ""
                for link in entry.findall("atom:link", ns):
                    if link.get("title") == "pdf":
                        pdf_link = link.get("href", "")
                        break

                output += f"### [{i}] {title}\n"
                output += f"- **作者**: {authors_str}\n"
                output += f"- **arXiv ID**: `{arxiv_id}`\n"
                output += f"- **发布日期**: {published}\n"
                output += f"- **分类**: {cat_name}\n"
                output += f"- **摘要**: {summary}...\n"
                if pdf_link:
                    output += f"- **PDF**: {pdf_link}\n"
                output += "\n"

            return output

        except Exception as e:
            return f"arXiv 解析结果失败: {type(e).__name__}: {str(e)[:300]}"


# ============================================================
#  arXiv 论文详情获取工具
# ============================================================

class ArxivFetchInput(BaseModel):
    """arXiv 论文详情获取参数"""
    arxiv_id: str = Field(..., description="arXiv 论文 ID，如 '2301.12345' 或 '2301.12345v2'")


class ArxivFetchTool(BaseTool):
    name: str = "arxiv_fetch"
    description: str = (
        "获取指定 arXiv 论文的完整摘要和元数据。输入 arXiv ID（如 '2301.12345'）。"
        "返回完整的标题、作者列表（含机构）、摘要、分类、DOI（如有）等信息。"
    )
    args_schema: Type[BaseModel] = ArxivFetchInput

    def _run(self, arxiv_id: str) -> str:
        import urllib.request
        import xml.etree.ElementTree as ET

        def _do_fetch():
            url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "ScientificAgent/1.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.read().decode("utf-8")

        try:
            xml_data = _retry(_do_fetch, attempts=3, delay=1.0)
        except Exception as e:
            return f"arXiv 获取论文失败 ({arxiv_id}): {type(e).__name__}: {str(e)[:300]}"

        try:
            root = ET.fromstring(xml_data)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "arxiv": "http://arxiv.org/schemas/atom",
            }

            entry = root.find("atom:entry", ns)
            if entry is None:
                return f"arXiv 未找到论文: {arxiv_id}"

            title = entry.findtext("atom:title", default="无标题", namespaces=ns).strip().replace("\n", " ")
            authors = []
            for a in entry.findall("atom:author", ns):
                name = a.findtext("atom:name", default="", namespaces=ns).strip()
                affil = a.findtext("arxiv:affiliation", default="", namespaces=ns).strip()
                authors.append(f"{name}" + (f" ({affil})" if affil else ""))
            abstract = entry.findtext("atom:summary", default="无摘要", namespaces=ns).strip().replace("\n", " ")
            published = entry.findtext("atom:published", default="未知日期", namespaces=ns)[:10]
            doi = entry.findtext("arxiv:doi", default="", namespaces=ns).strip()

            cat_attr = entry.find("arxiv:primary_category", ns)
            cat_name = cat_attr.get("term", "") if cat_attr is not None else ""

            output = f"## 论文详情: {title}\n\n"
            output += f"- **arXiv ID**: `{arxiv_id}`\n"
            output += f"- **作者**: {', '.join(authors)}\n"
            output += f"- **发布日期**: {published}\n"
            output += f"- **分类**: {cat_name}\n"
            if doi:
                output += f"- **DOI**: [{doi}](https://doi.org/{doi})\n"
            output += f"\n### 摘要\n\n{abstract}\n"

            return output

        except Exception as e:
            return f"arXiv 解析论文详情失败 ({arxiv_id}): {type(e).__name__}: {str(e)[:300]}"


# ============================================================
#  PubMed 搜索工具
# ============================================================

class PubMedSearchInput(BaseModel):
    """PubMed 搜索参数"""
    query: str = Field(..., description="搜索关键词（支持 PubMed 查询语法，如: '(cancer) AND (immunotherapy)'）")
    max_results: int = Field(10, description="最大返回结果数（3-20）", ge=3, le=20)
    date_range: str = Field("", description="日期范围（可选），如 '2020:2026' 表示 2020-2026 年")


class PubMedSearchTool(BaseTool):
    name: str = "pubmed_search"
    description: str = (
        "搜索 PubMed/NCBI 生物医学文献数据库（3500 万+引用）。"
        "覆盖临床医学、生物学、遗传学、公共卫生等领域。"
        "返回 PMID、标题、作者、期刊、出版日期、摘要摘要。"
        "支持 MeSH 词和布尔运算符。最适合生物医学和生命科学领域的文献检索。"
    )
    args_schema: Type[BaseModel] = PubMedSearchInput

    def _run(self, query: str, max_results: int = 10, date_range: str = "") -> str:
        import urllib.request
        import urllib.parse

        requests_mod = _safe_import_requests()

        # 限定最近 5 年（如果用户未指定）
        if not date_range:
            date_range = "2021:2026"

        def _do_esearch():
            full_query = query
            if date_range:
                full_query += f" AND ({date_range}[pdat])"

            url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            params = {
                "db": "pubmed",
                "term": full_query,
                "retmax": max_results,
                "retmode": "json",
                "sort": "relevance",
            }
            resp = requests_mod.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()

        # 休眠以遵守 PubMed 速率限制（无 API Key 时 3 次/秒）
        time.sleep(0.4)

        try:
            data = _retry(_do_esearch, attempts=3, delay=2.0)
        except Exception as e:
            return f"PubMed 搜索失败: {type(e).__name__}: {str(e)[:300]}"

        id_list = data.get("esearchresult", {}).get("idlist", [])
        total = data.get("esearchresult", {}).get("count", "0")

        if not id_list:
            return f"PubMed 未找到与 '{query}' 相关的结果。\n建议：尝试更宽泛的关键词或英文搜索。"

        # 获取论文摘要信息
        time.sleep(0.4)

        def _do_esummary():
            url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            params = {
                "db": "pubmed",
                "id": ",".join(id_list),
                "retmode": "json",
            }
            resp = requests_mod.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()

        try:
            summary_data = _retry(_do_esummary, attempts=3, delay=1.5)
        except Exception as e:
            return f"PubMed 获取摘要失败: {type(e).__name__}: {str(e)[:300]}"

        output = f"## PubMed 搜索结果: {query}\n"
        output += f"共找到 {total} 篇文献，返回 {len(id_list)} 篇：\n\n"

        for i, pmid in enumerate(id_list, 1):
            paper = summary_data.get("result", {}).get(pmid, {})
            title = paper.get("title", "无标题")
            authors_list = []
            for a in paper.get("authors", []):
                authors_list.append(a.get("name", ""))
            authors_str = ", ".join(authors_list[:5]) + (" 等" if len(authors_list) > 5 else "")
            journal = paper.get("source", "未知期刊")
            pub_date = paper.get("pubdate", "未知日期")
            doi = ""
            for aid in paper.get("articleids", []):
                if aid.get("idtype") == "doi":
                    doi = aid.get("value", "")
                    break

            output += f"### [{i}] {title}\n"
            output += f"- **PMID**: `{pmid}`\n"
            output += f"- **作者**: {authors_str}\n"
            output += f"- **期刊**: {journal}\n"
            output += f"- **日期**: {pub_date}\n"
            if doi:
                output += f"- **DOI**: [{doi}](https://doi.org/{doi})\n"
            output += "\n"

        return output


# ============================================================
#  PubMed 论文详情获取工具
# ============================================================

class PubMedFetchInput(BaseModel):
    """PubMed 论文详情获取参数"""
    pmid: str = Field(..., description="PubMed 论文 ID（PMID），如 '12345678'")


class PubMedFetchTool(BaseTool):
    name: str = "pubmed_fetch"
    description: str = (
        "获取指定 PubMed 论文的完整摘要和元数据。输入 PMID（如 '12345678'）。"
        "返回完整摘要、所有作者、MeSH 词、DOI 和出版物类型。"
    )
    args_schema: Type[BaseModel] = PubMedFetchInput

    def _run(self, pmid: str) -> str:
        import urllib.request

        requests_mod = _safe_import_requests()

        time.sleep(0.4)  # 遵守限速

        def _do_fetch():
            url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            params = {
                "db": "pubmed",
                "id": pmid,
                "retmode": "xml",
            }
            resp = requests_mod.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.text

        try:
            xml_data = _retry(_do_fetch, attempts=3, delay=1.5)
        except Exception as e:
            return f"PubMed 获取论文失败 ({pmid}): {type(e).__name__}: {str(e)[:300]}"

        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_data)

            article = root.find(".//PubmedArticle")
            if article is None:
                return f"PubMed 未找到论文: {pmid}"

            title = ""
            title_elem = article.find(".//ArticleTitle")
            if title_elem is not None and title_elem.text:
                title = title_elem.text.strip()

            abstract = ""
            abstract_elem = article.find(".//Abstract/AbstractText")
            if abstract_elem is not None and abstract_elem.text:
                abstract = abstract_elem.text.strip()
            else:
                # 结构化摘要（多个 AbstractText 标签）
                abs_parts = []
                for abs_elem in article.findall(".//Abstract/AbstractText"):
                    label = abs_elem.get("Label", "")
                    text = abs_elem.text or ""
                    abs_parts.append(f"{label}: {text}" if label else text)
                abstract = "\n".join(abs_parts) if abs_parts else "无摘要"

            authors = []
            for a in article.findall(".//Author"):
                last = a.findtext("LastName", default="")
                fore = a.findtext("ForeName", default="")
                if last:
                    authors.append(f"{last} {fore}" if fore else last)

            journal = ""
            journal_elem = article.find(".//Journal/Title")
            if journal_elem is not None and journal_elem.text:
                journal = journal_elem.text.strip()

            pub_date = "未知"
            date_elem = article.find(".//PubDate")
            if date_elem is not None:
                y = date_elem.findtext("Year", default="")
                m = date_elem.findtext("Month", default="")
                pub_date = f"{y}-{m}" if y and m else (y or "未知")

            doi = ""
            for eid in article.findall(".//ELocationID"):
                if eid.get("EIdType") == "doi" and eid.text:
                    doi = eid.text.strip()
                    break

            mesh_list = []
            for mh in article.findall(".//MeshHeading/DescriptorName"):
                if mh.text:
                    mesh_list.append(mh.text.strip())

            output = f"## 论文详情: {title}\n\n"
            output += f"- **PMID**: `{pmid}`\n"
            output += f"- **作者**: {', '.join(authors)}\n"
            output += f"- **期刊**: {journal}\n"
            output += f"- **发表日期**: {pub_date}\n"
            if doi:
                output += f"- **DOI**: [{doi}](https://doi.org/{doi})\n"
            if mesh_list:
                output += f"- **MeSH 主题词**: {', '.join(mesh_list[:10])}\n"
            output += f"\n### 摘要\n\n{abstract[:3000]}\n"

            return output

        except Exception as e:
            return f"PubMed 解析论文详情失败 ({pmid}): {type(e).__name__}: {str(e)[:300]}"


# ============================================================
#  Semantic Scholar 搜索工具
# ============================================================

class SemanticScholarSearchInput(BaseModel):
    """Semantic Scholar 搜索参数"""
    query: str = Field(..., description="搜索关键词（支持中英文）")
    limit: int = Field(10, description="最大返回结果数（3-20）", ge=3, le=20)
    year: str = Field("", description="年份范围（可选），如 '2021-2026'")


class SemanticScholarSearchTool(BaseTool):
    name: str = "semantic_scholar_search"
    description: str = (
        "搜索 Semantic Scholar 学术论文数据库（2 亿+论文，跨所有学科）。"
        "返回论文标题、作者、摘要、年份、引用计数、发表刊物和研究领域。"
        "优势：跨学科广度大、引用关系明确、免费无限制（无 API Key 时 100 次/5 分钟）。"
    )
    args_schema: Type[BaseModel] = SemanticScholarSearchInput

    def _run(self, query: str, limit: int = 10, year: str = "") -> str:
        requests_mod = _safe_import_requests()

        def _do_search():
            params = {
                "query": query,
                "limit": limit,
                "fields": "title,authors,abstract,year,citationCount,venue,externalIds,fieldsOfStudy,publicationTypes",
            }
            if year:
                # Semantic Scholar API 使用 year=2021-2026 格式
                params["year"] = year
            url = "https://api.semanticscholar.org/graph/v1/paper/search"
            resp = requests_mod.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()

        try:
            data = _retry(_do_search, attempts=3, delay=1.5)
        except Exception as e:
            return f"Semantic Scholar 搜索失败: {type(e).__name__}: {str(e)[:300]}"

        papers = data.get("data", [])
        total = data.get("total", 0)

        if not papers:
            return f"Semantic Scholar 未找到与 '{query}' 相关的结果。\n建议：尝试更换关键词，或用英文搜索。"

        output = f"## Semantic Scholar 搜索结果: {query}\n"
        output += f"共找到约 {total} 篇论文，返回 {len(papers)} 篇：\n\n"

        for i, paper in enumerate(papers, 1):
            title = paper.get("title", "无标题")
            authors_list = []
            for a in paper.get("authors", []):
                authors_list.append(a.get("name", ""))
            authors_str = ", ".join(authors_list[:5]) + (" 等" if len(authors_list) > 5 else "")
            abstract = (paper.get("abstract") or "无摘要")[:500]
            year_val = paper.get("year", "未知")
            citations = paper.get("citationCount", 0)
            venue = paper.get("venue", "") or ""
            venue_str = f"\n- **发表刊物**: {venue}" if venue else ""
            fields = paper.get("fieldsOfStudy", []) or []
            fields_str = ", ".join(fields[:5]) if fields else "未分类"
            paper_id = paper.get("paperId", "")
            doi = (paper.get("externalIds") or {}).get("DOI", "")
            arxiv = (paper.get("externalIds") or {}).get("ArXiv", "")
            is_open = paper.get("isOpenAccess", False)

            output += f"### [{i}] {title}\n"
            output += f"- **作者**: {authors_str}\n"
            output += f"- **年份**: {year_val}  |  引用: {citations} 次  |  {'🔓 开放获取' if is_open else '🔒'}\n"
            output += f"- **领域**: {fields_str}{venue_str}\n"
            if doi:
                output += f"- **DOI**: [{doi}](https://doi.org/{doi})\n"
            if arxiv:
                output += f"- **arXiv**: `{arxiv}`\n"
            output += f"- **Semantic Scholar ID**: `{paper_id}`\n"
            if abstract:
                output += f"- **摘要**: {abstract}...\n"
            output += "\n"

        return output


# ============================================================
#  Semantic Scholar 论文详情获取工具
# ============================================================

class SemanticScholarFetchInput(BaseModel):
    """Semantic Scholar 论文详情获取参数"""
    paper_id: str = Field(..., description="Semantic Scholar 论文 ID 或 DOI（如 'DOI:10.1038/nature12373'）")


class SemanticScholarFetchTool(BaseTool):
    name: str = "semantic_scholar_fetch"
    description: str = (
        "获取指定论文在 Semantic Scholar 上的详细信息，包括引用数、参考文献列表和引用列表。"
        "输入 Semantic Scholar paper ID（如 '649def34...'）或以 'DOI:' 开头的 DOI。"
        "最适合：评估论文影响力、查找相关工作、了解引用关系网络。"
    )
    args_schema: Type[BaseModel] = SemanticScholarFetchInput

    def _run(self, paper_id: str) -> str:
        requests_mod = _safe_import_requests()

        def _do_fetch():
            # 如果是 DOI，使用特殊端点
            if paper_id.startswith("DOI:"):
                url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
            else:
                url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"

            params = {
                "fields": "title,authors,abstract,year,citationCount,referenceCount,venue,"
                         "externalIds,fieldsOfStudy,publicationTypes,journal,tldr,"
                         "references.title,references.authors,references.year,references.venue,references.externalIds,"
                         "citations.title,citations.authors,citations.year,citations.venue",
            }
            resp = requests_mod.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()

        try:
            data = _retry(_do_fetch, attempts=3, delay=1.5)
        except Exception as e:
            return f"Semantic Scholar 获取论文失败 ({paper_id}): {type(e).__name__}: {str(e)[:300]}"

        try:
            title = data.get("title", "无标题")
            abstract = (data.get("abstract") or "无摘要")[:2000]
            year = data.get("year", "未知")
            citations = data.get("citationCount", 0)
            refs_count = data.get("referenceCount", 0)
            venue = (data.get("venue") or {}).get("name", "") if isinstance(data.get("venue"), dict) else (data.get("venue") or "")
            journal = (data.get("journal") or {}).get("name", "") if isinstance(data.get("journal"), dict) else ""
            doi = (data.get("externalIds") or {}).get("DOI", "")

            authors_list = [a.get("name", "") for a in data.get("authors", [])]

            output = f"## Sematic Scholar 论文详情\n\n"
            output += f"**{title}**\n\n"
            output += f"- **作者**: {', '.join(authors_list)}\n"
            output += f"- **年份**: {year}\n"
            output += f"- **被引**: {citations} 次  |  参考文献: {refs_count} 篇\n"
            if venue:
                output += f"- **刊物**: {venue}\n"
            elif journal:
                output += f"- **期刊**: {journal}\n"
            if doi:
                output += f"- **DOI**: [{doi}](https://doi.org/{doi})\n"
            output += f"\n### 摘要\n\n{abstract}\n"

            # 最重要的参考文献（前 5 篇）
            refs = data.get("references", [])[:5]
            if refs:
                output += f"\n### 主要参考文献（前 {len(refs)} 篇）\n\n"
                for j, ref in enumerate(refs, 1):
                    r_title = ref.get("title", "无标题")
                    r_authors = ", ".join([a.get("name", "") for a in ref.get("authors", [])][:3])
                    r_year = ref.get("year", "")
                    r_doi = (ref.get("externalIds") or {}).get("DOI", "")
                    output += f"[{j}] {r_authors} ({r_year}). *{r_title}*"
                    if r_doi:
                        output += f". DOI: {r_doi}"
                    output += "\n"

            return output

        except Exception as e:
            return f"Semantic Scholar 解析论文详情失败: {type(e).__name__}: {str(e)[:300]}"


# ============================================================
#  Crossref DOI 查询工具
# ============================================================

class CrossrefLookupInput(BaseModel):
    """Crossref DOI 查询参数"""
    doi: str = Field(..., description="论文 DOI，如 '10.1038/nature12373'")


class CrossrefLookupTool(BaseTool):
    name: str = "crossref_lookup"
    description: str = (
        "通过 DOI 在 Crossref 上查询论文的已验证元数据。"
        "返回经过出版社正式注册的标题、作者、期刊、卷、期、页码、出版日期、出版社等信息。"
        "最适合：验证引用信息、获取正式的著录格式数据。"
    )
    args_schema: Type[BaseModel] = CrossrefLookupInput

    def _run(self, doi: str) -> str:
        requests_mod = _safe_import_requests()

        def _do_lookup():
            url = f"https://api.crossref.org/works/{doi}"
            resp = requests_mod.get(url, timeout=30,
                                    headers={"User-Agent": "ScientificAgent/1.0 (mailto:pangu@example.com)"})
            resp.raise_for_status()
            return resp.json()

        try:
            data = _retry(_do_lookup, attempts=3, delay=1.0)
        except Exception as e:
            return f"Crossref 查询失败 ({doi}): {type(e).__name__}: {str(e)[:300]}"

        try:
            msg = data.get("message", {})
            title_list = msg.get("title", ["无标题"])
            title = title_list[0] if title_list else "无标题"
            authors_list = []
            for a in msg.get("author", []):
                given = a.get("given", "")
                family = a.get("family", "")
                authors_list.append(f"{given} {family}".strip() or "未知")
            journal = (msg.get("container-title") or ["未知期刊"])[0]
            publisher = msg.get("publisher", "未知出版社")
            pub_date_parts = msg.get("published-print", {}) or msg.get("published-online", {}) or msg.get("created", {})
            year = pub_date_parts.get("date-parts", [[0]])[0][0] if pub_date_parts else "未知"
            volume = msg.get("volume", "")
            issue = msg.get("issue", "")
            pages = msg.get("page", "")
            doi_full = msg.get("DOI", doi)
            type_label = msg.get("type", "未知类型")
            abstract = (msg.get("abstract") or "")[:1000]

            output = f"## Crossref 文献信息\n\n"
            output += f"**{title}**\n\n"
            output += f"- **作者**: {', '.join(authors_list)}\n"
            output += f"- **期刊**: {journal}\n"
            output += f"- **出版社**: {publisher}\n"
            output += f"- **年份**: {year}\n"
            if volume:
                output += f"- **卷(期)**: {volume}({issue})" if issue else f"- **卷**: {volume}"
                output += "\n"
            if pages:
                output += f"- **页码**: {pages}\n"
            output += f"- **类型**: {type_label}\n"
            output += f"- **DOI**: [{doi_full}](https://doi.org/{doi_full})\n"
            if abstract:
                output += f"\n### 摘要\n\n{abstract}...\n"

            # 著录格式参考
            first_author = authors_list[0] if authors_list else "Unknown"
            output += "\n---\n"
            output += f"**标准引用格式**: {first_author} 等. \"{title}\". *{journal}*, {year}"
            if volume:
                output += f", {volume}" + (f"({issue})" if issue else "")
            if pages:
                output += f": {pages}"
            output += f". DOI: {doi_full}\n"

            return output

        except Exception as e:
            return f"Crossref 解析失败 ({doi}): {type(e).__name__}: {str(e)[:300]}"


# ============================================================
#  批量获取工具 — 自动路由到正确的 API
# ============================================================

class AcademicMultiFetchInput(BaseModel):
    """批量获取多个学术论文"""
    identifiers: str = Field(..., description="要获取的论文 ID 列表，用换行或逗号分隔。"
                                            "支持: arXiv ID (如 '2301.12345')、PMID (如 '12345678')、"
                                            "DOI (如 '10.1038/nature12373')、Semantic Scholar ID")


class AcademicMultiFetchTool(BaseTool):
    name: str = "academic_multi_fetch"
    description: str = (
        "批量获取多篇学术论文的摘要/详情，自动根据 ID 类型路由到正确的 API。"
        "支持 arXiv ID、PMID、DOI 和 Semantic Scholar ID。"
        "ID 用换行或逗号分隔，最多 10 个。"
    )
    args_schema: Type[BaseModel] = AcademicMultiFetchInput

    def _run(self, identifiers: str) -> str:
        id_list = re.split(r"[\n,;]+", identifiers)
        id_list = [i.strip() for i in id_list if i.strip()]

        if not id_list:
            return "错误：未提供有效的论文 ID"

        if len(id_list) > 10:
            id_list = id_list[:10]

        results = []

        for i, pid in enumerate(id_list, 1):
            results.append(f"--- 论文 {i}/{len(id_list)} ---")

            # 自动识别 ID 类型
            if re.match(r"^\d{4}\.\d{4,}(v\d+)?$", pid):
                # arXiv ID
                fetcher = ArxivFetchTool()
                results.append(fetcher._run(pid))
            elif re.match(r"^\d{7,8}$", pid):
                # PMID
                fetcher = PubMedFetchTool()
                results.append(fetcher._run(pid))
            elif "/" in pid:
                # DOI
                fetcher = CrossrefLookupTool()
                results.append(fetcher._run(pid))
            else:
                # Semantic Scholar ID
                fetcher = SemanticScholarFetchTool()
                results.append(fetcher._run(pid))

            results.append("")

        return "\n".join(results)
