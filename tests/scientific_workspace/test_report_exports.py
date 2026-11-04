"""Real reading/OOXML/PDF outputs; no mocked renderers or network resources."""
from copy import deepcopy
import hashlib
import io
import re
from xml.etree import ElementTree as ET
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.scientific_workspace import create_router
from scientific_agent import report_exports as exports
from test_recovery import import_payload
from test_workspace import setup, create, action


REPORT = ('# 研究发现\n\n<!-- claim:c1 -->\n中文研究结论由已提供材料支持 [1]。\n\n'
          '## 适用边界\n\n仅核对提供的材料，不代表独立复现。\n\n'
          '## 参考文献\n\n[1] 中文工程文档。https://example.org/document\n')


def document():
    project = {'title': '架构研究报告', 'requirements': {'question': '架构研究'}}
    version = {'id': 'version-1', 'created_at': '2026-11-03T16:30:00Z',
               'report_markdown': REPORT, 'origin': 'user_import', 'finalized': False}
    return project, version


def xml_docx(raw):
    archive = zipfile.ZipFile(io.BytesIO(raw))
    assert archive.testzip() is None
    root = ET.fromstring(archive.read('word/document.xml'))
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    text = ''.join(item.text or '' for item in root.findall('.//w:t', ns))
    return archive, root, ns, text


def pdf_reader(raw):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw), strict=True)
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    return reader, text


def test_reading_export_is_a_derivative_and_never_mutates_the_raw_version():
    project, version = document()
    original = deepcopy(version)
    raw_hash = hashlib.sha256(version['report_markdown'].encode()).hexdigest()
    markdown = exports.reading_markdown(project, version)
    assert markdown.startswith('# 架构研究报告\n')
    assert '<!-- claim:c1 -->' not in markdown
    assert '[[1]](#reference-1)' in markdown and '<a id="reference-1"></a>' in markdown
    assert '2026-11-04 00:30:00 Asia/Shanghai' in markdown
    assert '内容来源：用户导入' in markdown and '状态：草稿' in markdown
    assert version == original and hashlib.sha256(version['report_markdown'].encode()).hexdigest() == raw_hash


def test_marker_removal_preserves_fenced_and_indented_code_examples():
    report = ('<!-- claim:real -->\n正文。\n\n```html\n<!-- claim:code -->\n```\n\n'
              '    <!-- claim:indented -->\n\n~~~text\n<!-- claim:tilde -->\n~~~\n')
    result = exports.readable_body(report)
    assert '<!-- claim:real -->' not in result
    for marker in ('code', 'indented', 'tilde'):
        assert '<!-- claim:' + marker + ' -->' in result


def test_marker_removal_preserves_inline_code_literals():
    report = ('<!-- claim:real -->\nUse `<!-- claim:example -->` as an example.\n'
              'A ``literal ` and <!-- claim:double -->`` is also code.\n')
    result = exports.readable_body(report)
    assert '<!-- claim:real -->' not in result
    assert '`<!-- claim:example -->`' in result
    assert '``literal ` and <!-- claim:double -->``' in result


def test_code_only_reference_does_not_create_a_dangling_citation_jump():
    project, version = document()
    version['report_markdown'] = '# Findings\nAn unmatched [13].\n\n```text\n[13] Code example only.\n```\n'
    markdown = exports.reading_markdown(project, version)
    assert '[[13]](#reference-13)' not in markdown
    assert '[13] Code example only.' in markdown


def test_inline_code_citation_stays_literal_in_reading_export():
    project, version = document()
    version['report_markdown'] = '# Findings\nLiteral `[1]` stays code; actual [1].\n\n## References\n[1] Source.\n'
    markdown = exports.reading_markdown(project, version)
    assert 'Literal `[1]` stays code; actual [[1]](#reference-1).' in markdown


def test_docx_contains_editable_chinese_text_and_real_bookmark_links():
    raw = exports.docx(exports.reading_markdown(*document()))
    archive, root, ns, text = xml_docx(raw)
    assert '架构研究报告' in text and '中文研究结论' in text
    assert '<!-- claim:c1 -->' not in text
    bookmarks = root.findall('.//w:bookmarkStart', ns)
    names = {row.attrib['{' + ns['w'] + '}name'] for row in bookmarks}
    assert 'reference_1' in names
    anchors = {row.attrib.get('{' + ns['w'] + '}anchor') for row in root.findall('.//w:hyperlink', ns)}
    assert anchors == {'reference_1'}
    assert anchors <= names
    assert not any(name.startswith('word/media/') for name in archive.namelist())
    assert root.findall('.//w:t', ns)
    relationships = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
    assert all(row.attrib.get('TargetMode') != 'External' for row in relationships)


def test_docx_bookmarks_use_word_names_and_unique_paired_small_integer_ids():
    number = '1234567890' * 8
    markdown = ('# Findings\nCitations [[1]](#reference-1) and [[' + number + ']](#reference-' + number + ').\n'
        '## References\n[1] First source.\n[1] Duplicate label remains visible.\n[' + number + '] A long imported label.\n')
    _, root, ns, text = xml_docx(exports.docx(markdown))
    attr = '{' + ns['w'] + '}'
    starts = root.findall('.//w:bookmarkStart', ns)
    ends = root.findall('.//w:bookmarkEnd', ns)
    names = [item.attrib[attr + 'name'] for item in starts]
    ids = [item.attrib[attr + 'id'] for item in starts]
    assert len(names) == len(set(names)) == len(set(ids)) == 2
    assert all(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,39}', name) for name in names)
    assert all(0 <= int(identifier) < 2 ** 31 for identifier in ids)
    assert sorted(ids) == sorted(item.attrib[attr + 'id'] for item in ends)
    targets = {item.attrib[attr + 'anchor'] for item in root.findall('.//w:hyperlink', ns)}
    assert targets <= set(names)
    assert 'Duplicate label remains visible.' in text


def test_docx_html_like_source_is_text_and_cannot_inject_ooxml():
    markdown = '# 中文标题\n\nLiteral <w:object> and <script>payload</script> & text.\n'
    _, root, ns, text = xml_docx(exports.docx(markdown))
    assert '<w:object>' in text and '<script>payload</script>' in text and '& text' in text
    assert root.findall('.//w:object', ns) == []


def test_native_docx_keeps_code_operators_and_literal_backticks():
    code = 'value = a * b\ntext = "`literal`"'
    _, _, _, text = xml_docx(exports.docx('# Code\n\n```python\n' + code + '\n```\n'))
    assert 'value = a * b' in text and 'text = "`literal`"' in text


def test_pdf_is_parseable_searchable_chinese_and_has_internal_citation_link():
    raw = exports.pdf(exports.reading_markdown(*document()))
    assert raw.startswith(b'%PDF-')
    reader, text = pdf_reader(raw)
    assert reader.pages and not reader.is_encrypted
    assert '架构研究报告' in ''.join(text.split())
    assert '中文研究结论' in ''.join(text.split())
    assert '<!-- claim:c1 -->' not in text
    annotations = [item.get_object() for page in reader.pages for item in page.get('/Annots', [])]
    links = [item for item in annotations if item.get('/Subtype') == '/Link']
    assert links and any('/Dest' in item for item in links)
    assert not any('/JavaScript' in item or '/JS' in item for item in annotations)


def test_pdf_long_bibliography_still_resolves_the_citation_destination():
    markdown = '# 中文报告\n\n结论 [[1]](#reference-1)。\n\n## References\n[1] ' + '来源说明' * 2500 + '\n'
    reader, text = pdf_reader(exports.pdf(markdown))
    assert reader.pages and '中文报告' in ''.join(text.split())
    assert '来源说明' in ''.join(text.split())


def test_native_pdf_keeps_code_operators_and_literal_backticks():
    code = 'value = a * b\ntext = "`literal`"'
    _, text = pdf_reader(exports.pdf('# Code\n\n```python\n' + code + '\n```\n'))
    assert 'value = a * b' in text and 'text = "`literal`"' in text


def test_office_and_pdf_blocks_do_not_close_a_fence_with_an_info_suffix():
    code = '```text\n<a id="reference-example"></a>\n```python\n[99] Still literal code.\n```\n'
    markdown = '# Findings\nActual [[1]](#reference-1).\n\n' + code + '\n## References\n[1] Real source.\n'
    _, root, ns, text = xml_docx(exports.docx(markdown))
    assert '<a id="reference-example"></a>' in text and '```python' in text
    assert '[99] Still literal code.' in text
    assert len(root.findall('.//w:bookmarkStart', ns)) == 1
    reader, pdf_text = pdf_reader(exports.pdf(markdown))
    assert reader.pages and 'Real source.' in pdf_text


@pytest.fixture
def api(setup):
    store, service, _ = setup
    project = action(store, create(store), 'import_report', dict(import_payload(), report_markdown=REPORT))
    version_id = project['versions'][0]['id']
    app = FastAPI()
    app.include_router(create_router(service))
    with TestClient(app, headers={'X-Forwarded-User': 'alice'}) as client:
        yield client, store, project, version_id


@pytest.mark.parametrize('format', ['markdown', 'reading-markdown', 'sources', 'docx', 'pdf'])
def test_every_export_format_is_owner_scoped_before_rendering(api, format):
    client, _, project, version_id = api
    response = client.get('/api/v1/research/projects/' + project['id'] + '/versions/' + version_id + '/download',
        params={'format': format}, headers={'X-Forwarded-User': 'bob'})
    assert response.status_code == 404


@pytest.mark.parametrize('format', ['markdown', 'reading-markdown', 'sources', 'docx', 'pdf'])
def test_owner_downloads_real_selected_version_without_changing_store(api, format):
    client, store, project, version_id = api
    before = store.raw('alice', project['id'])
    response = client.get('/api/v1/research/projects/' + project['id'] + '/versions/' + version_id + '/download',
        params={'format': format})
    assert response.status_code == 200, response.text[:500]
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['x-research-document-origin'] == 'user_import'
    assert response.headers['x-research-document-status'] == 'draft'
    assert 'attachment;' in response.headers['content-disposition']
    if format == 'markdown':
        assert response.content == REPORT.encode()
    elif format == 'reading-markdown':
        assert '<!-- claim:c1 -->' not in response.text
        assert '[[1]](#reference-1)' in response.text
    elif format == 'sources':
        assert response.json()['report_sha256'] == hashlib.sha256(REPORT.encode()).hexdigest()
        assert response.json()['claims'] == response.json()['evidence'] == []
    elif format == 'docx':
        assert '中文研究结论' in xml_docx(response.content)[3]
    else:
        assert '中文研究结论' in ''.join(pdf_reader(response.content)[1].split())
    assert store.raw('alice', project['id']) == before


@pytest.mark.parametrize('format', ['markdown', 'reading-markdown', 'sources', 'docx', 'pdf'])
def test_downloading_selected_old_version_cannot_switch_to_current_candidate(api, format):
    client, store, original, old_id = api
    project = action(store, original, 'confirm_import', dict(version_id=old_id,
        source_reviewed=True, allow_limited_evidence=True))
    project = action(store, project, 'adopt', {'version_id': old_id})
    newer_report = REPORT.replace('中文研究结论', '新版不同结论')
    project = action(store, project, 'import_report', dict(import_payload(), report_markdown=newer_report,
        origin='manual_edit', parent_version_id=old_id))
    new_id = project['versions'][-1]['id']
    project = action(store, project, 'confirm_import', dict(version_id=new_id,
        source_reviewed=True, allow_limited_evidence=True))
    project = action(store, project, 'adopt', {'version_id': new_id})
    assert project['current_version_id'] == new_id
    before = store.raw('alice', project['id'])
    response = client.get('/api/v1/research/projects/' + project['id'] + '/versions/' + old_id + '/download',
        params={'format': format})
    assert response.status_code == 200, response.text[:500]
    if format == 'sources':
        assert response.json()['version_id'] == old_id
        assert response.json()['report_sha256'] == hashlib.sha256(REPORT.encode()).hexdigest()
    else:
        text = (xml_docx(response.content)[3] if format == 'docx' else
                pdf_reader(response.content)[1] if format == 'pdf' else response.text)
        assert '中文研究结论' in ''.join(text.split())
        assert '新版不同结论' not in ''.join(text.split())
    assert store.raw('alice', project['id']) == before
