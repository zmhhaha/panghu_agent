"""Fixed-path author feedback prevents late-only structural rejection."""
from copy import deepcopy
import hashlib
import json
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service import animation_wikiskill_contract as C
from agent_service import compilation_input_tools as tool
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_wikiskill_v4 as P
from test_wikiskill_compiler_v3 import compact


def stage(root):
    snapshot = C.make_snapshot('fixture', [dict(record_id=f'fact-{i}', kind='method',
        content=dict(observed='Keep stable ownership', motion_verified=False)) for i in range(4)], captured_at=1000.)
    document = C.build_input(snapshot)
    files = C.input_files(snapshot, document)
    for name, raw in files.items():
        path = root / 'inputs' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return document, files


def check(root, document, params=None):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    source = '''
const m=await import(process.argv[1]);const tools=new Map();
m.default({registerTool(t){tools.set(t.name,t);}});
try {
  const reply=await tools.get('check_compilation_output').execute('offline',JSON.parse(process.argv[2]),new AbortController().signal);
  process.stdout.write(JSON.stringify({tools:[...tools.keys()],report:JSON.parse(reply.content[0].text)}));
}catch(e){process.stdout.write(JSON.stringify({error:e.message}));}
'''
    run = subprocess.run([node, '--input-type=module', '-e', source,
        tool.COMPILATION_CHECKED_EXTENSION.resolve().as_uri(),
        json.dumps(params if params is not None else dict(input_sha256=digest(document)))],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_agent_can_check_and_self_correct_without_host_repair(tmp_path):
    document, files = stage(tmp_path)
    output = compact(document)
    output['index'][0].pop('source_record_ranges')
    output['index'][0]['path'] = 'invented/SKILL.md'
    raw = canonical_bytes(output)
    target = tmp_path / 'output.json'
    target.write_bytes(raw)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    failed = check(tmp_path, document)
    assert failed['tools'] == ['read_compilation_input', 'check_compilation_output']
    assert not failed['report']['valid']
    assert any(row['path'] == '$.index[0]' for row in failed['report']['errors'])
    assert failed['report']['checked_output_sha256'] == hashlib.sha256(raw).hexdigest()
    assert failed['report']['read_only'] is True
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    # This is the Agent's next write, never a checker repair or a trusted rewrite.
    target.write_bytes(canonical_bytes(compact(document)))
    passed = check(tmp_path, document)['report']
    assert passed['valid'] and passed['errors'] == []
    assert passed['counts']['used'] == 1 and passed['counts']['excluded'] == 3
    assert all((tmp_path / 'inputs' / name).read_bytes() == raw for name, raw in files.items())


@pytest.mark.parametrize('raw,code', [(None, 'output_json_missing'), (b'{}', 'exact_top_level_fields_required'),
    (b'not JSON', 'output_json_invalid_utf8_or_json'), (b'\xff', 'output_json_invalid_utf8_or_json'),
    (b'', 'output_json_unavailable_unsafe_or_size_limit')])
def test_missing_or_invalid_output_reports_actionable_error(tmp_path, raw, code):
    document, _ = stage(tmp_path)
    if raw is not None:
        (tmp_path / 'output.json').write_bytes(raw)
    report = check(tmp_path, document)['report']
    assert not report['valid'] and any(row['code'] == code for row in report['errors'])


@pytest.mark.parametrize('params', [dict(input_sha256='0' * 64), dict(input_sha256='bad'),
    dict(input_sha256='0' * 64, path='../outside.json'), {}, dict(input_sha256=True)])
def test_check_has_exact_hash_and_fixed_path_scope(tmp_path, params):
    document, _ = stage(tmp_path)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(compact(document)))
    assert check(tmp_path, document, params)['error'].startswith('compilation_check_')


def test_linked_output_is_rejected_without_following_it(tmp_path):
    document, _ = stage(tmp_path)
    original = tmp_path / 'do-not-read.json'
    original.write_bytes(canonical_bytes(compact(document)))
    try:
        (tmp_path / 'output.json').symlink_to(original)
    except OSError:
        pytest.skip('Host disallows symlink creation')
    report = check(tmp_path, document)['report']
    assert report['errors'] == [dict(path='$', code='output_json_unavailable_unsafe_or_size_limit')]


def test_v4_pins_entire_import_closure_and_retains_older_reader_bytes():
    assert tool.verified_compilation_input_extension(tool.COMPILATION_CHECKED_ENVIRONMENT_NAME) == tool.COMPILATION_CHECKED_EXTENSION
    assert set(tool.COMPILATION_CHECKED_FILES) == {'checked_compilation_extension.mjs',
        'compilation_output_preflight.mjs', 'read_compilation_evidence_extension.mjs'}
    assert digest(tool.COMPILATION_CHECKED_FILES) == tool.COMPILATION_CHECKED_TOOLS_SHA256
    assert tool.COMPILATION_CHECKED_FILES['read_compilation_evidence_extension.mjs'] == tool.COMPILATION_EVIDENCE_EXTENSION_SHA256
    assert P.verified_bundle() and P.PROFILE == 'video.animation-wikiskill-compile.v4'
    prompt = implementation(P.PROFILE).build_prompt(dict(input_sha256='a' * 64))
    assert 'check_compilation_output' in prompt and 'cross-package' in prompt


def test_v4_final_collector_still_rejects_invalid_output_and_requires_stopped_inputs(tmp_path):
    document, files = stage(tmp_path)
    handler = implementation(P.PROFILE)
    assert handler.required_tools == ('read_compilation_input', 'write', 'check_compilation_output')
    envelope = SimpleNamespace(attempt_id=str(uuid4()), profile=P.PROFILE, profile_revision='1',
        task={'input_sha256': digest(document)}, checkpoint_inputs=[], checkpoint_ref=None,
        inputs=[InlineFile.from_bytes(name, raw) for name, raw in files.items()])
    proof = StopEvidence(execution_id=envelope.attempt_id, all_writers_stopped=True, resource_ids=('offline',))
    output = compact(document)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    result = json.loads(handler.collect_output(tmp_path, envelope, proof)[0].content())
    assert C.validate_result(result, digest(document), document) == result
    # A check is advice, never authorization: modifying the file invalidates it.
    output['exclusions'] = []
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    with pytest.raises(ServiceError, match='coverage_incomplete'):
        handler.collect_output(tmp_path, envelope, proof)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(compact(document)))
    with pytest.raises(ServiceError, match='writers_not_stopped'):
        handler.collect_output(tmp_path, envelope, StopEvidence(execution_id=envelope.attempt_id,
            all_writers_stopped=False, resource_ids=('offline',)))
