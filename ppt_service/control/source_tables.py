"""Bounded, deterministic numeric authority from exact authorized Markdown.

The control process reads admitted originals, never Actor output, summaries or
Office documents. Plans contain references only. Frozen bindings carry the
complete source cells and the exact expectations reused by later validation.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import hashlib
import os
from pathlib import Path
import re
from uuid import UUID

from agent_service.contracts import canonical_bytes, digest
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
NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
SEPARATOR = re.compile(r':?-{3,}:?\Z')
RESERVED = frozenset(('source_chart_binding', 'source_table_catalog'))
SPEC_FIELDS = frozenset(('table_id', 'category_column', 'series_columns', 'slide', 'type'))


def require(value, code='source_chart_binding_invalid'):
    if not value:
        raise ControlError(code)


def plain(value, limit=160):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and not any(ord(c) < 32 or ord(c) == 127 or c in '|\\`<>[]' for c in value))


def number(value):
    """No inferred units, separators, formulas, missing data or rounding."""
    if not isinstance(value, str) or len(value) > 64 or not NUMBER.fullmatch(value):
        raise ValueError('unsupported_numeric_cell')
    try:
        parsed = Decimal(value)
        digits, exponent = parsed.as_tuple().digits, parsed.as_tuple().exponent
        significant = len(''.join(map(str, digits)).lstrip('0')) or 1
        explicit_exponent = int(value.lower().split('e')[1]) if 'e' in value.lower() else 0
        if (not parsed.is_finite() or significant > 15 or abs(exponent) > 30
                or abs(explicit_exponent) > 12 or abs(parsed) > Decimal('1e12')
                or parsed != 0 and abs(parsed) < Decimal('1e-12')):
            raise ValueError('numeric_precision_limit')
        return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)
    except (InvalidOperation, OverflowError):
        raise ValueError('unsupported_numeric_cell') from None


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
    numeric = []
    for cell in values:
        try:
            number(cell)
            numeric.append(True)
        except ValueError:
            numeric.append(False)
    if all(numeric):
        return 'number'
    if any(numeric) or any(not plain(cell) or cell[:1] in '=+@'
            or re.match(r'^[+-]?[0-9]+[,%]', cell) for cell in values):
        return 'unsupported'
    return 'text'


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
    body = {key: snapshot[key] for key in ('project_id', 'title', 'settings', 'assets')}
    require(digest(body) == snapshot['hash'], 'input_snapshot_mismatch')
    require(isinstance(body['assets'], list) and len(body['assets']) <= 10)
    return body


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
                        if len(canonical_bytes(candidate)) > MAX_WIRE - 1536:
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


def _chart(table, spec, page_count):
    require(isinstance(spec, dict) and set(spec) == SPEC_FIELDS)
    require(spec.get('table_id') == table['table_id'])
    category, series = spec.get('category_column'), spec.get('series_columns')
    require(type(category) is int and 0 <= category < len(table['headers'])
            and isinstance(series, list) and 1 <= len(series) <= 4
            and all(type(col) is int and 0 <= col < len(table['headers']) and col != category for col in series)
            and len(set(series)) == len(series))
    require(type(spec.get('slide')) is int and 1 <= spec['slide'] <= page_count
            and spec.get('type') in ('column', 'line', 'pie')
            and (spec['type'] != 'pie' or len(series) == 1))
    require(all(table['column_types'][col] == 'number' for col in series)
            and table['column_types'][category] != 'unsupported')
    categories = [row[category] for row in table['rows']]
    require(len(set(categories)) == len(categories))
    try:
        expected = {'slide': spec['slide'], 'type': spec['type'], 'categories': categories,
                    'series': [{'name': table['headers'][col], 'values': [number(row[col]) for row in table['rows']]}
                               for col in series]}
    except ValueError:
        raise ControlError('source_chart_binding_invalid') from None
    provenance = {key: copy.deepcopy(table[key]) for key in
                  ('table_id', 'asset_id', 'asset_sha256', 'line_start', 'headers', 'rows')}
    provenance.update(copy.deepcopy(spec))
    return provenance, expected


def bind_direction(direction, snapshot, catalog):
    """Service-only construction, after the isolated planning projection passed."""
    require(catalog.get('input_snapshot_sha256') == snapshot['hash'])
    selected = strip_reserved(direction)
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
    catalog = discover_tables(root, snapshot) if catalog is None else catalog
    require(isinstance(clean.get('directions'), list))
    clean['directions'] = [bind_direction(row, snapshot, catalog) for row in clean['directions']]
    return clean


def validate_direction_binding(direction, snapshot, *, page_count=None):
    try:
        return _validate_direction_binding(direction, snapshot, page_count=page_count)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ControlError('source_chart_binding_invalid') from None


def _validate_direction_binding(direction, snapshot, *, page_count=None):
    """Resolve only DB/outbox direction metadata, never current source or mappings.

    A digest is a corruption/binding check, not an Actor signature. Callers must
    supply the website-owned plan or immutable DB task, never a candidate plan.
    """
    snapshot_body(snapshot)
    specs = direction.get('native_chart_specs', [])
    binding = direction.get('source_chart_binding')
    require(isinstance(specs, list) and len(specs) <= MAX_CHARTS)
    require(len({digest(spec) for spec in specs}) == len(specs))
    if binding is None:
        require(not specs, 'source_chart_binding_required')
        return {'expected_charts': [], 'source_sha256': []}
    require(isinstance(binding, dict) and set(binding) == {
        'schema_version', 'input_snapshot_sha256', 'direction_id', 'charts', 'expected_charts', 'binding_sha256'})
    require(type(binding['schema_version']) is int and binding['schema_version'] == 1
            and binding['input_snapshot_sha256'] == snapshot['hash'] and binding['direction_id'] == direction.get('id')
            and isinstance(binding['charts'], list) and len(binding['charts']) == len(specs)
            and isinstance(binding['expected_charts'], list) and len(binding['expected_charts']) == len(specs)
            and len(canonical_bytes(binding)) <= MAX_WIRE
            and binding['binding_sha256'] == digest({k: v for k, v in binding.items() if k != 'binding_sha256'}))
    page_count = snapshot['settings'].get('page_count', 8) if page_count is None else page_count
    require(type(page_count) is int and 3 <= page_count <= 20)
    assets = {row['id']: row for row in snapshot['assets']}
    sources, expectations = [], []
    for chart, spec in zip(binding['charts'], specs):
        require(isinstance(chart, dict) and set(chart) == SPEC_FIELDS | {
            'asset_id', 'asset_sha256', 'line_start', 'headers', 'rows'})
        asset = assets.get(chart['asset_id'])
        require(asset is not None and asset['sha256'] == chart['asset_sha256']
                and asset['media_type'] == 'text/markdown'
                and type(chart['line_start']) is int and chart['line_start'] >= 1)
        headers, rows = chart['headers'], chart['rows']
        require(isinstance(headers, list) and 2 <= len(headers) <= MAX_COLUMNS
                and all(plain(h) for h in headers) and len(set(headers)) == len(headers)
                and isinstance(rows, list) and 1 <= len(rows) <= MAX_ROWS
                and all(isinstance(row, list) and len(row) == len(headers)
                        and all(plain(cell) for cell in row) for row in rows))
        table = {**chart, 'column_types': [_column([row[n] for row in rows]) for n in range(len(headers))]}
        rebuilt, expected = _chart(table, spec, page_count)
        require(rebuilt == chart)
        expectations.append(expected)
        if asset['sha256'] not in sources:
            sources.append(asset['sha256'])
    require(expectations == binding['expected_charts'])
    return {'expected_charts': copy.deepcopy(expectations), 'source_sha256': sources}
