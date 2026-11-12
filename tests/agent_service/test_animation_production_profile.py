"""Stopped production artifacts and independently packaged worker contract."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_production as contract
from agent_service.profiles.video_animation_production_contract import validate_input


def document():
    # Plain wire fixture deliberately imports no video_workbench code.
    narration = '对象先变化。'
    beats = dict(schema='video.beats.v1', source_text=narration, spoken_text=narration,
        segments=[dict(id='beat_' + '1' * 16, source_start=0, source_end=len(narration),
            spoken_start=0, spoken_end=len(narration), source_text=narration, spoken_text=narration)])
    alignment = dict(schema='video.alignment.v1', method='estimated', review_status='pending',
        audio_sha256='a' * 64, spoken_sha256=hashlib.sha256(narration.encode()).hexdigest(),
        audio_seconds=3, version='fixture-clock', segments=[dict(beat_id=beats['segments'][0]['id'], start=0, end=3)],
        reviewed_by=None, reviewed_at=None)
    core = dict(scene=dict(scene_id='fixture', title='对象变化', visual_description='对象自己移动。',
        narration=narration, beats=beats, alignment=alignment, audio_sha256='a' * 64, style='', instruction=''),
        constraints=dict(width=1280, height=720, fps=24, duration_seconds=3, allowed_engines=['html-svg']),
        assets={}, base_source=None)
    from agent_service.profiles.video_animation_production_contract import DELIVERY_POLICY
    delivery = dict(schema='video.production-method-delivery.v1', recipient_owner='fixture-user',
        consumer_id='fixture-operation', task_sha256=digest(core), allowed_engines=['html-svg'],
        selection=dict(algorithm='conservative-lexical-shortlist-v1', offered=[], excluded=[],
                       disposition='free_creation_no_match'), methods=[], read_receipt=None, policy=DELIVERY_POLICY,
        evidence_scope='frozen_input_delivery_not_agent_application')
    delivery['sha256'] = digest(delivery)
    return validate_input(dict(schema=contract.INPUT_SCHEMA, **core, method_delivery=delivery))


def raw_result(context):
    return dict(schema=contract.RESULT_SCHEMA, input_sha256=digest(context),
        source=dict(engine='html-svg', strategy=dict(description='Original object motion', tradeoffs=''),
                    files={'scene.html': '<svg id="object"></svg>'}),
        method_decision=dict(delivery_sha256=context['method_delivery']['sha256'], decisions=[]))


def engine_document(engine='canvas'):
    from agent_service.profiles.video_animation_production_contract import THREE_ASSET_SHA256
    context = document()
    context['constraints']['allowed_engines'] = [engine]
    context['constraints']['runtime_identity'] = dict(image_digest='sha256:' + 'a' * 64,
        host_sha256='b' * 64, engine_runtime_sha256='c' * 64, admission_id='UNIT-TEST-only')
    if engine == 'three':
        context['assets'] = {'three.module.min.js': THREE_ASSET_SHA256,
                            'three.LICENSE.txt': '4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454'}
    delivery = context['method_delivery']
    delivery['allowed_engines'] = [engine]
    delivery['task_sha256'] = digest({key: context[key] for key in ('scene', 'constraints', 'assets', 'base_source')})
    delivery['sha256'] = digest({key: value for key, value in delivery.items() if key != 'sha256'})
    return context


@pytest.mark.parametrize('engine', ['canvas', 'three'])
def test_v2_accepts_exact_runtime_authorized_subset_and_cannot_expand_legacy_v1(engine):
    from agent_service.profiles.video_animation_production_contract import validate_core_task
    context = engine_document(engine)
    assert validate_input(context) == context
    core = {key: context[key] for key in ('scene', 'constraints', 'assets', 'base_source')}
    assert validate_core_task(core, engine_aware=True) == core
    # The frozen v1 path keeps both its input identity and HTML-only admission.
    legacy = deepcopy(context)
    legacy['schema'] = contract.LEGACY_INPUT_SCHEMA
    with pytest.raises(ServiceError):
        validate_input(legacy)
    without_identity = deepcopy(core)
    del without_identity['constraints']['runtime_identity']
    with pytest.raises(ServiceError, match='runtime_identity_required'):
        validate_core_task(without_identity, engine_aware=True)
    bad = deepcopy(core)
    bad['constraints']['allowed_engines'] = [engine, engine]
    with pytest.raises(ServiceError, match='engine_not_admitted'):
        validate_core_task(bad, engine_aware=True)
    if engine == 'three':
        bad = deepcopy(core)
        bad['assets']['three.module.min.js'] = 'd' * 64
        with pytest.raises(ServiceError, match='three_asset_not_admitted'):
            validate_core_task(bad, engine_aware=True)


def test_result_schema_must_match_input_generation_and_cannot_claim_another_engine():
    context = engine_document()
    output = raw_result(context)
    output['source']['engine'] = 'canvas'
    output['source']['files_sha256'] = {'scene.html': hashlib.sha256(
        output['source']['files']['scene.html'].encode()).hexdigest()}
    assert contract.validate_result(output, digest(context), context) == output
    output['schema'] = contract.LEGACY_RESULT_SCHEMA
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        contract.validate_result(output, digest(context), context)
    output['schema'] = contract.RESULT_SCHEMA
    output['source']['engine'] = 'html-svg'
    with pytest.raises(ServiceError, match='engine_not_allowed'):
        contract.validate_result(output, digest(context), context)


def test_v2_bundle_is_complete_and_legacy_prompt_resources_remain_verifiable():
    from agent_service import build_image
    assert contract.BUNDLE_REVISION == 'animation-production-v2'
    assert set(contract.verified_bundle()) == contract.BUNDLE_FILES
    assert set(contract.verified_bundle(legacy=True)) == contract.BUNDLE_FILES
    profile = contract.VideoAnimationProductionProfile()
    current = profile.build_prompt({'input_sha256': 'a' * 64})
    legacy = profile.build_prompt({'input_sha256': 'a' * 64}, legacy=True)
    assert 'animation-production-result.v2' in current and 'WebGL2' in current
    assert 'animation-production-instructions-3' in current
    assert 'Engine is `html-svg`.' in legacy
    pins = {revision: sha for revision, sha, _ in build_image.ANIMATION_RUNTIME_BUNDLES}
    assert pins[contract.BUNDLE_REVISION] == contract.BUNDLE_MANIFEST_SHA256
    assert pins[contract.LEGACY_BUNDLE_REVISION] == contract.LEGACY_BUNDLE_MANIFEST_SHA256


def staged(tmp_path):
    context = document()
    raw = canonical_bytes(context)
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs' / contract.INPUT_NAME).write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(raw_result(context)))
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=contract.PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(context)}, inputs=[InlineFile.from_bytes(contract.INPUT_NAME, raw)],
        checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('offline-fixture',))
    return context, envelope, proof


def test_registered_profile_has_its_own_bundle_tools_and_provenance():
    handler = implementation(contract.PROFILE)
    assert type(handler) is contract.VideoAnimationProductionProfile
    assert handler.required_tools == ('read_inputs', 'write')
    assert handler.required_model_capabilities == ('text', 'tool_calls')
    assert set(contract.verified_bundle()) == contract.BUNDLE_FILES
    prompt = handler.build_prompt({'input_sha256': 'a' * 64})
    assert contract.INPUT_NAME in prompt and 'apply' in prompt and 'reject' in prompt
    assert 'source_anchor' in prompt and 'do not prove' in prompt.lower()
    assert 'animation-production-instructions-3' in prompt
    assert 'Strategy has exactly `description` and `tradeoffs`.' in prompt
    assert 'Both must be non-empty\n  JSON strings, at most 3000 characters each.' in prompt
    assert 'Do not use arrays, objects, null' in prompt
    assert '`source`: `engine`, `strategy`, `files` only.' in prompt
    assert 'The host computes `files_sha256`; do not include\n  hashes' in prompt
    assert 'video_workbench' not in Path(contract.__file__).read_text()


def test_collector_requires_stopped_writers_and_computes_source_hash(tmp_path):
    context, envelope, proof = staged(tmp_path)
    artifact = contract.VideoAnimationProductionProfile().collect_output(tmp_path, envelope, proof)[0]
    result = json.loads(artifact.content())
    assert artifact.name == contract.RESULT_NAME
    assert result['source']['files_sha256']['scene.html'] == hashlib.sha256(b'<svg id="object"></svg>').hexdigest()
    assert contract.validate_result(result, digest(context), context) == result
    proof = StopEvidence(execution_id=envelope.attempt_id, all_writers_stopped=False, resource_ids=('fixture',))
    with pytest.raises(ServiceError, match='writers_not_stopped'):
        contract.VideoAnimationProductionProfile().collect_output(tmp_path, envelope, proof)


@pytest.mark.parametrize('field', ['description', 'tradeoffs'])
@pytest.mark.parametrize('value', [['One limitation', 'Another limitation'], 'x' * 3001])
def test_collector_rejects_strategy_arrays_and_oversized_strings(tmp_path, field, value):
    context, envelope, proof = staged(tmp_path)
    result = raw_result(context)
    result['source']['strategy'][field] = value
    (tmp_path / 'output.json').write_bytes(canonical_bytes(result))
    with pytest.raises(ServiceError):
        contract.VideoAnimationProductionProfile().collect_output(tmp_path, envelope, proof)


def test_collector_accepts_strategy_string_limits_and_keeps_host_hash_custody(tmp_path):
    context, envelope, proof = staged(tmp_path)
    result = raw_result(context)
    result['source']['strategy'] = dict(description='d' * 3000, tradeoffs='t' * 3000)
    assert set(result['source']) == {'engine', 'strategy', 'files'}
    (tmp_path / 'output.json').write_bytes(canonical_bytes(result))
    collected = contract.VideoAnimationProductionProfile().collect_output(tmp_path, envelope, proof)[0]
    output = json.loads(collected.content())
    assert output['source']['strategy'] == result['source']['strategy']
    assert output['source']['files_sha256'] == {'scene.html': hashlib.sha256(
        result['source']['files']['scene.html'].encode()).hexdigest()}


@pytest.mark.parametrize('change', ['profile', 'reference', 'extra-file', 'hash', 'precomputed-hash', 'checkpoint'])
def test_invalid_custody_or_legacy_contract_cannot_be_collected(tmp_path, change):
    context, envelope, proof = staged(tmp_path)
    if change == 'profile': envelope.profile = 'video.animation-experiment.v1'
    elif change == 'reference': envelope.inputs = [InlineFile.from_bytes(contract.INPUT_NAME, b'changed')]
    elif change == 'extra-file': (tmp_path / 'inputs' / 'unapproved.txt').write_text('extra')
    elif change == 'hash': envelope.task['input_sha256'] = 'b' * 64
    elif change == 'checkpoint': envelope.checkpoint_ref = 'unapproved'
    else:
        value = raw_result(context); value['source']['files_sha256'] = {}
        (tmp_path / 'output.json').write_bytes(canonical_bytes(value))
    with pytest.raises(ServiceError):
        contract.VideoAnimationProductionProfile().collect_output(tmp_path, envelope, proof)


def test_canonical_input_is_required_before_model_admission(tmp_path):
    context = document()
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs' / contract.INPUT_NAME).write_text(json.dumps(context, indent=2), encoding='utf-8')
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        contract.VideoAnimationProductionProfile().validate_inputs(tmp_path, {'input_sha256': digest(context)})
