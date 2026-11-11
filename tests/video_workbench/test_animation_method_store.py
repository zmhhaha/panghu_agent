"""Real persistence, human publication and many-to-many method custody."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_method_store import AnimationMethodStore


def method_body(**changes):
    return dict(title='看懂群体筛选', problem='筛选后统计突然变化，观众无法关联',
        guidance='先展示对象去向，再保持群体可见地改变统计。',
        applicability='解释群体变化与结果之间的因果',
        limitations=['只验证当前HTML/SVG运行环境'],
        criteria=[dict(id='causal-link', description='看清留下的群体与统计变化的联系')],
        runtime_requirements=dict(engine='html-svg'), **changes)


def demonstration_body(mark='a', **changes):
    result = dict(title='筛选与统计完整片段', goal='让观众看懂群体筛选后的统计变化',
        media=dict(sha256=mark * 64, duration_seconds=14,
                   url='/internal/demonstrations/' + mark + '/media'),
        source=None,
        runtime=dict(engine='html-svg', admission=dict(
            status='admitted', id='verified-html-svg', evidence_sha256='c' * 64)),
        technical=dict(status='passed', evidence_sha256='d' * 64),
        provenance=dict(creation_job_id='job-' + mark, render_id='render-' + mark))
    result.update(changes)
    return result


@pytest.fixture
def store(tmp_path):
    return AnimationMethodStore(tmp_path / 'methods.sqlite3')


def create(store, *, owner='alice', key='method-1', **changes):
    body = method_body()
    body.update(changes)
    return store.create_method(owner, body, key)


def demo(store, mark='a', *, owner='alice', **changes):
    return store.add_demonstration(owner, demonstration_body(mark, **changes), 'demo-' + mark)


def validation(store, method, demonstration, *, baseline=None, outcome='passed',
               key=None, structural_changes=None):
    role = 'transfer' if baseline else 'reference'
    body = dict(method_version_id=method['id'],
        demonstration_version_id=demonstration['id'], role=role,
        criteria_sha256=method['criteria_sha256'], outcome=outcome,
        evidence=dict(media_sha256=demonstration['media']['sha256'],
                      criteria_result='observed and compared'))
    if baseline:
        body.update(baseline_demonstration_version_id=baseline['id'],
                    structural_changes=structural_changes or {
                        'object_count': dict(before=5, after=9)})
    return store.link_validation('alice', body, key or role + '-' + method['id'] + '-' + demonstration['id'])


def acceptance(store, method, checked, *, owner='alice', key=None, **changes):
    body = dict(validation_id=checked['id'], comment='已看完整片段，认可这个结果',
                method_content_sha256=method['content_sha256'])
    body.update(changes)
    return store.accept(owner, body, key or 'accept-' + checked['id'])


def publish(store, method):
    first = demo(store, 'a')
    second = demo(store, 'b')
    reference = validation(store, method, first)
    transfer = validation(store, method, second, baseline=first)
    acceptance(store, method, reference)
    acceptance(store, method, transfer)
    return store.publish('alice', method['id'], 'publish-' + method['id'])


def error(code):
    return pytest.raises(ServiceError, match='^' + code + '$')


def test_methods_start_without_finished_demonstration_or_source(store):
    original = method_body()
    method = store.create_method('alice', original, 'first')
    assert method['status'] == 'candidate'
    assert method['version'] == 1
    assert method['guidance'] == original['guidance']
    assert 'source' not in method
    assert store.list_demonstrations('alice') == []
    original['criteria'][0]['description'] = 'changed caller object'
    method['guidance'] = 'changed returned object'
    actual = store.get_method('alice', method['id'])
    assert actual['guidance'] != method['guidance']
    assert actual['criteria'][0]['description'] != original['criteria'][0]['description']
    assert store.list_methods('alice', query='群体 因果') == [actual]
    assert store.list_methods('alice', query='unknown') == []
    assert store.list_methods('bob') == []
    with error('animation_method_not_found'):
        store.get_method('bob', actual['id'])
    with error('animation_method_not_found'):
        store.read_published('alice', [actual['id']], 'before-publication')


def test_one_demonstration_can_validate_multiple_methods_and_the_reverse(store):
    first = create(store)
    second = create(store, key='second', guidance='按事件逐步揭示关联信息')
    common = demo(store, 'a')
    transfer = demo(store, 'b')
    links = [validation(store, first, common), validation(store, second, common),
             validation(store, first, transfer, baseline=common)]
    assert len(store.list_demonstrations('alice')) == 2
    assert len(store.list_validations('alice', demonstration_version_id=common['id'])) == 2
    assert len(store.list_validations('alice', method_version_id=first['id'])) == 2
    assert all(row['acceptance'] is None for row in store.list_validations('alice'))
    assert {link['method_version_id'] for link in links} == {first['id'], second['id']}
    with error('animation_method_not_found'):
        store.get_demonstration('bob', common['id'])
    assert store.list_validations('bob') == []


def test_publication_needs_both_explicit_approvals_of_bound_evidence(store):
    method = create(store)
    first, second = demo(store), demo(store, 'b')
    reference = validation(store, method, first)
    transfer = validation(store, method, second, baseline=first)
    with error('animation_method_publication_blocked'):
        store.publish('alice', method['id'], 'not-yet')
    acceptance(store, method, reference)
    with error('animation_method_publication_blocked'):
        store.publish('alice', method['id'], 'still-not')
    acceptance(store, method, transfer)
    assert store.get_method('alice', method['id'])['status'] == 'candidate'
    published = store.publish('alice', method['id'], 'publish')
    assert published['status'] == 'published'
    assert published['publication']['reference_validation_ids'] == [reference['id']]
    assert published['publication']['transfer_validation_ids'] == [transfer['id']]
    assert store.get_method('bob', method['id']) == published
    assert store.list_methods('bob', published_only=True) == [published]
    with error('animation_method_not_found'):
        store.publish('bob', method['id'], 'steal')


def test_human_acceptance_cannot_target_another_version_or_be_forged_as_model_output(store):
    method = create(store)
    first = demo(store)
    checked = validation(store, method, first)
    with error('animation_method_not_found'):
        acceptance(store, method, checked, owner='bob')
    with error('animation_method_acceptance_target_mismatch'):
        acceptance(store, method, checked, method_content_sha256='f' * 64)
    with error('invalid_animation_method'):
        acceptance(store, method, checked, source='agent_review')
    with error('invalid_animation_method'):
        acceptance(store, method, checked, comment='')
    accepted = acceptance(store, method, checked)
    assert accepted['source'] == 'explicit_developer_acceptance'
    assert accepted['acceptance_target_sha256'] == checked['acceptance_target_sha256']


@pytest.mark.parametrize('outcome', ['failed', 'incomplete'])
def test_unsatisfied_or_unobserved_validation_cannot_be_accepted_as_passed(store, outcome):
    method, first = create(store), demo(store)
    checked = validation(store, method, first, outcome=outcome)
    with error('animation_method_validation_not_passed'):
        acceptance(store, method, checked)


@pytest.mark.parametrize('failure', ['technical', 'admission'])
def test_runtime_and_technical_evidence_gate_acceptance(store, failure):
    method = create(store)
    body = demonstration_body()
    if failure == 'technical':
        body['technical']['status'] = 'unverified'
    else:
        body['runtime']['admission']['status'] = 'not_admitted'
    first = store.add_demonstration('alice', body, 'demo')
    checked = validation(store, method, first)
    with error('animation_method_demonstration_not_admitted'):
        acceptance(store, method, checked)


def test_transfer_requires_real_structure_change_and_distinct_media(store):
    method = create(store)
    first, second = demo(store), demo(store, 'b')
    with error('animation_method_transfer_baseline_required'):
        validation(store, method, second, baseline=first)
    validation(store, method, first)
    with error('animation_method_structural_change_required'):
        validation(store, method, second, baseline=first, key='renamed',
                   structural_changes=dict(labels=dict(before='old', after='new')))
    with error('animation_method_structural_change_required'):
        validation(store, method, second, baseline=first, key='same-count',
                   structural_changes=dict(object_count=dict(before=5, after=5)))
    duplicated = demo(store, 'c', media=deepcopy(first['media']))
    with error('animation_method_transfer_not_distinct'):
        validation(store, method, duplicated, baseline=first)
    with error('animation_method_transfer_duration_mismatch'):
        validation(store, method, second, baseline=first, key='false-duration',
                   structural_changes=dict(duration=dict(before=14, after=20)))
    result = validation(store, method, second, baseline=first)
    assert result['structural_changes']['object_count'] == dict(before=5, after=9)


def test_new_method_version_does_not_inherit_acceptances_or_change_frozen_delivery(store):
    original = publish(store, create(store))
    delivered = store.read_published('bob', [original['id']], 'video-task-1')
    assert delivered['methods'][0]['guidance'] == original['guidance']
    assert delivered['read_receipt']['scope'] == 'methods_delivered_to_caller_not_model_observation'
    changed = create(store, key='method-2', parent_version_id=original['id'],
                     guidance='先保持群体位置稳定，再用聚合动作显示统计关系。')
    assert changed['method_id'] == original['method_id']
    assert changed['version'] == 2 and changed['status'] == 'candidate'
    assert store.list_validations('alice', method_version_id=changed['id']) == []
    with error('animation_method_publication_blocked'):
        store.publish('alice', changed['id'], 'premature-v2')
    with error('animation_method_not_found'):
        store.read_published('bob', [changed['id']], 'video-task-2')
    assert store.read_published('bob', [original['id']], 'video-task-1') == delivered
    assert store.get_method('alice', original['id'])['status'] == 'published'


def test_method_combination_delivery_is_atomic_and_consumer_bound(store):
    first = publish(store, create(store))
    second = create(store, key='method-2')
    reference, transferred = store.list_demonstrations('alice')
    r2 = validation(store, second, reference)
    t2 = validation(store, second, transferred, baseline=reference)
    acceptance(store, second, r2)
    acceptance(store, second, t2)
    store.publish('alice', second['id'], 'publish-second')
    combined = store.read_published('bob', [first['id'], second['id']], 'combined-task')
    assert [row['id'] for row in combined['methods']] == [first['id'], second['id']]
    with error('animation_method_idempotency_conflict'):
        store.read_published('bob', [second['id']], 'combined-task')
    with error('invalid_animation_method'):
        store.read_published('bob', [first['id'], first['id']], 'duplicate')
    assert store.read_published('bob', [first['id'], second['id']], 'combined-task') == combined


def test_restarts_and_concurrent_replays_keep_single_identity_and_event(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: create(store), range(8)))
    assert all(row == results[0] for row in results)
    assert len(store.list_methods('alice')) == 1
    assert len(store.events('alice')) == 1
    reopened = AnimationMethodStore(store.path)
    assert create(reopened) == results[0]
    with error('animation_method_idempotency_conflict'):
        create(reopened, guidance='different')
    assert len(reopened.events('alice')) == 1
    assert reopened.events('bob') == []


def test_foreign_database_and_nonfinite_records_are_rejected(tmp_path, store):
    path = tmp_path / 'business.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY)')
    with error('animation_method_database_not_isolated'):
        AnimationMethodStore(path)
    body = method_body()
    body['runtime_requirements']['invalid'] = float('nan')
    with error('invalid_animation_method_json'):
        store.create_method('alice', body, 'nan')


def test_demonstration_does_not_require_narration_or_discard_sound_effects(store):
    body = demonstration_body()
    body['media'].update(audio_sha256='e' * 64, audio_kind='sound_effects')
    body['source'] = dict(files={'scene.html': '<html>agent generated</html>'},
                          sound_events=[dict(time=1.2, asset_sha256='f' * 64)])
    value = store.add_demonstration('alice', body, 'sound-effect-demo')
    assert value['media']['audio_kind'] == 'sound_effects'
    assert value['source']['sound_events'][0]['time'] == 1.2
    assert 'narration' not in value


def test_human_verification_can_complete_unobserved_criteria_without_rewriting_agent_review(store):
    method = create(store, criteria=[
        dict(id='motion', description='对象移动可跟踪', requires_motion=True),
        dict(id='sound', description='声音和动作配合', requires_audio=True)])
    original = demonstration_body()
    original['media']['audio_sha256'] = 'e' * 64
    first = store.add_demonstration('alice', original, 'demo-audio-first')
    adapted = demonstration_body('b')
    adapted['media']['audio_sha256'] = 'f' * 64
    second = store.add_demonstration('alice', adapted, 'demo-audio-transfer')
    reference = validation(store, method, first, outcome='incomplete')
    transfer = validation(store, method, second, baseline=first, outcome='incomplete')
    human = dict(criteria_ids=['motion', 'sound'], motion_watched=True, audio_listened=True,
                 comment='实际看过并听过这两个完整片段')
    for checked in (reference, transfer):
        with error('animation_method_validation_not_passed'):
            acceptance(store, method, checked, key='unobserved-' + checked['id'])
        accepted = acceptance(store, method, checked, human_verification=human)
        assert accepted['acceptance_basis'] == 'explicit_human_verification'
        assert accepted['review_outcome_at_acceptance'] == 'incomplete'
        assert accepted['human_verification']['audio_listened'] is True
    published = store.publish('alice', method['id'], 'publish-human-observed')
    assert published['status'] == 'published'
    unchanged = store.list_validations('alice', method_version_id=method['id'])
    assert all(row['outcome'] == 'incomplete' for row in unchanged)
    assert all(row['evidence'] == checked['evidence']
               for row, checked in zip(unchanged, (reference, transfer)))


@pytest.mark.parametrize('missing,code', [
    ('criterion', 'animation_method_human_criteria_incomplete'),
    ('motion', 'animation_method_human_motion_required'),
    ('audio', 'animation_method_human_audio_required'),
    ('track', 'animation_method_audio_evidence_required'),
])
def test_human_cannot_accept_unwatched_unheard_or_missing_media_evidence(store, missing, code):
    method = create(store, criteria=[
        dict(id='motion', description='看过连续移动', requires_motion=True),
        dict(id='sound', description='听过音画配合', requires_audio=True)])
    media = dict(demonstration_body()['media'], audio_sha256='e' * 64)
    if missing == 'track':
        media.pop('audio_sha256')
    first = demo(store, media=media)
    checked = validation(store, method, first, outcome='incomplete')
    human = dict(criteria_ids=['motion', 'sound'], motion_watched=True, audio_listened=True)
    if missing == 'criterion':
        human['criteria_ids'] = ['motion']
    elif missing in {'motion', 'audio'}:
        human[missing + ('_watched' if missing == 'motion' else '_listened')] = False
    with error(code):
        acceptance(store, method, checked, human_verification=human)


def test_human_observation_is_not_a_bypass_for_an_explicit_failed_criterion(store):
    method, first = create(store), demo(store)
    checked = validation(store, method, first, outcome='failed')
    human = dict(criteria_ids=['causal-link'], motion_watched=True, audio_listened=False)
    with error('animation_method_validation_not_passed'):
        acceptance(store, method, checked, human_verification=human)
