"""New production contract: actual host tool reads and separate signed evidence.

Native events and media/source here are synthetic contract fixtures. No model,
renderer or developer acceptance is exercised or implied by these tests.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import build_skill_package, bundle_files, freeze_skill_catalog
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_skill_production as profile
from agent_service.profiles.video_animation_skill_production_contract import (
    CORE_FIELDS, INPUT_SCHEMA, RESULT_SCHEMA, validate_input, validate_result,
)
from test_animation_film_production_profile import film_document, handoff


def published(index):
    package = build_skill_package(skill_id=f'fixture-{index}', version=1, kind='expression',
        files={'SKILL.md': f'---\nname: fixture-{index}\ndescription: Track a relation and preserve identity.\n---\n'
            'Choose a mechanism suited to the question. See [checks](references/checks.md).',
            'references/checks.md': 'Keep the same subject identifiable before and after change.'},
        runtime_requirements={'engines': ['html-svg']})
    method = dict(schema='video.animation-method.v2', id=f'method-{index}', status='published',
        content_sha256=digest(dict(fixture=index)), skill_package=package)
    method['publication'] = dict(schema='video.animation-method-publication.v2',
        method_version_id=method['id'], method_content_sha256=method['content_sha256'],
        skill_package_sha256=package['sha256'], reference_validation_ids=[f'reference-{index}'],
        transfer_validation_ids=[f'transfer-{index}'])
    return method


def context(count=1, index=0):
    document = film_document(index)
    document.pop('method_delivery')
    document['schema'] = INPUT_SCHEMA
    methods = [published(i) for i in range(count)]
    snapshot = freeze_skill_catalog(methods, recipient_owner='fixture-user', consumer_id='fixture-production',
        task_sha256=digest({key: document[key] for key in CORE_FIELDS}),
        allowed_engines=document['constraints']['allowed_engines'])
    document['skill_catalog'] = snapshot
    return validate_input(document), {row['skill_package']['sha256']: row['skill_package'] for row in methods}


def decision(package_sha256, action='adapt'):
    targets = [] if action == 'reject' else [dict(target_id='object', description='Preserve the subject through change.',
        source_file='scene.html', source_anchor='id="object"')]
    return dict(package_sha256=package_sha256, action=action, reason='Fits this specific identity problem.',
                scene_targets=targets)


def raw_result(document, decisions=None):
    return dict(schema=RESULT_SCHEMA, input_sha256=digest(document),
        source=dict(engine='html-svg', strategy=dict(description='Original continuity mechanism.', tradeoffs='Fixed view.'),
                    files={'scene.html': '<svg id="object"></svg>'}),
        skill_decisions=[] if decisions is None else decisions,
        handoff=handoff(document['film_context']['film_sha256'], document['scene']['scene_id']))


def stage(tmp_path, count=1, index=0):
    document, packages = context(count, index)
    inputs = [InlineFile.from_bytes(profile.INPUT_NAME, canonical_bytes(document)),
              *bundle_files(document['skill_catalog'], packages)]
    for item in inputs:
        target = tmp_path / 'inputs' / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content())
    (tmp_path / 'output.json').write_bytes(canonical_bytes(raw_result(document)))
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=profile.PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(document)}, inputs=inputs, checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('synthetic-stopped-fixture',))
    return document, packages, envelope, proof


def observe(observer, tool, params, call_id):
    observer.observe(dict(type='tool_execution_start', toolName=tool, toolCallId=call_id, args=params))
    observer.observe(dict(type='tool_execution_end', toolName=tool, toolCallId=call_id,
        isError=False, result=observer.tools.tool_result(tool, params)))


def collect(tmp_path, handler, envelope, proof, observer):
    return handler.collect_output_with_host_evidence(tmp_path, envelope, proof, observer.finish(envelope.attempt_id))


def test_new_profile_registered_separately_and_preserves_frozen_earlier_bundles():
    from agent_service.profiles import video_animation_production as previous
    handler = implementation(profile.PROFILE)
    assert type(handler) is profile.VideoAnimationSkillProductionProfile
    assert handler.required_tools == ('read_inputs', 'skill_search', 'skill_read', 'write')
    assert handler.required_model_capabilities == ('text', 'tool_calls')
    assert set(profile.verified_bundle()) == profile.BUNDLE_FILES
    assert (profile.BUNDLE_ROOT / 'foundation/SKILL.md').read_bytes() == (
        profile.BUNDLE_ROOT.parent / 'animation-hyperframes-foundation-v1/SKILL.md').read_bytes()
    prompt = handler.build_prompt({'input_sha256': 'a' * 64})
    assert 'animation-production-instructions-5' in prompt and 'skill_search' in prompt
    assert 'skill_tool_evidence_sha256' in prompt and 'film_context' in prompt
    assert 'top-three' in prompt and 'unread packages' in prompt
    assert set(previous.verified_bundle()) == previous.BUNDLE_FILES
    assert set(previous.verified_bundle(film=True)) == previous.BUNDLE_FILES
    old = previous.VideoAnimationFilmProductionProfile().build_prompt({'input_sha256': 'a' * 64})
    assert 'animation-production-instructions-4' in old and 'skill_search' not in old


@pytest.mark.parametrize('index', [0, 1])
def test_full_film_context_and_catalog_bind_the_exact_whole_task(index):
    document, _ = context(5, index)
    assert len(document['skill_catalog']['skills']) == 5  # No fixed max-three shortlist.
    assert validate_input(document) == document
    changed = deepcopy(document)
    changed['film_context']['film']['style'] = 'Changed full-film style.'
    changed['film_context']['film_sha256'] = digest(changed['film_context']['film'])
    if index:
        changed['film_context']['predecessor']['handoff']['film_sha256'] = changed['film_context']['film_sha256']
    with pytest.raises(ServiceError, match='catalog_binding_mismatch'):
        validate_input(changed)


def test_no_match_and_no_root_reads_allow_free_creation_without_manufactured_decisions(tmp_path):
    document, _, envelope, proof = stage(tmp_path, 0)
    handler = profile.VideoAnimationSkillProductionProfile()
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    observe(observer, 'skill_search', {'query': 'an unavailable mechanism'}, 'search-empty')
    artifacts = collect(tmp_path, handler, envelope, proof, observer)
    assert [row.name for row in artifacts] == [profile.RESULT_NAME, profile.EVIDENCE_NAME]
    result, evidence = (json.loads(row.content()) for row in artifacts)
    assert result['skill_decisions'] == []
    assert result['skill_tool_evidence_sha256'] == evidence['sha256']
    assert validate_result(result, digest(document), document, read_package_sha256s=set(),
                           tool_evidence_sha256=evidence['sha256']) == result


def test_actual_root_reads_require_one_decision_but_unread_catalog_entries_need_none(tmp_path):
    document, packages, envelope, proof = stage(tmp_path, 5, 1)
    handler = profile.VideoAnimationSkillProductionProfile()
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    keys = list(packages)
    observe(observer, 'skill_search', {'query': 'identity'}, 'search')
    observe(observer, 'skill_read', {'package_sha256': keys[0]}, 'read-first-root')
    observe(observer, 'skill_read', {'package_sha256': keys[0], 'path': 'references/checks.md'}, 'read-attachment')
    observe(observer, 'skill_read', {'package_sha256': keys[0], 'path': 'SKILL.md'}, 'repeat-root')
    observe(observer, 'skill_read', {'package_sha256': keys[1]}, 'read-second-root')
    authored = raw_result(document, [decision(keys[0]), decision(keys[1], 'reject')])
    (tmp_path / 'output.json').write_bytes(canonical_bytes(authored))
    result_file, evidence_file = collect(tmp_path, handler, envelope, proof, observer)
    result, evidence = json.loads(result_file.content()), json.loads(evidence_file.content())
    assert len(result['skill_decisions']) == 2 and len(evidence['events']) == 5
    assert result['source']['files_sha256']['scene.html'] == hashlib.sha256(b'<svg id="object"></svg>').hexdigest()
    assert 'method_delivery' not in result and 'method_decision' not in result
    assert validate_result(result, digest(document), document, read_package_sha256s={keys[0], keys[1]},
                           tool_evidence_sha256=evidence['sha256']) == result


@pytest.mark.parametrize('read_mode', ['search_only', 'attachment_only', 'failed_root'])
def test_search_hits_attachment_reads_and_failed_calls_are_not_successful_root_reads(tmp_path, read_mode):
    document, packages, envelope, proof = stage(tmp_path)
    handler = profile.VideoAnimationSkillProductionProfile()
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    key = next(iter(packages))
    if read_mode == 'search_only':
        observe(observer, 'skill_search', {'query': ''}, 'search')
    elif read_mode == 'attachment_only':
        observe(observer, 'skill_read', {'package_sha256': key, 'path': 'references/checks.md'}, 'attachment')
    else:
        observer.observe(dict(type='tool_execution_start', toolName='skill_read', toolCallId='failed', args={'package_sha256': key}))
        observer.observe(dict(type='tool_execution_end', toolName='skill_read', toolCallId='failed', isError=True))
    (tmp_path / 'output.json').write_bytes(canonical_bytes(raw_result(document, [decision(key)])))
    with pytest.raises(ServiceError, match='decisions_incomplete'):
        collect(tmp_path, handler, envelope, proof, observer)


@pytest.mark.parametrize('bad', ['missing', 'extra', 'duplicate', 'anchor'])
def test_decisions_are_for_exact_actually_read_versions_and_inspectable_source_anchors(tmp_path, bad):
    document, packages, envelope, proof = stage(tmp_path, 2)
    handler = profile.VideoAnimationSkillProductionProfile()
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    keys = list(packages)
    observe(observer, 'skill_read', {'package_sha256': keys[0]}, 'root')
    decisions = [] if bad == 'missing' else [decision(keys[0])]
    if bad == 'extra': decisions = [decision(keys[1])]
    elif bad == 'duplicate': decisions *= 2
    elif bad == 'anchor': decisions[0]['scene_targets'][0]['source_anchor'] = 'does-not-exist'
    (tmp_path / 'output.json').write_bytes(canonical_bytes(raw_result(document, decisions)))
    with pytest.raises(ServiceError):
        collect(tmp_path, handler, envelope, proof, observer)


@pytest.mark.parametrize('field', ['skill_tool_evidence_sha256', 'tool_evidence', 'read_receipt', 'publication'])
def test_agent_cannot_supply_host_evidence_or_manufacture_permission(tmp_path, field):
    document, _, envelope, proof = stage(tmp_path, 0)
    handler = profile.VideoAnimationSkillProductionProfile()
    result = raw_result(document)
    result[field] = 'f' * 64
    (tmp_path / 'output.json').write_bytes(canonical_bytes(result))
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    with pytest.raises(ServiceError, match='agent_output_invalid'):
        collect(tmp_path, handler, envelope, proof, observer)


@pytest.mark.parametrize('bad', ['attempt', 'input', 'source', 'snapshot', 'hash'])
def test_host_evidence_must_match_exact_attempt_input_catalog_and_native_scope(tmp_path, bad):
    _, _, envelope, proof = stage(tmp_path, 0)
    handler = profile.VideoAnimationSkillProductionProfile()
    evidence = handler.create_tool_observer(tmp_path, envelope.task).finish(envelope.attempt_id)
    key = {'attempt': 'attempt_id', 'input': 'input_sha256', 'source': 'source',
           'snapshot': 'snapshot_sha256', 'hash': 'sha256'}[bad]
    evidence[key] = str(uuid4()) if bad == 'attempt' else 'f' * 64
    if bad != 'hash':
        evidence['sha256'] = digest({k: v for k, v in evidence.items() if k != 'sha256'})
    with pytest.raises(ServiceError):
        handler.collect_output_with_host_evidence(tmp_path, envelope, proof, evidence)


@pytest.mark.parametrize('bad', ['unstopped', 'checkpoint', 'checkpoint_ref', 'envelope_file', 'extra_input', 'tamper_skill'])
def test_stopped_writer_exact_normal_input_and_complete_package_membership_gates(tmp_path, bad):
    _, packages, envelope, proof = stage(tmp_path)
    handler = profile.VideoAnimationSkillProductionProfile()
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    if bad == 'unstopped':
        proof = StopEvidence(execution_id=envelope.attempt_id, all_writers_stopped=False, resource_ids=('fixture',))
    elif bad == 'checkpoint': envelope.checkpoint_inputs = [envelope.inputs[0]]
    elif bad == 'checkpoint_ref': envelope.checkpoint_ref = 'fake-checkpoint'
    elif bad == 'envelope_file': envelope.inputs = envelope.inputs[:-1]
    elif bad == 'extra_input': (tmp_path / 'inputs' / 'unadmitted.txt').write_text('Not in the frozen inputs.')
    else:
        sha = next(iter(packages))
        (tmp_path / 'inputs' / 'animation-skills' / 'packages' / sha / 'SKILL.md').write_text('Changed bytes.')
    with pytest.raises(ServiceError):
        collect(tmp_path, handler, envelope, proof, observer)


def test_regular_collection_cannot_fall_back_to_agent_self_reported_reads(tmp_path):
    _, _, envelope, proof = stage(tmp_path, 0)
    with pytest.raises(ServiceError, match='host_evidence_required'):
        profile.VideoAnimationSkillProductionProfile().collect_output(tmp_path, envelope, proof)


def test_new_profile_rejects_older_input_instead_of_rewriting_frozen_contract(tmp_path):
    old = film_document()
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs' / profile.INPUT_NAME).write_bytes(canonical_bytes(old))
    with pytest.raises(ServiceError, match='input_invalid'):
        profile.VideoAnimationSkillProductionProfile().validate_inputs(tmp_path, {'input_sha256': digest(old)})


@pytest.mark.parametrize('limit', ['max_input_files', 'max_input_total_bytes'])
def test_too_large_discovery_fails_explicitly_instead_of_truncating_the_catalog(tmp_path, limit):
    _, _, envelope, _ = stage(tmp_path, 5)
    handler = profile.VideoAnimationSkillProductionProfile()
    handler.limits = replace(handler.limits, **{limit: 1})
    with pytest.raises(ServiceError, match='input_too_large'):
        handler.validate_inputs(tmp_path, envelope.task)
