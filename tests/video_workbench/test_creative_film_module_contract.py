"""Pure module/worker custody fixtures; no execution, rendering or approval."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
from types import SimpleNamespace
from uuid import uuid4
import wave

import pytest

from agent_service.animation_skills_contract import bundle_files
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import video_animation_film_module as P
from agent_service.profiles import video_animation_film_module_contract as C
from agent_service.skill_tool_evidence import SkillToolObserver
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.creative_film_module_contract import (
    build_module_input, collect_module_result, validate_module_package, prepare_core_task,
    package_modules, package_boundary, predecessor_from_package, module_files,
)
from video_workbench.hyperframes_film_project import compile_film_project, verify_film_project, module_identity
from video_workbench.motion import make_beats, estimated_alignment
from test_animation_skill_production import published
from test_creative_hyperframes_renderer import RUNTIME


def recordings_and_film():
    rows, recordings = [], []
    for index, narration in enumerate(['先认清同一个对象。', '再理解对象之间的变化。']):
        data = io.BytesIO()
        with wave.open(data, 'wb') as stream:
            stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0' * 24000)
        raw = data.getvalue()
        sha = hashlib.sha256(raw).hexdigest()
        rows.append(dict(scene_id=f'scene-{index}', title=f'Passage {index}', narration=narration,
            visual_intent='Preserve an identifiable subject while its relationships change.',
            audio_version_id=f'audio-{index}', audio_sha256=sha, audio_seconds=1,
            start_seconds=index, end_seconds=index + 1,
            leading_silence_seconds=0, trailing_silence_seconds=0))
        recordings.append(dict(scene_id=f'scene-{index}', audio_version_id=f'audio-{index}',
            sha256=sha, spoken_text=narration, data=raw))
    film = dict(project_id='project', joint_plan_version_id='plan', content_binding_sha256='d' * 64,
        title='One subject through a changing relation', script=''.join(row['narration'] for row in rows),
        understanding_goal='Explain a relationship through an identifiable subject.',
        style='Let spatial relationships carry the explanation.', scenes=rows, total_seconds=2)
    return film, recordings


def core(index=0, predecessor=None):
    film, _ = recordings_and_film()
    row = film['scenes'][index]
    beats = make_beats(row['narration'])
    return prepare_core_task(scene_id=row['scene_id'], title=row['title'], visual_description=row['visual_intent'],
        narration=row['narration'], beats=beats, alignment=estimated_alignment(beats, row['audio_sha256'], 1),
        audio_sha256=row['audio_sha256'], duration_seconds=1,
        film_context=dict(schema=C.CONTEXT_SCHEMA, film=film, film_sha256=digest(film),
            current_scene_index=index, predecessor=deepcopy(predecessor)),
        module_id=f'module-{index}', namespace=f'scene_{index}',
        transition_target=dict(module_id='transition-0', namespace='relation_transition') if index else None,
        runtime_identity=dict(image_digest=RUNTIME['image_digest'], host_sha256='a' * 64,
            engine_runtime_sha256=RUNTIME['engine_runtime_sha256'], admission_id='synthetic-module-fixture'))


def raw_module(document, *, transition=False, entry_x=0, exit_x=1):
    target = document['target']['transition_target'] if transition else document['target']
    entry = f"modules/{target['namespace']}/entry.mjs"
    source = ('// Pure custody fixture, not a rendered or approved animation.\n'
        'let node;\nexport async function mount(root, context) { node=document.createElement("div"); '
        'node.id="subject"; root.append(node); }\n'
        'export async function renderAt(context) { node.textContent=String(context.global_frame); }\n'
        'export async function dispose() { node?.remove(); node=null; }\n')
    return dict(schema=C.MODULE_SCHEMA, module_id=target['module_id'], namespace=target['namespace'],
        role='transition' if transition else 'scene', scene_id=None if transition else document['scene']['scene_id'],
        film_sha256=document['film_context']['film_sha256'], engine='html-svg', entry=entry,
        files={entry: source}, lifecycle=deepcopy(C.LIFECYCLE), assets=[],
        states=dict(entry={'subject': {'x': entry_x}}, exit={'subject': {'x': exit_x}}))


def raw_result(document, *, transform=False, decisions=None):
    index = document['film_context']['current_scene_index']
    entry_x = 2 if transform else index
    module = raw_module(document, entry_x=entry_x, exit_x=entry_x + 1)
    incoming = None
    if index:
        incoming = dict(intent='Continue the same subject through the relationship change.',
            objects=[dict(object_id='subject', mode='transform' if transform else 'retain')], transition=None)
        if transform:
            incoming['transition'] = dict(module=raw_module(document, transition=True, entry_x=1, exit_x=2),
                                          start_frame=21, end_frame=27)
    return dict(schema=C.RESULT_SCHEMA, input_sha256=digest(document), module=module,
        strategy=dict(description='Author an original lifecycle module.', tradeoffs='Nonvisual custody fixture.'),
        incoming_boundary=incoming, skill_decisions=[] if decisions is None else decisions)


def stage(tmp_path, *, index=0, predecessor=None, count=0):
    task = core(index, predecessor)
    delivery = SkillProductionAdapter(lambda _: [published(i) for i in range(count)], None,
        tmp_path / 'skill-ledger.sqlite3').freeze('fixture-owner', f'module-op-{index}', task, ['html-svg'])
    document = build_module_input(task, delivery)
    files = [InlineFile.from_bytes(P.INPUT_NAME, canonical_bytes(document)),
             *bundle_files(delivery['catalog'], delivery['packages'])]
    workspace = tmp_path / f'workspace-{index}'
    for item in files:
        target = workspace / 'inputs' / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content())
    (workspace / 'output.json').write_bytes(canonical_bytes(raw_result(document)))
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=P.PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(document)}, inputs=files, checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('synthetic-stopped-writer',))
    return workspace, document, delivery, envelope, proof


def collected(stage_value, raw=None, observer=None):
    workspace, document, delivery, envelope, proof = stage_value
    handler = P.VideoAnimationFilmModuleProfile()
    if raw is not None:
        (workspace / 'output.json').write_bytes(canonical_bytes(raw))
    observer = observer or handler.create_tool_observer(workspace, envelope.task)
    artifacts = handler.collect_output_with_host_evidence(workspace, envelope, proof,
        observer.finish(envelope.attempt_id))
    result, evidence = (json.loads(item.content()) for item in artifacts)
    package = collect_module_result(document, result, evidence, skill_delivery=delivery)
    return result, evidence, package


def observe(observer, name, arguments, call_id='native-read'):
    observer.observe(dict(type='tool_execution_start', toolName=name, toolCallId=call_id, args=arguments))
    observer.observe(dict(type='tool_execution_end', toolName=name, toolCallId=call_id,
        isError=False, result=observer.tools.tool_result(name, arguments)))


def test_new_profile_pins_self_contained_lifecycle_resources_without_changing_legacy_contract():
    from agent_service.profiles.video_animation_skill_production import verified_bundle
    assert set(P.verified_bundle()) == P.BUNDLE_FILES
    prompt = P.VideoAnimationFilmModuleProfile().build_prompt({'input_sha256': 'a' * 64})
    for marker in ['mount(root, context)', 'renderAt(context)', 'dispose()', 'absolute-global-frame',
                   'skill_search', 'current_scene_index', 'Canvas', 'Three', 'incoming_boundary']:
        assert marker in prompt
    assert 'Expose `window.__animationExperiment' in verified_bundle()['CONTRACT.md']
    assert P.EVIDENCE_NAME == 'animation-skill-tool-evidence.json'
    assert P.VideoAnimationFilmModuleProfile().required_tools == ('read_inputs', 'skill_search', 'skill_read', 'write')


def test_host_adds_source_hashes_and_a_new_module_package_without_rewriting_original_source(tmp_path):
    staged = stage(tmp_path)
    raw = raw_result(staged[1])
    result, evidence, package = collected(staged, raw)
    assert raw == raw_result(staged[1])  # Normalization never mutates the Agent artifact.
    assert result['module']['files'] == raw['module']['files']
    assert result['module']['files_sha256'] == {name: hashlib.sha256(source.encode()).hexdigest()
        for name, source in raw['module']['files'].items()}
    assert package['schema'] == 'video.creative-film-module.v1'
    assert package['profile']['name'] == P.PROFILE and 'source' not in result
    assert package['skill_tool_evidence_sha256'] == evidence['sha256']
    assert validate_module_package(package) == package
    assert module_files(package) == {name: source.encode() for name, source in raw['module']['files'].items()}
    assert module_identity(package_modules(package)[0]) == C.module_identity(result['module'])
    assert package_boundary(package) is None


@pytest.mark.parametrize('transform', [False, True])
def test_exact_predecessor_and_original_optional_transition_compile_into_one_project(tmp_path, transform):
    first = collected(stage(tmp_path))[2]
    predecessor = predecessor_from_package(first)
    staged = stage(tmp_path, index=1, predecessor=predecessor)
    second = collected(staged, raw_result(staged[1], transform=transform))[2]
    modules = package_modules(first) + package_modules(second)
    boundary = package_boundary(second)
    assert boundary['from_module_sha256'] == predecessor['module_sha256']
    assert boundary['to_module_sha256'] == module_identity(second['result']['module'])
    assert boundary['at_frame'] == 24
    assert (boundary['transition'] is not None) == transform
    film, recordings = recordings_and_film()
    files = compile_film_project(film=film, film_sha256=digest(film), modules=modules,
        recordings=recordings, boundaries=[boundary], assets={}, runtime_fingerprint=RUNTIME)
    manifest = verify_film_project(files, expected_manifest_sha256=hashlib.sha256(files['manifest.json']).hexdigest())
    assert len(manifest['modules']) == (3 if transform else 2)
    assert manifest['clock']['frame_count'] == 48
    assert manifest['execution']['status'] == 'not_executed'


@pytest.mark.parametrize('bad', ['source', 'input', 'result', 'profile', 'catalog', 'evidence'])
def test_frozen_creative_package_rejects_rebound_source_input_or_host_evidence(tmp_path, bad):
    package = collected(stage(tmp_path))[2]
    if bad == 'source':
        module = package['result']['module']; module['files'][module['entry']] += '// Changed source'
    elif bad == 'input': package['input_document']['scene']['instruction'] = 'Unfrozen request'
    elif bad == 'result': package['result']['strategy']['description'] = 'Changed decision'
    elif bad == 'profile': package['profile']['name'] = 'video.animation-production.v3'
    elif bad == 'catalog': package['skill_catalog_sha256'] = 'f' * 64
    else: package['skill_tool_evidence']['source'] = 'agent_self_report'
    with pytest.raises(ServiceError):
        validate_module_package(package)


@pytest.mark.parametrize('bad', ['module_id', 'namespace', 'film', 'path', 'lifecycle', 'asset', 'engine', 'hash'])
def test_module_cannot_escape_its_stable_target_resources_or_new_lifecycle(tmp_path, bad):
    staged = stage(tmp_path)
    raw = raw_result(staged[1]); module = raw['module']
    if bad == 'module_id': module['module_id'] = 'other-module'
    elif bad == 'namespace': module['namespace'] = 'other_namespace'
    elif bad == 'film': module['film_sha256'] = 'f' * 64
    elif bad == 'path': module['files']['modules/scene_0/../outside.mjs'] = 'export const escaped=1;'
    elif bad == 'lifecycle': module['lifecycle']['seek'] = 'legacy-seek'
    elif bad == 'asset': module['assets'] = ['assets/unadmitted.js']
    elif bad == 'engine': module['engine'] = 'canvas'
    else: module['files_sha256'] = {module['entry']: 'f' * 64}
    with pytest.raises(ServiceError):
        collected(staged, raw)


@pytest.mark.parametrize('bad', ['clock_left', 'clock_right', 'long', 'state', 'object', 'retain', 'missing_transition', 'no_target'])
def test_transition_must_bind_all_shared_states_and_fit_the_real_adjacent_audio_clock(tmp_path, bad):
    first = collected(stage(tmp_path))[2]
    staged = stage(tmp_path, index=1, predecessor=predecessor_from_package(first))
    raw = raw_result(staged[1], transform=True)
    incoming = raw['incoming_boundary']
    if bad == 'clock_left': incoming['transition']['start_frame'] = -1
    elif bad == 'clock_right': incoming['transition']['end_frame'] = 49
    elif bad == 'long': incoming['transition']['start_frame'] = 24
    elif bad == 'state': incoming['transition']['module']['states']['entry']['subject']['x'] = 999
    elif bad == 'object': incoming['objects'] = []
    elif bad == 'retain': incoming['objects'][0]['mode'] = 'retain'
    elif bad == 'missing_transition': incoming['transition'] = None
    else:
        # The frozen directory cannot be reused for a changed target.
        staged[1]['target']['transition_target'] = None
    if bad == 'long':
        incoming['transition']['end_frame'] = 24  # Empty window at the cut.
    with pytest.raises(ServiceError):
        collected(staged, raw)


def test_real_root_read_requires_an_anchored_decision_but_unread_directory_entries_need_none(tmp_path):
    staged = stage(tmp_path, count=5)
    workspace, document, delivery, envelope, _ = staged
    assert 'INDEPENDENT-BODY' not in canonical_bytes(document).decode()
    observer = P.VideoAnimationFilmModuleProfile().create_tool_observer(workspace, envelope.task)
    key = next(iter(delivery['packages']))
    observe(observer, 'skill_search', {'query': 'relationship'}, 'search')
    observe(observer, 'skill_read', {'package_sha256': key}, 'root')
    raw = raw_result(document)
    raw['skill_decisions'] = [dict(package_sha256=key, action='adapt', reason='Maintain a distinct subject.',
        scene_targets=[dict(target_id='subject', description='One identifiable subject.',
            source_file=raw['module']['entry'], source_anchor='node.id="subject"')])]
    assert len(collected(staged, raw, observer)[0]['skill_decisions']) == 1


@pytest.mark.parametrize('mode', ['unread', 'search', 'failed_root', 'missing_decision', 'wrong_anchor'])
def test_self_reported_reads_search_hits_and_invalid_anchors_do_not_prove_skill_use(tmp_path, mode):
    staged = stage(tmp_path, count=1)
    workspace, document, delivery, envelope, _ = staged
    observer = P.VideoAnimationFilmModuleProfile().create_tool_observer(workspace, envelope.task)
    key = next(iter(delivery['packages']))
    if mode == 'search': observe(observer, 'skill_search', {'query': ''})
    elif mode == 'failed_root':
        observer.observe(dict(type='tool_execution_start', toolName='skill_read', toolCallId='failed', args={'package_sha256': key}))
        observer.observe(dict(type='tool_execution_end', toolName='skill_read', toolCallId='failed', isError=True))
    elif mode in {'missing_decision', 'wrong_anchor'}: observe(observer, 'skill_read', {'package_sha256': key})
    raw = raw_result(document)
    if mode != 'missing_decision':
        raw['skill_decisions'] = [dict(package_sha256=key, action='adapt', reason='An asserted read.',
            scene_targets=[dict(target_id='subject', description='A target.', source_file=raw['module']['entry'],
                source_anchor='not present' if mode == 'wrong_anchor' else 'node.id="subject"')])]
    with pytest.raises(ServiceError):
        collected(staged, raw, observer)


@pytest.mark.parametrize('bad', ['unstopped', 'wrong_attempt', 'checkpoint', 'extra_input', 'wrong_profile', 'evidence'])
def test_stop_proof_exact_input_inventory_and_native_host_evidence_are_required(tmp_path, bad):
    staged = stage(tmp_path)
    workspace, _, _, envelope, proof = staged
    handler = P.VideoAnimationFilmModuleProfile()
    evidence = handler.create_tool_observer(workspace, envelope.task).finish(envelope.attempt_id)
    if bad == 'unstopped': proof = replace(proof, all_writers_stopped=False)
    elif bad == 'wrong_attempt': proof = replace(proof, execution_id=str(uuid4()))
    elif bad == 'checkpoint': envelope.checkpoint_ref = 'manufactured'
    elif bad == 'extra_input': (workspace / 'inputs' / 'not-frozen.txt').write_text('Extra input')
    elif bad == 'wrong_profile': envelope.profile = 'video.animation-production.v3'
    else: evidence['input_sha256'] = 'f' * 64
    with pytest.raises(ServiceError):
        handler.collect_output_with_host_evidence(workspace, envelope, proof, evidence)


@pytest.mark.parametrize('field', ['skill_tool_evidence_sha256', 'sound_events', 'publication', 'source'])
def test_agent_cannot_supply_host_fields_unsupported_audio_or_old_seek_output(tmp_path, field):
    staged = stage(tmp_path)
    raw = raw_result(staged[1]); raw[field] = []
    with pytest.raises(ServiceError, match='agent_output_invalid'):
        collected(staged, raw)


def test_plain_collector_rejects_output_without_independent_skill_read_evidence(tmp_path):
    workspace, _, _, envelope, proof = stage(tmp_path)
    with pytest.raises(ServiceError, match='host_evidence_required'):
        P.VideoAnimationFilmModuleProfile().collect_output(workspace, envelope, proof)


@pytest.mark.parametrize('field', ['from_module_sha256', 'to_module_sha256', 'at_frame',
                                 'outgoing_state_sha256', 'module_sha256', 'orphan_transition'])
def test_normalized_boundary_is_recomputed_from_source_and_predecessor_not_trusted_as_a_receipt(tmp_path, field):
    first = collected(stage(tmp_path))[2]
    staged = stage(tmp_path, index=1, predecessor=predecessor_from_package(first))
    result, evidence, _ = collected(staged, raw_result(staged[1], transform=True))
    incoming = result['incoming_boundary']; boundary = incoming['boundary']
    if field in {'from_module_sha256', 'to_module_sha256'}:
        boundary[field] = 'f' * 64
    elif field == 'at_frame': boundary[field] = 23
    elif field == 'outgoing_state_sha256': boundary['objects'][0][field] = 'f' * 64
    elif field == 'module_sha256': boundary['transition'][field] = 'f' * 64
    else:
        boundary['transition'] = None
        boundary['objects'][0]['mode'] = 'retain'
    with pytest.raises(ServiceError):
        C.validate_result(result, digest(staged[1]), staged[1], read_package_sha256s=set(),
                          tool_evidence_sha256=evidence['sha256'])


@pytest.mark.parametrize('bad', ['identity', 'source_hash', 'state_hash', 'foreign_film', 'legacy'])
def test_new_predecessor_requires_exact_module_custody_instead_of_legacy_handoff(tmp_path, bad):
    first = collected(stage(tmp_path))[2]
    task = core(1, predecessor_from_package(first))
    predecessor = task['film_context']['predecessor']
    if bad == 'identity': predecessor['module_sha256'] = 'f' * 64
    elif bad == 'source_hash':
        name = next(iter(predecessor['module']['files_sha256']))
        predecessor['module']['files_sha256'][name] = 'f' * 64
    elif bad == 'state_hash': predecessor['module']['states']['exit']['subject']['x'] = 100
    elif bad == 'foreign_film':
        predecessor['module']['film_sha256'] = 'f' * 64
        predecessor['module_sha256'] = digest(predecessor['module'])
    else: task['film_context']['predecessor'] = {'handoff': 'old source declaration'}
    with pytest.raises(ServiceError):
        C.validate_core_task(task)


@pytest.mark.parametrize('assets', [None, ['assets/valid.svg', 1], {'assets/not-a-list.svg': 'x'}])
def test_malformed_agent_asset_declarations_fail_as_contract_errors(tmp_path, assets):
    staged = stage(tmp_path)
    raw = raw_result(staged[1]); raw['module']['assets'] = assets
    with pytest.raises(ServiceError, match='assets_invalid'):
        collected(staged, raw)
