"""Offline video contracts bind immutable input and reject active sources."""
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video
from agent_service.registry import TaskContract


ATTEMPT = '00000000-0000-0000-0000-000000000052'
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
       '<rect width="1280" height="720" fill="#101827"/>'
       '<text x="80" y="100" fill="white">Opening</text></svg>')
HTML = ('<!doctype html><html><head><style>body {margin:0;background:#101827;'
        'color:white;font-family:sans-serif} .slide {display:flex;align-items:center;'
        'width:1280px;height:720px}</style></head><body><main class="slide">'
        '<h1>Opening</h1></main></body></html>')


def storyboard():
    return dict(schema_version='1', kind='storyboard', title='Opening',
        script='Describe three workflow steps.', style='Dark, concise diagrams', max_scenes=2)


def scene(format='svg'):
    return dict(schema_version='1', kind='scene', title='Opening',
        narration='Describe three workflow steps.', visual_description='Three labeled rectangles.',
        style='Dark, concise diagrams', format=format, width=1280, height=720,
        duration_seconds=10, base_source=None, instruction='')


def output(kind='storyboard', format='svg'):
    if kind == 'storyboard':
        return dict(schema_version='1', scenes=[dict(title='Opening', narration='Three steps.',
            visual_description='Three labeled rectangles.', duration_seconds=10, format=format)])
    return dict(schema_version='1', format=format, source=SVG if format == 'svg' else HTML,
                summary='A title on a dark background.')


def prepare(directory, document, result=None):
    raw = canonical_bytes(document)
    task = {'input_sha256': hashlib.sha256(raw).hexdigest()}
    root = directory / 'inputs'
    root.mkdir()
    (root / 'video-input.json').write_bytes(raw)
    if result is None:
        result = output(document['kind'], document.get('format', 'svg'))
    (directory / 'output.json').write_bytes(canonical_bytes(result))
    profile = video.VideoProfile(document['kind'])
    envelope = SimpleNamespace(task=task, profile=profile.name, profile_revision='1',
        attempt_id=ATTEMPT, inputs=[InlineFile.from_bytes('video-input.json', raw)],
        checkpoint_inputs=[], checkpoint_ref=None)
    return profile, envelope


def collect(profile, directory, envelope):
    return profile.collect_output(directory, envelope, StopEvidence(ATTEMPT, True, ('writer',)))


@pytest.mark.parametrize('kind', ['storyboard', 'scene'])
def test_profiles_registered_with_read_write_only_and_pinned_prompt(kind):
    profile = implementation('video.' + kind + '.v1')
    assert profile.required_tools == ('read', 'write')
    assert profile.supports_inputs is True
    contract = TaskContract(name=profile.name, revision='1', task_schema=profile.task_schema,
        output_schema=profile.output_schema, required_tools=['read', 'write'],
        required_model_capabilities=['text', 'tool_calls'])
    assert contract.limits()['max_input_files'] == 1
    prompt = profile.build_prompt({'input_sha256': 'a' * 64})
    assert 'inputs/video-input.json' in prompt
    assert 'output.json in the workspace root' in prompt
    assert 'video-workbench' in prompt
    assert set(video.verified_bundle()) == video.BUNDLE_FILES


@pytest.mark.parametrize('document', [storyboard(), scene(), scene('html')])
def test_collector_returns_one_independently_validated_candidate(tmp_path, document):
    profile, envelope = prepare(tmp_path, document)
    files = collect(profile, tmp_path, envelope)
    assert [item.name for item in files] == ['video-result.json']
    result = video.decode_video_json(files[0].content())
    assert video.validate_video_result(result, envelope.task['input_sha256'], document['kind']) == result
    assert result == dict(schema_version='1', kind=document['kind'],
        input_sha256=envelope.task['input_sha256'], output=output(document['kind'], document.get('format', 'svg')))


@pytest.mark.parametrize('proof', [None, StopEvidence('other-attempt', True, ('writer',)),
    StopEvidence(ATTEMPT, False, ('writer',)), StopEvidence(ATTEMPT, True, ())])
def test_output_cannot_be_read_before_matching_all_writer_stop(tmp_path, proof):
    profile = video.VideoProfile('storyboard')
    with pytest.raises(ServiceError, match='writers_not_stopped'):
        profile.collect_output(tmp_path, SimpleNamespace(attempt_id=ATTEMPT), proof)


@pytest.mark.parametrize('mutation', ['staged_bytes', 'task_hash', 'ref_hash', 'extra_input',
    'wrong_name', 'checkpoint', 'profile', 'revision', 'noncanonical'])
def test_collector_rejects_input_or_execution_binding_changes(tmp_path, mutation):
    profile, envelope = prepare(tmp_path, storyboard())
    path = tmp_path / 'inputs' / 'video-input.json'
    if mutation == 'staged_bytes':
        document = storyboard()
        document['script'] = 'Substituted input.'
        path.write_bytes(canonical_bytes(document))
    elif mutation == 'task_hash':
        envelope.task['input_sha256'] = 'b' * 64
    elif mutation == 'ref_hash':
        envelope.inputs = [InlineFile.from_bytes('video-input.json', b'{}')]
    elif mutation == 'extra_input':
        (tmp_path / 'inputs' / 'unexpected.json').write_bytes(b'{}')
    elif mutation == 'wrong_name':
        envelope.inputs = [InlineFile.from_bytes('other.json', path.read_bytes())]
    elif mutation == 'checkpoint':
        envelope.checkpoint_ref = object()
    elif mutation == 'profile':
        envelope.profile = 'video.scene.v1'
    elif mutation == 'revision':
        envelope.profile_revision = '2'
    else:
        path.write_text(json.dumps(storyboard(), indent=2), encoding='utf-8')
    with pytest.raises(ServiceError):
        collect(profile, tmp_path, envelope)


def test_max_scenes_and_requested_format_are_enforced(tmp_path):
    document = storyboard()
    document['max_scenes'] = 1
    result = output()
    result['scenes'] *= 2
    profile, envelope = prepare(tmp_path, document, result)
    with pytest.raises(ServiceError, match='video_scene_limit_exceeded'):
        collect(profile, tmp_path, envelope)
    (tmp_path / 'inputs' / 'video-input.json').unlink()
    (tmp_path / 'inputs').rmdir()
    profile, envelope = prepare(tmp_path, scene(), output('scene', 'html'))
    with pytest.raises(ServiceError, match='video_format_mismatch'):
        collect(profile, tmp_path, envelope)


@pytest.mark.parametrize('format,source', [
    ('svg', '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
    ('svg', '<svg onload="alert(1)"/>'),
    ('svg', '<svg><foreignObject><div>Active</div></foreignObject></svg>'),
    ('svg', '<svg><image href="https://example.com/image.png"/></svg>'),
    ('svg', '<svg><use href="https://example.com/image.svg#x"/></svg>'),
    ('svg', '<svg><rect fill="url(https://example.com/x)"/></svg>'),
    ('svg', '<svg><style>@import "https://example.com/style.css";</style></svg>'),
    ('svg', '<!DOCTYPE svg [<!ENTITY x "text">]><svg><text>&x;</text></svg>'),
    ('svg', '<svg xmlns="http://www.w3.org/2000/svg" xml:base="https://example.com"/>'),
    ('svg', '<?xml-stylesheet href="https://example.com/a.css"?><svg/>'),
    ('html', '<script>fetch("https://example.com")</script>'),
    ('html', '<div onclick="alert(1)">Click</div>'),
    ('html', '<iframe srcdoc="hello"></iframe>'),
    ('html', '<object data="https://example.com"></object>'),
    ('html', '<img src="https://example.com">'),
    ('html', '<meta http-equiv="refresh" content="0;url=https://example.com">'),
    ('html', '<style>@font-face {font-family:x;src:url(https://example.com)}</style>'),
    ('html', '<div style="background:image-set(\'https://example.com\' 1x)">X</div>'),
    ('html', '<div style="background:u\\72l(https://example.com)">X</div>'),
    ('html', '<div style="background:u/**/rl(https://example.com)">X</div>'),
    ('html', '<div style="behavior:expression(alert(1))">X</div>'),
    ('html', '<div style="color:red" style="background:url(https://example.com)">X</div>'),
    ('html', '<div>Unclosed'),
])
def test_active_or_remote_source_is_rejected(format, source):
    with pytest.raises(ServiceError):
        video.validate_scene_source(source, format)


def test_local_svg_gradient_and_inline_html_svg_are_accepted():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="g">'
           '<stop offset="0" stop-color="red"/></linearGradient></defs>'
           '<rect width="100" height="100" fill="url(#g)"/></svg>')
    assert video.validate_scene_source(svg, 'svg') == svg
    html = '<div style="display:grid"><svg viewBox="0 0 10 10"><circle r="2"/></svg></div>'
    assert video.validate_scene_source(html, 'html') == html


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'\xff'])
def test_duplicate_nonfinite_and_non_utf8_json_rejected(raw):
    with pytest.raises(ServiceError, match='invalid_video_json'):
        video.decode_video_json(raw)


def test_result_input_binding_and_output_contract_are_independent():
    document = dict(schema_version='1', kind='scene', input_sha256='a' * 64, output=output('scene'))
    with pytest.raises(ServiceError, match='video_input_binding_mismatch'):
        video.validate_video_result(document, 'b' * 64, 'scene')
    document['output']['unreviewed'] = True
    with pytest.raises(ServiceError):
        video.validate_video_result(document, 'a' * 64, 'scene')
    with pytest.raises(ServiceError):
        video.validate_scene_source('<svg><text>' + '字' * 50000 + '</text></svg>', 'svg')


@pytest.mark.parametrize('change', [{'max_scenes': True}, {'max_scenes': 13}, {'extra': 'x'}])
def test_input_rejects_bool_scene_limit_and_unknown_keys(change):
    with pytest.raises(ServiceError):
        video.validate_video_input(dict(storyboard(), **change), 'storyboard')


def test_scene_input_rejects_unsafe_revision_material_and_invalid_duration():
    document = scene()
    document['base_source'] = '<svg onload="alert(1)"/>'
    with pytest.raises(ServiceError):
        video.validate_video_input(document, 'scene')
    document['base_source'] = None
    for duration in (True, float('nan'), float('inf'), 10 ** 1000):
        document['duration_seconds'] = duration
        with pytest.raises(ServiceError):
            video.validate_video_input(document, 'scene')


def test_collector_does_not_follow_hardlinked_output(tmp_path):
    profile, envelope = prepare(tmp_path, scene())
    os.link(tmp_path / 'output.json', tmp_path / 'output-alias.json')
    with pytest.raises(ServiceError, match='invalid_video_file'):
        collect(profile, tmp_path, envelope)


def test_resource_bundle_rejects_local_instruction_tampering(tmp_path, monkeypatch):
    for name in ('manifest.json', *video.BUNDLE_FILES):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((video.BUNDLE_ROOT / name).read_bytes())
    monkeypatch.setattr(video, 'BUNDLE_ROOT', tmp_path)
    assert video.verified_bundle()['CONTRACT.md']
    (tmp_path / 'CONTRACT.md').write_text('Changed instructions.', encoding='utf-8')
    with pytest.raises(ServiceError, match='video_bundle_integrity'):
        video.verified_bundle()
