"""Real source parsers on synthetic files; Linux server acceptance only.

No production inputs, network, OCR, model, browser or Office editing is used.
Reduced limits are explicit in the few aggregate-budget tests; PDF pages and
Markdown text boundaries exercise the actual production constants.
"""
import hashlib
import os
import stat
import struct
import zipfile

import fitz
from PIL import Image
import pytest

from ppt_service.control import parsing as p
from ppt_service.control.execution import ControlError

DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
TYPES = b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
DOCUMENT = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:p><w:r><w:t>synthetic source</w:t></w:r></w:p></w:document>').encode()


def package(path, extra=(), *, document=DOCUMENT, compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(path, 'w', compression=compression) as archive:
        archive.writestr('[Content_Types].xml', TYPES)
        archive.writestr('word/document.xml', document)
        for name, data in extra:
            archive.writestr(name, data)
    return path


def rejected(code, action):
    with pytest.raises(ControlError) as exc:
        action()
    assert exc.value.code == code


def source(path, mime='text/markdown', **changes):
    raw = path.read_bytes()
    return dict(id='synthetic-' + path.name, path=path.name,
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                media_type=mime) | changes


@pytest.mark.parametrize('format,mime', [('PNG', 'image/png'), ('JPEG', 'image/jpeg')])
def test_images_are_verified_without_claiming_ocr(tmp_path, format, mime):
    path = tmp_path / 'source.image'
    Image.new('RGB', (48, 36), '#446688').save(path, format=format)
    result = p.parse_one(path, mime)
    assert result == {'text': '', 'image': {'width': 48, 'height': 36}, 'pages': None}


@pytest.mark.parametrize('dimensions', [(20001, 1), (20000, 1001)])
def test_actual_image_dimension_and_pixel_budgets(tmp_path, dimensions):
    path = tmp_path / 'large.png'
    Image.new('1', dimensions).save(path, format='PNG')
    rejected('image_limit', lambda: p.parse_one(path, 'image/png'))


def test_other_image_codec_is_not_accepted_as_png(tmp_path):
    path = tmp_path / 'pretend.png'
    Image.new('RGB', (8, 8)).save(path, format='BMP')
    rejected('image_limit', lambda: p.parse_one(path, 'image/png'))


def test_docx_extracts_text_without_external_fetch(tmp_path):
    assert p.parse_one(package(tmp_path / 'source.docx'), DOCX) == {
        'text': 'synthetic source', 'pages': None}


@pytest.mark.parametrize('name', ['word/vbaProject.bin', 'word/activeX/control.xml',
    'word/oleObject.xml', 'EncryptedPackage', 'EncryptionInfo', 'word/payload.vba'])
def test_office_active_and_encrypted_parts_are_refused(tmp_path, name):
    path = package(tmp_path / 'active.docx', [(name, b'payload')])
    rejected('active_office_content', lambda: p.parse_one(path, DOCX))


@pytest.mark.parametrize('name', ['../escape.xml', '/absolute.xml', 'word/../../escape.xml',
                                 'word\\escape.xml', 'C:/escape.xml'])
def test_zip_paths_cannot_escape_source(tmp_path, name):
    path = package(tmp_path / 'unsafe.docx', [(name, b'payload')])
    with pytest.raises(ControlError):
        p.parse_one(path, DOCX)
    assert not (tmp_path / 'escape.xml').exists()


def test_casefold_duplicate_zip_parts_are_refused(tmp_path):
    path = package(tmp_path / 'duplicate.docx', [('WORD/DOCUMENT.XML', DOCUMENT)])
    rejected('duplicate_zip_part', lambda: p.parse_one(path, DOCX))


def test_external_office_relationship_is_not_fetched(tmp_path):
    relationships = b'<Relationships><Relationship TargetMode="External" Target="https://example.invalid/private"/></Relationships>'
    path = package(tmp_path / 'external.docx', [('word/_rels/document.xml.rels', relationships)])
    rejected('external_office_reference', lambda: p.parse_one(path, DOCX))


def test_macro_content_type_is_refused(tmp_path):
    path = package(tmp_path / 'macro.docx', [('word/types.xml', b'<Type ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/>')])
    rejected('active_office_content', lambda: p.parse_one(path, DOCX))


def test_zip_symlink_is_refused(tmp_path):
    item = zipfile.ZipInfo('word/link')
    item.create_system = 3
    item.external_attr = (stat.S_IFLNK | 0o777) << 16
    path = package(tmp_path / 'link.docx', [(item, b'/etc/passwd')])
    rejected('unsupported_zip_part', lambda: p.parse_one(path, DOCX))


def test_actual_zip_encrypted_flag_is_refused_before_read(tmp_path):
    path = package(tmp_path / 'encrypted.docx')
    raw = bytearray(path.read_bytes())
    for signature, offset in [(b'PK\x03\x04', 6), (b'PK\x01\x02', 8)]:
        cursor = raw.find(signature)
        while cursor >= 0:
            flags = struct.unpack_from('<H', raw, cursor + offset)[0]
            struct.pack_into('<H', raw, cursor + offset, flags | 1)
            cursor = raw.find(signature, cursor + 4)
    path.write_bytes(raw)
    rejected('unsupported_zip_part', lambda: p.parse_one(path, DOCX))


def test_compression_bomb_is_refused(tmp_path):
    path = package(tmp_path / 'bomb.docx', [('word/bomb.xml', b'x' * 200000)], compression=zipfile.ZIP_DEFLATED)
    rejected('zip_expansion_limit', lambda: p.parse_one(path, DOCX))


def test_archive_entry_count_bound_is_real(tmp_path):
    path = package(tmp_path / 'entries.docx', [(f'word/part-{i}', b'x') for i in range(2047)])
    rejected('zip_entry_limit', lambda: p.parse_one(path, DOCX))


def test_total_archive_expansion_budget_is_enforced(tmp_path, monkeypatch):
    path = package(tmp_path / 'total.docx', [('word/a', b'x' * 1000), ('word/b', b'x' * 1000)])
    monkeypatch.setattr(p, 'MAX_ZIP', 1500)  # Explicit reduced aggregate budget.
    rejected('zip_expansion_limit', lambda: p.parse_one(path, DOCX))


@pytest.mark.parametrize('raw', [b'<!DOCTYPE a><a/>', b'<!ENTITY x "secret"><a/>'])
def test_xml_entity_and_doctype_are_refused(raw):
    rejected('unsafe_xml', lambda: p.xml(raw))


def test_xml_depth_and_node_budgets_are_enforced():
    rejected('xml_limit', lambda: p.xml(b'<a>' * 66 + b'</a>' * 66))
    rejected('xml_limit', lambda: p.xml(b'<root>' + b'<a/>' * 100000 + b'</root>'))


@pytest.mark.parametrize('pages,accepted', [(150, True), (151, False)])
def test_actual_pdf_page_boundary(tmp_path, pages, accepted):
    path = tmp_path / 'pages.pdf'
    with fitz.open() as document:
        for number in range(pages):
            document.new_page().insert_text((30, 30), f'Synthetic page {number + 1}')
        document.save(path)
    if accepted:
        result = p.parse_one(path, 'application/pdf')
        assert result['pages'] == 150 and 'Synthetic page 150' in result['text']
    else:
        rejected('pdf_page_limit', lambda: p.parse_one(path, 'application/pdf'))


@pytest.mark.parametrize('kind,code', [('encrypted', 'unsupported_pdf'),
    ('attachment', 'unsupported_pdf'), ('form', 'pdf_active_form'),
    ('scanned', 'scanned_pdf_unsupported')])
def test_pdf_unsupported_content_is_refused(tmp_path, kind, code):
    path = tmp_path / (kind + '.pdf')
    with fitz.open() as document:
        page = document.new_page()
        if kind != 'scanned':
            page.insert_text((30, 30), 'Synthetic PDF')
        if kind == 'attachment':
            document.embfile_add('source.txt', b'synthetic attachment')
        if kind == 'form':
            widget = fitz.Widget()
            widget.field_name = 'synthetic'
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            widget.rect = fitz.Rect(50, 50, 100, 70)
            page.add_widget(widget)
        options = dict(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader') if kind == 'encrypted' else {}
        document.save(path, **options)
    rejected(code, lambda: p.parse_one(path, 'application/pdf'))


@pytest.mark.parametrize('name,mime,raw', [('broken.pdf', 'application/pdf', b'%PDF-1.7\ncorrupted'),
    ('broken.docx', DOCX, b'PK\x03\x04broken'), ('broken.png', 'image/png', b'\x89PNG\r\n\x1a\nbroken')])
def test_corrupt_input_does_not_return_success(tmp_path, name, mime, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    with pytest.raises(Exception):
        p.parse_one(path, mime)


@pytest.mark.parametrize('length,accepted', [(500000, True), (500001, False)])
def test_actual_markdown_text_boundary(tmp_path, length, accepted):
    path = tmp_path / 'source.md'
    path.write_text('字' * length, encoding='utf-8')
    if accepted:
        assert len(p.parse_one(path, 'text/markdown')['text']) == length
    else:
        rejected('text_limit', lambda: p.parse_one(path, 'text/markdown'))


@pytest.mark.parametrize('raw', [b'invalid\x00text', b'\xff\xfeinvalid'])
def test_non_utf8_or_nul_markdown_is_not_accepted(tmp_path, raw):
    path = tmp_path / 'invalid.md'
    path.write_bytes(raw)
    with pytest.raises((ControlError, UnicodeDecodeError)):
        p.parse_one(path, 'text/markdown')


def test_snapshot_keeps_exact_source_hashes_and_refuses_tampering(tmp_path):
    path = tmp_path / 'source.md'
    path.write_text('合成资料：没有外部研究结论。', encoding='utf-8')
    row = source(path)
    inventory = {'input_snapshot_sha256': 'a' * 64, 'sources': [row]}
    parsed = p.parse_inputs(tmp_path, inventory)
    assert parsed['input_snapshot_sha256'] == inventory['input_snapshot_sha256']
    assert parsed['sources'][0]['sha256'] == row['sha256']
    assert parsed['sources'][0]['text'] == path.read_text(encoding='utf-8')
    path.write_bytes(b'different bytes')
    rejected('source_hash_mismatch', lambda: p.parse_inputs(tmp_path, inventory))


@pytest.mark.parametrize('mode', ['symlink', 'hardlink', 'traversal'])
def test_inventory_cannot_read_other_paths(tmp_path, mode):
    original = tmp_path / 'original.md'
    original.write_bytes(b'synthetic')
    row = source(original)
    if mode == 'traversal':
        row['path'] = '../original.md'
    else:
        linked = tmp_path / 'linked.md'
        (linked.symlink_to(original) if mode == 'symlink' else os.link(original, linked))
        row['path'] = linked.name
    with pytest.raises((ControlError, OSError)):
        p.parse_inputs(tmp_path, {'input_snapshot_sha256': 'a' * 64, 'sources': [row]})


def test_source_count_and_project_text_budget_are_enforced(tmp_path, monkeypatch):
    path = tmp_path / 'source.md'
    path.write_bytes(b'synthetic')
    row = source(path)
    rejected('source_limit', lambda: p.parse_inputs(tmp_path, {'sources': [row] * 11}))
    monkeypatch.setattr(p, 'MAX_TOTAL_TEXT', 8)  # Explicit reduced aggregate budget.
    rejected('project_text_limit', lambda: p.parse_inputs(tmp_path, {
        'sources': [row], 'input_snapshot_sha256': 'a' * 64}))
