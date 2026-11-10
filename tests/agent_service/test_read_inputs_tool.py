"""Exercise real local input bytes and immutable tool identity; no model/network."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from agent_service import input_tools
from agent_service.registry import ToolEnvironment


NODE = shutil.which('node')
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC')
RUN = '''
const mod = await import(process.argv[1]);
let tool;
mod.default({registerTool(value){tool=value;}});
try {
  const result = await tool.execute('offline-call', JSON.parse(process.argv[2]),
    JSON.parse(process.argv[3]) ? AbortSignal.abort() : new AbortController().signal);
  process.stdout.write(JSON.stringify({ok:true,name:tool.name,parameters:tool.parameters,result}));
} catch (error) {
  process.stdout.write(JSON.stringify({ok:false,error:error.message}));
}
'''


def execute(workspace, params=None, *, aborted=False):
    if NODE is None:
        pytest.skip('Node unavailable for real extension test')
    result = subprocess.run([NODE, '--input-type=module', '-e', RUN,
        input_tools.READ_INPUTS_EXTENSION.resolve().as_uri(), json.dumps({} if params is None else params),
        json.dumps(aborted)], cwd=workspace, capture_output=True, text=True, encoding='utf-8', timeout=10,
        env={key: value for key, value in os.environ.items()
             if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / 'attempt'; root.mkdir()
    inputs = root / 'inputs'; inputs.mkdir()
    (inputs / 'task.json').write_text('{"title":"真实字节"}', encoding='utf-8')
    (inputs / 'frame-00.png').write_bytes(PNG)
    return root


def test_one_noargument_call_returns_all_24_original_images_with_filename_labels(workspace):
    inputs = workspace / 'inputs'
    for index in range(1, 24):
        (inputs / f'frame-{index:02}.png').write_bytes(PNG + bytes([index]))
    result = execute(workspace)
    assert result['ok'] and result['name'] == 'read_inputs'
    assert result['parameters'] == {'type': 'object', 'properties': {}, 'additionalProperties': False}
    content = result['result']['content']
    assert content[0] == {'type': 'text', 'text': 'Input file: task.json\n{"title":"真实字节"}'}
    assert len(content) == 49
    for index in range(24):
        label, image = content[index * 2 + 1:index * 2 + 3]
        assert label == {'type': 'text', 'text': f'Input file: frame-{index:02}.png'}
        expected = (inputs / f'frame-{index:02}.png').read_bytes()
        assert image['type'] == 'image' and image['mimeType'] == 'image/png'
        assert base64.b64decode(image['data'], validate=True) == expected
    assert result['result']['details']['totalBytes'] == sum(path.stat().st_size for path in inputs.iterdir())


@pytest.mark.parametrize('params', [{'path': '/etc/passwd'}, {'files': ['task.json']}, [], 'inputs'])
def test_tool_has_no_caller_selected_file_or_directory(workspace, params):
    assert execute(workspace, params)['ok'] is False


def test_tool_aborts_before_reading(workspace):
    assert execute(workspace, aborted=True) == {'ok': False, 'error': 'read_inputs_cancelled'}


@pytest.mark.parametrize('name,data', [('extra.txt', b'unsupported'), ('bad.png', b'not-a-png'),
    ('bad.json', b'{"unfinished"'), ('oversize.png', b'x' * (2 * 1024 * 1024 + 1))],
    ids=['unsupported-extension', 'invalid-image', 'invalid-json', 'oversize-image'])
def test_unsupported_malformed_and_oversize_inputs_are_rejected(workspace, name, data):
    (workspace / 'inputs' / name).write_bytes(data)
    assert execute(workspace)['ok'] is False


def test_input_file_count_is_bounded(workspace):
    for index in range(63):
        (workspace / 'inputs' / f'extra-{index:02}.json').write_text('{}')
    assert execute(workspace)['ok'] is False


def test_linked_inputs_are_rejected(workspace):
    source = workspace / 'inputs' / 'frame-00.png'
    linked = workspace / 'inputs' / 'linked.png'
    try:
        os.link(source, linked)
    except OSError:
        pytest.skip('Hardlinks unavailable')
    assert execute(workspace)['ok'] is False


def environment(**changes):
    data = dict(name=input_tools.READ_INPUTS_ENVIRONMENT_NAME,
        tools=['read_inputs', 'write'],
        capabilities=['text-output.v1'], image_digest='sha256:' + 'a' * 64, runtime_revision='fixture-v1')
    data['revision'] = input_tools.read_inputs_environment_revision(data)
    return ToolEnvironment.model_validate(dict(data, **changes))


def test_extension_bytes_and_tool_identity_share_the_fixed_digest():
    expected = hashlib.sha256(input_tools.READ_INPUTS_EXTENSION.read_bytes()).hexdigest()
    assert expected == input_tools.READ_INPUTS_EXTENSION_SHA256
    assert environment().revision.startswith('inputs-' + expected + '-')
    assert len(environment().revision) == 92
    assert input_tools.verified_read_inputs_extension() == input_tools.READ_INPUTS_EXTENSION


@pytest.mark.parametrize('changes', [dict(name='caller-selected'), dict(revision='unverified-v1'),
    dict(tools=['read_inputs', 'read', 'write']), dict(tools=['read_inputs', 'write', 'bash'])])
def test_input_reader_environment_cannot_widen_scope_or_omit_fixed_identity(changes):
    with pytest.raises(ValueError):
        environment(**changes)


def test_modified_extension_bytes_are_rejected(monkeypatch):
    monkeypatch.setattr(input_tools, '_read', lambda *args: b'altered extension')
    with pytest.raises(ValueError, match='read_inputs_extension_integrity'):
        input_tools.verified_read_inputs_extension()


def test_input_environment_revision_binds_image_and_runtime():
    original = environment().model_dump(mode='json')
    for key, value in [('image_digest', 'sha256:' + 'b' * 64), ('runtime_revision', 'fixture-v2')]:
        changed = dict(original, **{key: value})
        assert input_tools.read_inputs_environment_revision(changed) != original['revision']
        with pytest.raises(ValueError):
            ToolEnvironment.model_validate(changed)
        changed['revision'] = input_tools.read_inputs_environment_revision(changed)
        assert ToolEnvironment.model_validate(changed).revision == changed['revision']


def test_neutral_domain_selects_only_read_inputs_and_write(monkeypatch):
    from agent_service import domain_runtime
    monkeypatch.setattr(domain_runtime, 'implementation', lambda name: SimpleNamespace(
        required_tools=('read_inputs', 'write')))
    profile = domain_runtime.DomainExecutionProfile(SimpleNamespace(name='fixture.visual.v1'), 'fixture-v1',
        extensions=(input_tools.READ_INPUTS_EXTENSION,))
    assert profile.allowed_tools == ('read_inputs', 'write')
    assert profile.extension_paths == (input_tools.READ_INPUTS_EXTENSION,)
