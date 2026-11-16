"""New animation candidate roles preserve explicit immutable projected sources."""
import base64
import hashlib

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.deploy import acceptance_probe, candidate_scenario
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from agent_service.worker_entry import host_policy
from test_animation_review_catalog import IMAGE, prepare
from test_candidate_scenario import write_scenario, synthetic_source_root_owner
from test_video_animation_create_profile import input_document as creation_input
from test_video_animation_review_profile import PNG, input_document as visual_input


PROFILES = (visual.OBSERVE_PROFILE, visual.REVIEW_PROFILE, creation.PROFILE)


def input_name(profile):
    return creation.INPUT_NAME if profile == creation.PROFILE else implementation(profile).input_name


def files(profile):
    handler = implementation(profile)
    if profile == creation.PROFILE:
        document = creation_input(True)
        return document, {creation.INPUT_NAME: canonical_bytes(document)}
    document = visual_input(profile == visual.REVIEW_PROFILE)
    return document, {handler.input_name: canonical_bytes(document),
                      **{row['file_name']: PNG for row in document['observation']['frames']}}


@pytest.mark.parametrize('profile', PROFILES)
def test_actual_sources_are_validated_in_place_and_rendered_refs_preserve_names(tmp_path, monkeypatch, profile):
    _, hosts, _ = prepare()
    host = hosts[profile]
    _, sources = files(profile)
    name = input_name(profile)
    task = dict(input_sha256=hashlib.sha256(sources[name]).hexdigest())
    path, root, document = write_scenario(tmp_path / 'role-candidate', profile, task, sources)
    before = {file.name: file.read_bytes() for file in root.iterdir()}
    seen = []
    handler = implementation(profile)
    original = handler.validate_inputs

    def validate(workspace, request, *, input_root=None):
        seen.append((workspace, input_root))
        return original(workspace, request, input_root=input_root)

    monkeypatch.setattr(handler, 'validate_inputs', validate)
    native = candidate_scenario.implementation
    monkeypatch.setattr(candidate_scenario, 'implementation',
                        lambda name: handler if name == profile else native(name))
    loaded = candidate_scenario.load_scenario(path, root, None, host_policy(
        acceptance_probe.HostConfig.model_validate(host)))
    assert seen == [(None, root.absolute())]
    assert loaded.source_dir == root.absolute()
    assert loaded.files == sources == before
    assert set(row['name'] for row in document['inputs']) == set(sources)
    assert loaded.checkpoint_ref is None and not loaded.checkpoint_files
    assert loaded.metadata['production_acceptance_created'] is False
    manifest = acceptance_probe.render_probe(acceptance_probe.HostConfig.model_validate(host),
        name='role-offline-candidate', worker_image=IMAGE,
        node='offline-node', uid_start=2100000000, bootstrap_secret='offline-bootstrap',
        upstream_secret='offline-upstream', scenario=path, source_dir=root)
    # The JSON+frames are projected from the original path, not embedded into
    # ConfigMaps or silently renamed by an implicit fixture.
    encoded = str(manifest)
    volumes = [volume for item in manifest['items'] if item.get('kind') == 'Pod'
               for volume in item['spec']['volumes'] if volume['name'] == 'scenario-sources']
    assert volumes and all(volume['hostPath']['path'] == str(root.absolute()) for volume in volumes)
    assert base64.b64encode(PNG).decode() not in encoded
    assert {file.name: file.read_bytes() for file in root.iterdir()} == before


@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('change', ['task_sha', 'noncanonical', 'wrong_name', 'extra_source'])
def test_candidate_rejects_rebound_or_undeclared_sources(tmp_path, profile, change):
    _, hosts, _ = prepare()
    _, sources = files(profile)
    name = input_name(profile)
    if change == 'noncanonical':
        sources[name] += b'\n'
    task = dict(input_sha256='0' * 64 if change == 'task_sha' else hashlib.sha256(sources[name]).hexdigest())
    if change == 'wrong_name':
        sources['other-input.json'] = sources.pop(name)
    path, root, _ = write_scenario(tmp_path / 'invalid-role', profile, task, sources)
    if change == 'extra_source':
        (root / 'unlisted.js').write_text('not a declared source', encoding='utf-8')
    with pytest.raises(ServiceError):
        candidate_scenario.load_scenario(path, root, None, host_policy(
            acceptance_probe.HostConfig.model_validate(hosts[profile])))


@pytest.mark.parametrize('change', ['missing_frame', 'wrong_frame_digest', 'fake_png'])
def test_visual_candidate_validates_real_frames_not_just_json_manifest(tmp_path, change):
    _, hosts, _ = prepare()
    document, sources = files(visual.REVIEW_PROFILE)
    if change == 'missing_frame':
        del sources['frame-1.png']
    elif change == 'wrong_frame_digest':
        sources['frame-1.png'] += b'changed'
    else:
        sources['frame-1.png'] = b'not a PNG'
        document['observation']['frames'][0]['sha256'] = hashlib.sha256(sources['frame-1.png']).hexdigest()
        sources[visual.REVIEW_INPUT_NAME] = canonical_bytes(document)
    task = dict(input_sha256=hashlib.sha256(sources[visual.REVIEW_INPUT_NAME]).hexdigest())
    path, root, _ = write_scenario(tmp_path / 'invalid-frames', visual.REVIEW_PROFILE, task, sources)
    with pytest.raises(ServiceError):
        candidate_scenario.load_scenario(path, root, None, host_policy(
            acceptance_probe.HostConfig.model_validate(hosts[visual.REVIEW_PROFILE])))
