"""Native report tables: actual OOXML and searchable, paginated PDF outputs."""
import io
from xml.etree import ElementTree as ET
import zipfile

import pytest

from scientific_agent import report_exports as exports


NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{' + NS['w'] + '}'


def word_document(markdown):
    with zipfile.ZipFile(io.BytesIO(exports.docx(markdown))) as archive:
        assert archive.testzip() is None
        return ET.fromstring(archive.read('word/document.xml'))


def word_text(element):
    return ''.join(node.text or '' for node in element.findall('.//w:t', NS))


def word_rows(table):
    return [[word_text(cell) for cell in row.findall('w:tc', NS)]
            for row in table.findall('w:tr', NS)]


def pdf_document(markdown):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(exports.pdf(markdown)), strict=True)
    pages = [''.join((page.extract_text() or '').split()) for page in reader.pages]
    return reader, pages


def test_word_table_is_editable_bounded_and_has_a_repeating_header():
    root = word_document('# 来源比较\n\n| 来源 | 年份 | 结论 |\n'
                         '| :--- | :---: | ---: |\n| 工程文档 | 2026 | 可复核 |\n')
    tables = root.findall('.//w:tbl', NS)
    assert len(tables) == 1
    table = tables[0]
    assert word_rows(table) == [['来源', '年份', '结论'], ['工程文档', '2026', '可复核']]
    rows = table.findall('w:tr', NS)
    assert rows[0].find('w:trPr/w:tblHeader', NS) is not None
    assert rows[1].find('w:trPr/w:tblHeader', NS) is None
    assert table.find('w:tblPr/w:tblBorders', NS) is not None
    widths = [int(column.attrib[W + 'w']) for column in table.findall('w:tblGrid/w:gridCol', NS)]
    section = root.find('w:body/w:sectPr', NS)
    size, margin = section.find('w:pgSz', NS), section.find('w:pgMar', NS)
    printable = int(size.attrib[W + 'w']) - int(margin.attrib[W + 'left']) - int(margin.attrib[W + 'right'])
    assert len(widths) == 3 and min(widths) > 0 and sum(widths) <= printable
    assert [cell.find('w:p/w:pPr/w:jc', NS).attrib[W + 'val']
            for cell in rows[1].findall('w:tc', NS)] == ['left', 'center', 'right']
    # A row taller than a page must remain splittable in office applications.
    assert not table.findall('.//w:cantSplit', NS)


@pytest.mark.parametrize('markdown, expected', [
    ('来源 | 结论\n--- | ---\n文档 | 支持\n', [['来源', '结论'], ['文档', '支持']]),
    ('| 来源 | 结论 |\n| --- | --- |\n| 文档 |\n| 论文 | 支持 | 多余 |\n',
     [['来源', '结论'], ['文档', ''], ['论文', '支持']]),
    (r'| 路径 | 表达式 |' + '\n| --- | --- |\n' + r'| a\|b | `x\|y` |' + '\n',
     [['路径', '表达式'], ['a|b', '`x|y`']]),
])
def test_word_table_parses_optional_outer_pipes_escapes_and_ragged_rows(markdown, expected):
    tables = word_document(markdown).findall('.//w:tbl', NS)
    assert len(tables) == 1
    assert word_rows(tables[0]) == expected


def test_word_table_keeps_an_unescaped_pipe_inside_an_inline_code_span():
    root = word_document('| 表达式 | 说明 |\n| --- | --- |\n| `a|b` | 代码 |\n')
    assert word_rows(root.find('.//w:tbl', NS)) == [['表达式', '说明'], ['`a|b`', '代码']]


@pytest.mark.parametrize('markdown', [
    '```markdown\n| 来源 | 结论 |\n| --- | --- |\n| 文档 | 支持 |\n```\n',
    '    | 来源 | 结论 |\n    | --- | --- |\n    | 文档 | 支持 |\n',
    '正文 a | b\n不是分隔符\n',
    '| 来源 | 结论 |\n| --- |\n| 文档 | 支持 |\n',
])
def test_literal_code_and_invalid_delimiters_never_become_word_tables(markdown):
    root = word_document(markdown)
    assert root.findall('.//w:tbl', NS) == []
    assert '|' in word_text(root)


def test_word_cell_citations_resolve_without_creating_external_relationships():
    markdown = ('| 结论 | 根据 |\n| --- | --- |\n'
                '| 中文结论 | [[1]](#reference-1) |\n\n## 参考文献\n[1] 来源说明。\n')
    raw = exports.docx(markdown)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        cell_links = root.findall('.//w:tbl//w:hyperlink', NS)
        assert len(cell_links) == 1
        destinations = {item.attrib[W + 'name'] for item in root.findall('.//w:bookmarkStart', NS)}
        assert cell_links[0].attrib[W + 'anchor'] in destinations
        assert word_text(cell_links[0]) == '[1]'
        relationships = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
        assert all(item.attrib.get('TargetMode') != 'External' for item in relationships)


def test_numbered_cell_text_does_not_steal_the_bibliography_destination():
    root = word_document('| 编号 | 结论 |\n| --- | --- |\n'
                         '| [1] 示例编号 | [[1]](#reference-1) |\n\n'
                         '## 参考文献\n[1] 真正的来源条目。\n')
    assert root.findall('.//w:tbl//w:bookmarkStart', NS) == []
    bookmarks = root.findall('.//w:bookmarkStart', NS)
    assert len(bookmarks) == 1 and bookmarks[0].attrib[W + 'name'] == 'reference_1'
    destination = next(paragraph for paragraph in root.findall('w:body/w:p', NS)
                       if paragraph.find('w:bookmarkStart', NS) is not None)
    assert word_text(destination) == '[1] 真正的来源条目。'


def test_word_cell_xml_is_text_and_never_an_executable_object():
    payload = '<w:object> & <script>alert(1)</script>'
    root = word_document('| 材料 |\n| --- |\n| ' + payload + ' |\n')
    assert word_rows(root.find('.//w:tbl', NS)) == [['材料'], [payload]]
    assert root.findall('.//w:object', NS) == []


def test_pdf_table_paginates_and_repeats_header_without_losing_rows():
    markdown = ('# 文献比较\n\n| 来源编号 | 来源结论 |\n| --- | --- |\n' +
                ''.join('| SRC%03d | 第%03d项中文证据支持结论。 |\n' % (i, i) for i in range(120)))
    reader, pages = pdf_document(markdown)
    assert len(pages) >= 3
    assert all('来源编号' in page and '来源结论' in page for page in pages)
    text = ''.join(pages)
    for i in range(120):
        assert text.count('SRC%03d' % i) == 1
        assert text.count('第%03d项中文证据支持结论。' % i) == 1
    assert not reader.is_encrypted


def test_pdf_single_oversize_cell_splits_across_pages_and_preserves_all_text():
    payload = '段落' * 4000
    markdown = '| 名称 | 长内容 |\n| --- | --- |\n| LONGCELL | 开始标记' + payload + '结束标记 |\n'
    _, pages = pdf_document(markdown)
    assert len(pages) >= 3
    text = ''.join(pages)
    assert text.count('开始标记') == 1 and text.count('结束标记') == 1
    assert text.count('段落') == 4000
    assert all('长内容' in page for page in pages)


def test_pdf_table_handles_long_tokens_escapes_and_code_without_text_loss():
    url = 'https://example.org/' + 'abcdefghijklmnopqrstuvwxyz0123456789' * 12
    markdown = ('| 类型 | 内容 |\n| --- | --- |\n| URL | ' + url + ' |\n' +
                r'| CODE | `a\|b` |' + '\n')
    _, pages = pdf_document(markdown)
    text = ''.join(pages)
    assert url in text and 'a|b' in text


def test_pdf_long_cell_keeps_a_citation_spanning_an_internal_chunk_boundary():
    markdown = ('| 内容 |\n| --- |\n| ' + 'a' * 3994 +
                '[[1]](#reference-1) |\n\n## 参考文献\n[1] 来源说明。\n')
    reader, pages = pdf_document(markdown)
    text = ''.join(pages)
    assert text.count('a') == 3994
    assert '(#reference-1)' not in text and '[1]' in text
    annotations = [item.get_object() for page in reader.pages for item in page.get('/Annots', [])]
    assert any(item.get('/Subtype') == '/Link' and '/Dest' in item for item in annotations)


def test_pdf_oversized_header_can_continue_without_truncating_its_contents():
    markdown = '| ' + '超长表头' * 1000 + ' |\n| --- |\n| HEADEREND |\n'
    _, pages = pdf_document(markdown)
    assert len(pages) >= 2
    text = ''.join(pages)
    assert text.count('超长表头') == 1000
    assert text.count('HEADEREND') == 1


def test_pdf_cell_citation_is_an_internal_link_and_html_stays_literal():
    payload = '<script>alert(1)</script> & <img src="https://example.org/private">'
    markdown = ('| 结论 | 根据 |\n| --- | --- |\n| ' + payload +
                ' | [[1]](#reference-1) |\n\n## 参考文献\n[1] 中文来源。\n')
    reader, pages = pdf_document(markdown)
    assert ''.join(payload.split()) in ''.join(pages)
    annotations = [item.get_object() for page in reader.pages for item in page.get('/Annots', [])]
    links = [item for item in annotations if item.get('/Subtype') == '/Link']
    assert links and all('/Dest' in link for link in links)
    for page in reader.pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        for ref in page.get('/Annots', []):
            annotation = ref.get_object()
            if annotation.get('/Subtype') == '/Link':
                x1, y1, x2, y2 = map(float, annotation['/Rect'])
                assert 0 <= x1 <= x2 <= width and 0 <= y1 <= y2 <= height
        assert all(resource.get_object().get('/Subtype') != '/Image'
                   for resource in page['/Resources'].get('/XObject', {}).values())
    assert not any('/JS' in item or '/JavaScript' in item for item in annotations)
