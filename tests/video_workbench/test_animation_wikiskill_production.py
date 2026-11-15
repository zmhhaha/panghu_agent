"""Published library batches keep real identities across active changes/restarts."""
from copy import deepcopy
import sqlite3
from uuid import UUID

import pytest

from agent_service.animation_skills_contract import build_skill_package, bundle_files
from agent_service.contracts import ServiceError, digest
from agent_service.animation_wikiskill_contract import EVIDENCE_SCOPE, COMPILATION_LIMITATION
from video_workbench.animation_wikiskill_production import LibrarySkillProductionAdapter
from video_workbench.animation_skill_production import SkillProductionAdapter


def release(sequence=1, count=6):
    packages = [build_skill_package(skill_id=f'skill-{i}', version=1, kind='foundation' if i == 0 else 'expression',
        files={'SKILL.md': f'---\nname: skill-{i}\ndescription: Understand a meaningful mechanism.\n---\n'
            f'Explain relationships independently, revision {sequence}.'}, runtime_requirements={'engines': ['html-svg']})
        for i in range(count)]
    value = dict(schema='video.animation-wikiskill-release.v1', owner='publisher', id=str(UUID(int=sequence)),
        sequence=sequence, job_id=str(UUID(int=sequence + 100)), snapshot_id=str(UUID(int=sequence + 200)),
        snapshot_sha256='a' * 64, input_sha256='b' * 64, created_at=1000 + sequence,
        summary='A complete published library.', packages=packages,
        index=[dict(skill_id=row['skill_id'], parent_skill_id=None if i == 0 else 'skill-0',
                    source_record_ids=['source-a']) for i, row in enumerate(packages)],
        exclusions=[], limitations=[COMPILATION_LIMITATION], evidence_scope=EVIDENCE_SCOPE)
    value['sha256'] = digest(value)
    return value


def adapter(tmp_path, state, calls):
    def active(owner):
        calls.append(('active', owner))
        return deepcopy(state['active'])
    def historical(owner, identity):
        calls.append(('historical', owner, identity))
        if identity not in state['recent']:
            raise ServiceError('wikiskill_release_not_found', 404)
        return deepcopy(state['recent'][identity])
    return LibrarySkillProductionAdapter(active, historical, lambda owner: dict(releases=[]), tmp_path / 'ledger.sqlite3')


def test_shared_batch_uses_all_packages_real_release_identity_and_no_fake_approvals(tmp_path):
    original = release()
    state, calls = dict(active=original, recent={original['id']: original}), []
    service = adapter(tmp_path, state, calls)
    pin = service.pin('recipient', 'creative-batch')
    planning = service.freeze('recipient', 'planning', {'goal': 'first'}, ['html-svg'], library_binding=pin)
    state['active'] = release(2)
    drawing = service.freeze('recipient', 'module-1', {'goal': 'drawing'}, ['html-svg'], library_binding=pin)
    polish = service.freeze('recipient', 'polish', {'goal': 'improve'}, ['html-svg'], library_binding=pin)
    assert len(planning['catalog']['skills']) == 6
    assert planning['packages'] == drawing['packages'] == polish['packages']
    assert planning['catalog']['task_sha256'] != drawing['catalog']['task_sha256']
    assert all(row['method_version_id'].startswith('wikiskill:' + original['id'] + ':')
        and row['publication_sha256'] == original['sha256'] for row in drawing['catalog']['skills'])
    assert 'reference_validation_ids' not in str(drawing) and 'human_approval' not in str(drawing)
    assert bundle_files(drawing['catalog'], drawing['packages'])
    assert calls == [('active', 'recipient')]
    # Both task freeze and batch resources recover without reading today's API.
    service = adapter(tmp_path, state, calls)
    assert service.pin('recipient', 'creative-batch') == pin
    assert service.freeze('recipient', 'planning', {'goal': 'first'}, ['html-svg'], library_binding=pin) == planning
    assert service.get_bundle('recipient', 'module-1', drawing['catalog']['sha256']) == drawing
    assert len(calls) == 1
    latest = service.pin('recipient', 'new-full-production')
    assert latest['release_id'] == state['active']['id']


def test_historical_selection_is_bounded_but_frozen_batch_survives_retirement(tmp_path):
    old, current = release(), release(2)
    state, calls = dict(active=current, recent={old['id']: old, current['id']: current}), []
    service = adapter(tmp_path, state, calls)
    selected = service.pin('recipient', 'historical', old['id'])
    state['recent'].pop(old['id'])
    frozen = service.freeze('recipient', 'late-module', {}, ['html-svg'], library_binding=selected)
    assert {row['publication_sha256'] for row in frozen['catalog']['skills']} == {old['sha256']}
    with pytest.raises(ServiceError, match='not_found'):
        service.pin('recipient', 'new-selection', old['id'])
    with pytest.raises(ServiceError, match='frozen_input_changed'):
        service.pin('recipient', 'historical')


def test_cross_service_resource_import_resolves_exact_archived_release_without_active(tmp_path):
    original = release()
    first = adapter(tmp_path / 'workbench', dict(active=original, recent={}), [])
    pin = first.pin('recipient', 'batch')
    calls = []
    def archived(owner, binding):
        calls.append((owner, deepcopy(binding)))
        return deepcopy(original)
    remote = LibrarySkillProductionAdapter(lambda owner: pytest.fail('Never read active'), None, None,
        tmp_path / 'creation' / 'ledger.sqlite3', import_release=archived)
    assert remote.pin_import('recipient', pin) == pin
    assert remote.pin_import('recipient', pin) == pin
    resource = remote.freeze('recipient', 'resource', {'goal': 'recipe'}, ['html-svg'], library_binding=pin)
    assert len(resource['packages']) == 6 and len(calls) == 1
    changed = dict(pin, release_sha256='f' * 64)
    with pytest.raises(ServiceError, match='frozen_input_changed'):
        remote.pin_import('recipient', changed)


def test_no_active_is_an_immutable_empty_directory_and_never_legacy_fallback(tmp_path):
    state, calls = dict(active=None, recent={}), []
    service = adapter(tmp_path, state, calls)
    pin = service.pin('recipient', 'empty')
    state['active'] = release()
    frozen = service.freeze('recipient', 'drawing', {}, ['html-svg'], library_binding=pin)
    assert frozen['packages'] == {} and frozen['catalog']['skills'] == []
    assert pin['release_id'] is None
    assert calls == [('active', 'recipient')]


def test_edited_release_and_ledger_are_rejected(tmp_path):
    original = release()
    original['summary'] = 'A changed unsigned payload'
    service = adapter(tmp_path, dict(active=original, recent={}), [])
    with pytest.raises(ServiceError, match='release_changed'):
        service.pin('recipient', 'tampered')
    state = dict(active=release(), recent={})
    service = adapter(tmp_path, state, [])
    pin = service.pin('recipient', 'batch')
    with sqlite3.connect(service.ledger_path) as db:
        db.execute("UPDATE animation_wikiskill_batch_freezes SET record=?", (b'{}',))
    with pytest.raises(ServiceError, match='ledger_changed'):
        service.freeze('recipient', 'module', {}, ['html-svg'], library_binding=pin)


def test_legacy_pending_intent_recovers_only_its_exact_old_package_without_selecting_new_release(tmp_path):
    from test_animation_skill_production import published
    # These are the old contract's explicit historical fixtures, never a new
    # WikiSkill publication manufactured to pass method acceptance.
    old_method = published()
    metadata = {key: value for key, value in old_method.items() if key not in {'skill_package', 'publication'}}
    path = tmp_path / 'historical.sqlite3'
    previous = SkillProductionAdapter(lambda owner: [metadata], None, path)
    task = {'goal': 'An earlier frozen task.'}
    with pytest.raises(ServiceError, match='read_required'):
        previous.freeze('recipient', 'old-operation', task, ['html-svg'])
    calls = []
    def old_read(owner, request):
        calls.append((owner, request))
        assert request['method_version_ids'] == [old_method['id']]
        return dict(methods=[deepcopy(old_method)], read_receipt=None)
    replacement = LibrarySkillProductionAdapter(lambda owner: pytest.fail('Never refresh an old intent'),
        None, None, path, legacy_read=old_read)
    recovered = replacement.freeze('recipient', 'old-operation', task, ['html-svg'])
    assert recovered['catalog']['skills'][0]['method_version_id'] == old_method['id']
    assert len(calls) == 1
    assert replacement.freeze('recipient', 'old-operation', task, ['html-svg']) == recovered
    assert len(calls) == 1
