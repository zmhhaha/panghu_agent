"""Measured writing checks, not semantic evaluation or scientific verification.

Only explicitly requested literal examples are mandatory. Source filenames,
links, exclusions and search queries do not become report requirements merely
because they occur in a research context. No source body is interpreted here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re


REPORTING_TIMEZONE = 'Asia/Shanghai'
_SHANGHAI = timezone(timedelta(hours=8), REPORTING_TIMEZONE)
_FILES = re.compile(
    r'(?<![A-Za-z0-9_./\\-])(?:[A-Za-z0-9_.-]+[/\\])*[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.'
    r'(?:md|markdown|toml|py|json|ya?ml|ini|txt|cfg|xml|jsx?|tsx?|rs|go|sh|bash|cpp|c|h|sql|ipynb)'
    r'(?![A-Za-z0-9_.-])', re.I)
_FIXED = re.compile(r'固定|必须|务必|指定|共同|同一|贯穿|保持|保留|包含|包括|涵盖|读取|阅读|案例|示例|场景|'
                    r'\b(?:must|required|include|retain|preserve|read|fixed|same|example|case|scenario)\b', re.I)
_EXAMPLE = re.compile(r'固定|指定|共同|同一|贯穿|案例|示例|正文.{0,8}(?:提及|包含)|'
                      r'\b(?:fixed|same|example|case|scenario|mention)\b', re.I)
_NEGATIVE = re.compile(r'不要|无需|不必|不要求|不包含|排除|不讨论|不展示|不得展示|无需提及|'
                       r'\b(?:do not|don.t|without|exclude|need not|not required)\b', re.I)
_SOURCE = re.compile(r'上传|附件|材料包|资料包|资料文件|官方文档|仓库正文|研究来源|资料来源|作为(?:资料|来源|参考)|'
                     r'\b(?:source[ -]?pack|attachment|uploaded|provided material|as (?:a )?(?:source|reference))\b', re.I)
_OUTPUT_LOCATION = re.compile(r'(?:输出|保存|导出|生成)(?:为|到|文件|格式|路径)|'
                              r'\b(?:output|destination|save as|write(?: the| a)?(?: report)? to|export (?:as|to))\b', re.I)
_BODY_LITERAL = re.compile(r'同一|共同|贯穿|案例|示例|正文.{0,12}(?:提及|包含|出现|写出)|'
                          r'\b(?:example|case|scenario|mention|body.*(?:include|contain))\b', re.I)
_QUESTION = re.compile(r'[“"「]([^”"」\n]{4,160})[”"」]')
_QUESTION_START = re.compile(r'^(?:如何|怎样|怎么|为何|为什么|是否|what\b|how\b|why\b|which\b)', re.I)
_LENGTH = re.compile(r'(?<![\d.])([1-9]\d*(?:,\d{3})*(?:\.\d+)?)\s*([千萬万kK]?)\s*[-－]?\s*'
                     r'(?:个)?(汉字|字(?!节)|words?\b|characters?\b)', re.I)
_LOCAL_LENGTH = re.compile(r'每[章节段页]|单[章节段页]|摘要|\b(?:per section|per paragraph|abstract|summary)\b', re.I)
_GLOBAL_LENGTH = re.compile(r'全[文篇]|报告|总[计共长字]|\b(?:whole|entire|total|report)\b', re.I)
_STRICT_LENGTH = re.compile(r'不超过|最多|至多|以内|上限|\b(?:at most|no more than|maximum|max)\b', re.I)
_REFERENCE_TITLE = re.compile(r'(?:references?|bibliography|参考文献|参考资料|引用文献){1,2}', re.I)


def _reference_title(title):
    title = re.sub(r'^\s*(?:\d+(?:\.\d+)*[.)、．:]?|[一二三四五六七八九十]+[、.．])\s*', '', title)
    title = re.sub(r'[\s/／|｜·—–\-:：()（）]+', '', title)
    return _REFERENCE_TITLE.fullmatch(title) is not None


def body_text(report):
    """Exclude reference lists and internal comments from reading measurements."""
    from .pi_sections import index_sections
    visible = re.sub(r'<!--[\s\S]*?(?:-->|$)', '', report)
    # Reuse the actual Markdown section parser: fenced/indented code and HTML
    # are not headings; Setext, numbered and bilingual reference headings are.
    for section in index_sections(visible)['sections']:
        if section['kind'] == 'section' and _reference_title(section['title']):
            return visible.encode('utf-8')[:section['start_byte']].decode('utf-8')
    return visible


def _texts(requirements, confirmed_plan=None):
    requirements = requirements if isinstance(requirements, dict) else {}
    for field in ('question', 'include', 'notes'):
        if isinstance(requirements.get(field), str):
            yield field, requirements[field]
    if isinstance(confirmed_plan, dict) and isinstance(confirmed_plan.get('content'), dict):
        plan = confirmed_plan['content']
        if isinstance(plan.get('scope'), str):
            yield 'plan.scope', plan['scope']
        for field in ('research_questions', 'inclusion_criteria'):
            for text in plan.get(field, []):
                if isinstance(text, str):
                    yield 'plan.' + field, text


def literal_requirements(requirements, confirmed_plan=None):
    """Extract a small *kind* of deterministic requirement, never a source list."""
    literals = []
    for field, text in _texts(requirements, confirmed_plan):
        for sentence in re.split(r'[。；;\n]', text):
            if not sentence.strip():
                continue
            explicit = field == 'include' or bool(_FIXED.search(sentence))
            if not explicit or (_SOURCE.search(sentence) and not _EXAMPLE.search(sentence)):
                continue
            for match in _FILES.finditer(sentence):
                prefix = re.split(r'[,，]', sentence[:match.start()])[-1]
                if _NEGATIVE.search(prefix):
                    continue
                if _NEGATIVE.search(sentence[:match.start()]) and not _FIXED.search(prefix):
                    continue
                if _OUTPUT_LOCATION.search(prefix) and not _BODY_LITERAL.search(sentence):
                    continue
                # URLs are discovery locations, not mandatory body literals.
                before = sentence[:match.start()].rsplit(None, 1)[-1:]
                if before and '://' in before[0]:
                    continue
                literals.append(match.group(0))
            if _EXAMPLE.search(sentence):
                for match in _QUESTION.finditer(sentence):
                    if _QUESTION_START.search(match.group(1)) and not _NEGATIVE.search(sentence[:match.start()]):
                        literals.append(match.group(1).rstrip('？?'))
    return list(dict.fromkeys(literals))


def length_target(requirements, confirmed_plan=None):
    candidates = []
    texts = list(_texts(requirements, confirmed_plan))
    if isinstance(requirements, dict):
        texts.extend((field, requirements[field]) for field in ('purpose', 'audience')
                     if isinstance(requirements.get(field), str))
    for _, text in texts:
        for match in _LENGTH.finditer(text):
            start = max(text.rfind(mark, 0, match.start()) for mark in ('。', '；', ';', '\n', '，')) + 1
            prefix = text[start:match.start()]
            global_target = bool(_GLOBAL_LENGTH.search(prefix))
            if _LOCAL_LENGTH.search(prefix) and not global_target:
                continue
            multiplier = {'千': 1000, 'k': 1000, '万': 10000, '萬': 10000}.get(match.group(2).lower(), 1)
            target = float(match.group(1).replace(',', '')) * multiplier
            if not target.is_integer() or not 1 <= target <= 1000000:
                continue
            suffix = text[match.end():match.end() + 12]
            unit = 'words' if match.group(3).lower().startswith('word') else 'characters' if match.group(3).lower().startswith('character') else '汉字'
            candidates.append((global_target, int(target), unit, bool(_STRICT_LENGTH.search(prefix + suffix))))
    if not candidates:
        return None
    selected = next((item for item in candidates if item[0]), candidates[0])
    return dict(target_length=selected[1], unit=selected[2], strict=selected[3])


def _length(text, unit):
    if unit == '汉字':
        return len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', text))
    if unit == 'characters':
        return len(re.sub(r'\s', '', text))
    return len(re.findall(r"\b[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*\b", text))


def assess_writing(report, requirements, confirmed_plan=None, parent_report=None):
    """Measured length and literal coverage for a saved report or candidate.

    Warnings do not authorize adoption, rewriting, new evidence or publication.
    Length uses headings/tables, excluding bibliography and internal comments;
    it is intentionally not advertised as Microsoft Word's word count.
    """
    body = body_text(report)
    target = length_target(requirements, confirmed_plan)
    unit = target['unit'] if target else '汉字' if re.search(r'[\u3400-\u4dbf\u4e00-\u9fff]', body) else 'words'
    result = dict(actual_length=_length(body, unit), unit=unit, warnings=[],
                  missing_requirements=[item for item in literal_requirements(requirements, confirmed_plan) if item not in body])
    if parent_report is not None:
        result['parent_length'] = _length(body_text(parent_report), unit)
        result['length_delta'] = result['actual_length'] - result['parent_length']
    if target:
        result['target_length'] = target['target_length']
        if result['actual_length'] > target['target_length'] * (1 if target['strict'] else 1.2):
            result['warnings'].append('正文长度超过已确认目标；可精炼本节后生成新候选，其他章节仍保持不变。')
    paragraphs = [item for item in re.split(r'\n\s*\n', body) if item.strip() and not item.lstrip().startswith('#')]
    repeated = sum(bool(re.search(r'未知|证据不足|资料不足|unknown|insufficient evidence', item, re.I)) for item in paragraphs)
    if len(paragraphs) >= 6 and repeated >= 4 and repeated / len(paragraphs) >= .5:
        result['warnings'].append('多段出现证据限制，请检查能否集中说明；保留影响结论的必要限定，不补造事实。')
    return result


def local_timestamp(value):
    """Express the same recorded instant in the explicit user-facing timezone."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(_SHANGHAI).isoformat(timespec='seconds')
    except (ValueError, OverflowError):
        return None


def writing_contract(context):
    """Controller-derived index; original context/digests remain authoritative."""
    target = length_target(context['requirements'], context.get('confirmed_plan'))
    timestamps = {}
    snapshot = context.get('evidence_snapshot', {})
    included = {item['source_id'] for item in snapshot.get('selections', []) if item['included']}
    for batch in snapshot.get('search_batches', []):
        for paper in batch.get('papers', []):
            if paper['paper_id'] not in included:
                continue
            original = paper.get('retrieved_at')
            local = local_timestamp(original)
            if local:
                timestamps[paper['paper_id']] = dict(source_id=paper['paper_id'],
                    kind='retrieved_at', recorded_instant=original, local=local)
    for material in context.get('materials', []):
        if snapshot and material['material_id'] not in included:
            continue
        original = material.get('recorded_at')
        local = local_timestamp(original)
        if local:
            timestamps[material['material_id']] = dict(source_id=material['material_id'],
                kind='material_recorded_at', recorded_instant=original, local=local)
    return dict(schema_version='1', reporting_timezone=REPORTING_TIMEZONE,
        required_literals=literal_requirements(context['requirements'], context.get('confirmed_plan')),
        length_target=target,
        requirements=context['requirements'], confirmed_plan=context.get('confirmed_plan'),
        recorded_source_times=[timestamps[key] for key in sorted(timestamps)],
        date_scope='These are recorded access/upload instants, never source publication/version dates or guaranteed coverage',
        validation_scope='Literal coverage and measured length only; not semantic or scientific verification')
