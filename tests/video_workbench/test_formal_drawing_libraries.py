"""Exact formal dependency custody; synthetic roles do not claim artwork quality."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench import formal_drawing_libraries as formal
from video_workbench import animation_skill_compatibility as compatibility
from video_workbench.animation_runtime_capabilities import validate_runtime_capabilities
from video_workbench.animation_wikiskill_production import LibrarySkillProductionAdapter
from video_workbench.ink_landscape_library import descriptor, read_assets
from video_workbench.film_production_config import FilmProductionSettings
from video_workbench.hyperframes_film_browser import verify_film_browser_bundle
from video_workbench.hyperframes_film_snapshot import freeze_film_execution_snapshot, decode_film_execution_snapshot
from test_animation_method_service import runtime_capabilities
from test_ink_landscape_knowledge import _published
from test_film_production_config import release
from test_creative_film_module_contract import core, RUNTIME
from test_film_creation_workflow import env, module_request, OWNER


def libraries():
    return {'ink-landscape': descriptor()}


def test_formal_release_requires_complete_exact_current_descriptor_and_code():
    legacy = runtime_capabilities(['html-svg', 'canvas'])
    caps = formal.enable_in_capabilities(legacy)
    assert caps['schema'] == formal.SCHEMA and 'drawing_libraries' not in legacy
    assert caps['drawing_libraries'] == libraries()
    assert validate_runtime_capabilities(legacy) == legacy
    for mutation in ['missing', 'bytes', 'revision', 'entrypoint', 'undeclared', 'engine']:
        bad = deepcopy(caps)
        if mutation == 'missing': bad['assets'].pop('ink-landscape.LICENSE.txt')
        elif mutation == 'bytes': bad['assets']['ink-landscape.mjs'] = 'f' * 64
        elif mutation == 'revision': bad['drawing_libraries']['ink-landscape']['source_revision'] = 'f' * 40
        elif mutation == 'entrypoint': bad['drawing_libraries']['ink-landscape']['entrypoint'] = 'https://external/library.js'
        elif mutation == 'engine': bad['allowed_engines'] = ['html-svg']
        else: bad['schema'] = 'video.animation-runtime-capabilities.v1';bad.pop('drawing_libraries');bad.pop('authoring_libraries')
        with pytest.raises(ServiceError): validate_runtime_capabilities(bad)


def test_task_metadata_or_matching_bytes_do_not_admit_drawing_dependency():
    requirements = {'drawing_libraries': libraries()}
    task = {'assets': descriptor()['assets'], 'drawing_libraries': libraries()}
    original = compatibility.freeze_compatibility(task)
    assert original['schema'] == compatibility.SCHEMA
    assert not compatibility.requirements_compatible(requirements, original)
    admitted = compatibility.freeze_compatibility(task, drawing_libraries=libraries())
    assert admitted['schema'] == compatibility.DRAWING_SCHEMA
    assert compatibility.requirements_compatible(requirements, admitted)
    changed = deepcopy(requirements);changed['drawing_libraries']['ink-landscape']['version'] = '99.0.0'
    assert not compatibility.requirements_compatible(changed, admitted)
    with pytest.raises(ServiceError):
        compatibility.freeze_compatibility(dict(task, base_source=None), drawing_libraries=libraries())
    with pytest.raises(ServiceError):
        compatibility.freeze_compatibility({'assets': {}}, drawing_libraries=libraries())


def test_new_film_delivery_offers_real_skill_and_reopens_original_pins(tmp_path, monkeypatch):
    value = _published()
    adapter = LibrarySkillProductionAdapter(lambda _: value, None, None, tmp_path / 'ledger.db')
    binding = adapter.pin('owner', 'film-batch')
    task = dict(assets=descriptor()['assets'])
    excluded = adapter.freeze('owner', 'old-consumer', task, ['canvas'], library_binding=binding)
    drawing_package = value['packages'][-1]
    assert drawing_package['sha256'] not in excluded['packages']
    admitted = adapter.freeze('owner', 'admitted-consumer', task, ['canvas'], library_binding=binding,
        drawing_libraries=libraries())
    assert admitted['packages'][drawing_package['sha256']] == drawing_package
    import video_workbench.ink_landscape_library as ink
    # Existing delivery never resolves today's release/pins; it keeps the
    # original exact descriptor, hashes and source package after an upgrade.
    monkeypatch.setattr(ink, 'descriptor', lambda: pytest.fail('Historical intent must not re-admit current pins'))
    assert adapter.freeze('owner', 'admitted-consumer', task, ['canvas'], library_binding=binding) == admitted
    assert adapter.freeze('owner', 'old-consumer', task, ['canvas'], library_binding=binding) == excluded


def test_frozen_drawing_context_is_self_contained_across_new_pin_version(monkeypatch):
    context = compatibility.freeze_compatibility({'assets': descriptor()['assets']}, drawing_libraries=libraries())
    old = libraries()
    import video_workbench.ink_landscape_library as ink
    updated = descriptor();updated['source_revision'] = 'f' * 40;updated['assets']['ink-landscape.mjs'] = 'f' * 64
    monkeypatch.setattr(ink, 'descriptor', lambda: updated)
    assert compatibility.requirements_compatible({'drawing_libraries': old}, context)
    damaged = deepcopy(context);damaged['assets']['ink-landscape.mjs'] = '0' * 64
    with pytest.raises(ServiceError): compatibility.validate_compatibility(damaged)


def test_pending_admitted_delivery_recovers_frozen_pins_without_current_registry(tmp_path, monkeypatch):
    value = _published()
    adapter = LibrarySkillProductionAdapter(lambda _: value, None, None, tmp_path / 'pending.db')
    pin = adapter.pin('owner', 'batch')
    task = {'assets': descriptor()['assets']}
    binding = dict(recipient_owner='owner', consumer_id='pending', task_sha256=digest(task), allowed_engines=['canvas'])
    adapter._entry(binding, intent=dict(library_binding=pin, release=value, discovery_only=False,
        compatibility=compatibility.freeze_compatibility(task, drawing_libraries=libraries())))
    import video_workbench.ink_landscape_library as ink
    monkeypatch.setattr(ink, 'descriptor', lambda: pytest.fail('Pending task cannot consult new pins'))
    resumed = adapter.freeze('owner', 'pending', task, ['canvas'], library_binding=pin)
    assert value['packages'][-1]['sha256'] in resumed['packages']


def test_formal_api_guidance_requires_admitted_assets_and_module_lifecycle():
    caps = formal.enable_in_capabilities(runtime_capabilities(['canvas']))
    task = core(allowed_engines=['canvas'])
    task['assets'].update({'assets/' + name: sha for name, sha in descriptor()['assets'].items()})
    result = formal.attach_drawing_libraries(task, caps)
    assert result['assets'] == task['assets'] and result['constraints'] == task['constraints']
    assert 'APP-FROZEN-DRAWING-LIBRARIES\n' in result['scene']['instruction']
    assert 'export mount(root,context)' in result['scene']['instruction']
    assert 'do not use window.__animationExperiment' in result['scene']['instruction']
    assert 'Each group requires at least one stroke' in result['scene']['instruction']
    assert 'resets the Canvas transform' in result['scene']['instruction']
    assert 'read_inputs({}) has no mode/path parameters' in result['scene']['instruction']
    task['assets'].pop('assets/ink-landscape.LICENSE.txt')
    with pytest.raises(ServiceError, match='assets_missing'): formal.attach_drawing_libraries(task, caps)


def test_formal_child_freezes_server_configuration_and_supplies_dependency_to_skill_adapter(env):
    caps = deepcopy(env.creation.runtime_capabilities)
    caps['allowed_engines'].append('canvas')
    caps = formal.enable_in_capabilities(caps)
    env.creation.runtime_capabilities = caps
    env.creation.assets = {'assets/' + name: raw for name, raw in read_assets().items()}
    env.creation.configuration['runtime_capabilities'] = deepcopy(caps)
    env.creation.configuration['sha256'] = digest({key: value for key, value in env.creation.configuration.items()
        if key != 'sha256'})
    request = module_request(env)
    op = env.creation.start_module(OWNER, env.project['id'], env.project['scenes'][0]['id'], request, 'admitted-drawing-child')
    # The server release, not the request, supplies both the bytes inventory and
    # the separate eligibility argument into the normal immutable Skill ledger.
    prepared = env.creation.prepare_module(op['id'], env.adapter)
    core = prepared['document']
    for name, sha in descriptor()['assets'].items(): assert core['assets']['assets/' + name] == sha
    binding = {key: prepared['document']['skill_catalog'][key] for key in (
        'recipient_owner', 'consumer_id', 'task_sha256', 'allowed_engines')}
    context = env.adapter._entry(binding)['intent']['compatibility']
    assert context['drawing_libraries'] == libraries()
    assert context['schema'] == compatibility.DRAWING_SCHEMA
    assert 'APP-FROZEN-DRAWING-LIBRARIES\n' in core['scene']['instruction']


def test_film_release_loads_actual_drawing_bytes_and_refuses_substitution(tmp_path):
    value, path = release(tmp_path)
    value['runtime_capabilities'] = formal.enable_in_capabilities(value['runtime_capabilities'])
    path.write_text(json.dumps(value), encoding='utf-8')
    directory = tmp_path / 'assets';directory.mkdir()
    for name, raw in read_assets().items(): (directory / name).write_bytes(raw)
    settings = {'VIDEO_FILM_PRODUCTION_RELEASE_FILE': str(path),
        'VIDEO_FILM_PRODUCTION_ASSET_DIRECTORY': str(directory)}
    loaded = FilmProductionSettings.from_env(settings)
    assert loaded.assets == {'assets/' + name: raw for name, raw in read_assets().items()}
    (directory / 'ink-landscape.mjs').write_bytes(b'export const substituted=true;')
    with pytest.raises(ValueError): FilmProductionSettings.from_env(settings)


def test_real_library_module_is_importable_and_in_complete_frozen_formal_snapshot(tmp_path):
    from video_workbench.examples.build_ink_film_probe import build_probe_bundle
    root = Path(__file__).resolve().parents[2]
    files, checksum = build_probe_bundle(RUNTIME, source_root=root)
    bundle = verify_film_browser_bundle(files, expected_bundle_sha256=checksum)
    hosts = bundle['root_config']['host_files_sha256']
    image = 'registry.internal/video-motion-renderer:latest@' + RUNTIME['image_digest']
    snapshot = freeze_film_execution_snapshot(files, expected_bundle_sha256=checksum,
        runtime_fingerprint=RUNTIME, trusted_host_sha256=hosts, image=image,
        project_revision=1, production_run_id='technical-probe', timeout_seconds=600)
    restored = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=RUNTIME, trusted_host_sha256=hosts, image=image)
    assert restored == files
    for name, raw in read_assets().items(): assert files['project/assets/' + name] == raw
    # Real module evaluation and API execution, not a metadata-only asset test.
    module = (tmp_path / 'ink.mjs');module.write_bytes(read_assets()['ink-landscape.mjs'])
    code = '''const ink=await import(process.argv[1]);
const doc=ink.createStrokeGroupDocument([{id:'g',strokes:[{id:'s',points:[{x:0,y:0},{x:1,y:1}]}]}],{width:2,height:2,seed:1});
console.log(JSON.stringify({marks:doc.result.marks.length,render:typeof ink.renderCanvas}));'''
    result = subprocess.run(['node', '--input-type=module', '-e', code, module.as_uri()],
        check=True, capture_output=True, text=True, timeout=30)
    assert json.loads(result.stdout) == {'marks': 1, 'render': 'function'}
