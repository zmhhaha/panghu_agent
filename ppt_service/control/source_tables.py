"""Bounded, deterministic numeric authority from exact authorized Markdown.

The control process reads admitted originals, never Actor output, summaries or
Office documents. Plans contain references only. Frozen bindings carry the
complete source cells and the exact expectations reused by later validation.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import re
from uuid import UUID

from agent_service.contracts import canonical_bytes, digest
from ppt_service.server import source_charts
from .execution import ControlError
from .staging import open_regular

MAX_BYTES = 25 * 1024**2
MAX_TEXT = 500000
MAX_TOTAL_TEXT = 1500000
MAX_WIRE = 8192
MAX_ROWS = 40
MAX_COLUMNS = 8
MAX_TABLES = 8
MAX_CHARTS = 8
SHA = re.compile(r'[a-f0-9]{64}\Z')
SEPARATOR = re.compile(r':?-{3,}:?\Z')
RESERVED = frozenset(('source_chart_binding', 'source_table_catalog'))
SPEC_FIELDS = frozenset(('table_id', 'category_column', 'series_columns', 'slide', 'type'))


def require(value, code='source_chart_binding_invalid'):
    if not value:
        raise ControlError(code)


def plain(value, limit=160):
    return source_charts.plain(value, limit)


def number(value):
    return source_charts.number(value)


def _cells(line):
    text = line.strip()
    if '|' not in text or '\\' in text:
        return None
    if text.startswith('|'):
        text = text[1:]
    if text.endswith('|'):
        text = text[:-1]
    return [part.strip() for part in text.split('|')]


def _column(values):
    return source_charts.column_type(values)


def _tables(text, asset):
    """Recognize plain rectangular pipe tables outside fenced code blocks."""
    lines = text.splitlines(keepends=True)
    i, fence, ordinal = 0, None, 0
    while i < len(lines):
        # Four-space/tab indented code is not a Markdown table either.
        if lines[i].startswith(('    ', '\t')) and fence is None:
            i += 1
            continue
        stripped = lines[i].lstrip()
        marker = re.match(r'(`{3,}|~{3,})', stripped)
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1] and not stripped[len(run):].strip():
                fence = None
            i += 1
            continue
        if fence is not None:
            i += 1
            continue
        headers = _cells(lines[i])
        separators = _cells(lines[i + 1]) if i + 1 < len(lines) else None
        if not headers or not separators or not all(SEPARATOR.fullmatch(v) for v in separators):
            i += 1
            continue
        ordinal += 1
        start, body, invalid = i, [], None
        i += 2
        while i < len(lines) and '|' in lines[i] and lines[i].strip():
            row = _cells(lines[i])
            if row is None or len(row) != len(headers):
                invalid = 'table_not_rectangular'
            elif len(body) < MAX_ROWS + 1:
                body.append(row)
            else:
                invalid = 'table_size_limit'
            i += 1
        if (len(headers) != len(separators) or len(headers) < 2
                or len(headers) > MAX_COLUMNS or not body or len(body) > MAX_ROWS):
            invalid = invalid or 'table_size_limit'
        if len(set(headers)) != len(headers) or any(not plain(h) for h in headers):
            invalid = invalid or 'table_headers_unsupported'
        if any(not plain(cell) for row in body for cell in row):
            invalid = invalid or 'table_cells_unsupported'
        if ordinal > MAX_TABLES:
            yield None, 'table_count_limit'
            return
        if invalid:
            yield None, invalid
            continue
        types = [_column([row[n] for row in body]) for n in range(len(headers))]
        if 'number' not in types:
            yield None, 'no_supported_numeric_column'
            continue
        identity = {'schema_version': 1, 'asset_id': asset['id'], 'asset_sha256': asset['sha256'],
                    'line_start': start + 1, 'source_table_sha256': hashlib.sha256(
                        ''.join(lines[start:i]).encode('utf-8')).hexdigest()}
        yield dict(table_id=digest(identity), asset_id=asset['id'], asset_sha256=asset['sha256'],
                   source_label=f'资料表 {ordinal}', line_start=start + 1, headers=headers,
                   rows=body, column_types=types), None


def snapshot_body(snapshot):
    try:
        return source_charts.snapshot_body(snapshot)
    except source_charts.SourceChartError as exc:
        raise ControlError(exc.code) from None


def discover_tables(root: Path, snapshot):
    """Return a bounded catalog; omitted tables cannot be referenced by plans."""
    snapshot_body(snapshot)
    catalog = {'schema_version': 1, 'input_snapshot_sha256': snapshot['hash'], 'tables': [], 'unsupported': []}
    total_text, seen = 0, set()
    for asset in snapshot['assets']:
        aid = asset['id']
        try:
            require(str(UUID(aid)) == aid and aid not in seen)
        except (ValueError, TypeError, AttributeError):
            raise ControlError('source_chart_binding_invalid') from None
        seen.add(aid)
        require(isinstance(asset.get('sha256'), str) and SHA.fullmatch(asset['sha256'])
                and type(asset.get('bytes')) is int and 0 < asset['bytes'] <= MAX_BYTES)
        require(asset.get('storage_key') == f"projects/{snapshot['project_id']}/assets/{aid}/original")
        reasons = set()
        if asset['media_type'] != 'text/markdown':
            reasons.add('markdown_tables_only')
        else:
            try:
                with open_regular(root, asset['storage_key'], MAX_BYTES) as stream:
                    before = os.fstat(stream.fileno())
                    raw = stream.read(MAX_BYTES + 1)
                    after = os.fstat(stream.fileno())
                require(len(raw) == asset['bytes'] == before.st_size == after.st_size
                        and before.st_mtime_ns == after.st_mtime_ns
                        and hashlib.sha256(raw).hexdigest() == asset['sha256'], 'source_hash_mismatch')
            except OSError:
                raise ControlError('source_table_source_unavailable') from None
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeError:
                text = None
                reasons.add('table_encoding_unsupported')
            if text is not None:
                total_text += len(text)
                if '\x00' in text or len(text) > MAX_TEXT or total_text > MAX_TOTAL_TEXT:
                    reasons.add('table_text_limit')
                else:
                    found = False
                    for table, reason in _tables(text, asset):
                        found = True
                        if reason:
                            reasons.add(reason)
                            continue
                        candidate = dict(catalog, tables=catalog['tables'] + [table])
                        # Reserve space for at most ten bounded failure summaries.
                        if len(catalog['tables']) >= 32:
                            reasons.add('table_count_limit')
                        elif len(canonical_bytes(candidate)) > MAX_WIRE - 1536:
                            reasons.add('table_catalog_limit')
                        else:
                            catalog['tables'].append(table)
                    if not found:
                        reasons.add('no_supported_markdown_table')
        if reasons:
            catalog['unsupported'].append({'asset_id': aid, 'reason': sorted(reasons)[0]})
    require(len(canonical_bytes(catalog)) <= MAX_WIRE)
    return catalog


def strip_reserved(value):
    if isinstance(value, dict):
        return {key: strip_reserved(item) for key, item in value.items() if key not in RESERVED}
    if isinstance(value, list):
        return [strip_reserved(item) for item in value]
    return copy.deepcopy(value)


def _strip_display_chart_data(value):
    """Keep Actor prose/layout, remove typed numeric payloads from display.

    Original source_plan is retained separately for audit. This does not claim
    to verify every number inside free prose; ordinary fidelity review remains.
    """
    fields = {'categories', 'values', 'series', 'chart_series', 'chart_data',
              'native_chart_data', 'expected_charts', 'data_points', 'datasets'}
    if isinstance(value, dict):
        return {key: _strip_display_chart_data(item) for key, item in value.items() if key not in fields}
    if isinstance(value, list):
        return [_strip_display_chart_data(item) for item in value]
    return copy.deepcopy(value)


def _chart(table, spec, page_count):
    try:
        return source_charts.chart_from_table(table, spec, page_count)
    except source_charts.SourceChartError as exc:
        raise ControlError(exc.code) from None


def bind_direction(direction, snapshot, catalog):
    """Service-only construction, after the isolated planning projection passed."""
    require(catalog.get('input_snapshot_sha256') == snapshot['hash'])
    selected = _strip_display_chart_data(strip_reserved(direction))
    specs = selected.get('native_chart_specs', [])
    require(isinstance(specs, list) and len(specs) <= MAX_CHARTS)
    tables = {row['table_id']: row for row in catalog['tables']}
    page_plan = selected.get('page_plan')
    pages = page_plan.get('pages') if isinstance(page_plan, dict) else page_plan
    require(isinstance(pages, list) and 3 <= len(pages) <= 20)
    binding = {'schema_version': 1, 'input_snapshot_sha256': snapshot['hash'],
               'direction_id': selected['id'], 'charts': [], 'expected_charts': []}
    for spec in specs:
        require(isinstance(spec, dict) and isinstance(spec.get('table_id'), str))
        table = tables.get(spec['table_id'])
        require(table is not None)
        chart, expected = _chart(table, spec, len(pages))
        binding['charts'].append(chart)
        binding['expected_charts'].append(expected)
    require(len({digest(spec) for spec in specs}) == len(specs))
    binding['binding_sha256'] = digest(binding)
    require(len(canonical_bytes(binding)) <= MAX_WIRE)
    selected['source_chart_binding'] = binding
    return selected


def bind_planning(design, root, snapshot, *, catalog=None):
    """Discard Actor authority everywhere and replace each direction binding."""
    clean = strip_reserved(design)
    source_plan = clean.pop('source_plan', None)
    clean = _strip_display_chart_data(clean)
    if source_plan is not None:
        clean['source_plan'] = source_plan
    catalog = discover_tables(root, snapshot) if catalog is None else catalog
    require(isinstance(clean.get('directions'), list))
    clean['directions'] = [bind_direction(row, snapshot, catalog) for row in clean['directions']]
    return clean


def validate_direction_binding(direction, snapshot, *, page_count=None):
    try:
        return source_charts.validate_direction_binding(direction, snapshot, page_count=page_count)
    except source_charts.SourceChartError as exc:
        raise ControlError(exc.code) from None
