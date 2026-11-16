"""Ledger integration with synthetic stopped jobs and inert media, never art evidence."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import build_skill_package
from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_animation_collaboration_contract as contract
from video_workbench.animation_method_service import AnimationMethodService
from test_animation_method_service import Gateway, Renderer, finish, start, runtime_capabilities
from test_animation_skill_method_service import SkillGateway


CRITERIA = [dict(id='coherence', description='All marks use one coordinate system.', requires_motion=False)]
PLAN = dict(engine='canvas', composition='A coherent synthetic landscape.',
    material='Directional marks follow the contours.', clock='Absolute seconds and a fixed seed.',
    interfaces='Every module exposes draw(ctx, seconds, shared).', keyframe_times=[0, 4, 8],
    components=[dict(id='structure', responsibility='Create the shared mountain contours.', depends_on=[],
                     interface='draw(ctx, seconds, shared)'),
                dict(id='foreground', responsibility='Create the foreground mass.', depends_on=[],
                     interface='draw(ctx, seconds, shared)'),
                dict(id='atmosphere', responsibility='Create atmosphere aligned with the forms.',
                     depends_on=['structure', 'foreground'], interface='draw(ctx, seconds, shared)')])


class CollaborationGateway(Gateway):
    def __init__(self):
        super().__init__()
        self.deadlines = []
        self.lookups = []
        self.fail_components = set()

    def upload(self, document, files):
        return [dict(path=contract.INPUT_NAME, sha256=digest(document), input_id=str(uuid4()),
                     role='animation_collaboration_input')]

    def prepare_submission(self, operation_id, document, deadline, refs):
        self.deadlines.append(deadline)
        return super().prepare_submission(operation_id, document, deadline, refs)

    def lookup(self, operation_id):
        self.lookups.append(operation_id)
        return super().lookup(operation_id)

    def collect(self, job_id, checksum, document):
        assert digest(self._job(job_id)['document']) == checksum == digest(document)
        if document.get('component_id') in self.fail_components:
            raise ServiceError('synthetic_invalid_component', 422)
        result = dict(schema=contract.RESULT_SCHEMA, input_sha256=checksum, phase=document['phase'],
            criteria=deepcopy(document['criteria'] or CRITERIA), methods=[], source=None, sound_events=[],
            review=None, knowledge_proposals=[], plan=None, component=None)
        if document['phase'] == 'plan':
            result['plan'] = deepcopy(PLAN)
        elif document['phase'] == 'component':
            result['component'] = dict(component_id=document['component_id'], engine='canvas',
                interface='draw(ctx, seconds, shared)', source='export function draw(ctx,t,shared) {}')
        else:
            result['source'] = dict(engine='canvas', strategy=dict(description='Synthetic composition',
                tradeoffs='No real rendering or quality claim.'), files={'scene.html': '<canvas></canvas>',
                'scene.js': 'window.__animationExperiment={seek(seconds){return seconds;}};'})
            result['methods'] = [dict(schema='video.animation-method.v2', title='Shared coordinates',
                problem='Independent responsibilities must share space.', guidance='Agree on space and time first.',
                applicability='Collaborative effects.', limitations=['Synthetic fixture only.'],
                criteria=deepcopy(result['criteria']), runtime_requirements={'engine': 'canvas'},
                research_record_ids=[], skill_package=build_skill_package(skill_id='fixture-coordinates',
                    version=1, kind='expression', runtime_requirements={'engines': ['canvas']},
                    files={'SKILL.md': '---\nname: fixture-coordinates\ndescription: Share coordinates.\n---\n'
                        'Agree on coordinates and clock first. This is an unrendered synthetic test fixture.'}))]
        return contract.validate_result(result, checksum, document)


class CanvasRenderer(Renderer):
    def render(self, operation_id, source, document, **options):
        super().render(operation_id, source, document, **options)
        result = self.results[operation_id]
        result['runtime']['engine'] = source['engine']
        result['media']['duration_seconds'] = document['constraints']['duration_seconds']
        return deepcopy(result)

    def frames(self, operation_id, max_frames=12):
        frames = super().frames(operation_id, max_frames=max_frames)
        frames[-1]['timestamp_seconds'] = 4
        return frames


@pytest.fixture
def work(tmp_path):
    ordinary, reviewer, collaboration = SkillGateway(), SkillGateway(True), CollaborationGateway()
    renderer = CanvasRenderer(tmp_path / 'renders')
    renderer.capabilities = lambda: runtime_capabilities(['html-svg', 'canvas'])
    service = AnimationMethodService(tmp_path / 'lab', author_gateway=ordinary,
        review_gateway=reviewer, renderer=renderer, collaboration_gateway=collaboration)
    return service, ordinary, reviewer, collaboration, renderer


def begin(service, **changes):
    return start(service, collaboration={'max_components': 3}, duration_seconds=8, **changes)


def until_parts(service, run_id):
    for _ in range(15):
        service.advance('developer', run_id)
        run = service.commissions.get('developer', run_id)
        parts = [op for op in run['operations'] if op['input'].get('phase') == 'component']
        if len(parts) == 2:
            return run, parts
    pytest.fail('two independent components were not reserved')


def test_complete_collaboration_has_one_candidate_exact_budget_and_scoped_context(work):
    service, ordinary, reviewer, author, renderer = work
    row = begin(service)
    run, parts = until_parts(service, row['id'])
    assert [op['input']['component_id'] for op in parts] == ['structure', 'foreground']
    assert all(op['status'] == 'reserved' for op in parts)
    result = finish(service, row['id'], steps=80)
    assert result['state'] == 'completed' and result['stop_reason'] == 'goal_met'
    run = service.commissions.get('developer', row['id'])
    creator = [op for op in run['operations'] if op['stage'] == 'creator']
    assert [op['input']['phase'] for op in creator] == ['plan', 'component', 'component', 'component', 'integrate']
    assert len(author.submissions) == 5 and ordinary.submissions == []
    assert len(renderer.calls) == len(reviewer.submissions) == len(run['candidates']) == 1
    assert run['budget']['creator'] == 5 and run['remaining'] == dict(creator=0, render=0, review=0)
    assert len(set(author.deadlines)) == 1 and author.deadlines[0] == run['deadline_at']
    assert all('knowledge_snapshot' not in op['input'] for op in creator)
    atmosphere = creator[3]['input']
    assert [dep['component_id'] for dep in atmosphere['dependencies']] == ['structure', 'foreground']
    assert all(op['input']['criteria'] == CRITERIA for op in creator[1:])
    assert all(op['input_resource_manifest'][0]['sha256'] == op['input_sha256'] for op in creator)
    render = next(op for op in run['operations'] if op['stage'] == 'render')
    assert render['input']['document'] == creator[-1]['input']
    assert render['input']['document']['schema'] == contract.INPUT_SCHEMA
    review = next(op for op in run['operations'] if op['stage'] == 'review')
    assert review['input']['observation']['frames']
    assert review['input']['knowledge_snapshot']['pages'] == []
    assert result['collaboration']['author_calls_used'] == result['collaboration']['author_calls_max'] == 5
    assert result['publication_ready'] is False


def test_unknown_submission_looks_up_same_operation_without_another_reservation(work):
    service, _, _, author, _ = work
    row = begin(service)
    author.lose_response = True
    service.advance('developer', row['id'])
    service.advance('developer', row['id'])
    run = service.commissions.get('developer', row['id'])
    original = run['operations'][0]
    assert original['status'] == 'submission_unknown'
    author.lose_response = False
    service.advance('developer', row['id'])
    assert author.lookups == [original['id']]
    assert author.submissions == [original['id']]
    assert len(service.commissions.get('developer', row['id'])['operations']) == 1


def test_cancel_drains_both_live_components_before_terminal_state(work):
    service, _, _, author, renderer = work
    row = begin(service)
    run, parts = until_parts(service, row['id'])
    author.active = True
    service.advance('developer', row['id'])
    assert len(author.submissions) == 3
    service.cancel('developer', row['id'], {})
    service.advance('developer', row['id'])
    current = service.commissions.get('developer', row['id'])
    assert current['state'] not in {'completed', 'cancelled', 'failed'}
    assert all(author._job(op['job_id'])['stop_proven'] for op in current['operations'][1:])
    result = finish(service, row['id'])
    assert result['state'] == 'cancelled' and renderer.calls == []
    assert len(author.submissions) == 3


def test_failed_part_releases_unsubmitted_peer_and_does_not_integrate(work):
    service, _, _, author, renderer = work
    row = begin(service)
    _, parts = until_parts(service, row['id'])
    # Submit one first, so collection failure occurs before its peer is claimed.
    service._advance_agent('developer', service.commissions.get('developer', row['id']), parts[0])
    author.fail_components = {'structure'}
    result = finish(service, row['id'])
    assert result['state'] == 'failed' and renderer.calls == []
    run = service.commissions.get('developer', row['id'])
    assert len(author.submissions) == 2
    assert run['operations'][-1]['status'] == 'released'


def test_continuation_inherits_collaboration_but_ordinary_request_stays_ordinary(work):
    service, ordinary, _, author, _ = work
    first = finish(service, begin(service)['id'], steps=80)
    continuation = start(service, base_commission_id=first['id'], duration_seconds=8)
    assert service.commissions.get('developer', continuation['id'])['frozen']['collaboration'] == {'max_components': 3}
    assert continuation['collaboration']['enabled'] is True
    ordinary_row = start(service)
    assert 'collaboration' not in service.commissions.get('developer', ordinary_row['id'])['frozen']
    assert len(author.submissions) == 5 and ordinary.submissions == []


def test_disabled_or_invalid_collaboration_spends_nothing(work):
    service, ordinary, _, author, renderer = work
    for changes in [dict(collaboration={'max_components': 5}),
                    dict(collaboration={}, budget=dict(max_rounds=2, max_seconds=300)),
                    dict(collaboration={}, role='transfer')]:
        with pytest.raises(ServiceError):
            start(service, **changes)
    service.collaboration_author = None
    with pytest.raises(ServiceError, match='not_admitted'):
        begin(service)
    assert author.submissions == ordinary.submissions == renderer.calls == []
