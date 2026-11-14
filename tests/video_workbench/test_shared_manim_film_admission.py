"""Shared-resource consumer custody using synthetic media, not live admission."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import film_authoring_resources, manim_film_admission as admission
from video_workbench.resource_placement import freeze_manim_placement
from test_film_authoring_resources import env, resources, start, declare_resource_in_synthetic_author
from test_film_creation_workflow import OWNER, module_request, render_request
from test_film_render_adapter import VIDEO, result_metadata


@pytest.fixture
def shared_evidence(resources, monkeypatch, tmp_path):
    current = resources
    attach = film_authoring_resources.attach_film_resource
    binding = {}

    def add_placement(core, descriptor, assets, *args, **kwargs):
        core = attach(core, descriptor, assets, *args, **kwargs)
        text = core['scene']['instruction']
        reference = json.loads(text.split(film_authoring_resources.BEGIN, 1)[1]
            .split(film_authoring_resources.END, 1)[0])
        placement = freeze_manim_placement(descriptor, start_frame=12,
            project_frame_count=round(current.film['total_seconds'] * 24))
        reference.update(placement=placement, resource_version_id='resource-version-synthetic')
        core['scene']['instruction'] = text.split(film_authoring_resources.BEGIN, 1)[0] \
            + film_authoring_resources.BEGIN + canonical_bytes(reference).decode('utf-8') + film_authoring_resources.END
        binding.update(project_id=current.project['id'], placement=placement,
            resource_version_id=reference['resource_version_id'])
        return core

    monkeypatch.setattr(film_authoring_resources, 'attach_film_resource', add_placement)
    declare_resource_in_synthetic_author(monkeypatch)
    first = start(current)
    current.workflow.tick()
    current.client.state, current.client.proven = 'completed', True
    current.workflow.tick()
    first = current.workflow.get_module(OWNER, first['id'])
    package = current.creation.module_package(OWNER, first['version_id'])
    second = current.workflow.start_module(OWNER, current.project['id'], current.project['scenes'][1]['id'],
        module_request(current, 1, first['version_id']), 'shared-admission-native-second')
    current.workflow.tick()
    second = current.workflow.get_module(OWNER, second['id'])
    render = current.workflow.start_render(OWNER, current.project['id'],
        render_request(current, [first, second]), 'shared-admission-synthetic-film')
    snapshot = current.creation.render_snapshot(render['id'])
    descriptor = current.client.descriptors[first['id']]
    required = {descriptor['manifest_path'], 'assets/manim_frame_layer.mjs',
        *(row['path'] for row in descriptor['manifest']['frames'])}
    capture = dict(bundle_sha256=snapshot['bundle_sha256'], film_sha256=snapshot['film_sha256'],
        probe=dict(loaded_resources=sorted('/project/' + name for name in required)))
    result = result_metadata(snapshot)
    result['film_export']['capture_sha256'] = digest(capture)
    view = current.client.get(OWNER, first['id'])
    view.pop('film_sha256')  # A prepared resource has no permanent origin-film binding.
    view['cancel_requested'] = False
    service = SimpleNamespace(root=tmp_path / 'producer', get=lambda owner, operation: deepcopy(view),
        envelope=current.client.envelope)
    binding['resource_operation_id'] = first['id']
    return dict(resource_service=service, owner=OWNER, operation_id=first['id'],
        snapshot=snapshot, result=result, media=VIDEO, capture=capture,
        consumer_binding=binding, consumer_package=package)


def test_shared_resource_requires_exact_consumer_binding_and_package(shared_evidence):
    value = admission.build_admission(**shared_evidence)
    assert value['schema'] == admission.SHARED_SCHEMA
    assert value['consumer']['binding'] == shared_evidence['consumer_binding']
    assert value['consumer']['package_sha256'] == digest(shared_evidence['consumer_package'])
    assert 'not_visual_acceptance' in value['scope']
    for absent in ['consumer_binding', 'consumer_package']:
        with pytest.raises(ServiceError, match='binding_required'):
            admission.build_admission(**{key: item for key, item in shared_evidence.items() if key != absent})
    legacy = {key: item for key, item in shared_evidence.items() if not key.startswith('consumer_')}
    with pytest.raises(ServiceError, match='consumer_film_mismatch'):
        admission.build_admission(**legacy)


def test_foreign_consumer_or_rehashed_placement_cannot_replace_author_input(shared_evidence):
    original = shared_evidence['consumer_binding']
    for field in ['project_id', 'resource_operation_id']:
        with pytest.raises(ServiceError, match='consumer_binding_invalid'):
            admission.build_admission(**dict(shared_evidence, consumer_binding=dict(original, **{field: 'another'})))
    with pytest.raises(ServiceError, match='consumer_reference_mismatch'):
        admission.build_admission(**dict(shared_evidence,
            consumer_binding=dict(original, resource_version_id='a-different-version')))
    altered = deepcopy(original)
    altered['placement']['global_start_frame'] += 1
    altered['placement']['sha256'] = digest({key: item for key, item in altered['placement'].items() if key != 'sha256'})
    with pytest.raises(ServiceError, match='consumer_reference_mismatch'):
        admission.build_admission(**dict(shared_evidence, consumer_binding=altered))
    altered['placement']['global_start_frame'] = 30  # The package's module is only active in [0,24).
    altered['placement']['sha256'] = digest({key: item for key, item in altered['placement'].items() if key != 'sha256'})
    with pytest.raises(ServiceError, match='consumer_placement_inactive'):
        admission.build_admission(**dict(shared_evidence, consumer_binding=altered))


def test_stored_assets_and_source_references_do_not_replace_actual_loaded_evidence(shared_evidence):
    changed = deepcopy(shared_evidence['capture'])
    changed['probe']['loaded_resources'].pop()
    result = deepcopy(shared_evidence['result'])
    result['film_export']['capture_sha256'] = digest(changed)
    with pytest.raises(ServiceError, match='assets_not_loaded'):
        admission.build_admission(**dict(shared_evidence, capture=changed, result=result))


def test_restart_reopens_original_consumer_package_and_retains_frozen_resource_binding(shared_evidence, tmp_path):
    value = admission.build_admission(**shared_evidence)
    path = tmp_path / 'shared-admission.json'
    path.write_bytes(canonical_bytes(value))
    root = shared_evidence['resource_service'].root / 'admissions' / shared_evidence['operation_id']
    root.mkdir(parents=True)
    for name in ['snapshot', 'result', 'capture']:
        (root / (name + '.json')).write_bytes(canonical_bytes(shared_evidence[name]))
    (root / 'video.mp4').write_bytes(VIDEO)
    (root / 'consumer-package.json').write_bytes(canonical_bytes(shared_evidence['consumer_package']))
    assert admission.validate_admission(path, resource_service=shared_evidence['resource_service']) == value
    changed = deepcopy(shared_evidence['consumer_package'])
    changed['input_document']['scene']['instruction'] += '\nchanged after admission'
    (root / 'consumer-package.json').write_bytes(canonical_bytes(changed))
    with pytest.raises(ServiceError, match='consumer_package_changed'):
        admission.validate_admission(path, resource_service=shared_evidence['resource_service'])
