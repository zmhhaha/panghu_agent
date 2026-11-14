"""Run-level resource preparation before any complete-film module authoring.

The shared graph owns scheduling and attempt custody. Existing audio work keeps
its original service; measured recordings are adopted as finished resources.
Only the private Manim adapter can request a source recipe or Cairo execution.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from agent_service.contracts import ServiceError, digest
from .audio_store import narration_binding
from .film_authoring_resources import expected_scope
from .manim_method_coordinator import authoring_request
from .manim_method_resources import method_resource
from .manim_resource import MAX_DECODED_BYTES
from .resource_plan import validate_request, verify_producer
from .speech import inspect_wav


def require(value, code='production_resource_plan_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def audio_node_id(scene_id):
    return 'audio_' + scene_id


def production_plan(value, frozen, *, deadline_at):
    """Add real recording dependencies to the confirmed creative requirements."""
    require(value is None or type(value) is dict and set(value) <= {'nodes', 'budget'},
        'production_resource_plan_invalid')
    value = {} if value is None else deepcopy(value)
    nodes = value.get('nodes', [])
    require(type(nodes) is list and len(nodes) <= 32)
    scenes = {scene['id']: scene for scene in frozen['scenes']}
    reserved = {audio_node_id(scene_id) for scene_id in scenes}
    for node in nodes:
        require(type(node) is dict and node.get('id') not in reserved)
        placement = node.get('placement', {})
        require(type(placement) is dict and type(placement.get('scene_id')) is str
            and placement['scene_id'] in scenes and type(placement.get('start_frame', 0)) is int
            and placement.get('start_frame', 0) >= 0, 'production_resource_placement_invalid')
        node.setdefault('dependencies', [])
        require(type(node['dependencies']) is list, 'production_resource_plan_invalid')
        dependency = audio_node_id(placement['scene_id'])
        if dependency not in node['dependencies']:
            node['dependencies'].append(dependency)
        if node.get('producer') == 'manim':
            require(node.get('kind') == 'animation' and type(node.get('inputs')) is dict,
                'production_resource_manim_options_invalid')
            options = authoring_request(node['inputs'].get('resource_authoring'), 60)
            require(options['global_start_frame'] == 0, 'production_resource_local_clock_required')
            node['inputs']['resource_authoring'] = options
            timing = dict(fps=24, frame_count=options['frame_count'])
            require(not node.get('local_timing') or node['local_timing'] == timing,
                'production_resource_local_clock_required')
            node['local_timing'] = timing
            guidance = node['inputs'].get('guidance', '')
            require(type(guidance) is str and len(guidance) <= 8000
                and not any(ord(c) < 32 and c not in '\t\r\n' or 0xD800 <= ord(c) <= 0xDFFF for c in guidance),
                'production_resource_guidance_invalid')
            text_budget = sum(len(scenes[key[6:]]['audio_input']['narration'])
                + len(scenes[key[6:]]['audio_input']['spoken_text'])
                for key in node['dependencies'] if type(key) is str and key in reserved)
            require(len(guidance) + text_budget + 1500 * len(node['dependencies']) <= 15000,
                'production_resource_guidance_too_large', 413)
    require(all(sum(node['placement']['scene_id'] == scene_id for node in nodes) <= 16
        for scene_id in scenes), 'production_resource_reference_limit', 413)
    for scene_id, scene in scenes.items():
        nodes.append(dict(id=audio_node_id(scene_id), kind='narration', producer='verified-recording',
            inputs=dict(scene_id=scene_id, input_sha256=narration_binding(scene['audio_input'])),
            dependencies=[], objective='Use the complete original measured narration recording.',
            required_capabilities=['measured-recording'], key_effects=[], local_timing={},
            placement=dict(scene_id=scene_id, start_frame=0)))
    budget = value.get('budget', dict(max_attempts=max(1, len(nodes) - len(scenes)), max_attempts_per_node=1))
    result = validate_request(dict(nodes=nodes, budget=budget, deadline_at=deadline_at))
    for node in result['nodes']:
        if node['producer'] == 'manim':
            require(len(json.dumps(node['key_effects'], ensure_ascii=False))
                + len(node['inputs'].get('guidance', ''))
                + sum(len(scenes[key[6:]]['audio_input']['narration'])
                    + len(scenes[key[6:]]['audio_input']['spoken_text']) + 1500
                    for key in node['dependencies'] if key in reserved) <= 15000,
                'production_resource_guidance_too_large', 413)
    return result


class ManimPlanProducer:
    """Adapter from common resource tasks to the private stopped-Cairo service."""
    def __init__(self, film_creation, owner):
        self.film_creation, self.owner = film_creation, owner
        self.client = getattr(film_creation, 'resource_client', None)

    def capabilities(self):
        require(self.client is not None, 'film_resource_not_admitted', 503)
        self.film_creation.preflight_resources(self.owner)
        return dict(admitted=True, kind='animation', revision='manim-cairo-1',
            capabilities=['manim', 'transparent-png-sequence', 'local-frame-clock'], key_effects=[])

    @staticmethod
    def request(task):
        options = authoring_request(task['inputs']['resource_authoring'], 60)
        require(options['global_start_frame'] == 0
            and task['local_timing'] == dict(fps=24, frame_count=options['frame_count']),
            'production_resource_local_clock_required')
        recordings = []
        for node_id, output in task.get('dependency_outputs', {}).items():
            if output.get('kind') == 'narration' and output.get('producer') == 'verified-recording':
                recordings.append(dict(node_id=node_id, content=output['content'],
                    local_timing=output['local_timing']))
        guidance = task['inputs'].get('guidance', '')
        if recordings:
            guidance += '\nAPP-VERIFIED-NARRATION-DEPENDENCIES\n' + json.dumps(dict(
                timing_scope='complete_recording_measurements_not_word_level_alignment',
                recordings=recordings), ensure_ascii=False, separators=(',', ':'))
            guidance += '\nEND-APP-VERIFIED-NARRATION-DEPENDENCIES\n'
        if task['key_effects']:
            guidance += '\nCreative requirements to implement; attainment remains subject to expression review:\n'
            guidance += json.dumps(task['key_effects'], ensure_ascii=False)
        require(len(guidance) <= 16000, 'production_resource_guidance_too_large', 413)
        return dict(schema='video.prepared-manim-resource-request.v1', operation_id=task['task_id'],
            project_id=task['project_id'], node_id=task['node_id'], goal=task['objective'],
            guidance=guidance, duration_seconds=options['frame_count'] / 24,
            resource_authoring=options, deadline_at=task['deadline_at'])

    def _receipt(self, task, view):
        request = self.request(task)
        require(view.get('operation_id') == task['task_id'] and view.get('project_id') == task['project_id']
            and view.get('node_id') == task['node_id'] and view.get('request_sha256') == digest(request),
            'production_resource_producer_binding_mismatch', 502)
        state = view['status'] if view['status'] in {'queued', 'completed', 'failed', 'cancelled'} else 'running'
        result = dict(task_id=task['task_id'], request_sha256=task['request_sha256'],
            status=state, stop_proven=view['stop_proven'])
        if view['status'] == 'completed' and view['stop_proven']:
            result['output'] = dict(content=dict(operation_id=task['task_id'], descriptor=view['descriptor'],
                resource_image=view['resource_image'],
                expression_acceptance='not_assessed',
                key_effects_scope='preserved_creative_requirements_not_achievement'),
                local_timing=deepcopy(task['local_timing']),
                capabilities=['manim', 'transparent-png-sequence', 'local-frame-clock'],
                key_effects=deepcopy(task['key_effects']))
        if view.get('error'):
            result['error'] = view['error']
        return result

    def submit(self, task):
        return self._receipt(task, self.client.start(self.owner, self.request(task)))

    def lookup(self, task):
        try:
            view = self.client.get(self.owner, task['task_id'])
        except ServiceError as error:
            if error.status != 404:
                raise
            return dict(task_id=task['task_id'], request_sha256=task['request_sha256'],
                status='submission_unknown', stop_proven=False, error='resource_reservation_unconfirmed')
        return self._receipt(task, view)

    def cancel(self, task):
        return self._receipt(task, self.client.cancel_reservation(self.owner, self.request(task)))

    def validate_output(self, task, output):
        content = output['content']
        require(content['operation_id'] == task['task_id'], 'production_resource_producer_binding_mismatch', 409)
        envelope = self.client.envelope(self.owner, task['task_id'])
        descriptor, _ = method_resource(envelope, expected_sha256=envelope['sha256'],
            expected_scope=expected_scope(self.owner, task['task_id']),
            image_digest=content['resource_image'].rsplit('@', 1)[1])
        require(descriptor == content['descriptor'] and envelope['request']['global_start_frame'] == 0
            and all(envelope['request'][key] == value for key, value in task['inputs']['resource_authoring'].items()
                if key != 'kind'), 'production_resource_output_changed', 409)
        context = self.film_creation.configuration.get('resource_authoring', {})
        require(context.get('resource_image') == content['resource_image'], 'film_resource_image_changed', 409)
        return True


class ProductionResourceStage:
    def __init__(self, business_store, film_creation, *, ledger=None, producers=None):
        from .resource_plan_store import ResourcePlanStore
        self.business, self.film_creation = business_store, film_creation
        self.ledger = ledger or ResourcePlanStore(Path(business_store.path).with_suffix('.resources.sqlite3'))
        self.producers = {} if producers is None else dict(producers)

    def _producers(self, owner):
        return dict({'manim': ManimPlanProducer(self.film_creation, owner)}, **self.producers)

    def preflight(self, owner, request):
        producers = self._producers(owner)
        manim = []
        for node in request['nodes']:
            if node['producer'] == 'verified-recording':
                continue
            require(node['producer'] in producers, 'resource_plan_producer_unavailable', 503)
            require(node['producer'] == 'manim', 'production_resource_consumer_unavailable', 503)
            verify_producer(producers[node['producer']].capabilities(), node)
            if node['producer'] == 'manim':
                manim.append(node['inputs']['resource_authoring'])
        caps = self.film_creation.configuration['runtime_capabilities']
        for scene_id in {node['placement']['scene_id'] for node in request['nodes']}:
            selected = [node['inputs']['resource_authoring'] for node in request['nodes']
                if node['producer'] == 'manim' and node['placement']['scene_id'] == scene_id]
            require(sum(row['frame_count'] + 1 for row in selected) + (1 if selected else 0) <= 64,
                'production_run_resource_module_budget_exceeded', 413)
        require(sum(row['width'] * row['height'] * row['frame_count'] * 4 for row in manim) <= MAX_DECODED_BYTES
            and sum(row['frame_count'] + 1 for row in manim) + (2 if manim else 0) + len(caps['assets']) <= 128,
            'production_run_resource_budget_exceeded', 413)

    def ensure(self, run):
        registration = run['resource_plan_registration']
        if registration['status'] == 'registered':
            try:
                view = self.ledger.get(run['owner'], registration['operation_id'])
            except ServiceError as error:
                if error.status != 404:
                    raise
                raise ServiceError('production_resource_ledger_missing', 503) from None
        else:
            view = self.ledger.create(run['owner'], run['project_id'], registration['operation_id'], run['resource_plan'])
        require(view['project_id'] == run['project_id'] and view['operation_id'] == run['id']
            and view['request_sha256'] == registration['request_sha256'],
            'production_resource_plan_binding_changed', 409)
        registration['status'] = 'registered'
        return view

    def validate_timing(self, run):
        rows = {row['scene_id']: row for row in run['film']['scenes']}
        for node in run['resource_plan']['nodes']:
            if node['producer'] == 'verified-recording':
                continue
            placement = node['placement']
            row = rows[placement['scene_id']]
            frames = node['local_timing'].get('frame_count')
            require(type(frames) is int and frames > 0
                and placement.get('start_frame', 0) + frames <= round((row['end_seconds'] - row['start_seconds']) * 24),
                'production_resource_clock_outside_scene', 409)

    def _workflow(self, owner):
        from .resource_plan_workflow import ResourcePlanWorkflow
        return ResourcePlanWorkflow(self.ledger, producers=self._producers(owner))

    def advance(self, run, *, admit=True):
        require(run['resource_plan_registration']['status'] == 'registered', 'production_resource_plan_not_registered', 409)
        self.ledger.get(run['owner'], run['id'])
        for item, frozen in zip(run['items'], run['frozen']['scenes']):
            if not item['audio_version_id']:
                continue
            audio = self.business.audio_version(run['owner'], item['audio_version_id'])
            info = inspect_wav(audio['data'])
            require(audio['project_id'] == run['project_id'] and audio['scene_id'] == item['scene_id']
                and audio['input_sha256'] == narration_binding(frozen['audio_input'])
                and audio['narration'] == frozen['audio_input']['narration']
                and audio['settings'] == frozen['audio_input']['settings']
                and audio['sha256'] == item['audio_sha256'] == hashlib.sha256(audio['data']).hexdigest()
                and info.duration_seconds == item['audio_seconds'], 'production_run_audio_binding_mismatch', 409)
            output = dict(content=dict(scene_id=item['scene_id'], audio_version_id=audio['id'],
                audio_sha256=audio['sha256'], input_sha256=audio['input_sha256'],
                narration=frozen['audio_input']['narration'],
                spoken_text=frozen['audio_input']['spoken_text'],
                text_scope='bound_narration_input_not_asr_transcript'),
                local_timing=dict(duration_seconds=info.duration_seconds, sample_rate=info.sample_rate,
                    sample_count=info.frame_count), capabilities=['measured-recording'], key_effects=[])
            self.ledger.adopt(run['owner'], run['id'], audio_node_id(item['scene_id']), output,
                'recording-' + audio['id'])
        if all(item['audio_version_id'] for item in run['items']):
            return self._workflow(run['owner']).advance(run['owner'], run['id'], admit=admit)
        return self.ledger.get(run['owner'], run['id'])

    def cancel(self, run):
        require(run['resource_plan_registration']['status'] == 'registered', 'production_resource_plan_not_registered', 409)
        self.ledger.cancel(run['owner'], run['id'])
        return self._workflow(run['owner']).advance(run['owner'], run['id'])

    def module_references(self, run, view):
        from .resource_placement import freeze_manim_placement
        require(view['status'] == 'completed' and view['stop_proven'], 'production_resources_not_ready', 409)
        result = {item['scene_id']: [] for item in run['items']}
        rows = {row['scene_id']: row for row in run['film']['scenes']}
        for node in view['nodes'].values():
            definition, output = node['definition'], node['output']
            if definition['producer'] == 'verified-recording':
                continue
            require(definition['producer'] == 'manim', 'production_resource_consumer_unavailable', 503)
            content = output['content']
            source = self._producers(run['owner'])['manim']
            source.validate_output(node['task'], {key: output[key]
                for key in ('content', 'local_timing', 'capabilities', 'key_effects')})
            envelope = source.client.envelope(run['owner'], content['operation_id'])
            self.film_creation.register_prepared_resource(run['owner'], run['project_id'], output, envelope)
            placement = definition['placement']
            row = rows[placement['scene_id']]
            first = round(row['start_seconds'] * 24) + placement.get('start_frame', 0)
            frozen = freeze_manim_placement(content['descriptor'], start_frame=first,
                project_frame_count=round(run['film']['total_seconds'] * 24))
            require(first + frozen['frame_count'] <= round(row['end_seconds'] * 24),
                'production_resource_clock_outside_scene', 409)
            result[placement['scene_id']].append(dict(resource_version_id=output['resource_version_id'], placement=frozen))
        return result
