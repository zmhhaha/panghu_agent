"""Explainable, conservative source screening outside protected observations.

These are suggestions for inclusion, never a truth or identifier authenticity
verdict. Measured provider records and the user's explicit selections stay intact.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_STOPWORDS = frozenset('a an and or the of in on to for with by from as is are be '
    'review survey research study approach approaches method methods recent current latest '
    'overview architecture architectures framework frameworks documentation official '
    'what which when where why how does do did can could should would has have had '
    'will shall it its this that these those there their our your best '
    '综述 研究 架构 方案 方法 论文 分析 概述 官方 文档'.split())
_HAN_GENERIC = frozenset(('综述 研究 架构 方案 方法 论文 分析 概述 官方 文档 相关 影响 进行 '
    '目前 问题 机制 作用 如何 什么 一种 采用 基于 是否 使用 以及 不同 之间 具有 '
    '介绍 最新 进展 讨论 对于 其中 方面 及其').split())
_PLACEHOLDERS = (
    r'^(?:new\s+)?test\s+pub(?:lication)?\s*\d+\s*$',
    r'^(?:test|dummy|placeholder)\s+(?:record|article|publication|paper)(?:\s*\d+)?\s*$',
    r'\b(?:BY|CY)\s+(?:print|online)\s+pub\s+date\b',
    r'^untitled\s*$',
)


def _years(value):
    return [int(year) for year in re.findall(r'(?<!\d)(?:19\d{2}|20\d{2})(?!\d)', str(value or ''))]


def _year_range(value):
    """Interpret only explicit intervals; free text is not a single-year filter."""
    value = str(value or '').strip()
    years = _years(value)
    if len(years) == 2:
        bounded = re.search(r'(?<!\d)((?:19|20)\d{2})\s*年?\s*'
            r'(?:[—–−-]+|至|到|\bto\b|\bthrough\b|\buntil\b)\s*'
            r'((?:19|20)\d{2})(?!\d)', value, re.I)
        between = re.search(r'\bbetween\s+((?:19|20)\d{2})\s+and\s+((?:19|20)\d{2})\b', value, re.I)
        match = bounded or between
        return [min(years), max(years)] if match else None
    if len(years) != 1:
        return None
    year = years[0]
    if re.fullmatch(r'(?:year\s*)?' + str(year) + r'\s*(?:年度?|年间)?', value, re.I):
        return [year, year]
    lower = re.search(r'\b(?:since|from|after|onwards?)\b|自|从|起|以来|以后|之后|至今', value, re.I)
    upper = re.search(r'\b(?:before|until|through|prior\s+to|up\s+to)\b|截至|截止|以前|之前|不晚于', value, re.I)
    if bool(lower) == bool(upper):
        return None
    if lower:
        minimum = year + 1 if re.search(r'\bafter\b|之后|以后', value, re.I) else year
        maximum = datetime.now(timezone.utc).year if re.search(r'至今|以来|\bpresent\b|\bnow\b', value, re.I) else None
        return [minimum, maximum] if maximum is None or minimum <= maximum else None
    maximum = year - 1 if re.search(r'\b(?:before|prior\s+to)\b|之前|以前', value, re.I) else year
    return [None, maximum]


def _publication_year(paper):
    bibliography = paper.get('bibliography') or {}
    dates = bibliography.get('dates') or {}
    kind = bibliography.get('source_type')
    fields = ('journal_published', 'online_published', 'print_published') if kind == 'journal' else (
        ('first_posted',) if kind == 'preprint' else ())
    for field in fields:
        years = _years(dates.get(field))
        if years:
            return years[0], field
    year = paper.get('year')
    if type(year) is int:
        return year, 'record_year'
    years = _years(paper.get('date'))
    return (years[0], 'record_date') if years else (None, 'unknown')


def _terms(value):
    # Field prefixes and Boolean operators carry no subject evidence. Chinese
    # text needs short character sequences because it has no word separators.
    terms = []
    for term in re.findall(r'[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}', value):
        term = term.lower()
        if term in _STOPWORDS | {'all', 'ti', 'abs', 'cat', 'au', 'not', 'and', 'or'}:
            continue
        if re.fullmatch(r'[\u4e00-\u9fff]{2,}', term):
            pattern = '|'.join(sorted(_HAN_GENERIC, key=len, reverse=True)) + '|[的了和与及对在是]'
            for span in re.split(pattern, term):
                if len(span) < 2:
                    continue
                terms.append(span)
                terms.extend(span[index:index + 2] for index in range(len(span) - 1))
        else:
            terms.append(term)
    return terms


def assess_source(paper, requirements=None, queries=()):
    """Return a non-destructive default and reasons, suitable for selection UI."""
    requirements = requirements or {}
    reasons, warnings = [], []
    title = str(paper.get('title') or '').strip()
    abstract = str(paper.get('abstract') or '')
    year_range = _year_range(requirements.get('time_range'))
    year, year_basis = _publication_year(paper)
    years = [year] if year is not None else []
    if any(re.search(pattern, title, re.I) for pattern in _PLACEHOLDERS):
        reasons.append(dict(code='placeholder_title', message='题名呈现明显测试或占位记录特征，默认不纳入；可人工核对后修改。'))
    if year_range and year is not None and ((year_range[0] is not None and year < year_range[0])
            or (year_range[1] is not None and year > year_range[1])):
        description = '—'.join(str(bound) if bound is not None else '不限' for bound in year_range)
        reasons.append(dict(code='outside_year_range', message=f'来源年份 {year} 不在研究范围 {description} 内。'))
    elif year_range and year is None:
        reasons.append(dict(code='year_unconfirmed', message='研究要求限制年份，但来源年份未知，需核对后纳入。'))
    elif requirements.get('time_range') and year_range is None:
        warnings.append(dict(code='year_range_unconfirmed', message='年份范围为自然语言或边界不明确，未将其误作单年限制，请人工核对。'))
    query_text = ' '.join(str(query) for query in queries)
    terms = list(dict.fromkeys(_terms(str(requirements.get('question') or '') + ' ' + query_text)))
    haystack = (title + ' ' + abstract).lower()
    matched = [term for term in terms if (re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', haystack)
               if re.fullmatch(r'[a-z0-9_-]+', term) else term in haystack)]
    latin_terms = [term for term in terms if re.search('[a-z]', term)]
    if terms and not matched:
        latin_source = re.findall(r'[a-z]{2,}', haystack)
        han_source = re.findall(r'[\u4e00-\u9fff]', haystack)
        han_terms = [term for term in terms if re.fullmatch(r'[\u4e00-\u9fff]{2,}', term)]
        same_latin = (len(set(latin_terms)) >= 2 and len(latin_source) >= 3
            and sum(map(len, latin_source)) >= len(han_source) * 2)
        same_han = bool(han_terms and len(han_source) >= 4)
        if same_latin or same_han:
            reasons.append(dict(code='no_topic_match', message='题名与可用摘要未匹配研究主题或检索词，默认不纳入；这不是论文真伪判断。'))
        else:
            warnings.append(dict(code='cross_language_relevance_unconfirmed', message='主题与来源可能使用不同语言，词面相关性无法确认，请人工核对。'))
    if not abstract:
        warnings.append(dict(code='metadata_only', message='当前只有来源元数据，不能据此声称已读取摘要或全文。'))
    if not terms:
        warnings.append(dict(code='relevance_unconfirmed', message='没有可用于相关性核对的主题词，请人工核对。'))
    return dict(default_included=not reasons, reasons=reasons, warnings=warnings,
        matched_terms=matched, year_range=year_range, assessed_years=years, year_basis=year_basis,
        method='lexical_screening_v1')


def retrieval_summary(project):
    """Expose actual search rows and known coverage, without issuing new calls."""
    batches = project.get('search_batches') or []
    searches = [search for batch in batches for search in batch.get('searches', [])]
    papers = {paper['paper_id']: paper for batch in batches for paper in batch.get('papers', [])}
    plan = project.get('plan') or {}
    output = plan.get('output') or plan.get('content') or {}
    policy = plan.get('source_policy') or {}
    budget = policy.get('max_search_calls', 3)
    # Multiple providers share one bridge invocation. attempt+query is a lower
    # bound when old protected rows are absent; expose the basis honestly.
    per_batch_calls = []
    for batch in batches:
        observations = (batch.get('observations') or {}).get('observations')
        if isinstance(observations, list):
            per_batch_calls.append(sum(row.get('request', {}).get('tool') == 'academic_search' for row in observations))
        else:
            per_batch_calls.append(len({search.get('query') for search in batch.get('searches', [])}))
    calls = per_batch_calls[-1] if per_batch_calls else 0
    providers = [{key: search.get(key) for key in ('source', 'query', 'status', 'result_count', 'searched_at', 'error')}
                 for search in searches]
    for row in providers:
        row['provider'] = row.pop('source')
    queries = output.get('search_queries') or []
    missing = []
    for query in queries:
        successful = [search for search in searches if search.get('query') == query
                      and search.get('status') in ('success', 'partial') and (search.get('result_count') or 0) > 0]
        if not successful:
            missing.append(query)
    selected = {row['source_id'] for row in project.get('selections', []) if row.get('included')}
    included_count = sum(identifier in selected for identifier in papers)
    gaps = []
    if searches and included_count == 0:
        gaps.append('当前没有纳入的学术来源；可检查范围、纳入选择或补充来源。')
    if any(search.get('status') == 'error' for search in searches):
        gaps.append('部分检索来源请求失败；其他已取得来源继续保留。')
    if calls >= budget and missing:
        gaps.append('检索调用预算已用完；列出的计划检索词尚未取得可用结果，不代表已证明该主题没有文献。')
    return dict(calls_used=calls, call_budget=budget, budget_exhausted=calls >= budget,
        total_calls_used=sum(per_batch_calls), budget_scope='latest_retrieval_task',
        providers=providers, topics_without_evidence=missing, gaps=gaps,
        candidate_count=len(papers), included_count=included_count,
        relevance_method='lexical_screening_v1')
