"""Offline project proposals: provenance, strict shape and stopped Agent output."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent_service import catalog
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation, video_explanation as explanation
from agent_service.profiles.video_explanation_contract import FACT_REVIEW_WARNING
from test_catalog import image_packages


def document():
    return dict(schema_version='video-explanation-input.v1', project=dict(title='排队处理请求',
        script='请求先进入队列。执行节点取出任务。', style='清晰图解', voice='zm_yunjian'),
        brief=dict(audience='初学者', goal='理解接收与处理如何分开', tone='conversational'),
        scenes=[dict(scene_id='scene-a', title='请求进入队列', narration='请求先进入队列。',
            visual_description='请求从入口走向队列', duration_seconds=8, format='svg',
            speech=dict(speed=1.0, pronunciation={}))], instruction='保留事实，解释得易懂')


def result(doc=None):
    doc = document() if doc is None else doc
    return dict(schema_version='video-explanation-result.v1',
        input_sha256=hashlib.sha256(canonical_bytes(doc)).hexdigest(), summary='先说明接收与处理分开，再解释队列。',
        scenes=[dict(scene_id=row['scene_id'], title='请求先排队', narration='请求先进入队列。这样就能把接收和处理分开。',
            visual_description='先显示请求入口，再显示排队的请求；最后突出接收和处理分开。',
            duration_seconds=10, pronunciation={}, key_points=['先排队', '接收与处理分开'],
            visual_strategy='flow', visual_steps=['显示请求从入口到队列', '突出队列中的请求'],
            delivery_note='在两句话之间自然换气，第二句解释原因。', source_quotes=[row['narration']])
            for row in doc['scenes']], warnings=[])


def prepare(tmp_path):
    handler = implementation('video.explanation-plan.v1')
    raw = canonical_bytes(document())
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs/explanation-input.json').write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(result()))
    envelope = SimpleNamespace(profile=handler.name, profile_revision='1', attempt_id='explanation-attempt',
        task=dict(input_sha256=hashlib.sha256(raw).hexdigest()),
        inputs=[InlineFile.from_bytes('explanation-input.json', raw)], checkpoint_inputs=[], checkpoint_ref=None)
    return handler, envelope


def test_explanation_roundtrip_preserves_source_and_requires_human_review(tmp_path):
    handler, envelope = prepare(tmp_path)
    original = document()
    checked = explanation.validate_explanation_input(original)
    checked['scenes'][0]['narration'] = 'changed'
    assert original == document()
    handler.validate_inputs(tmp_path, envelope.task)
    files = handler.collect_output(tmp_path, envelope, StopEvidence(envelope.attempt_id, True, ('writer',)))
    assert len(files) == 1 and files[0].name == 'explanation-result.json'
    output = json.loads(files[0].content())
    assert output['scenes'] == result()['scenes']
    assert output['warnings'][0] == FACT_REVIEW_WARNING
    assert explanation.validate_explanation_result(output, result()['input_sha256'], original) == output
    assert original['project']['voice'] == 'zm_yunjian'
    assert handler.required_tools == ('read', 'write')
    prompt = handler.build_prompt(envelope.task)
    assert 'inputs/explanation-input.json' in prompt and 'Kokoro' in prompt
    assert 'SSML' in prompt and 'not prove every new sentence' in prompt


@pytest.mark.parametrize('change', ['source_not_found', 'other_scene_only', 'order', 'missing_scene',
    'voice', 'timing', 'emotion', 'script', 'ssml', 'dangling_pronunciation', 'input_sha', 'different_input',
    'scene_id', 'boolean_duration', 'infinite_duration', 'too_many_steps', 'empty_quote', 'title_limit',
    'extra_scene', 'control', 'duplicate_json'])
def test_untrusted_proposal_cannot_change_scope_or_claim_unsupported_controls(change):
    doc = document()
    if change in {'order', 'other_scene_only'}:
        other = deepcopy(doc['scenes'][0])
        other.update(scene_id='scene-b', narration='仅在第二镜出现的原文。')
        doc['scenes'].append(other)
    output = result(doc)
    row = output['scenes'][0]
    if change == 'source_not_found':
        row['source_quotes'] = ['不存在的原文']
    elif change == 'other_scene_only':
        row['source_quotes'] = ['仅在第二镜出现的原文。']
    elif change == 'order':
        output['scenes'].reverse()
    elif change == 'missing_scene':
        output['scenes'] = []
    elif change in {'voice', 'timing', 'emotion'}:
        row[{'voice':'voice', 'timing':'audio_start_seconds', 'emotion':'emotion'}[change]] = 'unsupported'
    elif change == 'script':
        row['visual_steps'][0] = '<script>fetch("https://invalid")</script>'
    elif change == 'ssml':
        row['narration'] = '<speak>请看队列。</speak>'
    elif change == 'dangling_pronunciation':
        row['pronunciation'] = {'不存在的词': '读法'}
    elif change == 'input_sha':
        output['input_sha256'] = '0' * 64
    elif change == 'different_input':
        doc['project']['voice'] = 'zm_yunxi'
    elif change == 'scene_id':
        row['scene_id'] = 'scene-other'
    elif change == 'boolean_duration':
        row['duration_seconds'] = True
    elif change == 'infinite_duration':
        row['duration_seconds'] = float('inf')
    elif change == 'too_many_steps':
        row['visual_steps'] *= 4
    elif change == 'empty_quote':
        row['source_quotes'] = ['']
    elif change == 'title_limit':
        row['title'] = '词' * 161
    elif change == 'extra_scene':
        output['scenes'].append(deepcopy(row))
    elif change == 'control':
        row['narration'] += '\x00'
    else:
        from agent_service.profiles.video import decode_video_json
        with pytest.raises(ServiceError):
            decode_video_json(b'{"summary":"duplicate",' + canonical_bytes(output)[1:])
        return
    with pytest.raises(ServiceError):
        explanation.validate_explanation_result(output, result()['input_sha256'] if change == 'different_input'
                                               else result(doc)['input_sha256'], doc)


def test_source_quotes_are_provenance_not_semantic_fact_verification():
    output = result()
    output['scenes'][0]['narration'] = '队列每秒能够处理一百万个请求。'
    # A structural validator cannot detect this unsupported factual claim.
    assert explanation.validate_explanation_result(output, output['input_sha256'], document()) == output
    assert '不能证明全部改写准确' in FACT_REVIEW_WARNING


@pytest.mark.parametrize('change', ['source_length', 'spoken_expansion'])
def test_proposed_narration_cannot_be_adopted_beyond_existing_speech_admission(change):
    output = result()
    row = output['scenes'][0]
    if change == 'source_length':
        row['narration'] = '稿' * 2001
    else:
        row['narration'] = 'AI' * 1000
        row['pronunciation'] = {'AI': '人工智能'}
    with pytest.raises(ServiceError, match='explanation_narration_requires_split'):
        explanation.validate_explanation_result(output, output['input_sha256'], document())


@pytest.mark.parametrize('narration,rules', [('稿' * 2000, {}), ('😀' * 2000, {}),
    ('A' * 1999 + 'B', {'AA': 'B', 'B': 'C' * 200}),
    ('A' * 2000, {'AAA': 'a', 'AA': 'z' * 200})], ids=['boundary', 'utf8', 'single-pass', 'longest-first'])
def test_proposed_speech_text_matches_longest_first_noncascading_audio_rules(narration, rules):
    from video_workbench.audio_store import narration_document
    output = result()
    row = output['scenes'][0]
    row.update(narration=narration, pronunciation=rules)
    assert explanation.validate_explanation_result(output, output['input_sha256'], document()) == output
    audio = narration_document(dict(narration=narration, speech_settings=dict(speed=1.0, pronunciation=rules)),
                               dict(model='kokoro-rk3588', voice='zm_yunjian'))
    assert len(audio['spoken_text']) <= 2000 and len(audio['spoken_text'].encode()) <= 16 * 1024


def test_original_long_narration_remains_compatible_for_manual_revision():
    doc = document()
    doc['scenes'][0]['narration'] = '稿' * 10000
    assert explanation.validate_explanation_input(doc) == doc
    prompt = implementation('video.explanation-plan.v1').build_prompt({'input_sha256': 'a' * 64})
    assert 'manual splitting' in prompt and 'do not silently drop facts' in prompt


@pytest.mark.parametrize('change', ['bytes', 'hash', 'name', 'extra_input', 'checkpoint', 'revision',
                                   'profile', 'noncanonical', 'stop', 'attempt'])
def test_collector_binds_original_input_and_stopped_execution(tmp_path, change):
    handler, envelope = prepare(tmp_path)
    proof = StopEvidence(envelope.attempt_id, True, ('writer',))
    if change == 'bytes':
        doc = document()
        doc['project']['script'] += '改动'
        (tmp_path / 'inputs/explanation-input.json').write_bytes(canonical_bytes(doc))
    elif change == 'hash':
        envelope.task['input_sha256'] = 'a' * 64
    elif change == 'name':
        envelope.inputs = [InlineFile.from_bytes('video-input.json', canonical_bytes(document()))]
    elif change == 'extra_input':
        (tmp_path / 'inputs/extra.txt').write_text('unexpected')
    elif change == 'checkpoint':
        envelope.checkpoint_ref = 'unexpected'
    elif change == 'revision':
        envelope.profile_revision = '2'
    elif change == 'profile':
        envelope.profile = 'video.motion-plan.v1'
    elif change == 'noncanonical':
        (tmp_path / 'inputs/explanation-input.json').write_bytes(canonical_bytes(document()) + b'\n')
    elif change == 'stop':
        proof = StopEvidence(envelope.attempt_id, False, ('writer',))
    else:
        proof = StopEvidence('other-attempt', True, ('writer',))
    with pytest.raises(ServiceError):
        handler.collect_output(tmp_path, envelope, proof)


@pytest.mark.parametrize('change', ['no_scenes', 'many_scenes', 'duplicate_id', 'speed', 'tone', 'voice',
                                   'format', 'huge_duration', 'surrogate'])
def test_input_limits_prevent_invalid_project_operations(change):
    doc = document()
    if change == 'no_scenes':
        doc['scenes'] = []
    elif change == 'many_scenes':
        doc['scenes'] *= 25
    elif change == 'duplicate_id':
        doc['scenes'] *= 2
    elif change == 'speed':
        doc['scenes'][0]['speech']['speed'] = True
    elif change == 'tone':
        doc['brief']['tone'] = 'unsupported'
    elif change == 'voice':
        doc['project']['voice'] = ''
    elif change == 'format':
        doc['scenes'][0]['format'] = 'javascript'
    elif change == 'huge_duration':
        doc['scenes'][0]['duration_seconds'] = 10 ** 1000
    else:
        doc['project']['script'] += '\ud800'
    with pytest.raises(ServiceError):
        explanation.validate_explanation_input(doc)


def test_resource_bundle_is_fixed_and_tampering_rejected(tmp_path, monkeypatch):
    assert set(explanation.verified_bundle()) == explanation.BUNDLE_FILES
    for name in ('manifest.json', *explanation.BUNDLE_FILES):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((explanation.BUNDLE_ROOT / name).read_bytes())
    monkeypatch.setattr(explanation, 'BUNDLE_ROOT', tmp_path)
    (tmp_path / 'CONTRACT.md').write_text('changed')
    with pytest.raises(ServiceError, match='explanation_bundle_integrity'):
        explanation.verified_bundle()


def test_explanation_catalog_is_opt_in_disabled_and_scope_is_explicit(image_packages):
    args = dict(pi_image='registry.invalid/pi@sha256:' + 'a' * 64, runtime_revision='test')
    old, _, _ = catalog.generate(**args)
    new, hosts, summary = catalog.generate(**args, include_explanation=True)
    assert not any(row['name'] == 'video.explanation-plan.v1' for row in old['task_contracts'])
    contract = next(row for row in new['task_contracts'] if row['name'] == 'video.explanation-plan.v1')
    assert contract['revision'] == '1' and contract['required_tools'] == ['read', 'write']
    binding = next(row for row in new['execution_bindings'] if row['profile'] == contract['name'])
    assert binding['bundle'] == dict(name='video-explanation', revision='video-explanation-v1')
    assert 'acceptance' not in binding
    assert all(row['enabled'] is False for row in new['routes'])
    assert all(row['profile']['enabled'] is False for row in hosts.values())
    assert next(row for row in new['callers'] if row['caller'] == 'video-workbench')['profiles'] == [
        *catalog.VIDEO_PROFILES, *catalog.EXPLANATION_PROFILES]
    assert old['capability_bundles'] == new['capability_bundles'][:3]
    assert old['task_contracts'] == new['task_contracts'][:-1]
    assert summary['network_requests'] == 0 and summary['acceptance_created'] is False
