"""Exercise the real bounded extension without a model or network request."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from agent_service import animation_wikiskill_contract as C
from agent_service import compilation_input_tools as tool
from agent_service.contracts import canonical_bytes, digest
from agent_service.registry import ToolEnvironment

NODE = shutil.which('node')
RUN = '''
const mod = await import(process.argv[1]);
let tool;
mod.default({registerTool(value){tool=value;}});
const output=[];
for(const params of JSON.parse(process.argv[2])) {
  try { const result=await tool.execute('offline-call',params,
      JSON.parse(process.argv[3]) ? AbortSignal.abort() : new AbortController().signal);
    output.push({ok:true,name:tool.name,result});
  } catch(error) { output.push({ok:false,error:error.message}); }
}
process.stdout.write(JSON.stringify(output));
'''


def execute(root, calls, *, aborted=False):
    if NODE is None:
        pytest.skip('Node unavailable')
    run = subprocess.run([NODE, '--input-type=module', '-e', RUN,
        tool.COMPILATION_INPUT_EXTENSION.resolve().as_uri(), json.dumps(calls), json.dumps(aborted)],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30,
        env={key: value for key, value in os.environ.items()
             if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}})
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def stage(tmp_path, *, count=3, content='原始证据🙂' * 25000):
    snapshot = C.make_snapshot('fixture-owner', [dict(record_id=f'case-{i:04}', kind='evidence',
        content=dict(text=content if i == 0 else f'不改写的第{i}个记录'))
        for i in range(count)], captured_at=1000.)
    document = C.build_input(snapshot)
    files = C.input_files(snapshot, document)
    root = tmp_path / 'attempt'
    for name, raw in files.items():
        file = root / 'inputs' / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
    return root, document, files


def request(document, mode='manifest', **params):
    return dict(mode=mode, input_sha256=digest(document), **params)


def body(value):
    assert value['ok'] is True, value
    assert value['name'] == 'read_compilation_input'
    assert len(value['result']['content'][0]['text'].encode()) <= 32768
    assert value['result']['details']['maximumReplyBytes'] == 32768
    return json.loads(value['result']['content'][0]['text'])


def test_manifest_larger_than_builtin_single_line_limit_is_paginated_losslessly(tmp_path):
    root, document, files = stage(tmp_path, count=650)
    raw = files[C.INPUT_NAME]
    assert len(raw) > 143 * 1024 and b'\n' not in raw
    rows, offset = [], 0
    while offset is not None:
        value = body(execute(root, [request(document, offset=offset, limit=128)])[0])
        assert value['snapshot'] == document['snapshot']
        assert value['input_sha256'] == hashlib.sha256(raw).hexdigest()
        assert value['input_bytes'] == len(raw)
        assert value['offset'] == len(rows)
        rows.extend(value['records'])
        offset = value['next_offset']
    assert rows == document['records']
    assert (root / 'inputs' / C.INPUT_NAME).read_bytes() == raw


def test_large_utf8_record_ranges_reassemble_exact_canonical_bytes(tmp_path):
    root, document, files = stage(tmp_path)
    name = 'records/000000.json'
    assert len(files[name]) > 50 * 1024 and b'\n' not in files[name]
    pieces, offset = [], 0
    while offset is not None:
        part = body(execute(root, [request(document, 'file', file_name=name,
            offset_bytes=offset, max_bytes=16384)])[0])
        assert part['offset_bytes'] == sum(map(len, pieces))
        assert part['file_sha256'] == hashlib.sha256(files[name]).hexdigest()
        pieces.append(part['text'].encode())
        offset = part['next_offset_bytes']
    assert b''.join(pieces) == files[name]


def test_explicit_batch_returns_original_records_and_partial_record_continuation(tmp_path):
    root, document, files = stage(tmp_path)
    names = list(files)[1:]
    result = body(execute(root, [request(document, 'batch', file_names=names)])[0])
    assert result['next_file_index'] is None
    assert [row['file_name'] for row in result['files']] == names
    assert result['files'][0]['next_offset_bytes'] > 0
    for part in result['files']:
        raw = files[part['file_name']]
        assert raw.startswith(part['text'].encode())
        assert part['next_offset_bytes'] == (len(part['text'].encode()) if len(raw) > 8192 else None)


def test_batch_does_not_dump_large_record_set_and_exposes_unreturned_index(tmp_path):
    root, document, files = stage(tmp_path, count=32, content='x' * 200000)
    # Make all records large enough to force the total-response bound.
    for i in range(1, 32):
        raw = canonical_bytes(dict(record_id=f'case-{i:04}', kind='evidence',
            content={'text':'x' * 9000}, sha256=digest({'text':'x' * 9000})))
        (root / 'inputs' / f'records/{i:06}.json').write_bytes(raw)
        row = document['records'][i]
        row.update(bytes=len(raw), file_sha256=hashlib.sha256(raw).hexdigest(),
                   sha256=digest({'text':'x' * 9000}))
    (root / 'inputs' / C.INPUT_NAME).write_bytes(canonical_bytes(document))
    result = body(execute(root, [request(document, 'batch',
        file_names=[row['file_name'] for row in document['records']])])[0])
    assert 0 < result['next_file_index'] < 32
    assert len(result['files']) == result['next_file_index']


@pytest.mark.parametrize('params', [
    dict(mode='manifest', offset=0, limit=0), dict(mode='manifest', offset=-1, limit=16),
    dict(mode='manifest', offset=True, limit=16), dict(mode='manifest', offset=0, limit=129),
    dict(mode='manifest', offset=0, limit=16, path='/etc/passwd'),
    dict(mode='file', file_name='../secret.json', offset_bytes=0, max_bytes=8),
    dict(mode='file', file_name='records/999999.json', offset_bytes=0, max_bytes=8),
    dict(mode='file', file_name='animation-wikiskill-input.json', offset_bytes=0, max_bytes=8),
    dict(mode='file', file_name='records/000000.json', offset_bytes=0, max_bytes=32768),
    dict(mode='batch', file_names=['records/000000.json'] * 2),
    dict(mode='batch', file_names=['../secret.json']), dict(mode='batch', file_names=[]),
])
def test_invalid_scope_unknown_files_and_unbounded_requests_are_rejected(tmp_path, params):
    root, document, _ = stage(tmp_path, content='small')
    assert execute(root, [dict(input_sha256=digest(document), **params)])[0]['ok'] is False


@pytest.mark.parametrize('changed', ['manifest', 'record', 'hardlink'])
def test_changed_or_linked_evidence_is_rejected(tmp_path, changed):
    root, document, _ = stage(tmp_path, content='small')
    name = C.INPUT_NAME if changed == 'manifest' else 'records/000000.json'
    file = root / 'inputs' / name
    if changed == 'hardlink':
        try:
            os.link(file, root / 'linked.json')
        except OSError:
            pytest.skip('Hardlinks unavailable')
    else:
        file.write_bytes(file.read_bytes() + b' ')
    params = request(document, 'file', file_name='records/000000.json', offset_bytes=0, max_bytes=8)
    assert execute(root, [params])[0]['ok'] is False


def test_mid_codepoint_offsets_and_cancelled_calls_fail_without_reading(tmp_path):
    root, document, files = stage(tmp_path, content='中文🙂')
    raw = files['records/000000.json']
    middle = next(i for i, value in enumerate(raw) if value & 0xc0 == 0x80)
    params = request(document, 'file', file_name='records/000000.json', offset_bytes=middle, max_bytes=8)
    assert execute(root, [params])[0]['ok'] is False
    assert execute(root, [request(document, offset=0, limit=16)], aborted=True) == [
        dict(ok=False, error='compilation_input_cancelled')]


def environment(**changes):
    data = dict(name=tool.COMPILATION_INPUT_ENVIRONMENT_NAME,
        tools=['read_compilation_input', 'write'], capabilities=['video-animation-wikiskill-compile-output.v1'],
        image_digest='sha256:' + 'a' * 64, runtime_revision='fixture-v1')
    data['revision'] = tool.compilation_input_environment_revision(data)
    return ToolEnvironment.model_validate(dict(data, **changes))


def test_verified_extension_and_exact_environment_bind_image_runtime_and_bytes(monkeypatch):
    assert hashlib.sha256(tool.COMPILATION_INPUT_EXTENSION.read_bytes()).hexdigest() == tool.COMPILATION_INPUT_EXTENSION_SHA256
    assert tool.verified_compilation_input_extension() == tool.COMPILATION_INPUT_EXTENSION
    original = environment().model_dump(mode='json')
    for changes in [dict(name='arbitrary'), dict(tools=['read_compilation_input','write','bash']),
                    dict(image_digest='sha256:' + 'b'*64), dict(runtime_revision='other'), dict(revision='fake')]:
        with pytest.raises(ValueError):
            environment(**changes)
    monkeypatch.setattr(tool, '_read', lambda *args:b'changed extension')
    with pytest.raises(ValueError, match='extension_integrity'):
        tool.verified_compilation_input_extension()
    assert original['revision'].startswith('compile-' + tool.COMPILATION_INPUT_EXTENSION_SHA256)


def test_domain_uses_only_fixed_reader_and_write(monkeypatch):
    from agent_service import domain_runtime
    monkeypatch.setattr(domain_runtime, 'implementation', lambda name, revision='1':SimpleNamespace(
        required_tools=('read_compilation_input','write')))
    profile = domain_runtime.DomainExecutionProfile(SimpleNamespace(name='fixture.v1', revision='1'), 'fixture',
        extensions=(tool.COMPILATION_INPUT_EXTENSION,))
    assert profile.allowed_tools == ('read_compilation_input','write')
    assert profile.extension_paths == (tool.COMPILATION_INPUT_EXTENSION,)
