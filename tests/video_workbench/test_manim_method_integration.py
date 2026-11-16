"""Resource prelude/method custody tests; fake execution never proves runtime admission."""
from copy import deepcopy
import json
import time

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.animation_method_renderer import MethodAnimationRenderer
from video_workbench.manim_generated_assets import freeze_generated_assets
from video_workbench.manim_method_coordinator import ManimMethodCoordinator, authoring_request
from video_workbench.manim_method_resources import method_resource, validate_descriptor
from video_workbench.manim_method_resources import PRODUCER_REQUIREMENT, require_method_producer
from video_workbench.manim_resource import compile_resource, frame_path
from test_manim_resource import png, request, IMAGE
from test_animation_method_renderer import SOURCE, document, IMAGE as METHOD_IMAGE

PINNED = 'registry.internal/manim:latest@' + IMAGE
OWNER = 'casdoor/user@example.com'


class RecipeGateway:
    def __init__(self):
        self.submissions = 0
        self.unknown = False
        self.job = None
        self.cancelled = False

    def preflight(self, deadline): return {'ready': True}
    def upload(self, document): return {'input_sha256': digest(document)}
    def prepare_submission(self, operation, document, deadline, refs):
        def admit():
            self.submissions += 1
            self.document = document
            self.job = dict(job_id='recipe-job', state='completed', stop_proven=True,
                stop_proof={'fixture': 'stopped fake Agent'})
            if self.unknown: raise TimeoutError('lost response after admission')
            return self.job
        return admit
    def lookup(self, operation): return self.job
    def poll(self, job_id): return self.job
    def cancel(self, job_id): self.cancelled = True
    def collect(self, job_id, checksum, document):
        recipe = request(**document['resource_constraints'])
        return {'result': {'request': recipe}, 'evidence': {'sha256': 'e' * 64}}


class ResourceExecutor:
    image = PINNED
    def __init__(self): self.result = None; self.calls = 0
    def lookup(self, **scope):
        return deepcopy(self.result) if self.result is not None and self.result['scope'] == scope else None
    def execute(self, plan):
        self.calls += 1
        recipe = plan['request']
        files = compile_resource(recipe, {frame_path(recipe['resource_id'], index):
            png(size=(recipe['width'], recipe['height']), color=(index * 17 % 255, 12, 33, 200))
            for index in range(recipe['frame_count'])}, runtime_image_digest=IMAGE)
        envelope = freeze_generated_assets(files, recipe, **plan['scope'], runtime_image_digest=IMAGE,
            stop_evidence=StopEvidence(plan['scope']['operation_id'], True, ('fake-stopped-container',)))
        self.result = dict(status='completed', scope=plan['scope'], plan_sha256=plan['sha256'],
            image=self.image, stop_proven=True, generated_assets=envelope, error=None)
        return deepcopy(self.result)
    def cancel(self, **scope): pass
    def recover(self, **scope): return self.lookup(**scope)


def setup(tmp_path, *, candidate=True):
    gateway, executor = RecipeGateway(), ResourceExecutor()
    adapter = SkillProductionAdapter(lambda owner: {'methods': []}, None, tmp_path/'skills.sqlite3')
    coordinator = ManimMethodCoordinator(tmp_path/'resource-coordinator', gateway=gateway,
        executor=executor, skill_adapter=adapter, candidate=candidate)
    run = dict(id='commission', deadline_at=time.time()+300,
        frozen=dict(duration_seconds=3, goal='Create an original geometry transformation.',
            resource_authoring=dict(kind='manim', width=320, height=180, frame_count=3, global_start_frame=24)))
    return coordinator, gateway, executor, run


def finish(coordinator, run):
    for _ in range(15):
        state = coordinator.advance(OWNER, run)
        if state in {'completed', 'failed', 'cancelled'}: return state
    raise AssertionError('prelude did not terminate')


def test_original_recipe_precedes_one_stopped_resource_and_exact_v5_method_context(tmp_path):
    coordinator, gateway, executor, run = setup(tmp_path)
    assert not coordinator.capabilities()['enabled']
    assert finish(coordinator, run) == 'completed'
    assert gateway.submissions == executor.calls == 1
    descriptor = coordinator.context(OWNER, run['id'])
    files = coordinator.resolve(descriptor)
    context = document(duration=3)
    context['constraints']['generated_resources'] = [descriptor]
    context['assets'] = descriptor['assets_sha256']
    renderer = MethodAnimationRenderer(tmp_path/'method-renders', METHOD_IMAGE,
        hyperframes=True, legacy_bridge=True, generated_resource_resolver=coordinator.resolve)
    binding = renderer._snapshot('method-one', SOURCE, context, [])
    assert binding['request']['schema'] == 'video.animation-method-render-request.v5'
    assert binding['request']['generated_resources'] == [descriptor]
    assert binding['request']['assets_sha256'] == {name: __import__('hashlib').sha256(raw).hexdigest() for name, raw in files.items()}
    assert descriptor['manifest']['global_start_frame'] == 24
    assert descriptor['manifest']['runtime']['image_digest'] == IMAGE
    assert not coordinator.capabilities()['enabled']  # A synthetic result cannot admit itself.
    assert finish(coordinator, run) == 'completed'
    assert gateway.submissions == executor.calls == 1


def test_lost_recipe_response_reconciles_original_operation_after_restart(tmp_path):
    coordinator, gateway, executor, run = setup(tmp_path)
    gateway.unknown = True
    for _ in range(3): coordinator.advance(OWNER, run)
    assert coordinator.view(OWNER, run['id'])['status'] == 'recipe_unknown'
    resumed = ManimMethodCoordinator(coordinator.root, gateway=gateway, executor=executor,
        skill_adapter=coordinator.skill_adapter, candidate=True)
    assert finish(resumed, run) == 'completed'
    assert gateway.submissions == executor.calls == 1


def test_reading_resource_status_or_context_never_submits_or_executes(tmp_path):
    coordinator, gateway, executor, run = setup(tmp_path)
    for _ in range(3): assert coordinator.view(OWNER, run['id'])['status'] == 'pending'
    with pytest.raises(ServiceError, match='not_ready'): coordinator.context(OWNER, run['id'])
    assert gateway.submissions == executor.calls == 0


def test_cancellation_before_claim_spends_no_recipe_or_render_call(tmp_path):
    coordinator, gateway, executor, run = setup(tmp_path)
    coordinator.advance(OWNER, run)
    assert coordinator.advance(OWNER, run, cancelled=True) == 'cancelled'
    assert gateway.submissions == executor.calls == 0


def test_generated_resource_reference_cannot_cross_owner_effect_or_attempt(tmp_path):
    coordinator, _, _, run = setup(tmp_path)
    finish(coordinator, run)
    original = coordinator.context(OWNER, run['id'])
    for field in ['owner', 'effect_id', 'experiment_id', 'operation_id']:
        changed = deepcopy(original)
        changed['scope'][field] = 'another'
        with pytest.raises(ServiceError, match='not_owned'): coordinator.resolve(changed)


def test_default_disabled_has_no_live_admission_or_preflight(tmp_path):
    coordinator, gateway, executor, run = setup(tmp_path, candidate=False)
    assert coordinator.capabilities()['status'] == 'prepared_not_admitted'
    with pytest.raises(ServiceError, match='not_admitted'): coordinator.preflight(run['deadline_at'])
    with pytest.raises(ServiceError, match='not_admitted'): coordinator.advance(OWNER, run)
    assert gateway.submissions == executor.calls == 0


def test_recipe_directory_can_read_published_producer_skill_without_reusing_old_pngs(tmp_path):
    from agent_service.animation_skills_contract import SkillCatalogTools
    from test_animation_skill_compatibility import dependent
    row = dependent(resources=True)
    coordinator, gateway, executor, run = setup(tmp_path)
    coordinator.skill_adapter = SkillProductionAdapter(lambda _: [row], None, tmp_path/'published-skills.sqlite3')
    assert finish(coordinator, run) == 'completed'
    first = coordinator._get(OWNER, run['id'])
    delivery = coordinator.skill_adapter.get_bundle(OWNER, first['scope']['operation_id'],
        first['input']['skill_catalog']['sha256'])
    tools = SkillCatalogTools(delivery['catalog'], delivery['packages'])
    assert tools.search(row['skill_package']['skill_id'])['skills']
    assert tools.read(row['skill_package']['sha256'])
    assert first['input']['skill_catalog'] == delivery['catalog']
    second_run = deepcopy(run)
    second_run['id'] = 'another-experiment'
    assert finish(coordinator, second_run) == 'completed'
    second = coordinator._get(OWNER, second_run['id'])
    assert first['scope'] != second['scope']
    assert first['input']['resource_constraints']['resource_id'] != second['input']['resource_constraints']['resource_id']
    assert gateway.submissions == executor.calls == 2


@pytest.mark.parametrize('changes', [dict(frame_count=61), dict(frame_count=240), dict(seed=-1),
    dict(width=1280, height=720, frame_count=48), dict(global_start_frame=72), dict(image='untrusted'),
    dict(kind='manim-js'), dict(source='not accepted')])
def test_browser_options_cannot_expand_resource_bounds_or_select_code_or_image(changes):
    with pytest.raises(ServiceError): authoring_request({'kind': 'manim'} | changes, 3)


def test_method_renderer_will_not_admit_generated_names_without_trusted_resolver(tmp_path):
    coordinator, _, _, run = setup(tmp_path)
    finish(coordinator, run)
    descriptor = coordinator.context(OWNER, run['id'])
    context = document(duration=3)
    context['assets'] = descriptor['assets_sha256']
    context['constraints']['generated_resources'] = [descriptor]
    renderer = MethodAnimationRenderer(tmp_path/'method-renders', METHOD_IMAGE, hyperframes=True)
    with pytest.raises(ServiceError, match='resource_not_admitted'):
        renderer._snapshot('untrusted-resource', SOURCE, context, [])


def test_actual_method_service_waits_for_prelude_then_delivers_resources_to_unchanged_author(tmp_path):
    from test_animation_method_service import Gateway, Renderer, start, finish as finish_service
    from video_workbench.animation_method_service import AnimationMethodService
    coordinator, gateway, executor, _ = setup(tmp_path)
    class ProducerGateway(Gateway):
        def collect(self, *args):
            result = super().collect(*args)
            for method in result['methods']:
                method['runtime_requirements']['resource_authoring'] = deepcopy(PRODUCER_REQUIREMENT)
            return result
    author, reviewer = ProducerGateway(), Gateway(True)
    service = AnimationMethodService(tmp_path/'lab', author_gateway=author, review_gateway=reviewer,
        renderer=Renderer(tmp_path/'renders'), manim_coordinator=coordinator)
    queued = start(service, resource_authoring=dict(kind='manim', width=320, height=180, frame_count=3))
    assert queued['resource_authoring']['status'] == 'pending'
    for _ in range(15):
        service.advance('developer', queued['id'])
        if service.resource_stage.view('developer', queued['id'])['status'] == 'completed': break
    assert service.resource_stage.view('developer', queued['id'])['status'] == 'completed'
    assert author.submissions == reviewer.submissions == []
    completed = finish_service(service, queued['id'])
    assert completed['state'] == 'completed'
    assert gateway.submissions == executor.calls == 1
    assert len(author.submissions) == 2
    for operation in service.commissions.get('developer', queued['id'])['operations']:
        if operation['stage'] != 'creator': continue
        context = operation['input']
        descriptor = context['constraints']['generated_resources'][0]
        assert set(descriptor['assets_sha256']) <= set(context['assets'])
        assert 'prepareManimMethodFrames' in context['constraints']['generated_resource_usage']
    assert completed['resource_authoring']['status'] == 'completed'
    first = service.resource_stage.context('developer', queued['id'])
    # Continuing a Manim method repeats the producer for this new content/run;
    # it cannot silently reuse the earlier task's immutable PNG resources.
    continued = start(service, base_commission_id=queued['id'], feedback='Use a different composition')
    again = finish_service(service, continued['id'])
    second = service.resource_stage.context('developer', continued['id'])
    assert again['state'] == 'completed' and second['scope'] != first['scope']
    assert second['manifest']['resource_id'] != first['manifest']['resource_id']
    assert gateway.submissions == executor.calls == 2


def test_method_service_resource_prelude_cancel_does_not_schedule_main_author(tmp_path):
    from test_animation_method_service import Gateway, Renderer, start
    from video_workbench.animation_method_service import AnimationMethodService
    coordinator, gateway, executor, _ = setup(tmp_path)
    author = Gateway()
    service = AnimationMethodService(tmp_path/'lab', author_gateway=author, review_gateway=Gateway(True),
        renderer=Renderer(tmp_path/'renders'), manim_coordinator=coordinator)
    queued = start(service, resource_authoring=dict(kind='manim', frame_count=3))
    service.advance('developer', queued['id'])
    service.cancel('developer', queued['id'], {})
    service.advance('developer', queued['id'])
    assert service.get('developer', queued['id'])['state'] == 'cancelled'
    assert gateway.submissions == executor.calls == 0
    assert author.submissions == []


def test_original_method_and_skill_must_both_declare_exact_manim_producer():
    method = dict(schema='video.animation-method.v2', runtime_requirements={'engine':'html-svg'},
        skill_package={'runtime_requirements': {'engines':['html-svg']}})
    before = deepcopy(method)
    with pytest.raises(ServiceError, match='producer_declaration'): require_method_producer([method])
    assert method == before
    method['runtime_requirements']['resource_authoring'] = deepcopy(PRODUCER_REQUIREMENT)
    with pytest.raises(ServiceError, match='skill_producer'): require_method_producer([method])
    method['skill_package']['runtime_requirements']['resource_authoring'] = deepcopy(PRODUCER_REQUIREMENT)
    original = deepcopy(method)
    require_method_producer([method])
    assert method == original
    require_method_producer([])  # A technical fixture does not invent/publish methods.


def test_lab_may_regenerate_manim_resources_but_formal_skill_freezes_still_reject_producer():
    from video_workbench.animation_skill_compatibility import (
        require_method_compatible, method_compatible, freeze_compatibility)
    method = dict(runtime_requirements={'engine':'html-svg', 'resource_authoring':deepcopy(PRODUCER_REQUIREMENT)},
        skill_package={'runtime_requirements': {'engines':['html-svg'], 'resource_authoring':deepcopy(PRODUCER_REQUIREMENT)}})
    original = deepcopy(method)
    with pytest.raises(ServiceError, match='dependency_not_admitted'):
        require_method_compatible(method, assets={}, authoring_libraries={})
    require_method_compatible(method, assets={}, authoring_libraries={}, resource_authoring=PRODUCER_REQUIREMENT)
    assert method == original
    assert not method_compatible(method, freeze_compatibility({'assets':{}}))
    wrong = deepcopy(PRODUCER_REQUIREMENT)
    wrong['manim']['version'] = '0.1.0'
    with pytest.raises(ServiceError):
        require_method_compatible(method, assets={}, authoring_libraries={}, resource_authoring=wrong)
