"""Owner-selected report rendering; no remote resources or document execution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import hashlib
import io
import re
import zipfile


def prose_mask(report, *, comments=False):
    """Same-length view excluding fenced, indented and inline code examples."""
    from .pi_citations import _mask, _escaped
    ranges, offset, fence, fence_start = [], 0, None, None
    for line in report.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                ranges.append((fence_start, offset + len(line)))
                fence = None
        elif marker:
            fence, fence_start = marker[1], offset
        elif line.startswith(('    ', '\t')):
            ranges.append((offset, offset + len(line)))
        offset += len(line)
    if fence:
        ranges.append((fence_start, len(report)))
    visible = _mask(report, ranges)
    runs = list(re.finditer(r'`+', visible))
    ends = {}
    for match in reversed(runs):
        ends.setdefault(len(match[0]), []).append(match)
    used_until, ranges = -1, []
    for match in runs:
        if match.start() < used_until or _escaped(visible, match.start()):
            continue
        candidates = ends[len(match[0])]
        while candidates and candidates[-1].start() <= match.start():
            candidates.pop()
        if candidates:
            used_until = candidates[-1].end()
            ranges.append((match.start(), used_until))
    visible = _mask(visible, ranges)
    if comments:
        visible = _mask(visible, [m.span() for m in re.finditer(r'<!--.*?(?:-->|\Z)', visible, re.S)])
    return visible


def readable_body(report):
    """Hide exact internal markers outside fenced/indented code examples."""
    visible = prose_mask(report)
    matches = list(re.finditer(r'<!--\s*claim:[A-Za-z0-9_.:-]+\s*-->[ \t]*', report))
    for match in reversed(matches):
        if visible[match.start():match.end()] == match[0]:
            report = report[:match.start()] + report[match.end():]
    return report


def _time(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(
            timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') + ' Asia/Shanghai'
    except (ValueError, TypeError, AttributeError):
        return '未记录'


def reading_markdown(project, version):
    title = str(project.get('title') or project['requirements']['question']).replace('\n', ' ').strip()
    origin = {'user_import': '用户导入', 'manual_edit': '人工编辑'}.get(version.get('origin'), '网站生成')
    metadata = [f'# {title}', '报告版本：' + version['id'],
        '生成时间：' + _time(version.get('created_at')),
        '状态：' + ('已私有定稿' if version.get('finalized') else '草稿'), '内容来源：' + origin]
    body = readable_body(version['report_markdown'])
    # Bibliography labels become local destinations; leave code byte-equivalent.
    visible = prose_mask(body, comments=True)
    reference_numbers = set(re.findall(r'^\s*\[(\d+)\]\s+\S', visible, re.M))
    result, fence = [], None
    for line, visible_line in zip(body.splitlines(), visible.splitlines()):
        match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if match:
            token = match[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[match.end():].strip():
                fence = None
            result.append(line)
            continue
        if fence or line.startswith(('    ', '\t')):
            result.append(line)
            continue
        reference = re.match(r'^\s*\[(\d+)\]\s+\S', visible_line)
        if reference:
            result.append('<a id="reference-' + reference[1] + '"></a>')
            result.append(line)
        else:
            matches = list(re.finditer(r'(?<!!)\[(\d+)\](?!\()', visible_line))
            for match in reversed(matches):
                if match[1] in reference_numbers:
                    line = line[:match.start()] + '[[' + match[1] + ']](#reference-' + match[1] + ')' + line[match.end():]
            result.append(line)
    return '\n\n'.join(metadata) + '\n\n' + '\n'.join(result).rstrip() + '\n'


def _blocks(markdown):
    fence = None
    for line in markdown.splitlines():
        if not fence and line.startswith('<a id="reference-'):
            continue
        match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if match:
            if fence is None:
                fence = match[1]
                continue
            elif (match[1][0] == fence[0] and len(match[1]) >= len(fence)
                    and not line[match.end():].strip()):
                fence = None
                continue
        if not line.strip():
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)$', line) if not fence else None
        if not fence and re.fullmatch(r'\s*\|?[\s:|\-]+\|\s*', line):
            continue
        yield ('code' if fence else len(heading[1]) if heading else 'text'), (heading[2] if heading else line)


def _plain(text):
    return re.sub(r'\[([^\]]+)\]\((?:https?://|#)[^\s)]+\)', r'\1',
                  text)


def _word_reference_name(number):
    # Word's bookmark UI only accepts letters, digits and underscores, with a
    # 40-character limit. HTML/PDF fragment names are a separate namespace.
    if number.isascii() and len(number) <= 24:
        return 'reference_' + number
    return 'reference_' + hashlib.sha256(number.encode('utf-8')).hexdigest()[:24]


def docx(markdown):
    """Small native OOXML document with headings, bookmarks and citation jumps."""
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    paragraphs, bookmark_ids = [], {}
    for kind, line in _blocks(markdown):
        plain = line if kind == 'code' else _plain(line)
        props = ('<w:pPr><w:pStyle w:val="Heading' + str(min(kind, 3)) + '"/></w:pPr>') if type(kind) is int else ''
        ref = re.match(r'^\s*\[(\d+)\]\s', plain) if kind != 'code' else None
        bookmark, bookmark_id = '', None
        if ref and ref[1] not in bookmark_ids:
            # IDs identify pairs in this document, rather than treating an
            # arbitrary imported citation number as a signed Word integer.
            bookmark_id = str(len(bookmark_ids) + 1)
            bookmark_ids[ref[1]] = bookmark_id
            bookmark = '<w:bookmarkStart w:id="' + bookmark_id + '" w:name="' + _word_reference_name(ref[1]) + '"/>'
        runs, start = [], 0
        for match in re.finditer(r'\[\[(\d+)\]\]\(#reference-\1\)', line) if kind != 'code' else []:
            runs.append('<w:r><w:t xml:space="preserve">' + escape(_plain(line[start:match.start()])) + '</w:t></w:r>')
            runs.append('<w:hyperlink w:anchor="' + _word_reference_name(match[1]) + '"><w:r><w:rPr><w:color w:val="12635A"/></w:rPr><w:t>[' + match[1] + ']</w:t></w:r></w:hyperlink>')
            start = match.end()
        runs.append('<w:r><w:t xml:space="preserve">' + escape(line[start:] if kind == 'code' else _plain(line[start:])) + '</w:t></w:r>')
        end = '<w:bookmarkEnd w:id="' + bookmark_id + '"/>' if bookmark_id is not None else ''
        paragraphs.append('<w:p>' + props + bookmark + ''.join(runs) + end + '</w:p>')
    document = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="' + ns + '"><w:body>' + ''.join(paragraphs) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>'
    styles = '<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="' + ns + '"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>' + ''.join('<w:style w:type="paragraph" w:styleId="Heading' + str(i) + '"><w:name w:val="heading ' + str(i) + '"/><w:rPr><w:b/><w:sz w:val="' + str(36 - 4 * i) + '"/></w:rPr></w:style>' for i in (1, 2, 3)) + '</w:styles>'
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', document)
        archive.writestr('word/styles.xml', styles)
        archive.writestr('word/_rels/document.xml.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    return raw.getvalue()


def pdf(markdown):
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    # PDF standard CJK font: text remains searchable, never a page bitmap.
    if 'STSong-Light' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    raw, story = io.BytesIO(), []
    reference_numbers = set(re.findall(r'^\s*\[(\d+)\]\s+\S', prose_mask(markdown, comments=True), re.M))
    for kind, line in _blocks(markdown):
        text = escape(line if kind == 'code' else _plain(line))
        reference = re.match(r'^\s*\[(\d+)\]\s', _plain(line)) if kind != 'code' else None
        if reference:
            text = '<a name="reference-' + reference[1] + '"/>' + text
        elif kind != 'code':
            text = re.sub(r'\[\[(\d+)\]\]\(#reference-\1\)', lambda m:
                '<link href="#reference-' + m[1] + '">[' + m[1] + ']</link>' if m[1] in reference_numbers else '[' + m[1] + ']', escape(line))
        size = (19 - min(kind, 4) * 2) if type(kind) is int else 10
        style = ParagraphStyle('report', fontName='STSong-Light', fontSize=size,
            leading=size * 1.6, wordWrap='CJK', alignment=TA_LEFT, spaceAfter=5)
        # Bound individual flowables so a maliciously long line cannot trigger
        # unbounded layout work. Text remains in order across the split.
        if len(text) > 8000:
            for start in range(0, len(line), 4000):
                chunk = line[start:start + 4000]
                anchor = '<a name="reference-' + reference[1] + '"/>' if start == 0 and reference else ''
                story.append(Paragraph(anchor + escape(chunk if kind == 'code' else _plain(chunk)), style))
        else:
            story.append(Paragraph(text, style))
        story.append(Spacer(1, 3))
    SimpleDocTemplate(raw, pagesize=A4, leftMargin=45, rightMargin=45,
        topMargin=45, bottomMargin=45, title='Research report').build(story)
    return raw.getvalue()
