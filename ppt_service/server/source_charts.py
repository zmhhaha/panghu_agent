"""Pure frozen chart-binding checks shared by the site and control process.

This module uses only the standard library. It never reads source files, parses
Office documents, resolves network resources or issues confirmation authority.
Callers must supply a website-owned plan or the original immutable DB task.
"""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

MAX_WIRE = 8192
MAX_ROWS = 40
MAX_COLUMNS = 8
MAX_CHARTS = 8
SPEC_FIELDS = frozenset(('table_id', 'category_column', 'series_columns', 'slide', 'type'))
NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')


class SourceChartError(ValueError):
    def __init__(self, code='source_chart_binding_invalid'):
        self.code = code
        super().__init__(code)


def require(value, code='source_chart_binding_invalid'):
    if not value:
        raise SourceChartError(code)


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


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


def column_type(values):
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


def snapshot_body(snapshot):
    body = {key: snapshot[key] for key in ('project_id', 'title', 'settings', 'assets')}
    require(digest(body) == snapshot['hash'], 'input_snapshot_mismatch')
    require(isinstance(body['assets'], list) and len(body['assets']) <= 10)
    return body


def chart_from_table(table, spec, page_count):
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
        raise SourceChartError() from None
    provenance = {key: copy.deepcopy(table[key]) for key in
                  ('table_id', 'asset_id', 'asset_sha256', 'line_start', 'headers', 'rows')}
    provenance.update(copy.deepcopy(spec))
    return provenance, expected


def validate_direction_binding(direction, snapshot, *, page_count=None):
    """Check binding integrity; its SHA is not an Actor signature."""
    try:
        return _validate_direction_binding(direction, snapshot, page_count=page_count)
    except SourceChartError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise SourceChartError() from None


def _validate_direction_binding(direction, snapshot, *, page_count=None):
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
        table = {**chart, 'column_types': [column_type([row[n] for row in rows]) for n in range(len(headers))]}
        rebuilt, expected = chart_from_table(table, spec, page_count)
        require(rebuilt == chart)
        expectations.append(expected)
        if asset['sha256'] not in sources:
            sources.append(asset['sha256'])
    require(expectations == binding['expected_charts'])
    return {'expected_charts': copy.deepcopy(expectations), 'source_sha256': sources}
