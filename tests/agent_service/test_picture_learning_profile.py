"""Image delivery, frozen knowledge and source contracts; no artistic acceptance."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation, video_picture_learning as protocol
from agent_service.picture_learning_tools import verified_extensions
from video_workbench.animation_knowledge import AnimationKnowledgeStore
from video_workbench.picture_learning_gateway import PictureAuthorGateway, PictureReviewGateway


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC')


def document(mode='create', work_kind='reproduction'):
    frames = []
    if work_kind == 'reproduction':
        frames.append(dict(file_name='reference.png', sha256=hashlib.sha256(PNG).hexdigest(), purpose='reference'))
    if mode != 'create':
        frames.append(dict(file_name='output.png', sha256=hashlib.sha256(PNG).hexdigest(), purpose='output'))
    return dict(schema=protocol.INPUT_SCHEMA, mode=mode, project_id='picture-1', experiment_id='experiment-1',
        work_kind=work_kind, goal='Learn a paper-like edge, not reference aspect ratio.', criteria=['Irregular ink edge.'],
        constraints=dict(width=1280, height=720, allowed_engines=['canvas']),
        observation=dict(frames=frames), base_source=None, feedback='', knowledge_snapshot=None,
        prior_review=None, learned_method=None)


def result(context):
    value = dict(schema=protocol.RESULT_SCHEMA, input_sha256=digest(context), mode=context['mode'],
        source=None, review=None, transfer_task=None, method=None)
    if context['mode'] == 'create':
        source = dict(engine='canvas', strategy=dict(description='Synthetic source fixture', tradeoffs='No quality evidence.'),
            files={'scene.html': '<canvas></canvas><script src="./scene.js"></script>',
                   'scene.js': 'window.__animationExperiment={seek(t){}};'})
        source['source_sha256'] = protocol.source_sha256(source)
        value['source'] = source
    else:
        value['review'] = dict(score=25, summary='Synthetic static fixture only.',
            issues=['Edge cannot be judged from one pixel.'], stop_recommended=False)
        if context['mode'] == 'transfer_plan':
            value['transfer_task'] = dict(goal='Change foreground structure.', criteria=['Different rock silhouette.'])
    return value


def stage(tmp_path, context):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / protocol.INPUT_NAME).write_bytes(canonical_bytes(context))
    for frame in context['observation']['frames']:
        (inputs / frame['file_name']).write_bytes(PNG)
    return inputs


def test_new_author_and_reviewer_require_direct_vision_without_changing_v2():
    from agent_service.profiles.video_animation_skill_method import VideoAnimationSkillMethodProfile
    for name in (protocol.AUTHOR_PROFILE, protocol.REVIEW_PROFILE):
        profile = implementation(name)
        assert 'vision' in profile.required_model_capabilities
        assert profile.required_tools == ('read_inputs', 'skill_search', 'skill_read', 'write')
    assert 'vision' not in VideoAnimationSkillMethodProfile().required_model_capabilities
    assert protocol.verified_bundle().keys() == protocol.BUNDLE_FILES
    assert verified_extensions()[0].name == 'picture_learning_extension.mjs'


@pytest.mark.parametrize('mode', ['create', 'review', 'transfer_plan'])
def test_mode_contract_and_exact_input_pixels_are_bound(tmp_path, mode):
    value = document(mode)
    inputs = stage(tmp_path, value)
    profile = protocol.VideoPictureLearningProfile(review=mode != 'create')
    profile.validate_inputs(tmp_path, dict(input_sha256=digest(value)))
    out = result(value)
    assert protocol.validate_result(out, digest(value), value) == out
    (inputs / 'reference.png').write_bytes(PNG + b'changed')
    with pytest.raises(ServiceError, match='image_changed'):
        profile.validate_inputs(tmp_path, dict(input_sha256=digest(value)))


def test_transfer_excludes_original_reference_and_review_requires_real_output():
    value = document('review')
    value['observation']['frames'] = value['observation']['frames'][:1]
    with pytest.raises(ServiceError, match='rendered_output_required'):
        protocol.validate_input(value)
    value = document(work_kind='transfer')
    assert protocol.validate_input(value) == value
    value['observation']['frames'] = document()['observation']['frames']
    with pytest.raises(ServiceError, match='transfer_reference_forbidden'):
        protocol.validate_input(value)


def test_comparison_requires_supplied_baseline_and_finite_enum():
    value = document('review')
    out = result(value)
    for preferred in ('other', {}, 'baseline'):
        out['review']['preferred'] = preferred
        with pytest.raises(ServiceError, match='comparison_invalid'):
            protocol.validate_result(out, digest(value), value)
    value['observation']['frames'].append(dict(file_name='baseline.png',
        sha256=hashlib.sha256(PNG).hexdigest(), purpose='baseline'))
    out = result(value)
    out['review'].update(preferred='baseline', comparison='Output loses the baseline dry brush structure.')
    assert protocol.validate_result(out, digest(value), value) == out


def test_collector_computes_actual_source_identity_and_requires_proven_stop(tmp_path):
    value = document()
    stage(tmp_path, value)
    out = result(value)
    expected_sha = out['source'].pop('source_sha256')
    (tmp_path / 'output.json').write_bytes(canonical_bytes(out))
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=protocol.AUTHOR_PROFILE, profile_revision='1', attempt_id=attempt,
        task=dict(input_sha256=digest(value)), checkpoint_ref=None, checkpoint_inputs=[], inputs=[
            InlineFile.from_bytes(protocol.INPUT_NAME, canonical_bytes(value)), InlineFile.from_bytes('reference.png', PNG)])
    stopped = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('fixture-writer',))
    collected = protocol.VideoPictureLearningProfile().collect_output(tmp_path, envelope, stopped)
    assert json.loads(collected[0].content())['source']['source_sha256'] == expected_sha
    unsafe = StopEvidence(execution_id=attempt, all_writers_stopped=False, resource_ids=('fixture-writer',))
    with pytest.raises(ServiceError, match='writers_not_stopped'):
        protocol.VideoPictureLearningProfile().collect_output(tmp_path, envelope, unsafe)
    envelope.inputs[-1] = InlineFile.from_bytes('reference.png', PNG + b'changed')
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        protocol.VideoPictureLearningProfile().collect_output(tmp_path, envelope, stopped)


def test_source_change_or_wrong_knowledge_policy_cannot_be_hidden(tmp_path):
    value = document()
    out = result(value)
    out['source']['files']['scene.js'] += '// different'
    with pytest.raises(ServiceError, match='source_changed'):
        protocol.validate_result(out, digest(value), value)
    store = AnimationKnowledgeStore(tmp_path / 'wiki.sqlite3')
    value['knowledge_snapshot'] = store.snapshot('fixture-owner')
    value['knowledge_binding'] = dict(enabled=False)
    with pytest.raises(ServiceError, match='disabled_knowledge_present'):
        protocol.validate_input(value)


def test_gateway_author_uploads_actual_reference_and_rejects_changed_bytes_before_upload():
    value = document()
    gateway = PictureAuthorGateway(SimpleNamespace())
    files = {protocol.INPUT_NAME: canonical_bytes(value), 'reference.png': PNG}
    assert gateway._files(value, files) == files
    files['reference.png'] += b'different'
    with pytest.raises(ServiceError, match='frame_integrity'):
        gateway._files(value, files)
    assert PictureReviewGateway.visual_input is PictureAuthorGateway.visual_input is True


def test_actual_tools_deliver_all_images_but_only_requested_frozen_knowledge(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    value = document('review')
    store = AnimationKnowledgeStore(tmp_path / 'wiki.sqlite3')
    evidence = store.record_experience('fixture-owner', 'brief', 'fixture-1', {'goal': 'Synthetic fixture only.'})
    store.revise_page('fixture-owner', 'ink-edge', dict(title='Ink edge technique',
        content='PRIVATE_BODY_ONLY_ON_REQUEST', applicability=['ink'], limitations=['Static fixture only.']),
        expected_revision=0, evidence_ids=[evidence['id']], change_note='Synthetic tool fixture.')
    value['knowledge_snapshot'] = store.snapshot('fixture-owner', experience_ids=[evidence['id']])
    stage(tmp_path, value)
    module = verified_extensions()[0].resolve().as_uri()
    script = '''
const m=await import(process.argv[1]);
const read=await m.readPictureInputs({});
const search=await m.searchPictureSkills({query:'ink'});
const page=await m.readPictureSkill({page_id:'ink-edge',limit:8});
let unknown=false;try{await m.readPictureSkill({page_id:'unselected'})}catch{unknown=true}
let path=false;try{await m.searchPictureSkills({path:'../../other'})}catch{path=true}
process.stdout.write(JSON.stringify({read,search,page,unknown,path}));
'''
    run = subprocess.run([node, '--input-type=module', '-e', script, module], cwd=tmp_path,
        capture_output=True, text=True, encoding='utf-8', timeout=15,
        env={key: val for key, val in os.environ.items()
             if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}})
    assert run.returncode == 0, run.stderr
    output = json.loads(run.stdout)
    assert 'PRIVATE_BODY_ONLY_ON_REQUEST' not in json.dumps(output['read'])
    assert len([row for row in output['read']['content'] if row['type'] == 'image']) == 2
    for row in output['read']['content']:
        if row['type'] == 'image':
            assert base64.b64decode(row['data']) == PNG
    assert 'ink-edge' in output['search']['content'][0]['text']
    assert json.loads(output['page']['content'][0]['text'])['text'] == 'PRIVATE_'
    assert output['page']['details']['understanding_verified'] is False
    assert output['unknown'] is output['path'] is True
