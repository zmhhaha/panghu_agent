"""Bounded scope/transport and fixed Job tests, never an actual cluster run."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.engines import StopEvidence
from video_workbench import manim_generated_assets as G
from video_workbench import manim_resource_job as J
from test_manim_resource import compiled, request, IMAGE

SCOPE = dict(owner='owner', effect_id='effect', experiment_id='experiment', operation_id='resource-op')
PINNED = 'registry.internal/manim-resource@' + IMAGE


def envelope():
    recipe, files, checksum = compiled()
    proof = StopEvidence(execution_id=SCOPE['operation_id'], all_writers_stopped=True,
        resource_ids=('isolated-fixture-pod',))
    value = G.freeze_generated_assets(files, recipe, **SCOPE, runtime_image_digest=IMAGE, stop_evidence=proof)
    return value, files


def test_only_exact_stopped_resource_round_trips_inside_same_owner_effect_and_attempt():
    value, files = envelope()
    assert G.decode_generated_assets(value, expected_sha256=value['sha256'], **SCOPE,
        runtime_image_digest=IMAGE) == files
    assert value['request']['source'] == request()['source']
    assert value['writer_stop']['all_writers_stopped'] is True


@pytest.mark.parametrize('field', list(SCOPE) + ['runtime_image_digest'])
def test_cross_owner_effect_experiment_or_runtime_cannot_reuse_transport(field):
    value, _ = envelope()
    expected = dict(**SCOPE, runtime_image_digest=IMAGE)
    expected[field] = 'other' if field != 'runtime_image_digest' else 'sha256:' + 'b' * 64
    with pytest.raises(ServiceError, match='binding_mismatch'):
        G.decode_generated_assets(value, expected_sha256=value['sha256'], **expected)


@pytest.mark.parametrize('change', ['missing', 'extra', 'bytes', 'base64', 'digest', 'source', 'stop'])
def test_even_rehashed_envelopes_reject_changed_resource_contract(change):
    value, _ = envelope()
    first = next(name for name in value['files'] if name.endswith('.png'))
    if change == 'missing':
        value['files'].pop(first)
    elif change == 'extra':
        value['files']['assets/manim/geometry/extra.mp4'] = value['files'][first]
    elif change == 'bytes':
        value['files'][first]['bytes'] += 1
    elif change == 'base64':
        value['files'][first]['base64'] = '!' * len(value['files'][first]['base64'])
    elif change == 'digest':
        value['files'][first]['sha256'] = 'f' * 64
    elif change == 'source':
        value['request']['source'] += '# changed\n'
    else:
        value['writer_stop']['all_writers_stopped'] = False
    value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
    with pytest.raises(ServiceError):
        G.decode_generated_assets(value, expected_sha256=value['sha256'], **SCOPE, runtime_image_digest=IMAGE)


def test_resource_merger_preserves_runtime_inventory_and_rejects_collisions_and_slots():
    recipe, files, checksum = compiled()
    existing = {'assets/gsap.min.js': b'existing runtime fixture'}
    merged = G.merge_generated_assets(existing, [(files, recipe, checksum, IMAGE)])
    assert merged == existing | files
    assert existing == {'assets/gsap.min.js': b'existing runtime fixture'}
    with pytest.raises(ServiceError, match='collision'):
        G.merge_generated_assets(files, [(files, recipe, checksum, IMAGE)])
    with pytest.raises(ServiceError, match='asset_slots_exceeded'):
        G.merge_generated_assets(existing, [(files, recipe, checksum, IMAGE)], max_module_assets=4)


def plan():
    return J.freeze_resource_job(request(), **SCOPE, image=PINNED,
        recipe_input_sha256='1' * 64, recipe_result_sha256='2' * 64,
        recipe_stop_proof_sha256='3' * 64)


def test_job_has_fixed_command_exact_pvc_paths_no_network_and_no_retry():
    value = plan()
    resources = J.resource_job_resources(value, expected_sha256=value['sha256'], image=PINNED,
        namespace='video', pvc_name='video-data', operation_key='c' * 64)
    job = resources['job']
    pod = job['spec']['template']['spec']
    container, = pod['containers']
    assert container['image'] == PINNED
    assert container['command'] == ['python', '-m', 'video_workbench.manim_resource_worker']
    assert container['args'] == ['--request', '/input/request.json', '--output', '/output/attempt']
    assert pod['automountServiceAccountToken'] is False
    assert container['securityContext']['readOnlyRootFilesystem'] is True
    assert container['securityContext']['capabilities']['drop'] == ['ALL']
    assert job['spec']['backoffLimit'] == 0
    assert pod['restartPolicy'] == 'Never'
    assert next(row for row in container['volumeMounts'] if row['mountPath'] == '/input')['readOnly'] is True
    assert all(row['subPath'] for row in container['volumeMounts'] if row['name'] == 'data')
    assert resources['network_policy']['spec']['ingress'] == resources['network_policy']['spec']['egress'] == []
    assert resources['evidence_scope'] == 'resources_not_submitted_or_stopped'


@pytest.mark.parametrize('field', ['request', 'scope', 'image', 'recipe_stop_proof_sha256'])
def test_job_identity_cannot_change_after_journal_binding(field):
    value = plan()
    expected = value['sha256']
    if field == 'request':
        value['request']['source'] += '# changed\n'
    elif field == 'scope':
        value['scope']['owner'] = 'other'
    else:
        value[field] = 'changed'
    with pytest.raises(ServiceError):
        J.resource_job_resources(value, expected_sha256=expected, image=PINNED,
            namespace='video', pvc_name='video-data', operation_key='c' * 64)
