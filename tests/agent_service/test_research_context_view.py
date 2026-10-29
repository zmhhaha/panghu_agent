"""Bounded Pi reads preserve the complete frozen context and source identity."""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_service.research_context_view import (MAX_CHUNK_LINES, MAX_CHUNK_UTF8,
    build_context_view)
from agent_service import research_context_view as views
from agent_service.contracts import ServiceError
from scientific_agent import workflow_contracts as contracts
from test_research_workflow import context, outline_context, task, uid, STAMP


def render(ctx, stage='plan'):
    raw = contracts.canonical_bytes(ctx)
    files = build_context_view(ctx, task(stage, ctx), input_bytes_sha256=hashlib.sha256(raw).hexdigest())
    return raw, files


def restore(value, files):
    if type(value) is dict and set(value) in ({'$readable_string'}, {'$readable_json'}):
        encoded_json = '$readable_json' in value
        descriptor = value['$readable_json' if encoded_json else '$readable_string']
        chunks = []
        for row in descriptor['chunks']:
            raw = files[row['path']]
            assert len(raw) == row['utf8_bytes']
            assert hashlib.sha256(raw).hexdigest() == row['sha256']
            # Each individual chunk fits both native Pi read bounds.
            assert len(raw) <= MAX_CHUNK_UTF8 < 50 * 1024
            assert len(raw.splitlines()) <= MAX_CHUNK_LINES < 2000
            raw.decode('utf-8', errors='strict')
            chunks.append(raw)
        combined = b''.join(chunks)
        assert len(combined) == descriptor['utf8_bytes']
        assert hashlib.sha256(combined).hexdigest() == descriptor['sha256']
        return json.loads(combined) if encoded_json else combined.decode('utf-8')
    if type(value) is dict:
        return {key: restore(row, files) for key, row in value.items()}
    if type(value) is list:
        return [restore(row, files) for row in value]
    return value


def material(text):
    return dict(material_id=uid(120), name='Exact provided UTF-8 material', kind='text',
        sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(), text=text,
        access_level='full_text', doi=None, status='ready', parse_error=None, recorded_at=STAMP)


def paginated_pi_reads(raw):
    """Model native line offsets under both bounds, without byte-offset tools."""
    lines = raw.splitlines(keepends=True)
    offset = 0
    while offset < len(lines):
        page, size = [], 0
        for line in lines[offset:offset + 2000]:
            if size + len(line) > 50 * 1024:
                break
            page.append(line)
            size += len(line)
        assert page, 'A single JSON line cannot be read by the admitted tool'
        offset += len(page)
        yield b''.join(page)


@pytest.mark.parametrize('text', [
    ('中é🙂abc\r\n\t' * 250000)[:2 * 1024 * 1024],
    '\n' * (2 * 1024 * 1024),
], ids=['multibyte-max-material', 'newline-max-material'])
def test_large_compact_input_and_two_megachar_scalar_are_lossless_under_pi_bounds(text):
    ctx = context()
    ctx['materials'] = [material(text)]
    # Full-text material must include some non-whitespace content.
    ctx['materials'][0]['text'] = 'Start' + text[:-5]
    before = deepcopy(ctx)
    raw, files = render(ctx)
    assert len(raw) > 50 * 1024 and len(raw.splitlines()) == 1
    assert ctx == before and contracts.canonical_bytes(ctx) == raw
    # Every structured line can be paginated by Pi without byte offsets/bash.
    for path, data in files.items():
        if path.endswith(('.json', '.jsonl')):
            assert all(len(line) < 16 * 1024 for line in data.splitlines())
            assert b''.join(paginated_pi_reads(data)) == data
    rebuilt = restore(json.loads(files['context.json']), files)
    assert rebuilt == ctx and contracts.digest(rebuilt) == task('plan', ctx)['context_sha256']
    start = json.loads(files['START.json'])
    assert start['authoritative_context']['input_bytes_sha256'] == hashlib.sha256(raw).hexdigest()
    assert start['read_order'] == ['brief.json', 'materials/index.jsonl']
    manifest = json.loads(files['manifest.json'])
    for entry in manifest['files']:
        data = files[entry['path']]
        assert len(data) == entry['utf8_bytes']
        assert hashlib.sha256(data).hexdigest() == entry['sha256']


def test_outline_index_exposes_only_included_latest_complete_record_and_retains_history():
    ctx = outline_context()
    snapshot = ctx['evidence_snapshot']
    first = snapshot['search_batches'][0]
    second = deepcopy(first)
    second.update(batch_id=uid(121), attempt_id=uid(122))
    second['observations']['attempt_id'] = second['attempt_id']
    second['papers'][0]['title'] = 'The later complete observed record'
    second['observations']['observations'][0]['response']['papers'][0]['title'] = second['papers'][0]['title']
    snapshot['search_batches'].append(second)
    ctx['materials'] = [material('Included provided text')]
    snapshot['materials'] = deepcopy(ctx['materials'])
    snapshot['selections'].append(dict(source_id=uid(120), included=False, reason='Explicit exclusion'))
    _, files = render(ctx, 'outline')
    index = [json.loads(line) for line in files['selected-sources/index.jsonl'].splitlines()]
    assert len(index) == 1
    assert index[0]['source_id'] == first['papers'][0]['paper_id']
    assert index[0]['json_pointer'] == '/evidence_snapshot/search_batches/1/papers/0'
    assert json.loads(files[index[0]['path']]) == second['papers'][0]
    rebuilt = restore(json.loads(files['context.json']), files)
    assert rebuilt == ctx
    assert rebuilt['evidence_snapshot']['search_batches'][0]['papers'][0] == first['papers'][0]
    assert rebuilt['materials'][0]['text'] == 'Included provided text'
    assert json.loads(files['START.json'])['read_order'] == ['brief.json', 'selected-sources/index.jsonl']


def test_view_is_deterministic_and_does_not_upgrade_an_abstract_to_full_text():
    ctx = outline_context()
    _, first = render(ctx, 'outline')
    _, second = render(ctx, 'outline')
    assert first == second
    row = json.loads(first['selected-sources/index.jsonl'])
    paper = json.loads(first[row['path']])
    assert paper['availability']['abstract']['available'] is True
    assert paper['availability']['full_text']['available'] is False
    assert paper['full_text_path'] is None


def test_outline_source_with_a_single_abstract_over_native_read_limit_is_fully_readable():
    ctx = outline_context()
    batch = ctx['evidence_snapshot']['search_batches'][0]
    abstract = 'A bounded synthetic abstract. 中文🙂\n' * 3000
    batch['papers'][0]['abstract'] = abstract
    batch['observations']['observations'][0]['response']['papers'][0]['abstract'] = abstract
    raw, files = render(ctx, 'outline')
    assert len(raw) > 50 * 1024 and len(raw.splitlines()) == 1
    index = json.loads(files['selected-sources/index.jsonl'])
    source = json.loads(b''.join(paginated_pi_reads(files[index['path']])))
    assert restore(source, files) == batch['papers'][0]
    assert restore(source, files)['abstract'] == abstract
    assert restore(json.loads(files['context.json']), files) == ctx


def test_view_rejects_wrong_frozen_digest_before_producing_files():
    ctx = context()
    request = task('plan', ctx)
    ctx['requirements']['question'] = 'Changed after admission'
    with pytest.raises(contracts.WorkflowContractError, match='context_mismatch'):
        build_context_view(ctx, request, input_bytes_sha256='0' * 64)


def test_view_rejects_the_existing_paper_material_identity_collision():
    ctx = outline_context()
    snapshot = ctx['evidence_snapshot']
    batch = snapshot['search_batches'][0]
    identifier = uid(120)
    batch['papers'][0]['paper_id'] = identifier
    batch['observations']['observations'][0]['response']['papers'][0]['paper_id'] = identifier
    snapshot['selections'][0]['source_id'] = identifier
    ctx['materials'] = [material('A different source with the colliding identifier')]
    snapshot['materials'] = deepcopy(ctx['materials'])
    contracts.validate_context(ctx, task('outline', ctx))
    with pytest.raises(contracts.WorkflowContractError, match='workflow_source_collision'):
        render(ctx, 'outline')


def with_opaque_notes(value):
    ctx = outline_context()
    ctx['evidence_snapshot']['search_batches'][0]['observations']['observations'][0]['response']['notes'] = value
    return ctx


def test_opaque_long_keys_and_literal_reserved_tags_are_lossless_and_unambiguous():
    value = {'x' * 60000: {'$readable_string': {'chunks': [{'path': '../../untrusted'}]}}}
    ctx = with_opaque_notes(value)
    _, files = render(ctx, 'outline')
    assert restore(json.loads(files['context.json']), files) == ctx
    manifest = json.loads(files['manifest.json'])
    assert manifest['encoded_values']
    assert all(len(row['json_pointer'].encode()) <= views.MAX_POINTER_UTF8
               for row in manifest['strings'] + manifest['encoded_values'])
    assert all(len(line) < 16 * 1024 for path, raw in files.items() if path.endswith(('.json', '.jsonl'))
               for line in raw.splitlines())
    assert all(path.startswith(('strings/', 'encoded-json/'))
        for row in manifest['strings'] + manifest['encoded_values'] for path in [chunk['path'] for chunk in row['chunks']])


def test_deep_opaque_json_and_many_small_elements_do_not_amplify_indentation():
    value = [0] * 85000
    for _ in range(400):
        value = {'nested': value}
    ctx = with_opaque_notes(value)
    raw, files = render(ctx, 'outline')
    assert len(raw) < 200000
    assert sum(map(len, files.values())) < 500000
    assert len(files['context.json']) < 50000
    assert restore(json.loads(files['context.json']), files) == ctx


@pytest.mark.parametrize('limit_name,limit', [
    ('MAX_DERIVED_FILE_BYTES', 100), ('MAX_DERIVED_BYTES', 200), ('MAX_DERIVED_FILES', 1),
])
def test_derived_limits_fail_during_rendering_before_inline_file_encoding(monkeypatch, limit_name, limit):
    monkeypatch.setattr(views, limit_name, limit)
    with pytest.raises(ServiceError) as failure:
        render(context())
    assert failure.value.code == 'research_context_view_limit'
