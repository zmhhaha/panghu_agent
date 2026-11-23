"""Adaptive studies keep real rendered evidence and immutable paid identities."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_picture_learning as picture
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench import learning_strategy as strategy
from video_workbench.learning_strategy_service import LearningStrategyMixin


def source(engine='canvas'):
    value = dict(engine=engine, strategy=dict(description='Editable study', tradeoffs='Static pixels only'),
        files={'scene.html': '<canvas id="scene"></canvas><script src="/scene.js"></script>',
            'scene.js': 'window.seek=()=>{};'})
    value['source_sha256'] = source_sha256(value)
    return value


def document():
    return dict(schema=picture.INPUT_SCHEMA, mode='create', project_id='project',
        experiment_id='experiment', work_kind='reproduction', goal='Learn material and depth.',
        criteria=['Keep the focal shape legible.'],
        constraints=dict(width=320, height=180, allowed_engines=['canvas', 'html-svg']),
        observation=dict(frames=[dict(frame_id='reference-1', file_name='reference-1.png',
            sha256='a' * 64, purpose='reference')]), base_source=None, feedback='Frozen feedback.',
        knowledge_snapshot=None, knowledge_binding=dict(use_wikiskill=False),
        prior_review=None, learned_method=None, assets={})


def plan(*, probes=True, components=True):
    return dict(rationale='Study texture first, then separate material and spatial composition.',
        criteria=['Texture supports the focal form.'],
        probes=[dict(id='texture', goal='Study one patch of dry brush texture.',
            criteria=['The patch has directional dry texture.'])] if probes else [],
        components=[
            dict(id='material', goal='Create the original material method.', criteria=['Material remains readable.'],
                depends_on=[], interface='Export material function in shared coordinates.'),
            dict(id='space', goal='Use the material in a layered spatial composition.',
                criteria=['Near and far remain distinct.'], depends_on=['material'],
                interface='Compose one coherent scene using the material method.'),
        ] if components else [])


class Store:
    def __init__(self):
        self.operations, self.blobs = {}, {}

    def read(self, table, owner, identity):
        assert table == 'operations'
        return deepcopy(self.operations[identity])

    def blob(self, raw):
        self.blobs[digest(raw.hex())] = raw
        return digest(raw.hex())


class Host(LearningStrategyMixin):
    protocol = picture

    def __init__(self):
        self.store = Store()
        self.clock = lambda: 10

    @staticmethod
    def _has_picture(candidate):
        return bool(candidate.get('picture_sha256')) and bool(candidate.get('observed_sha256'))


def run(config=None):
    base = document()
    return dict(id='experiment', owner='developer', project_id='project',
        stage='author', work_kind='reproduction', round=1, deadline_at=600,
        frozen=dict(goal=base['goal'], criteria=base['criteria'],
            learning_strategy=config if config is not None else strategy.options()),
        candidates=[], internal_attempts=[])


def complete(host, run_value, *, proposed=None, resource=None, review=False):
    base = document()
    base['mode'] = 'review' if review else 'create'
    if review:
        base['observation']['frames'].append(dict(frame_id='output-1', file_name='output-1.png',
            sha256='b' * 64, purpose='output'))
    projected = host._learning_document(run_value, base)
    picture.validate_input(projected)
    identity = 'operation-' + str(len(host.store.operations) + 1)
    result = dict(schema=picture.RESULT_SCHEMA, input_sha256=digest(projected),
        mode=projected['mode'], source=None if review else source(),
        review=dict(score=70, summary='Actual dry texture is visible.', issues=[], stop_recommended=False)
            if review else None,
        transfer_task=None, method=None if review else {})
    if proposed is not None:
        result['method']['learning_strategy'] = deepcopy(proposed)
    if resource is not None:
        result['method']['resource_plan'] = deepcopy(resource)
    picture.validate_result(result, digest(projected), projected)
    operation = dict(id=identity, stage='review' if review else 'author',
        status='completed', input=projected, input_sha256=digest(projected), result=result)
    host.store.operations[identity] = deepcopy(operation)
    handled = host._learning_consume_agent_result(run_value, operation, result)
    return handled, operation


def mark_probe_rendered(host, value):
    candidate = host._learning_candidate(value)
    candidate.update(status='rendered', picture_sha256='c' * 64,
        observed_sha256='b' * 64, observed_suffix='.png',
        artifacts=dict(source=candidate['artifacts']['source'],
            picture=dict(id=candidate['id'] + '-picture', sha256='c' * 64, mime_type='image/png')))
    value['stage'] = 'review'
    return candidate


def test_default_first_creator_can_deliver_without_manufacturing_extra_tasks():
    host, value = Host(), run()
    handled, operation = complete(host, value)
    assert handled is False
    assert value['learning_strategy_complete'] is True
    assert len(host.store.operations) == 1
    assert value['internal_attempts'] == []
    assert operation['input']['constraints']['learning_strategy_task']['phase'] == 'plan'
    assert 'learning_strategy_task' not in host._learning_document(value, document())['constraints']


def test_no_new_task_changes_old_frozen_experiments():
    host, value = Host(), run()
    value['frozen'].pop('learning_strategy')
    base = document()
    assert host._learning_document(value, deepcopy(base)) == base
    operation = dict(input=base)
    assert host._learning_consume_agent_result(value, operation, {}) is False


def test_probes_are_actually_rendered_reviewed_and_used_before_distinct_creators():
    host, value = Host(), run()
    handled, planning = complete(host, value, proposed=plan())
    assert handled and value['stage'] == 'author'
    assert value['learning_strategy']['phase'] == 'probe_author'
    handled, probe_author = complete(host, value)
    assert handled and value['stage'] == 'render'
    assert value['candidates'] == [] and len(value['internal_attempts']) == 1
    render_doc = host._learning_document(value, document())
    assert render_doc['mode'] == 'create'
    assert render_doc['constraints']['learning_strategy_task']['phase'] == 'probe_render'
    assert host._learning_progress(value)['phase'] == 'probe_render'
    picture.validate_input(render_doc)
    with pytest.raises(ServiceError, match='probe_not_rendered'):
        value['stage'] = 'review'
        host._learning_document(value, dict(document(), mode='review'))
    mark_probe_rendered(host, value)
    handled, probe_review = complete(host, value, review=True)
    assert handled and value['learning_strategy']['phase'] == 'component'
    assert value['learning_strategy']['component_id'] == 'material'
    handled, material = complete(host, value)
    assert handled and value['learning_strategy']['component_id'] == 'space'
    dependency = host._learning_document(value, document())['constraints']['learning_strategy_task']['dependencies'][0]
    assert dependency['id'] == 'material'
    assert dependency['source'] == material['result']['source']
    assert dependency['observation_scope'] == 'source_only_not_separately_rendered'
    handled, spatial = complete(host, value)
    assert handled and value['learning_strategy']['phase'] == 'compose'
    composition = host._learning_document(value, document())
    task = composition['constraints']['learning_strategy_task']
    assert len(task['dependencies']) == 2
    assert task['probes'][0]['review'] == probe_review['result']['review']
    assert task['probes'][0]['source'] == probe_author['result']['source']
    assert task['probes'][0]['evidence_frame_ids'] == ['probe-1-1']
    assert composition['observation']['frames'][-1]['sha256'] == 'b' * 64
    assert all(op['input']['feedback'] == 'Frozen feedback.' for op in host.store.operations.values())
    assert all(op['input']['knowledge_snapshot'] is None for op in host.store.operations.values())
    assert composition['knowledge_binding'] == dict(use_wikiskill=False)
    handled, integrated = complete(host, value)
    assert handled is False and value['learning_strategy_complete']
    assert len(host.store.operations) == 6
    assert len({planning['id'], probe_author['id'], probe_review['id'],
        material['id'], spatial['id'], integrated['id']}) == 6
    assert host._learning_progress(value)['complete'] is True


@pytest.mark.parametrize('flag, proposed, code', [
    ('allow_collaboration', plan(probes=False), 'collaboration_disabled'),
    ('allow_probes', plan(components=False), 'probes_disabled'),
])
def test_user_disabled_options_are_enforced_before_any_subtask(flag, proposed, code):
    config = strategy.options(**{flag: False})
    host, value = Host(), run(config)
    with pytest.raises(ServiceError, match=code):
        complete(host, value, proposed=proposed)
    assert len(host.store.operations) == 1 and value['internal_attempts'] == []


def test_cycle_and_foreign_dependencies_are_rejected_not_serialized_arbitrarily():
    for dependency in ('space', 'not-a-task'):
        proposed = plan(probes=False)
        proposed['components'][0]['depends_on'] = [dependency]
        with pytest.raises(ServiceError, match='dependency_cycle|dependencies_invalid'):
            strategy.validate_plan(proposed, config=strategy.options(), engine='canvas',
                allowed_engines=['canvas'])


def test_manim_resource_gate_does_not_claim_browser_collaboration():
    resource = dict(kind='manim', goal='Construct geometry.')
    with pytest.raises(ServiceError, match='resource_subtasks_not_admitted'):
        strategy.validate_plan(plan(), config=strategy.options(), engine='canvas',
            allowed_engines=['canvas'], resource_plan=resource)
    host, value = Host(), run()
    handled, operation = complete(host, value, resource=resource)
    assert handled and value['stage'] == 'learning_resources'
    host._learning_resources_ready(value)
    assert value['stage'] == 'author' and value['learning_strategy']['phase'] == 'compose'
    composed = host._learning_document(value, document())
    assert composed['constraints']['learning_strategy_task']['dependencies'] == []


def test_retry_preserves_actual_plan_probe_and_dependency_identities():
    host, value = Host(), run()
    complete(host, value, proposed=plan(components=False))
    complete(host, value)
    mark_probe_rendered(host, value)
    recovered = run()
    host._learning_restore_retry(value, recovered)
    assert recovered['learning_strategy'] == value['learning_strategy']
    assert recovered['internal_attempts'] == value['internal_attempts']
    recovered['internal_attempts'][0]['status'] = 'still-retained'
    assert value['internal_attempts'][0]['status'] == 'rendered'
    assert len(host.store.operations) == 2


def test_mutated_paid_input_or_component_result_is_rejected_before_more_work():
    host, value = Host(), run()
    _, planning = complete(host, value, proposed=plan(probes=False))
    host.store.operations[planning['id']]['input']['feedback'] = 'Changed during run'
    with pytest.raises(ServiceError, match='input_changed'):
        host._learning_document(value, document())
    host, value = Host(), run()
    complete(host, value, proposed=plan(probes=False))
    _, component = complete(host, value)
    host.store.operations[component['id']]['result']['source']['files']['scene.js'] = 'changed'
    with pytest.raises(ServiceError, match='operation_changed'):
        host._learning_document(value, document())


def test_frame_budget_never_silently_omits_a_completed_probe():
    host, value = Host(), run()
    complete(host, value, proposed=plan(components=False))
    complete(host, value)
    mark_probe_rendered(host, value)
    complete(host, value, review=True)
    full = document()
    full['observation']['frames'] *= picture.MAX_FRAMES
    with pytest.raises(ServiceError, match='probe_frame_budget_exceeded'):
        host._learning_document(value, full)


def test_deadline_after_a_completed_component_preserves_result_and_stops_without_scheduling():
    host, value = Host(), run()
    complete(host, value, proposed=plan(probes=False))
    stopped = []
    host._finish = lambda run_value, reason: stopped.append((run_value['id'], reason))
    host.clock = lambda: value['deadline_at']
    handled, component = complete(host, value)
    assert handled and stopped == [('experiment', 'budget_exhausted')]
    assert value['learning_strategy']['components'][0]['operation']['id'] == component['id']
    assert len(host.store.operations) == 2


@pytest.mark.parametrize('probes,wrong_task', [(False, 'space'), (True, 'another-study')])
def test_same_phase_result_for_a_different_task_is_rejected_before_recording(probes, wrong_task):
    host, value = Host(), run()
    complete(host, value, proposed=plan(probes=probes))
    projected = host._learning_document(value, document())
    original_phase = projected['constraints']['learning_strategy_task']['phase']
    projected['constraints']['learning_strategy_task']['task_id'] = wrong_task
    operation = dict(id='stale-task-result', input=projected)
    with pytest.raises(ServiceError, match='learning_strategy_task_changed'):
        host._learning_consume_agent_result(value, operation, dict(method={}))
    assert value['learning_strategy']['phase'] == original_phase
    assert not value['learning_strategy']['components'] and not value['internal_attempts']
