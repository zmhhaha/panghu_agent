"""Owner-selected report rendering; no remote resources or document execution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import hashlib
import io
import re
import unicodedata
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


def _table_cells(line):
    """Split a pipe row without treating escaped or code-span pipes as borders."""
    line = line.strip()
    cells, current, index, separators = [], [], 0, 0
    while index < len(line):
        char = line[index]
        if char == '\\' and index + 1 < len(line):
            # Only the pipe escape belongs to the table grammar. Keep other
            # escapes intact, including doubled backslashes before a border.
            next_char = line[index + 1]
            current.append(next_char if next_char == '|' else char + next_char)
            index += 2
            continue
        if char == '`':
            end = index + 1
            while end < len(line) and line[end] == '`':
                end += 1
            token = line[index:end]
            closing = re.search(r'(?<!`)' + re.escape(token) + r'(?!`)', line[end:])
            if closing:
                end += closing.end()
                current.append(line[index:end].replace('\\|', '|'))
                index = end
                continue
        if char == '|':
            cells.append(''.join(current).strip())
            current = []
            separators += 1
        else:
            current.append(char)
        index += 1
    cells.append(''.join(current).strip())
    if line.startswith('|'):
        cells.pop(0)
    if line.endswith('|') and separators and cells and not cells[-1]:
        # An escaped trailing pipe is part of the cell, not a border.
        cells.pop()
    return cells, separators


def _table_start(header, delimiter):
    if header.startswith(('    ', '\t')) or delimiter.startswith(('    ', '\t')):
        return None
    cells, header_pipes = _table_cells(header)
    markers, delimiter_pipes = _table_cells(delimiter)
    if not (header_pipes or delimiter_pipes) or not cells or len(cells) != len(markers):
        return None
    if not all(re.fullmatch(r':?-+:?', marker) for marker in markers):
        return None
    alignments = ['center' if marker.startswith(':') and marker.endswith(':') else
                  'right' if marker.endswith(':') else 'left' for marker in markers]
    return cells, alignments


def _blocks(markdown):
    fence, index = None, 0
    lines = markdown.splitlines()
    while index < len(lines):
        line = lines[index]
        index += 1
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
        if not fence and index < len(lines):
            table = _table_start(line, lines[index])
            if table:
                header, alignments = table
                rows = [header]
                index += 1
                while index < len(lines):
                    candidate = lines[index]
                    if (not candidate.strip() or candidate.startswith(('    ', '\t')) or
                            re.match(r'^ {0,3}(?:#{1,6}\s|`{3,}|~{3,}|>)', candidate)):
                        break
                    cells, separators = _table_cells(candidate)
                    if not separators:
                        break
                    # GFM fills missing cells and ignores cells beyond the
                    # header's width; it never silently adds new columns.
                    rows.append((cells + [''] * len(header))[:len(header)])
                    index += 1
                yield 'table', {'rows': rows, 'alignments': alignments}
                continue
        code = fence or line.startswith(('    ', '\t'))
        heading = re.match(r'^(#{1,6})\s+(.+)$', line) if not code else None
        yield ('code' if code else len(heading[1]) if heading else 'text'), (heading[2] if heading else line)


def _column_weights(rows):
    """Give longer prose columns room without letting a URL consume the page."""
    def width(text):
        return sum(2 if unicodedata.east_asian_width(char) in {'W', 'F'} else 1
                   for char in _plain(text))
    return [max(8, min(48, max(width(row[column]) for row in rows)))
            for column in range(len(rows[0]))]


def _column_widths(rows, available):
    weights = _column_weights(rows)
    # Half an equal share guarantees a usable minimum to short columns.
    return [available * (.5 / len(weights) + .5 * weight / sum(weights))
            for weight in weights]


def _plain(text):
    return re.sub(r'\[([^\]]+)\]\((?:https?://|#)[^\s)]+\)', r'\1',
                  text)


def _inline_chunks(text, limit=4000):
    """Bound paragraph work without cutting links or literal code in half."""
    visible = prose_mask(text)
    spans = [match.span() for match in re.finditer(
        r'\[\[\d+\]\]\(#reference-\d+\)|\[[^\]]+\]\((?:https?://|#)[^\s)]+\)', text)]
    # The mask preserves length. A run of masked spaces with non-space source
    # contains literal code, and must stay together to preserve its context.
    spans.extend(match.span() for match in re.finditer(r' +', visible)
                 if text[match.start():match.end()].strip())
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    start, span_index = 0, 0
    while start < len(text):
        end = min(start + limit, len(text))
        while span_index < len(merged) and merged[span_index][1] <= start:
            span_index += 1
        for left, right in merged[span_index:]:
            if left >= end:
                break
            if left < end < right:
                end = left if left > start else right
                break
        yield text[start:end]
        start = end


def _word_reference_name(number):
    # Word's bookmark UI only accepts letters, digits and underscores, with a
    # 40-character limit. HTML/PDF fragment names are a separate namespace.
    if number.isascii() and len(number) <= 24:
        return 'reference_' + number
    return 'reference_' + hashlib.sha256(number.encode('utf-8')).hexdigest()[:24]


def docx(markdown):
    """Native editable OOXML paragraphs and tables; no external relationships."""
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    paragraphs, bookmark_ids = [], {}
    reference_numbers = set(re.findall(r'^\s*\[(\d+)\]\s+\S', prose_mask(markdown, comments=True), re.M))

    def paragraph(kind, line, *, alignment=None, table_header=False, in_table=False):
        plain = line if kind == 'code' else _plain(line)
        properties = []
        if type(kind) is int:
            properties.append('<w:pStyle w:val="Heading' + str(min(kind, 3)) + '"/>')
            properties.append('<w:keepNext/>')
        if alignment:
            properties.append('<w:jc w:val="' + alignment + '"/>')
        if in_table:
            properties.append('<w:spacing w:after="60" w:before="60"/>')
            # Allow long URLs and identifiers to wrap at character boundaries.
            properties.append('<w:wordWrap w:val="1"/>')
        props = '<w:pPr>' + ''.join(properties) + '</w:pPr>' if properties else ''
        ref = re.match(r'^\s*\[(\d+)\]\s', plain) if kind != 'code' and not in_table else None
        bookmark, bookmark_id = '', None
        if ref and ref[1] not in bookmark_ids:
            # IDs identify pairs in this document, rather than treating an
            # arbitrary imported citation number as a signed Word integer.
            bookmark_id = str(len(bookmark_ids) + 1)
            bookmark_ids[ref[1]] = bookmark_id
            bookmark = '<w:bookmarkStart w:id="' + bookmark_id + '" w:name="' + _word_reference_name(ref[1]) + '"/>'
        run_props = ('<w:rPr>' + ('<w:b/>' if table_header else '') +
                     ('<w:sz w:val="18"/>' if in_table else '') + '</w:rPr>')

        def run(text):
            return '<w:r>' + run_props + '<w:t xml:space="preserve">' + escape(text) + '</w:t></w:r>'

        runs, start = [], 0
        visible = prose_mask(line) if kind != 'code' else ''
        for match in re.finditer(r'\[\[(\d+)\]\]\(#reference-\1\)', visible):
            runs.append(run(_plain(line[start:match.start()])))
            if match[1] in reference_numbers:
                runs.append('<w:hyperlink w:anchor="' + _word_reference_name(match[1]) + '">' + run('[' + match[1] + ']') + '</w:hyperlink>')
            else:
                runs.append(run('[' + match[1] + ']'))
            start = match.end()
        runs.append(run(line[start:] if kind == 'code' else _plain(line[start:])))
        end = '<w:bookmarkEnd w:id="' + bookmark_id + '"/>' if bookmark_id is not None else ''
        return '<w:p>' + props + bookmark + ''.join(runs) + end + '</w:p>'

    for kind, line in _blocks(markdown):
        if kind != 'table':
            paragraphs.append(paragraph(kind, line))
            continue
        rows, alignments = line['rows'], line['alignments']
        available = 11906 - 2 * 1134
        widths = [int(width) for width in _column_widths(rows, available)]
        widths[-1] += available - sum(widths)
        border = '<w:tblBorders>' + ''.join(
            '<w:' + side + ' w:val="single" w:sz="4" w:color="CBD5E1"/>'
            for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV')) + '</w:tblBorders>'
        table = ['<w:tbl><w:tblPr><w:tblW w:w="' + str(available) + '" w:type="dxa"/>'
                 '<w:tblLayout w:type="fixed"/>' + border +
                 '<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="75" w:type="dxa"/>'
                 '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="75" w:type="dxa"/></w:tblCellMar>'
                 '</w:tblPr><w:tblGrid>' + ''.join('<w:gridCol w:w="' + str(width) + '"/>'
                    for width in widths) + '</w:tblGrid>']
        for index, row in enumerate(rows):
            # No cantSplit: an oversized body row must continue on the next page.
            table.append('<w:tr>' + ('<w:trPr><w:tblHeader/></w:trPr>' if index == 0 else ''))
            for column, cell in enumerate(row):
                table.append('<w:tc><w:tcPr><w:tcW w:w="' + str(widths[column]) + '" w:type="dxa"/>'
                    '<w:vAlign w:val="top"/>' + ('<w:shd w:fill="EDF3F8"/>' if index == 0 else '') +
                    '</w:tcPr>' + paragraph('text', cell, alignment=alignments[column],
                        table_header=index == 0, in_table=True) + '</w:tc>')
            table.append('</w:tr>')
        table.append('</w:tbl>')
        paragraphs.append(''.join(table))
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
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle
    # PDF standard CJK font: text remains searchable, never a page bitmap.
    if 'STSong-Light' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    raw, story = io.BytesIO(), []
    reference_numbers = set(re.findall(r'^\s*\[(\d+)\]\s+\S', prose_mask(markdown, comments=True), re.M))

    def inline(line, kind, *, in_table=False):
        reference = re.match(r'^\s*\[(\d+)\]\s', _plain(line)) if kind != 'code' and not in_table else None
        text = escape(line if kind == 'code' else _plain(line))
        if reference:
            text = '<a name="reference-' + reference[1] + '"/>' + text
        elif kind != 'code':
            pieces, start = [], 0
            for match in re.finditer(r'\[\[(\d+)\]\]\(#reference-\1\)', prose_mask(line)):
                pieces.append(escape(_plain(line[start:match.start()])))
                pieces.append('<link href="#reference-' + match[1] + '">[' + match[1] + ']</link>'
                              if match[1] in reference_numbers else '[' + match[1] + ']')
                start = match.end()
            pieces.append(escape(_plain(line[start:])))
            text = ''.join(pieces)
        return text

    for kind, line in _blocks(markdown):
        if kind == 'table':
            rows, alignments = line['rows'], line['alignments']
            # Platypus frames reserve 6 points of padding on each side in
            # addition to the document margins.
            available = A4[0] - 90 - 12
            widths = _column_widths(rows, available)
            padding = min(5, min(widths) / 8)
            font_size = min(9, (min(widths) - padding * 2) / 1.2)
            alignment = {'left': TA_LEFT, 'center': TA_CENTER, 'right': TA_RIGHT}
            data = []
            for index, row in enumerate(rows):
                cells = []
                for column, cell in enumerate(row):
                    style = ParagraphStyle('table-cell', fontName='STSong-Light',
                        fontSize=font_size, leading=font_size * 1.55, wordWrap='CJK',
                        splitLongWords=True, alignment=alignment[alignments[column]],
                        spaceAfter=0, spaceBefore=0)
                    # A list of modest paragraphs bounds layout work and lets
                    # ReportLab split an individual cell across page boundaries.
                    cells.append([Paragraph(inline(chunk, 'text', in_table=True), style)
                                  for chunk in _inline_chunks(cell)] or [Paragraph('', style)])
                data.append(cells)
            table = LongTable(data, colWidths=widths, repeatRows=1, hAlign='LEFT',
                              splitByRow=1, splitInRow=1, longTableOptimize=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EDF3F8')),
                ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#CBD5E1')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), padding),
                ('RIGHTPADDING', (0, 0), (-1, -1), padding),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            table.wrap(available, A4[1] - 102)
            if table._rowHeights[0] > A4[1] - 102:
                # A header taller than an entire page cannot be repeated.
                # Let that exceptional row split normally instead of failing
                # or truncating the imported header text.
                table.repeatRows = 0
            story.extend((table, Spacer(1, 8)))
            continue
        size = (19 - min(kind, 4) * 2) if type(kind) is int else 10
        style = ParagraphStyle('report', fontName='STSong-Light', fontSize=size,
            leading=size * 1.6, wordWrap='CJK', alignment=TA_LEFT, spaceAfter=5)
        text = inline(line, kind)
        # Bound individual flowables so a maliciously long line cannot trigger
        # unbounded layout work. Text remains in order across the split.
        if len(text) > 8000:
            for chunk in _inline_chunks(line):
                story.append(Paragraph(inline(chunk, kind), style))
        else:
            story.append(Paragraph(text, style))
        story.append(Spacer(1, 3))
    SimpleDocTemplate(raw, pagesize=A4, leftMargin=45, rightMargin=45,
        topMargin=45, bottomMargin=45, title='Research report').build(story)
    return raw.getvalue()
