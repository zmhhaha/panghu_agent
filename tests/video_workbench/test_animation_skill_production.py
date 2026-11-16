"""Durable all-version discovery; publications are synthetic adapter fixtures."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
from threading import Barrier, get_ident

import pytest

from agent_service.animation_skills_contract import build_skill_package
from agent_service.contracts import ServiceError, digest
from video_workbench.animation_skill_production import SkillProductionAdapter, validate_delivery


TASK = {'understanding_goal': 'A task with words unrelated to the reusable examples.'}


def published(index=0, version=1, *, supporting_file=False):
    files = {'SKILL.md': f'---\nname: fixture-{index}\ndescription: Preserve meaningful identities.\n---\n'
             f'INDEPENDENT-BODY-{index}-{version}: organize relationships rather than copying a layout.'}
    if supporting_file:
        files['references/identity.md'] = 'Supporting detail does not substitute for reading the Skill instructions.'
    package = build_skill_package(skill_id=f'fixture-{index}', version=version, kind='expression',
        files=files,
        runtime_requirements={'engines': ['html-svg']})
    result = dict(schema='video.animation-method.v2', id=f'fixture-{index}-v{version}', status='published',
                  content_sha256=digest(dict(index=index, version=version)), skill_package=package)
    result['publication'] = dict(schema='video.animation-method-publication.v2',
        method_version_id=result['id'], method_content_sha256=result['content_sha256'],
        skill_package_sha256=package['sha256'], reference_validation_ids=[f'ref-{index}-{version}'],
        transfer_validation_ids=[f'transfer-{index}-{version}'])
    return result


def test_all_compatible_versions_are_discoverable_without_max_three_or_extra_remote_reads(tmp_path):
    rows = [published(index) for index in range(5)] + [published(0, 2)]
    rows.extend([dict(schema='video.animation-method.v1', status='published'),
                 dict(schema='video.animation-method.v2', status='candidate')])
    calls = []
    instance = SkillProductionAdapter(lambda owner: deepcopy(rows),
        lambda *args: pytest.fail('Full catalog records require no remote package read'), tmp_path / 'ledger.sqlite3')
    delivery = instance.freeze('alice', 'operation', TASK, ['html-svg'])
    assert len(delivery['catalog']['skills']) == 6
    assert len(delivery['packages']) == 6
    assert {row['version'] for row in delivery['catalog']['skills'] if row['skill_id'] == 'fixture-0'} == {1, 2}
    assert validate_delivery(delivery) == delivery
    assert delivery['retrieval_receipts'][0]['scope'] == 'host_retrieval_not_agent_observation'
    assert instance.get_bundle('alice', 'operation', delivery['catalog']['sha256']) == delivery


def test_new_publications_only_affect_new_operations_and_frozen_bytes_survive_restarts(tmp_path):
    rows, calls = [published()], []
    path = tmp_path / 'ledger.sqlite3'
    def catalogue(owner):
        calls.append(owner)
        return deepcopy(rows)
    instance = SkillProductionAdapter(catalogue, None, path)
    first = instance.freeze('alice', 'operation', TASK, ['html-svg'])
    original = deepcopy(first)
    rows.append(published(0, 2))
    first['packages'].clear()
    assert instance.freeze('alice', 'operation', TASK, ['html-svg']) == original
    reopened = SkillProductionAdapter(lambda _: pytest.fail('Must use durable freeze'), None, path)
    assert reopened.freeze('alice', 'operation', TASK, ['html-svg'], existing=original) == original
    assert reopened.get_bundle('alice', 'operation', original['catalog']['sha256']) == original
    assert calls == ['alice']
    assert len(instance.freeze('alice', 'new-operation', TASK, ['html-svg'])['catalog']['skills']) == 2
    for owner, consumer, checksum in [('bob', 'operation', original['catalog']['sha256']),
                                     ('alice', 'operation', 'f' * 64)]:
        with pytest.raises(ServiceError, match='bundle_not_found'):
            instance.get_bundle(owner, consumer, checksum)
    with pytest.raises(ServiceError, match='frozen_input_changed'):
        instance.freeze('alice', 'operation', dict(changed='task'), ['html-svg'])
    with pytest.raises(ServiceError, match='frozen_input_changed'):
        instance.freeze('alice', 'operation', TASK, ['canvas'])


def test_an_empty_frozen_library_stays_empty_when_the_global_library_grows(tmp_path):
    rows = []
    instance = SkillProductionAdapter(lambda _: deepcopy(rows), None, tmp_path / 'ledger.sqlite3')
    empty = instance.freeze('alice', 'empty', TASK, ['html-svg'])
    rows.append(published())
    assert instance.freeze('alice', 'empty', TASK, ['html-svg']) == empty
    assert empty['packages'] == {} and empty['catalog']['skills'] == []
    assert len(instance.freeze('alice', 'later', TASK, ['html-svg'])['packages']) == 1


def metadata(row):
    return dict(schema=row['schema'], status=row['status'], id=row['id'], content_sha256=row['content_sha256'],
                skill_package_sha256=row['skill_package']['sha256'], publication_sha256=digest(row['publication']))


def test_metadata_only_catalog_commits_intent_before_read_and_recovers_without_reselecting(tmp_path):
    original = published()
    calls, path = [], tmp_path / 'ledger.sqlite3'
    def interrupted(owner, body):
        calls.append(deepcopy(body))
        raise OSError('Synthetic interruption before authenticated read completion')
    instance = SkillProductionAdapter(lambda _: [metadata(original)], interrupted, path)
    with pytest.raises(OSError):
        instance.freeze('alice', 'operation', TASK, ['html-svg'])
    def recovered_read(owner, body):
        assert owner == 'alice' and body == calls[0]
        return dict(methods=[deepcopy(original)], read_receipt={'scope': 'host-caller-fixture'})
    instance = SkillProductionAdapter(lambda _: pytest.fail('Never refresh reserved metadata'), recovered_read, path)
    result = instance.freeze('alice', 'operation', TASK, ['html-svg'])
    assert result['catalog']['skills'][0]['method_version_id'] == original['id']
    assert result['retrieval_receipts'][0]['source'] == 'authenticated_method_read'
    assert result['retrieval_receipts'][0]['scope'] == 'host_retrieval_not_agent_observation'


def test_changed_read_or_unavailable_catalog_is_not_silently_treated_as_free_creation(tmp_path):
    original = published()
    changed = published(0, 2)
    instance = SkillProductionAdapter(lambda _: [metadata(original)],
        lambda *args: dict(methods=[changed]), tmp_path / 'changed.sqlite3')
    with pytest.raises(ServiceError, match='catalog_changed'):
        instance.freeze('alice', 'operation', TASK, ['html-svg'])
    def unavailable(_):
        raise ServiceError('synthetic_catalog_unavailable', 503)
    with pytest.raises(ServiceError, match='synthetic_catalog_unavailable'):
        SkillProductionAdapter(unavailable, None, tmp_path / 'unavailable.sqlite3').freeze(
            'alice', 'operation', TASK, ['html-svg'])


def test_simultaneous_freezes_use_one_exact_catalog_and_package_snapshot(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    instances = [SkillProductionAdapter(lambda _: [published(0)], None, path),
                 SkillProductionAdapter(lambda _: [published(0), published(1)], None, path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda instance: instance.freeze('alice', 'same', TASK, ['html-svg']), instances))
    assert results[0] == results[1]
    assert instances[0].get_bundle('alice', 'same', results[0]['catalog']['sha256']) == results[0]


def test_concurrent_metadata_reads_keep_the_first_receipt_for_identical_frozen_resources(tmp_path):
    original, barrier = published(), Barrier(2)
    path = tmp_path / 'concurrent-metadata.sqlite3'
    def read(owner, body):
        barrier.wait(timeout=10)
        return dict(methods=[deepcopy(original)], read_receipt={'request_id': str(get_ident())})
    instances = [SkillProductionAdapter(lambda _: [metadata(original)], read, path) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda instance: instance.freeze('alice', 'same', TASK, ['html-svg']), instances))
    assert results[0] == results[1]
    reopened = SkillProductionAdapter(lambda _: pytest.fail('Do not repeat discovery'),
        lambda *args: pytest.fail('Do not repeat retrieval'), path)
    assert reopened.freeze('alice', 'same', TASK, ['html-svg']) == results[0]


def test_metadata_retrieval_batches_preserve_every_discovered_version(tmp_path):
    originals = [published(index) for index in range(39)] + [published(0, 2)]
    by_id, calls = {row['id']: row for row in originals}, []
    def read(owner, body):
        calls.append(deepcopy(body))
        return dict(methods=[deepcopy(by_id[key]) for key in body['method_version_ids']],
                    read_receipt={'batch': len(calls)})
    instance = SkillProductionAdapter(lambda _: {'methods': [metadata(row) for row in originals]},
        read, tmp_path / 'batch.sqlite3')
    result = instance.freeze('alice', 'all-versions', TASK, ['html-svg'])
    assert [len(body['method_version_ids']) for body in calls] == [1] * 40
    assert len({body['consumer_id'] for body in calls}) == 40
    assert {row['method_version_id'] for row in result['catalog']['skills']} == set(by_id)
    assert len(result['packages']) == 40
    assert instance.get_bundle('alice', 'all-versions', result['catalog']['sha256']) == result


def test_oversized_catalog_is_rejected_whole_and_never_truncated_to_a_shortlist(tmp_path):
    rows = [published(index) for index in range(257)]
    instance = SkillProductionAdapter(lambda _: rows, None, tmp_path / 'large.sqlite3')
    with pytest.raises(ServiceError, match='catalog_too_large'):
        instance.freeze('alice', 'large', TASK, ['html-svg'])
    with pytest.raises(ServiceError, match='bundle_not_found'):
        instance.get_bundle('alice', 'large', 'f' * 64)


def test_foreign_or_changed_ledger_and_unreserved_existing_snapshot_fail_closed(tmp_path):
    path = tmp_path / 'foreign.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE projects (id TEXT)')
    with pytest.raises(ServiceError, match='ledger_not_isolated'):
        SkillProductionAdapter(lambda _: [], None, path)
    instance = SkillProductionAdapter(lambda _: [published()], None, tmp_path / 'ledger.sqlite3')
    original = instance.freeze('alice', 'operation', TASK, ['html-svg'])
    with pytest.raises(ServiceError, match='frozen_input_changed'):
        instance.freeze('alice', 'new-unreserved-operation', TASK, ['html-svg'], existing=original)
    with sqlite3.connect(instance.ledger_path) as db:
        db.execute('UPDATE animation_skill_production_freezes SET intent=?', (b'{"methods":[]}',))
    with pytest.raises(ServiceError, match='ledger_changed'):
        instance.freeze('alice', 'operation', TASK, ['html-svg'])
