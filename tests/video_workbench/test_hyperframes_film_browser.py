"""Bundle custody/transport contracts; no browser or real-core claims here."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.hyperframes_film_browser import (HOST_NAMES, compile_film_browser_bundle,
    verify_film_browser_bundle)
from video_workbench.hyperframes_film_project import compile_film_project
from test_hyperframes_film_project import inputs, explicit_clock
from test_creative_hyperframes_renderer import RUNTIME
from video_workbench.examples.build_hyperframes_film_browser_probe import build_probe_bundle

ROOT = Path(__file__).resolve().parents[2]


def bundle_inputs():
    project = compile_film_project(**explicit_clock(inputs(), {'scene-1'}))
    return dict(project_files=project, expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=640, height=360, host_files={name: (ROOT / 'video_workbench/motion_renderer' / name).read_bytes() for name in HOST_NAMES})


def test_frozen_root_dimensions_host_bytes_and_every_original_resource_recompile_exactly():
    value = bundle_inputs()
    original = deepcopy(value)
    files = compile_film_browser_bundle(**value)
    checksum = hashlib.sha256(files['browser-manifest.json']).hexdigest()
    bundle = verify_film_browser_bundle(files, expected_bundle_sha256=checksum)
    assert value == original
    assert bundle['root_config']['width'] == 640 and bundle['root_config']['height'] == 360
    assert bundle['execution_status'] == 'not_executed'
    assert bundle['root_config']['realm_protocol'] == 'opaque-sandbox-message-channel-1'
    for name, raw in value['project_files'].items(): assert files['project/' + name] == raw
    for name, raw in value['host_files'].items(): assert files['host/' + name] == raw
    assert set(bundle['realms']) == {'realms/scene_0/index.html', 'realms/scene_1/index.html',
        'realms/scene_2/index.html', 'realms/relation_transition/index.html'}
    assert bundle['resources']['project/recordings/000.wav']['role'] == 'recording-not-browser-served'
    value['host_files'].clear();bundle['root_config']['width'] = 1
    assert verify_film_browser_bundle(files, expected_bundle_sha256=checksum)['root_config']['width'] == 640


@pytest.mark.parametrize('field,value', [('width', 319), ('width', True), ('width', 1921), ('height', 179), ('height', 1081)])
def test_invalid_dimensions_refused(field, value):
    document = bundle_inputs();document[field] = value
    with pytest.raises(ServiceError): compile_film_browser_bundle(**document)


@pytest.mark.parametrize('change', ['missing_host', 'extra_host', 'host_type', 'host_utf8', 'project_source', 'project_digest'])
def test_changed_or_unverified_project_and_incomplete_hosts_are_refused(change):
    value = bundle_inputs()
    if change == 'missing_host': value['host_files'].pop(next(iter(HOST_NAMES)))
    elif change == 'extra_host': value['host_files']['external.mjs'] = b'code'
    elif change == 'host_type': value['host_files'][next(iter(HOST_NAMES))] = 'code'
    elif change == 'host_utf8': value['host_files'][next(iter(HOST_NAMES))] = b'\xff'
    elif change == 'project_source': value['project_files']['modules/scene_0/entry.mjs'] += b'changed'
    else: value['expected_project_manifest_sha256'] = 'f' * 64
    with pytest.raises(ServiceError): compile_film_browser_bundle(**value)


@pytest.mark.parametrize('change', ['bytes', 'extra', 'digest', 'root', 'identity', 'execution'])
def test_browser_bundle_needs_external_digest_exact_inventory_and_derived_recompilation(change):
    files = compile_film_browser_bundle(**bundle_inputs())
    checksum = hashlib.sha256(files['browser-manifest.json']).hexdigest()
    if change == 'bytes': files['host/index.html'] += b'changed'
    elif change == 'extra': files['host/extra.mjs'] = b'changed'
    elif change == 'digest': checksum = 'f' * 64
    else:
        manifest = json.loads(files['browser-manifest.json'])
        if change == 'root': manifest['root_config']['root_entry'] = 'host/other.html'
        elif change == 'identity': manifest['project_manifest_sha256'] = 'f' * 64
        else: manifest['execution_status'] = 'rendered'
        files['browser-manifest.json'] = canonical_bytes(manifest)
        checksum = hashlib.sha256(files['browser-manifest.json']).hexdigest()
    with pytest.raises(ServiceError): verify_film_browser_bundle(files, expected_bundle_sha256=checksum)


@pytest.mark.parametrize('case', ['positive','import','mount','render','dispose','prototype_without_context'])
def test_bounded_technical_fixture_cases_each_form_an_independently_verified_source_identity(case):
    files, checksum = build_probe_bundle(RUNTIME, source_root=ROOT, case=case)
    bundle = verify_film_browser_bundle(files, expected_bundle_sha256=checksum)
    assert bundle['execution_status'] == 'not_executed'
    source = files['project/modules/fixture_0/entry.mjs'].decode()
    if case in {'import','mount','render','dispose'}:
        assert "throw Error('fixture_" + case + "_failed')" in source
    if case == 'prototype_without_context':
        assert 'Array.prototype.map=' in source and 'Function.prototype.call=' in source
        project = json.loads(files['project/manifest.json'])
        assert next(row for row in project['modules'] if row['module_id']=='fixture_0')['engine']=='canvas'
