"""Requirement-library integration and isolated SVG component contracts.

The public developer library contains only the rebuilt cross-domain demands.
Legacy compiler recipes remain internal component fixtures; no old artifact
identity or historical annotation is a public-library compatibility contract.
"""
from copy import deepcopy
import http.client
import json
import shutil
import threading

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_lab import AnimationLab, LabHTTPServer, load_assets
from video_workbench.animation_lab_capture import capture_plan
from video_workbench.animation_lab_effects import COMPILER_VERSION, PLAN_SCHEMA
from video_workbench.animation_lab_effects_review import verify_effect_observations
from video_workbench.animation_lab_evaluate import load_run, prepare, revision_decision
from video_workbench.animation_lab_evaluation_view import revision_task
from video_workbench.animation_lab_samples import catalog, compile_sample
from video_workbench.animation_lab_domain_cases import DOMAIN_CASES
from video_workbench.animation_lab_scenarios import SCENARIOS
from video_workbench.motion import sha256


ASSETS = {'gsap.min.js': b'/*! GSAP 3.14.2 synthetic integration resource, not executable */',
          'NotoSansCJK-Regular.ttc': b'ttcf-synthetic-integration-font'}
RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='synthetic_contract_fixture', node='synthetic', browser='synthetic',
    ffmpeg='synthetic', gsap='3.14.2', gsap_sha256=sha256(ASSETS['gsap.min.js']),
    font_sha256=sha256(ASSETS['NotoSansCJK-Regular.ttc']),
    npm_lock_sha256='a'*64, dpkg_sha256='b'*64, image_digest='sha256:'+'c'*64)
EFFECTS = {
    'condition-branch': 'process', 'parallel-wait': 'process', 'code-execution': 'process',
    'architecture-expand': 'diagram', 'focus-shift': 'diagram', 'data-compare': 'diagram',
    'local-replace': 'diagram', 'svg-morph': 'diagram',
}



class Client:
    def __init__(self, lab):
        self.lab = lab
        self.server = LabHTTPServer(lab, 0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.host = '127.0.0.1:'+str(self.server.server_port)

    def request(self, method, path, value=None):
        headers, body = {}, None
        if value is not None:
            headers = {'Origin': 'http://'+self.host, 'Content-Type': 'application/json',
                       'X-Animation-Lab-Token': self.lab.csrf_token}
            body = json.dumps(value).encode()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            raw, status, content_type = response.read(), response.status, response.getheader('Content-Type', '')
        finally:
            connection.close()
        return status, json.loads(raw) if content_type.startswith('application/json') else raw

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def clients(tmp_path):
    created = []
    def create():
        client = Client(AnimationLab(tmp_path/'history', ASSETS))
        created.append(client)
        return client
    yield create
    for client in reversed(created):
        client.close()


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    base = tmp_path_factory.mktemp('effect-frozen-contracts')
    result = {}
    for case_id in SCENARIOS:
        root = base/case_id
        run = prepare(root, deepcopy(RUNTIME), case_id=case_id)
        loaded, bundles = load_run(root)
        assert loaded == run
        result[case_id] = dict(root=root, run=run, bundles=bundles)
    return result


@pytest.fixture(scope='module')
def checkpoint_catalog(tmp_path_factory):
    lab = AnimationLab(tmp_path_factory.mktemp('effect-checkpoint-catalog'), ASSETS)
    return {row['id']: row for row in lab.get_catalog()['cases']}


def test_public_catalog_contains_only_rebuilt_cross_domain_requirements():
    rows = {row['id']: row for row in catalog()}
    assert set(rows) == set(DOMAIN_CASES) | set(SCENARIOS)
    assert len(rows) == 27
    assert not set(EFFECTS) & set(rows)
    assert all(row['is_matrix_case'] and row['requirement']['goal'] and row['narrative'] for row in rows.values())
    assert len({row['input_sha256'] for row in rows.values()}) == 27


@pytest.mark.parametrize('case_id', sorted(EFFECTS))
def test_effect_pair_has_same_semantic_inputs_and_clock_but_distinct_actual_projects(case_id):
    pair = [compile_sample(case_id, variant) for variant in ('baseline', 'candidate')]
    assert pair[0]['inputs'] == pair[1]['inputs']
    assert pair[0]['input_sha256'] == pair[1]['input_sha256']
    assert pair[0]['artifact_sha256'] != pair[1]['artifact_sha256']
    assert pair[0]['clips'][0]['files']['audio.wav'] == pair[1]['clips'][0]['files']['audio.wav']
    assert pair[0]['duration_seconds'] == pair[1]['duration_seconds'] == 18
    for bundle in pair:
        assert bundle['compiler'] == COMPILER_VERSION
        assert bundle['agent_calls'] == bundle['tts_calls'] == 0
        assert bundle['production_write'] is False
        assert bundle['audio_kind'] == 'synthetic_silence_not_narration'
        clip = bundle['clips'][0]
        manifest = json.loads(clip['files']['manifest.json'])
        assert manifest['engine'] == clip['timeline']['engine'] == 'svg-effects'
        assert manifest['required_assets'] == ['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc']
        assert not manifest.get('engine_assets')
        assert all(sha256(clip['files'][name]) == digest for name, digest in manifest['files'].items())
        assert bundle['inputs']['inputs']['plan']['schema'] == PLAN_SCHEMA
        assert bundle['inputs']['inputs']['plan']['effect'] == case_id
        page = clip['files']['index.html'].replace(b'http://www.w3.org/2000/svg', b'')
        assert b'http://' not in page and b'https://' not in page


@pytest.mark.parametrize('case_id', sorted(SCENARIOS))
def test_preview_checkpoints_cover_every_declared_effect_action_and_actual_boundary_times(checkpoint_catalog, case_id):
    case = checkpoint_catalog[case_id]
    for variant in case['variants']:
        bundle = compile_sample(case_id, variant['id'])
        clip = bundle['clips'][0]
        points = variant['observation_times']
        duration = variant['duration_seconds']
        assert all(0 <= row['time'] <= duration-1/24 for row in points)
        for action in clip['timeline']['actions']:
            prefix = f"{clip['id']} · {action['ordinal']+1} · {action['target']} · {action['kind']} · "
            actual = {row['label'][len(prefix):]: row['time'] for row in points if row['label'].startswith(prefix)}
            assert actual == pytest.approx({
                '动作前': max(0, action['start']-1/24),
                '途中': min(duration-1/24, action['start']+action['duration']/2),
                '完成': min(duration-1/24, action['start']+action['duration']+1/24),
            }, abs=1e-9)


def test_all_new_cases_are_available_without_unrelated_three_resources(tmp_path):
    assets = tmp_path/'assets'
    assets.mkdir()
    for name, raw in ASSETS.items():
        (assets/name).write_bytes(raw)
    lab = AnimationLab(tmp_path/'no-webgl', load_assets(assets))
    for case_id in SCENARIOS:
        assert all(row['available'] for row in lab.cases[case_id]['variants'])
    assert all(not row['available'] for case in lab.cases.values() if case['module'] == 'three' for row in case['variants'])


def test_new_requirement_feedback_is_isolated_and_persists_after_restart(clients):
    original = clients()
    cases = {row['id']: row for row in original.request('GET', '/api/catalog')[1]['cases']}
    representatives = ['domain-diagram-water', *[next(key for key, row in SCENARIOS.items() if row['module'] == module) for module in ('charts', 'algorithms', 'interface')]]
    assert len({cases[key]['identity'] for key in representatives}) == 4
    saved = {}
    for case_id in representatives:
        case = cases[case_id]
        body = dict(case_id=case_id, case_identity=case['identity'], variant_id='candidate',
            kind='issue', start_seconds=6, end_seconds=7, region=dict(x=.2,y=.3,width=.25,height=.2),
            tags=['motion'], severity='minor', preference=None,
            comment='Synthetic integration record, not user aesthetic approval: '+case_id)
        status, response = original.request('POST', '/api/annotations', body)
        assert status == 201
        saved[case_id] = response['annotation']['id']
    restarted = clients()
    for case_id, annotation_id in saved.items():
        status, response = restarted.request('GET', '/api/annotations?case_id='+case_id)
        assert status == 200 and [row['id'] for row in response['annotations']] == [annotation_id]
        assert response['annotations'][0]['case_identity'] == cases[case_id]['identity']
    status, raw = restarted.request('GET', '/api/export')
    assert status == 200
    records = [json.loads(line) for line in raw.decode().splitlines()]
    annotations = {row['annotation']['case_id']: row['annotation'] for row in records if row['record_type'] == 'annotation'}
    assert {key: value['id'] for key,value in annotations.items()} == saved
    snapshots = {row['snapshot']['case_id']: row['snapshot'] for row in records if row['record_type'] == 'case_snapshot'}
    assert all(snapshots[key]['identity'] == cases[key]['identity'] for key in saved)
    stale = dict(body, case_id=representatives[0], case_identity=cases[representatives[1]]['identity'])
    assert restarted.request('POST', '/api/annotations', stale)[0] == 409
    assert len(restarted.request('GET', '/api/annotations?case_id='+representatives[0])[1]['annotations']) == 1


@pytest.mark.parametrize('case_id', sorted(SCENARIOS))
def test_frozen_effect_run_uses_original_fixed_pair_and_binds_actual_capture_dependencies(runs, case_id):
    fixture = runs[case_id]
    root, run, bundles = fixture['root'], fixture['run'], fixture['bundles']
    assert run['module'] == SCENARIOS[case_id]['module']
    assert run['limits']['local_candidates'] == 0 and run['limits']['captures'] == 2
    assert run['production_adopted'] is False and run['agent_calls'] == run['tts_calls'] == 0
    assert {bundle['variant_id'] for bundle in bundles} == {'baseline', 'candidate'}
    assert not (root/'model-attempt.json').exists() and not (root/'technical.json').exists()
    for bundle in bundles:
        expected = compile_sample(case_id, bundle['variant_id'])
        assert bundle['artifact_sha256'] == expected['artifact_sha256']
        assert bundle['clips'][0]['files'] == expected['clips'][0]['files']
        directory = root/'inputs'/bundle['variant_id']/'scene-1'
        request = json.loads((directory/'capture-request.json').read_bytes())
        assert request['engine_observer_sha256'] == sha256((root/'overlay'/'engine_observe.mjs').read_bytes())
        assert request['engine_assets'] == {}
        assert all(sha256((directory/name).read_bytes()) == digest for name,digest in request['files'].items())
        image = 'synthetic.invalid/renderer:latest@'+RUNTIME['image_digest']
        plan = capture_plan(directory, RUNTIME, image, root/'overlay')
        assert plan['network'] == 'none' and plan['pull'] == 'never'
        assert plan['automatic_retry'] is False


@pytest.mark.parametrize('case_id', sorted(SCENARIOS))
def test_structured_requirement_families_do_not_admit_unrelated_old_layouts(tmp_path, case_id):
    for template in ('flow', 'focus'):
        target = tmp_path/template
        with pytest.raises(ValueError, match='animation_evaluation_layout_not_supported'):
            prepare(target, deepcopy(RUNTIME), case_id=case_id, template=template)
        assert not target.exists()


@pytest.mark.parametrize('case_id', sorted(SCENARIOS))
def test_effect_revision_task_requires_developer_design_and_exposes_no_old_layout_editor(runs, case_id):
    fixture = runs[case_id]
    # Minimal explicit synthetic triage finding, never a claimed model output.
    finding = dict(id='synthetic-finding', source='synthetic_contract_fixture',
                   description='Synthetic source-binding test, not a real quality finding')
    snapshot = dict(identity='d'*64, run_identity=fixture['run']['identity'], evidence_identity='e'*64,
        module=SCENARIOS[case_id]['module'], case_id=case_id, semantic_input_sha256=fixture['run']['semantic_input_sha256'],
        findings=[finding])
    decision = dict(finding_id=finding['id'], report_identity=snapshot['identity'],
                    decision='needs_fix', effective=True)
    task = revision_task(dict(snapshot=snapshot, run=fixture['run'], bundles=fixture['bundles']), [decision])
    assert task['editable_scope'] == dict(kind='developer_design_required', fields=[])
    assert task['status'] == 'prepared_not_executed'
    assert task['agent_calls'] == task['model_calls'] == 0
    assert task['source_run_identity'] == fixture['run']['identity']
    review = dict(opinion=dict(suggestions=[dict(kind='no_change')]))
    result = revision_decision(fixture['run'], review)
    assert result['suggestions'] == [] and result['new_automatic_runs'] == 0


@pytest.mark.parametrize('target', ['page', 'engine-observer'])
def test_capture_rejects_changed_effect_source_or_observer_without_dispatch(runs, tmp_path, target):
    root = tmp_path/'frozen-copy'
    shutil.copytree(runs[next(iter(SCENARIOS))]['root'], root)
    directory = root/'inputs'/'candidate'/'scene-1'
    if target == 'page':
        (directory/'index.html').write_bytes(b'changed after freezing')
    else:
        (root/'overlay'/'engine_observe.mjs').write_bytes(b'changed after freezing')
    image = 'synthetic.invalid/renderer:latest@'+RUNTIME['image_digest']
    with pytest.raises(ValueError, match='animation_lab_executor_'):
        capture_plan(directory, RUNTIME, image, root/'overlay')
    assert not (directory/'capture-start.json').exists()
    assert not (directory/'capture-work').exists()


def _synthetic_effect_observations(plan, timeline):
    """Descriptor-only reviewer fixture; does not assert actual SVG execution."""
    paths = {row['target'] for row in timeline['actions'] if row['kind'] in {'draw', 'morph'}}
    def sample(time):
        objects = []
        for identity in timeline['object_ids']:
            attributes = {'data-state': str(time)}
            if identity in paths:
                attributes.update(d='M100 200 L'+str(180+time)+' 250')
                attributes['stroke-dashoffset'] = str(100-time)
            objects.append(dict(id=identity, tag='path' if identity in paths else 'rect',
                bounds=dict(x=100+time, y=200, width=80, height=50), opacity=1, text='',
                attributes=attributes))
        state = dict(engine='svg-effects', context='svg', deterministic=True,
            effect=plan['effect'], time=time, width=1280, height=720, objects=objects,
            text_boxes=[dict(text=label, opacity=1, font_size=18,
                bounds=dict(x=100, y=100+index*30, width=300, height=24))
                for index,label in enumerate(['Synthetic fixture', 'No actual render'])],
            semantic=dict(scope='synthetic_contract_fixture'))
        return dict(time=time, actual_time=time, frame_sha256=sha256(dict(synthetic_time=time)), engine_state=state)
    windows = []
    for action in timeline['actions']:
        windows.append(dict(ordinal=action['ordinal'], before=sample(max(0, action['start']-1/24)),
            middle=sample(action['start']+action['duration']/2),
            after=sample(min(18-1/24, action['start']+action['duration']+1/24))))
    first, last = sample(0), sample(18-1/24)
    return dict(schema='video.animation-lab-engine-observations.v1', timeline_sha256=sha256(timeline),
        engine='svg-effects', seek_protocol='hyperframes-core-renderSeek-exact', render_fps=24,
        rendering='svg_dom', page_errors=[], windows=windows, initial=first,
        repeated_initial=deepcopy(first), last=last,
        reverse_seek=dict(repeat_state_equal=True, first_sha256=first['frame_sha256'],
            last_sha256=last['frame_sha256'], repeated_first_sha256=first['frame_sha256']))


@pytest.mark.parametrize('case_id', sorted(EFFECTS))
def test_compiled_effect_reverse_contract_rejects_changed_state_pixels_or_declared_reset(case_id):
    bundle = compile_sample(case_id, 'candidate')
    plan = bundle['inputs']['inputs']['plan']
    timeline = bundle['clips'][0]['timeline']
    fixture = _synthetic_effect_observations(plan, timeline)
    assert verify_effect_observations(plan, timeline, fixture)['technical_status'] == 'passed'
    for change in ('state', 'frame', 'flag'):
        altered = deepcopy(fixture)
        if change == 'state':
            altered['repeated_initial']['engine_state']['objects'][0]['bounds']['x'] += 1
        elif change == 'frame':
            altered['repeated_initial']['frame_sha256'] = 'f'*64
            altered['reverse_seek']['repeated_first_sha256'] = 'f'*64
        else:
            altered['reverse_seek']['repeat_state_equal'] = False
        with pytest.raises(ServiceError, match='animation_lab_effect_reverse_seek_mismatch'):
            verify_effect_observations(plan, timeline, altered)
