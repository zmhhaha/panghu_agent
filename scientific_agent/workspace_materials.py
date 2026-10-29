"""Bounded user-provided research material parsing, with honest access labels.

Only supplied bytes are parsed. DOI registration never fetches an arbitrary URL,
and a failed/scanned PDF is never labelled as available full text.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import PurePath
import re
import subprocess
import sys
from uuid import uuid4

from .pi_contracts import utc_now

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_PDF_PAGES = 100


class MaterialError(ValueError):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


def _text(raw):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError:
        raise MaterialError('material_utf8_required') from None
    if '\x00' in text or any(ord(c) < 32 and c not in '\n\t\r' for c in text):
        raise MaterialError('material_not_text')
    return text


def _pdf_core(raw):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise MaterialError('pdf_parser_unavailable') from None
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted:
        raise MaterialError('encrypted_pdf_unsupported')
    if len(reader.pages) > MAX_PDF_PAGES:
        raise MaterialError('pdf_page_limit', 413)
    chunks, total = [], 0
    for page in reader.pages:
        value = page.extract_text() or ''
        total += len(value.encode('utf-8'))
        if total > MAX_TEXT_BYTES:
            raise MaterialError('material_text_limit', 413)
        chunks.append(value)
    text = '\n\n'.join(chunks)
    if not text.strip():
        raise MaterialError('pdf_has_no_extractable_text')
    return dict(text=text, page_count=len(reader.pages))


def _pdf(raw):
    # A malformed compressed PDF must not consume the API's memory or block its
    # request thread indefinitely. Production runs this child on Linux with a
    # memory/CPU limit, and every platform applies the wall-clock deadline.
    try:
        completed = subprocess.run([sys.executable, '-m', 'scientific_agent.workspace_materials',
            '--parse-pdf'], input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=12, check=False, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
    except subprocess.TimeoutExpired:
        raise MaterialError('pdf_parse_timeout') from None
    if completed.returncode != 0 or len(completed.stdout) > MAX_TEXT_BYTES * 6 + 1024:
        raise MaterialError('pdf_parse_limit')
    try:
        value = json.loads(completed.stdout)
    except (ValueError, UnicodeError):
        raise MaterialError('pdf_parse_failed') from None
    if value.get('error'):
        raise MaterialError(value['error'])
    if type(value.get('text')) is not str or type(value.get('page_count')) is not int:
        raise MaterialError('pdf_parse_failed')
    return value


def parse_material(filename, raw, *, material_id=None):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_UPLOAD_BYTES:
        raise MaterialError('material_upload_limit', 413)
    filename = str(filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    if not filename or len(filename) > 160 or any(ord(c) < 32 for c in filename):
        raise MaterialError('invalid_material_name')
    extension = PurePath(filename).suffix.lower()
    if extension not in {'.md', '.markdown', '.txt', '.pdf'}:
        raise MaterialError('material_type_unsupported')
    material = dict(material_id=material_id or str(uuid4()), name=filename,
        kind={'.md': 'markdown', '.markdown': 'markdown', '.txt': 'text', '.pdf': 'pdf'}[extension],
        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), text='',
        access_level='unavailable', status='failed', parse_error=None,
        recorded_at=utc_now(), included=True, excluded=False)
    try:
        if extension == '.pdf':
            if not raw.startswith(b'%PDF-'):
                raise MaterialError('invalid_pdf')
            parsed = _pdf(raw)
            text = parsed['text']
            material['page_count'] = parsed['page_count']
        else:
            text = _text(raw)
        if len(text.encode('utf-8')) > MAX_TEXT_BYTES:
            raise MaterialError('material_text_limit', 413)
        if not text.strip():
            raise MaterialError('material_has_no_text')
        material.update(text=text, access_level='full_text', status='ready',
                        text_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
    except MaterialError as exc:
        material['parse_error'] = exc.code
    except Exception:
        # Parser diagnostics can contain local paths or extracted private text.
        material['parse_error'] = 'pdf_parse_failed'
    return material


def doi_material(value, *, material_id=None):
    if not isinstance(value, str):
        raise MaterialError('invalid_doi')
    value = value.strip()
    for prefix in ('https://doi.org/', 'http://doi.org/', 'doi:'):
        if value.lower().startswith(prefix):
            value = value[len(prefix):].strip()
            break
    if not re.fullmatch(r'10\.\d{4,9}/[^\s<>"\\]{1,180}', value, re.I):
        raise MaterialError('invalid_doi')
    recorded = 'DOI: ' + value
    return dict(material_id=material_id or str(uuid4()), name=recorded, kind='doi',
        sha256=hashlib.sha256(recorded.encode('utf-8')).hexdigest(), doi=value,
        text='', size=0, access_level='metadata', status='metadata_only',
        parse_error=None, recorded_at=utc_now(), included=True, excluded=False,
        note='Identifier supplied by user; bibliographic metadata and full text have not been fetched.')


if __name__ == '__main__' and sys.argv[1:] == ['--parse-pdf']:
    try:
        if os.name == 'posix':
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        supplied = sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1)
        if len(supplied) > MAX_UPLOAD_BYTES:
            raise MaterialError('material_upload_limit')
        result = _pdf_core(supplied)
    except MaterialError as exc:
        result = {'error': exc.code}
    except Exception:
        result = {'error': 'pdf_parse_failed'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
