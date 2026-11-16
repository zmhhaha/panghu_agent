"""Admission custody tests with explicit synthetic media, no real admission."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import manim_film_admission as admission
from test_film_authoring_resources import (env, resources, start,
    declare_resource_in_synthetic_author)
from test_film_creation_workflow import OWNER, module_request, render_request
from test_film_render_adapter import VIDEO, result_metadata


@pytest.fixture
def evidence(resources, monkeypatch, tmp_path):
    current = resources
    declare_resource_in_synthetic_author(monkeypatch)
    first = start(current)
    current.workflow.tick()
    current.client.state, current.client.proven = 'completed', True
    current.workflow.tick()
    first = current.workflow.get_module(OWNER, first['id'])
    second = current.workflow.start_module(OWNER, current.project['id'], current.project['scenes'][1]['id'],
        module_request(current, 1, first['version_id']), 'admission-second-native')
    current.workflow.tick()
    second = current.workflow.get_module(OWNER, second['id'])
    render = current.workflow.start_render(OWNER, current.project['id'],
        render_request(current, [first, second]), 'admission-synthetic-film')
    snapshot = current.creation.render_snapshot(render['id'])
    descriptor = current.client.descriptors[first['id']]
    required = {descriptor['manifest_path'], 'assets/manim_frame_layer.mjs',
        *(row['path'] for row in descriptor['manifest']['frames'])}
    capture = dict(bundle_sha256=snapshot['bundle_sha256'], film_sha256=snapshot['film_sha256'],
        probe=dict(loaded_resources=sorted('/project/' + name for name in required)))
    result = result_metadata(snapshot)
    result['film_export']['capture_sha256'] = digest(capture)
    view = current.client.get(OWNER, first['id'])
    view['cancel_requested'] = False
    service = SimpleNamespace(root=tmp_path / 'producer', get=lambda owner, operation_id: deepcopy(view),
        envelope=current.client.envelope)
    values = dict(resource_service=service, owner=OWNER, operation_id=first['id'],
        snapshot=snapshot, result=result, media=VIDEO, capture=capture)
    return values


def test_actual_loads_are_required_beyond_source_and_declared_asset_inventory(evidence):
    value = admission.build_admission(**evidence)
    assert value['evidence']['capture_sha256'] == digest(evidence['capture'])
    assert 'not_visual_acceptance' in value['scope']
    original = evidence['capture']['probe']['loaded_resources']
    # A correctly hashed receipt still cannot replace any absent actual load.
    for missing in original:
        changed = deepcopy(evidence['capture'])
        changed['probe']['loaded_resources'].remove(missing)
        result = deepcopy(evidence['result'])
        result['film_export']['capture_sha256'] = digest(changed)
        with pytest.raises(ServiceError, match='assets_not_loaded'):
            admission.build_admission(**dict(evidence, capture=changed, result=result))
    with pytest.raises(ServiceError, match='capture_binding'):
        admission.build_admission(**dict(evidence, capture={**evidence['capture'], 'changed': True}))
    with pytest.raises(ServiceError, match='media_changed'):
        admission.build_admission(**dict(evidence, media=VIDEO + b'changed'))


def test_restart_reopens_exact_evidence_and_context_pins_actual_release(evidence, tmp_path):
    value = admission.build_admission(**evidence)
    path = tmp_path / 'admission.json'
    path.write_bytes(canonical_bytes(value))
    root = evidence['resource_service'].root / 'admissions' / evidence['operation_id']
    root.mkdir(parents=True)
    for name in ('snapshot', 'result', 'capture'):
        (root / (name + '.json')).write_bytes(canonical_bytes(evidence[name]))
    (root / 'video.mp4').write_bytes(VIDEO)
    assert admission.validate_admission(path, resource_service=evidence['resource_service']) == value
    context = admission.consumer_context(value)
    release = value['formal_release']
    assert admission.validate_consumer_context(context, runtime=release['runtime_fingerprint'],
        hosts=release['trusted_host_sha256'], image=release['image']) == context
    with pytest.raises(ServiceError, match='release_mismatch'):
        admission.validate_consumer_context(context, runtime=release['runtime_fingerprint'],
            hosts=release['trusted_host_sha256'], image=release['image'].replace('sha256:', 'sha256:a'))
    capture_path = root / 'capture.json'
    changed = json.loads(capture_path.read_bytes())
    changed['probe']['loaded_resources'] = []
    capture_path.write_bytes(canonical_bytes(changed))
    with pytest.raises(ServiceError, match='capture_binding'):
        admission.validate_admission(path, resource_service=evidence['resource_service'])
