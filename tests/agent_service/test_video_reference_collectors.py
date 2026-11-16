"""Reference preservation at real frozen-input/profile collector boundaries."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles.video_references import reference_urls
from test_video_profile import storyboard, output as storyboard_output
from test_video_explanation_profile import document as explanation_input, result as explanation_output
from test_video_motion_profile import document as motion_input, plan as motion_output


OFFICIAL = ('https://github.com/deepseek-ai/deepseek-harness/blob/'
            '8bd073392eef6f54e921a093b95076248eb08cbb/docs/architecture.md')
ATTEMPT = 'reference-collector-attempt'


def stage(root, kind, document, output=None):
    root.mkdir(exist_ok=True)
    input_name = {'storyboard': 'video-input.json', 'explanation-plan': 'explanation-input.json',
                  'motion-plan': 'motion-input.json'}[kind]
    handler = implementation('video.' + kind + '.v1')
    raw = canonical_bytes(document)
    (root / 'inputs').mkdir()
    (root / 'inputs' / input_name).write_bytes(raw)
    if output is not None:
        (root / 'output.json').write_bytes(canonical_bytes(output))
    envelope = SimpleNamespace(profile=handler.name, profile_revision='1', attempt_id=ATTEMPT,
        task=dict(input_sha256=hashlib.sha256(raw).hexdigest()),
        inputs=[InlineFile.from_bytes(input_name, raw)], checkpoint_inputs=[], checkpoint_ref=None)
    return handler, envelope


def collect(root, handler, envelope):
    files = handler.collect_output(root, envelope, StopEvidence(ATTEMPT, True, ('stopped-worker',)))
    assert len(files) == 1
    return json.loads(files[0].content())


def test_complete_official_url_survives_storyboard_explanation_and_motion_when_models_omit_it(tmp_path):
    storyboard_doc = storyboard()
    storyboard_doc['script'] += '\n来源完整网址须留在画面：' + OFFICIAL
    raw_storyboard = storyboard_output()
    storyboard_dir = tmp_path / 'storyboard'
    handler, envelope = stage(storyboard_dir, 'storyboard', storyboard_doc, raw_storyboard)
    handler.validate_inputs(storyboard_dir, envelope.task)
    storyboard_result = collect(storyboard_dir, handler, envelope)['output']
    assert reference_urls(storyboard_result['scenes'][0]['visual_description']) == [OFFICIAL]
    assert storyboard_result['scenes'][0]['narration'] == raw_storyboard['scenes'][0]['narration']

    explanation_doc = explanation_input()
    explanation_doc['project']['script'] += '\n来源：' + OFFICIAL
    explanation_doc['scenes'][0]['visual_description'] = storyboard_result['scenes'][0]['visual_description']
    raw_explanation = explanation_output(explanation_doc)
    explanation_dir = tmp_path / 'explanation'
    handler, envelope = stage(explanation_dir, 'explanation-plan', explanation_doc, raw_explanation)
    handler.validate_inputs(explanation_dir, envelope.task)
    explanation_result = collect(explanation_dir, handler, envelope)
    assert reference_urls(explanation_result['scenes'][0]['visual_description']) == [OFFICIAL]
    assert explanation_result['scenes'][0]['narration'] == raw_explanation['scenes'][0]['narration']

    motion_doc = motion_input()
    motion_doc['visual_description'] = explanation_result['scenes'][0]['visual_description']
    raw_motion = motion_output()
    motion_dir = tmp_path / 'motion'
    handler, envelope = stage(motion_dir, 'motion-plan', motion_doc, raw_motion)
    handler.validate_inputs(motion_dir, envelope.task)
    result = collect(motion_dir, handler, envelope)
    assert [row['url'] for row in result['plan']['sources']] == [OFFICIAL]
    assert result['plan']['elements'] == raw_motion['elements']
    assert result['plan']['actions'] == raw_motion['actions']
    assert motion_doc['beats'] == motion_input()['beats']
    for path, original in [(storyboard_dir, raw_storyboard), (explanation_dir, raw_explanation),
                           (motion_dir, raw_motion)]:
        assert (path / 'output.json').read_bytes() == canonical_bytes(original), 'Stopped model bytes remain untouched'


@pytest.mark.parametrize('kind', ['storyboard', 'explanation-plan'])
def test_multiple_project_urls_preserve_model_allocation_and_only_fill_missing_reference(tmp_path, kind):
    urls = [OFFICIAL, 'https://example.com/step-turn', 'https://example.com/log-history']
    if kind == 'storyboard':
        doc = storyboard()
        doc['script'] += '\n参考：' + ' '.join(urls)
        output = storyboard_output()
        output['scenes'].append(deepcopy(output['scenes'][0]))
    else:
        doc = explanation_input()
        doc['project']['script'] += '\n参考：' + ' '.join(urls)
        second = deepcopy(doc['scenes'][0])
        second['scene_id'] = 'scene-b'
        doc['scenes'].append(second)
        for row, url in zip(doc['scenes'], urls):
            row['visual_description'] += '\n来源：' + url
        output = explanation_output(doc)
    for row, url in zip(output['scenes'], urls):
        row['visual_description'] += '\n来源：' + url
    original = deepcopy(output)
    handler, envelope = stage(tmp_path, kind, doc, output)
    handler.validate_inputs(tmp_path, envelope.task)
    result = collect(tmp_path, handler, envelope)
    scenes = result['output']['scenes'] if kind == 'storyboard' else result['scenes']
    assert reference_urls(scenes[0]['visual_description']) == urls[:1]
    assert reference_urls(scenes[1]['visual_description']) == urls[1:]
    assert [row['narration'] for row in scenes] == [row['narration'] for row in original['scenes']]


@pytest.mark.parametrize('kind', ['storyboard', 'explanation-plan', 'motion-plan'])
@pytest.mark.parametrize('private', ['https://auth.example/callback?code=SECRET&state=public',
                                   'https://cdn.example/file.wav#access_token=SECRET'])
def test_sensitive_reference_is_rejected_during_input_validation_without_model_output(tmp_path, kind, private):
    if kind == 'storyboard':
        doc = storyboard()
        doc['script'] += '\n' + private
    elif kind == 'explanation-plan':
        doc = explanation_input()
        doc['instruction'] += '\n' + private
    else:
        doc = motion_input()
        doc['visual_description'] += '\n' + private
    handler, envelope = stage(tmp_path, kind, doc)
    with pytest.raises(ServiceError, match='video_reference_sensitive_url') as failure:
        handler.validate_inputs(tmp_path, envelope.task)
    assert 'SECRET' not in str(failure.value)
    assert not (tmp_path / 'output.json').exists(), 'Admission must reject before any model output is needed'


@pytest.mark.parametrize('kind', ['storyboard', 'explanation-plan', 'motion-plan'])
def test_collectors_refuse_model_invented_source_urls(tmp_path, kind):
    invented = 'https://invented.example/unsupported-evidence'
    if kind == 'storyboard':
        doc, output = storyboard(), storyboard_output()
        output['scenes'][0]['visual_description'] += '\n来源：' + invented
    elif kind == 'explanation-plan':
        doc = explanation_input()
        output = explanation_output(doc)
        output['scenes'][0]['visual_description'] += '\n来源：' + invented
    else:
        doc, output = motion_input(), motion_output()
        output['sources'] = [dict(url=invented, label='模型编造的参考来源')]
    handler, envelope = stage(tmp_path, kind, doc, output)
    handler.validate_inputs(tmp_path, envelope.task)
    code = 'motion_reference_not_in_input' if kind == 'motion-plan' else 'video_reference_not_in_input'
    with pytest.raises(ServiceError, match=code):
        collect(tmp_path, handler, envelope)
