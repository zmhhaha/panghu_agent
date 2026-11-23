"""Local library evidence and frozen delivery; no model or artistic acceptance."""
from copy import deepcopy
import hashlib
import json
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service import animation_wikiskill_contract as contract
from agent_service.animation_skills_contract import build_skill_package, SkillCatalogTools
from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import ink_landscape_knowledge as knowledge
from video_workbench.animation_laboratory_library import LaboratoryLibraryAdapter
from video_workbench.animation_skill_compatibility import freeze_compatibility, requirements_compatible
from video_workbench.animation_wikiskill_coordinator import WikiSkillCoordinator
from video_workbench.animation_wikiskill_production import LibrarySkillProductionAdapter
from video_workbench.animation_wikiskill_store import AnimationWikiSkillStore
from video_workbench.ink_landscape_library import descriptor, read_publication_files, read_skill_files
from video_workbench.picture_learning_service import PictureLearningService
from test_animation_wikiskill import Gateway, finish, records as fixture_records
from test_animation_wikiskill_production import release


def _requirements(library=None):
    return dict(engines=['canvas'], drawing_libraries={'ink-landscape': library or descriptor()})


def _published():
    # Synthetic immutable release containing the actual supplied API text;
    # this fixture does not publish or assert that the compiler authored it.
    value = release(count=1)
    package = build_skill_package(skill_id='ink-landscape', version=1, kind='expression',
        files=read_skill_files(), runtime_requirements=_requirements())
    value['packages'].append(package)
    value['index'].append(dict(skill_id=package['skill_id'], parent_skill_id='skill-0',
        source_record_ids=['source-a']))
    value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
    return value


def _adapter(root, value, calls):
    def active(owner):
        calls.append(('active', owner))
        return deepcopy(value)
    return LaboratoryLibraryAdapter(active,
        lambda *_: pytest.fail('Frozen laboratory delivery must not re-read a release'),
        None, root / 'ledger.sqlite3')


def _delivered_files(snapshot, package_sha256):
    files = {}
    for page in snapshot['pages']:
        identity = json.loads(page['body']['applicability'][1])
        if identity['package_sha256'] == package_sha256:
            files.setdefault(identity['path'], []).append(
                (identity['offset'], page['body']['content'].split('\n', 1)[1]))
    return {path: ''.join(text for _, text in sorted(parts)) for path, parts in files.items()}


def test_image_owned_sources_bind_exact_api_text_dependency_and_honest_limits():
    rows = knowledge.capture_ink_landscape_records()
    document_rows = [row for row in rows if row['kind'] == 'drawing_library_documentation']
    actual = read_publication_files()
    assert {row['content']['path']: row['content']['text'] for row in document_rows} == actual
    manifest, = [row['content'] for row in rows if row['kind'] == 'drawing_library']
    assert manifest['descriptor'] == descriptor()
    assert manifest['runtime_requirements'] == _requirements()
    for row in rows:
        assert row['sha256'] == digest(row['content'])
        assert row['content']['evidence_scope'] == knowledge.SCOPE
        for field in ('artistic_acceptance', 'static_visual_acceptance',
                      'continuous_motion_verified', 'audio_verified'):
            assert row['content'][field] is False
    for row in document_rows:
        assert row['content']['text_sha256'] == hashlib.sha256(
            actual[row['content']['path']].encode('utf-8')).hexdigest()
    frozen = contract.make_snapshot('publisher', rows, captured_at=1000.)
    request = contract.build_input(frozen)
    assert contract.validate_input_files(request, contract.input_files(frozen, request)) == frozen


def test_free_composition_is_explicit_verified_static_source_not_automatic_capture():
    ordinary = knowledge.capture_ink_landscape_records()
    assert not any(row['kind'].startswith('free_composition') for row in ordinary)
    rows = knowledge.capture_free_composition_records()
    assert {row['kind'] for row in rows} == {'free_composition_documentation',
        'free_composition_source', 'free_composition_render_evidence'}
    source, = [row['content'] for row in rows if row['kind'] == 'free_composition_source']
    assert hashlib.sha256(source['source_text'].encode('utf-8')).hexdigest() == source['source_sha256']
    render, = [row['content'] for row in rows if row['kind'] == 'free_composition_render_evidence']
    observed = render['observation']
    assert (observed['width'], observed['height']) == (1600, 1000)
    assert observed['png_sha256'] == observed['repeat_sha256']
    calls = {item['name']: item['calls'] for item in observed['actual_library_api_calls']}
    assert calls['createBrushDocument'] == calls['drawContactStroke'] == 359
    assert calls['renderCanvas'] == 6
    for row in rows:
        assert row['sha256'] == digest(row['content'])
        assert row['content']['runtime_requirements'] == _requirements()
        assert row['content']['input_images'] == row['content']['network_inputs'] == 0
        for field in ('artistic_acceptance', 'static_visual_acceptance',
                      'continuous_motion_verified', 'audio_verified'):
            assert row['content'][field] is False
        assert 'D:/' not in canonical_bytes(row).decode('utf-8')
    capture = knowledge.with_free_composition_records(lambda _: deepcopy(ordinary))
    combined = capture('publisher')
    assert combined == sorted(ordinary + rows, key=lambda row: row['record_id'])
    snapshot = contract.make_snapshot('publisher', combined, captured_at=1000.)
    request = contract.build_input(snapshot)
    assert contract.validate_input_files(request, contract.input_files(snapshot, request)) == snapshot


@pytest.mark.parametrize('name', ['scene.js', 'rain-cleared-river.png',
    'rain-cleared-river-receipt.json', 'rain-cleared-river.md', 'manifest.json'])
def test_modified_free_composition_artifacts_are_rejected(tmp_path, name):
    root = tmp_path / 'experience'
    shutil.copytree(knowledge.FREE_COMPOSITION_ROOT, root)
    path = root / name
    path.write_bytes(path.read_bytes() + b'\nchanged')
    with pytest.raises(ServiceError, match='ink_landscape_library_bytes_invalid'):
        knowledge.capture_free_composition_records(root)


def test_free_composition_capture_happens_only_for_explicit_start_and_preserves_old_release(
        tmp_path, monkeypatch):
    store = AnimationWikiSkillStore(tmp_path / 'releases.sqlite3', clock=lambda: 1000.)
    gateway = Gateway()
    coordinator = WikiSkillCoordinator(store, gateway, lambda _: fixture_records(), clock=lambda: 1000.)
    prior = coordinator.start('publisher', {'request_id': str(uuid4())})
    finish((store, gateway, coordinator, []), prior)
    previous = store.active('publisher')
    reads = []
    original_capture = knowledge.capture_free_composition_records
    def capture_case(directory=None):
        reads.append(True)
        return original_capture(directory)
    monkeypatch.setattr(knowledge, 'capture_free_composition_records', capture_case)
    coordinator.capture = knowledge.with_free_composition_records(lambda _: fixture_records())
    coordinator.catalogue('publisher')
    assert store.active('publisher') == previous and reads == []
    body = {'request_id': str(uuid4())}
    job = coordinator.start('publisher', body)
    frozen = store.snapshot('publisher', job['snapshot_id'])
    assert reads == [True] and store.active('publisher') == previous
    assert any(row['kind'] == 'free_composition_source' for row in frozen['records'])
    assert coordinator.start('publisher', body) == job and reads == [True]
    assert store.snapshot('publisher', job['snapshot_id']) == frozen
    assert store.get_release('publisher', previous['id']) == previous


@pytest.mark.parametrize('files', [
    {}, {'../outside.md': 'invalid'}, {'SKILL.md': b'not text'},
    {'SKILL.md': ''}, {'SKILL.md': 'valid', 1: 'mixed key'}, {'source.js': 'not a guide'},
])
def test_invalid_source_document_set_fails_explicitly(files):
    with pytest.raises(ServiceError, match='publication_source_invalid'):
        knowledge._source_records(descriptor(), files)


def test_publication_reads_library_only_on_manual_start_and_freezes_source_bytes(tmp_path, monkeypatch):
    store = AnimationWikiSkillStore(tmp_path / 'releases.sqlite3', clock=lambda: 1000.)
    gateway = Gateway()
    coordinator = WikiSkillCoordinator(store, gateway, lambda _: fixture_records(), clock=lambda: 1000.)
    work = store, gateway, coordinator, []
    prior_job = coordinator.start('publisher', {'request_id': str(uuid4())})
    finish(work, prior_job)
    previous = store.active('publisher')
    documents, reads = {'SKILL.md': 'Original API guide.\n'}, []
    def capture_library():
        reads.append(True)
        return knowledge._source_records(descriptor(), documents)
    monkeypatch.setattr(knowledge, 'capture_ink_landscape_records', capture_library)
    coordinator.capture = knowledge.with_ink_landscape_records(lambda _: fixture_records())
    coordinator.catalogue('publisher')
    coordinator.get('publisher', prior_job['id'])
    assert store.active('publisher') == previous and reads == []

    body = {'request_id': str(uuid4())}
    job = coordinator.start('publisher', body)
    original = store.snapshot('publisher', job['snapshot_id'])
    documents['SKILL.md'] = 'A changed API guide.\n'
    assert coordinator.start('publisher', body) == job and reads == [True]
    assert store.active('publisher') == previous
    assert store.snapshot('publisher', job['snapshot_id']) == original
    assert any(row['content'].get('text') == 'Original API guide.\n' for row in original['records'])
    finish(work, job)
    following = coordinator.start('publisher', {'request_id': str(uuid4())})
    assert reads == [True, True]
    updated = store.snapshot('publisher', following['snapshot_id'])
    assert any(row['content'].get('text') == documents['SKILL.md'] for row in updated['records'])
    assert store.snapshot('publisher', job['snapshot_id']) == original


def test_source_only_publication_suppresses_only_the_empty_artwork_error(monkeypatch):
    rows = knowledge._source_records(descriptor(), {'SKILL.md': 'An API guide.'})
    monkeypatch.setattr(knowledge, 'capture_ink_landscape_records', lambda: deepcopy(rows))
    def fail(code):
        def capture(_):
            raise ServiceError(code, 422)
        return capture
    assert knowledge.with_ink_landscape_records(fail('animation_wikiskill_library_empty'))('owner') == rows
    with pytest.raises(ServiceError, match='storage_failed'):
        knowledge.with_ink_landscape_records(fail('storage_failed'))('owner')


def test_formal_consumers_exclude_drawing_dependency_even_with_matching_task_assets(tmp_path):
    value = _published()
    package = value['packages'][1]
    context = freeze_compatibility(dict(assets=descriptor()['assets'],
        drawing_libraries={'ink-landscape': descriptor()}))
    assert not requirements_compatible(package['runtime_requirements'], context)
    assert requirements_compatible(dict(engines=['canvas'], drawing_libraries={}), context)
    formal = LibrarySkillProductionAdapter(lambda _: deepcopy(value), None, None, tmp_path / 'formal.sqlite3')
    binding = formal.pin('owner', 'film')
    bundle = formal.freeze('owner', 'film-draw', dict(assets=descriptor()['assets']),
        ['html-svg', 'canvas'], library_binding=binding)
    assert package['sha256'] not in bundle['packages']
    assert SkillCatalogTools(bundle['catalog'], bundle['packages']).search('ink-landscape')['skills'] == []


def test_learning_matches_exact_dependency_and_delivers_searchable_published_bodies(tmp_path):
    value, calls = _published(), []
    original = canonical_bytes(value)
    package = value['packages'][1]
    adapter = _adapter(tmp_path, value, calls)
    binding = adapter.pin('owner', 'learning')
    snapshot = adapter.laboratory_snapshot('owner', binding,
        drawing_libraries={'ink-landscape': descriptor()})
    assert _delivered_files(snapshot, package['sha256']) == package['files']
    ink_pages = [page for page in snapshot['pages']
        if json.loads(page['body']['applicability'][1])['package_sha256'] == package['sha256']]
    assert ink_pages and all(package['name'] in page['body']['applicability']
        and package['description'] in page['body']['applicability'] for page in ink_pages)
    for available in ({}, {'ink-landscape': dict(descriptor(), version='future-version')}):
        incompatible = adapter.laboratory_snapshot('owner', binding, drawing_libraries=available)
        assert _delivered_files(incompatible, package['sha256']) == {}
        assert incompatible['pages']  # Independent root knowledge remains available.
    assert _delivered_files(adapter.laboratory_snapshot('owner', binding), package['sha256']) == {}
    assert canonical_bytes(value) == original and calls == [('active', 'owner')]
    assert adapter.laboratory_snapshot('owner', binding,
        drawing_libraries={'ink-landscape': descriptor()}) == snapshot


def test_picture_freeze_supplies_runtime_dependency_but_disabled_reads_nothing(tmp_path):
    value, calls = _published(), []
    service = object.__new__(PictureLearningService)
    service.library_adapter = _adapter(tmp_path, value, calls)
    capability_reads = []
    def capabilities():
        capability_reads.append(True)
        return dict(drawing_libraries={'ink-landscape': descriptor()})
    service.renderer = SimpleNamespace(capabilities=capabilities)
    binding, snapshot = service._freeze_knowledge('owner', 'enabled', True)
    assert binding['release_id'] == value['id'] and capability_reads == [True]
    assert _delivered_files(snapshot, value['packages'][1]['sha256']) == read_skill_files()
    calls.clear()
    capability_reads.clear()
    disabled, empty = service._freeze_knowledge('owner', 'disabled', False)
    assert disabled['use_wikiskill'] is False and empty is None
    assert calls == capability_reads == []
    # A technical retry keeps the entire original knowledge snapshot; changing
    # today's runtime cannot re-select or rewrite its already frozen input.
    prior = dict(library_binding=binding, knowledge_snapshot=snapshot)
    assert service._freeze_knowledge('owner', 'retry', True, prior=prior) == (binding, snapshot)
    assert calls == capability_reads == []
