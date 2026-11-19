"""Import real encoded media without inventing admitted renders or paid calls.

The offline host reports below are synthetic custody fixtures. FFprobe and
sample extraction operate on an actual encoded 24fps MP4; no artistic or
production-backend acceptance is inferred from these tests.
"""
from copy import deepcopy
import hashlib
import json
import shutil
import subprocess
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import animation_method_renderer as shared
from video_workbench.video_learning_renderer import VideoLearningRenderer
from video_workbench.video_learning_service import VideoLearningService
from test_animation_method_renderer import IMAGE
from test_video_learning_service import Gateway, Renderer, real_mp4, start, finish


FILES = ('source.json', 'input.json', 'original-binding.json', 'render-request.json',
         'render-receipt.json', 'render-plan.json', 'render-outcome.json',
         'output.mp4', 'host.mjs', 'original-host.mjs')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.write_bytes(canonical_bytes(value))


def read_json(path):
    return json.loads(path.read_bytes())


def refresh_manifest(bundle):
    manifest = read_json(bundle / 'manifest.json')
    manifest['files'] = {name: sha((bundle / name).read_bytes()) for name in FILES}
    write_json(bundle / 'manifest.json', manifest)


def build_bundle(bundle, service, project, raw):
    bundle.mkdir()
    run = service.store.read('experiments', 'developer', project['latest_experiment_id'])
    candidate = run['candidates'][0]
    operation = next(service.store.read('operations', 'developer', identity)
        for identity in run['operation_ids']
        if service.store.read('operations', 'developer', identity)['stage'] == 'render')
    runtime = shared.MethodAnimationRenderer(bundle.parent / 'binding-runtime', IMAGE,
        enabled=True, hyperframes=True, legacy_bridge=True, candidate_engines=('canvas',))
    adapter = VideoLearningRenderer(bundle.parent / 'binding-renderer', runtime_renderer=runtime)
    adapter.candidate_engines = ('canvas',)  # Offline-only source binding, never an execution.
    binding = adapter._snapshot(operation['id'], candidate['source'], operation['input'], [])
    original_host = shared.HOST.read_bytes()
    host_patch = [
        ['page.setDefaultTimeout(10000)', 'page.setDefaultTimeout(180000)', 1],
        ["page.screenshot({type:'jpeg',quality:80,timeout:10000})",
         "page.screenshot({type:'jpeg',quality:80,timeout:180000})", 1],
        ["{waitUntil:'networkidle',timeout:20000}", "{waitUntil:'networkidle',timeout:180000}", 2],
    ]
    host = original_host
    for before, after, count in host_patch:
        assert host.count(before.encode()) == count
        host = host.replace(before.encode(), after.encode())
    assert host != original_host
    request = deepcopy(binding['request'])
    request['host_sha256'] = sha(host)
    request.pop('identity')
    request['identity'] = digest(request)
    bounds = request['constraints']
    count = int(bounds['duration_seconds'] * bounds['fps'])
    frames = [dict(name='frame-%06d.jpg' % index, timestamp_seconds=index / 24,
        sha256=sha(('fixture-frame-%d' % index).encode()), bytes=100)
        for index in range(count)]
    frames.append(dict(frames[0], name='reverse-initial.jpg'))
    receipt = dict(schema='video.animation-method-render-receipt.v3', status='rendered',
        request_identity=request['identity'], request_sha256=digest(request),
        host_sha256=request['host_sha256'], image_digest=request['image_digest'],
        engine=request['engine'], engine_runtime_sha256=request['engine_runtime_sha256'],
        project_runtime_sha256=request['project_runtime_sha256'],
        frames=frames, frame_count=count, output_sha256=sha(raw), output_bytes=len(raw),
        duration_seconds=bounds['duration_seconds'], reverse_seek_verified=True,
        decode_verified=True, audio_sha256=None, sound_events_sha256=digest([]),
        seek_checks=dict(schema='video.animation-engine-seek-checks.v1', absolute_seek=True,
            non_monotonic_seek_verified=True, repeated_seek_verified=True,
            idle_frame_verified=True, fresh_load_verified=True, probes=[]),
        probe=dict(streams=[dict(codec_type='video', codec_name='h264', width=bounds['width'],
            height=bounds['height'], avg_frame_rate='24/1', nb_frames=str(count))],
            format=dict(duration=str(bounds['duration_seconds']))))
    plan = dict(schema='video.offline-original-render-plan.v1', run_id='offline-fixture', mode='full',
        original_source_sha256=candidate['source_sha256'],
        original_host_sha256=sha(original_host), derived_host_sha256=sha(host),
        host_patch=host_patch,
        original_request_sha256=binding['request_sha256'], request_sha256=digest(request),
        identity=request['identity'], scene_files_sha256={
            'scene/' + key: value for key, value in request['files_sha256'].items()},
        runtime_sha256={'engine_runtime.mjs': request['engine_runtime_sha256'],
            'hyperframes_project.mjs': request['project_runtime_sha256']},
        image=IMAGE, image_id='sha256:' + 'b' * 64,
        resources=dict(cpus=4, memory='3g', shm='256m', tmpfs='512m', pids=256),
        screenshot_timeout_seconds=180, outer_timeout_seconds=7200, expected_frames=count,
        original_scene_unchanged=True, production_admission_changed=False,
        production_records_changed=False, production_acceptance=False)
    exited = dict(Status='exited', Running=False, ExitCode=0, OOMKilled=False)
    outcome = dict(schema='video.offline-original-outcome.v1', mode='full', status='completed',
        error=None, original_source_sha256=candidate['source_sha256'],
        original_host_sha256=sha(original_host), derived_host_sha256=sha(host),
        request_sha256=digest(request), container_id='c' * 64,
        container_states=dict(before_cleanup=exited, stopped_before_remove=deepcopy(exited)),
        cleanup='owner_verified_removed_and_confirmed_absent', frame_count=count,
        receipt=deepcopy(receipt), elapsed_seconds=1.25)
    for name, value in (('source.json', candidate['source']), ('input.json', operation['input']),
            ('original-binding.json', binding), ('render-request.json', request),
            ('render-receipt.json', receipt), ('render-plan.json', plan),
            ('render-outcome.json', outcome)):
        write_json(bundle / name, value)
    (bundle / 'output.mp4').write_bytes(raw)
    (bundle / 'host.mjs').write_bytes(host)
    (bundle / 'original-host.mjs').write_bytes(original_host)
    write_json(bundle / 'manifest.json', dict(schema='video.external-offline-import-bundle.v1',
        origin=dict(project_id=project['id'], candidate_id=candidate['id'], experiment_id=run['id'],
            operation_id=operation['id'], input_sha256=operation['input_sha256'],
            source_sha256=candidate['source_sha256']), files={}))
    refresh_manifest(bundle)
    return run, operation, candidate


@pytest.fixture(scope='module')
def seed(tmp_path_factory, real_mp4):
    root = tmp_path_factory.mktemp('offline-import-seed')
    renderer = Renderer(root / 'renders', real_mp4)
    renderer.fail = True
    service = VideoLearningService(root / 'videos', author_gateway=Gateway(),
        review_gateway=Gateway(), renderer=renderer)
    project, _ = start(service, real_mp4)
    project = finish(service, project)
    assert project['state'] == 'failed'
    old = build_bundle(root / 'bundle', service, project, real_mp4)
    return root, project['id'], old


@pytest.fixture
def case(tmp_path, seed, real_mp4):
    root, project_id, old = seed
    shutil.copytree(root / 'videos', tmp_path / 'videos')
    shutil.copytree(root / 'bundle', tmp_path / 'bundle')
    author, reviewer = Gateway(), Gateway()
    renderer = Renderer(tmp_path / 'renders', real_mp4)
    service = VideoLearningService(tmp_path / 'videos', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    return service, tmp_path / 'bundle', project_id, deepcopy(old), author, reviewer, renderer


def import_case(case, *, request_id=None, owner='developer'):
    from video_workbench.video_learning_import import import_external_render
    service, bundle, project_id, (run, operation, candidate), *_ = case
    return import_external_render(service.root, bundle, owner=owner, project_id=project_id,
        candidate_id=candidate['id'], request_id=request_id or uuid4().hex)


def no_paid_calls(case):
    *_, author, reviewer, renderer = case
    assert not author.submissions and not reviewer.submissions and not renderer.calls


@pytest.mark.parametrize('source_payload', [False, True])
def test_imported_real_media_is_a_new_candidate_without_rewriting_failed_evidence(
        case, real_mp4, source_payload):
    service, bundle, project_id, (old_run, old_operation, old_candidate), *_ = case
    if source_payload:
        source = read_json(bundle / 'source.json')
        source.pop('source_sha256')
        write_json(bundle / 'source.json', source)
        refresh_manifest(bundle)
    before = service.store.read('projects', 'developer', project_id)
    result = import_case(case)
    assert result['schema'] == 'video.external-offline-import-result.v1'
    assert result['project_id'] == project_id and result['candidate_id'] != old_candidate['id']
    detail = service.get('developer', project_id)
    candidate = next(row for row in detail['candidates'] if row['id'] == result['candidate_id'])
    assert candidate['review'] is None and candidate['status'] == 'rendered'
    assert candidate['source_sha256'] == old_candidate['source_sha256']
    assert candidate['external_import']['origin'] == 'external_offline'
    assert candidate['external_import']['production_admission'] is False
    assert candidate['external_import']['source_candidate_id'] == old_candidate['id']
    assert candidate['continuous_motion_verified'] is False and candidate['audio_verified'] is False
    assert len(candidate['observation']['frames']) == 8
    assert all(frame['media_sha256'] == sha(real_mp4) for frame in candidate['observation']['frames'])
    imported = service.store.read('experiments', 'developer', result['experiment_id'])
    event = service.store.read('operations', 'developer', result['operation_id'])
    assert imported['state'] == 'completed' and imported['stop_proven'] is True
    assert imported['frozen'] == old_run['frozen']
    assert event['stage'] != 'render' and event['status'] == 'completed'
    assert detail['best_candidate_id'] == before['best_candidate_id']
    assert detail['adopted_candidate_id'] == before['adopted_candidate_id']
    assert service.store.read('experiments', 'developer', old_run['id']) == old_run
    assert service.store.read('operations', 'developer', old_operation['id']) == old_operation
    path, mime = service.artifact('developer', project_id, candidate['artifacts']['media']['id'])
    assert mime == 'video/mp4' and path.read_bytes() == real_mp4
    source_path, _ = service.artifact('developer', project_id, candidate['artifacts']['source']['id'])
    assert read_json(source_path) == old_candidate['source']
    adopted = service.adopt('developer', project_id, dict(candidate_id=result['candidate_id']))
    assert adopted['adopted_candidate_id'] == result['candidate_id']
    assert adopted['best_candidate_id'] == before['best_candidate_id']
    no_paid_calls(case)


def test_same_request_reuses_import_event_and_changed_manifest_conflicts(case):
    service, bundle, project_id, old, *_ = case
    request_id = uuid4().hex
    result = import_case(case, request_id=request_id)
    runs = service.store.list('experiments', 'developer', project_id=project_id)
    operations = service.store.list('operations', 'developer')
    assert import_case(case, request_id=request_id) == result
    assert service.store.list('experiments', 'developer', project_id=project_id) == runs
    assert service.store.list('operations', 'developer') == operations
    manifest = read_json(bundle / 'manifest.json')
    manifest['origin']['source_sha256'] = '0' * 64
    write_json(bundle / 'manifest.json', manifest)
    with pytest.raises(ServiceError):
        import_case(case, request_id=request_id)
    assert service.store.list('experiments', 'developer', project_id=project_id) == runs
    no_paid_calls(case)


@pytest.mark.parametrize('change', ['bytes', 'source', 'input', 'request', 'receipt', 'cleanup'])
def test_untrusted_or_changed_bundle_cannot_create_a_candidate(case, change):
    service, bundle, project_id, old, *_ = case
    before = deepcopy(service.get('developer', project_id))
    if change == 'bytes':
        (bundle / 'output.mp4').write_bytes((bundle / 'output.mp4').read_bytes() + b'changed')
    else:
        filename = {'source': 'source.json', 'input': 'input.json', 'request': 'render-request.json',
            'receipt': 'render-receipt.json', 'cleanup': 'render-outcome.json'}[change]
        value = read_json(bundle / filename)
        if change == 'source':
            value['files']['scene.js'] += '\n// changed original code'
            from agent_service.profiles.video_animation_create import source_sha256
            value['source_sha256'] = source_sha256(value)
        elif change == 'input':
            value['goal'] = 'Another learning target'
        elif change == 'request':
            value['constraints']['width'] += 16
        elif change == 'receipt':
            value['output_sha256'] = '0' * 64
        else:
            value['cleanup'] = 'unconfirmed'
        write_json(bundle / filename, value)
        refresh_manifest(bundle)
    with pytest.raises(ServiceError):
        import_case(case)
    assert service.get('developer', project_id) == before
    no_paid_calls(case)


def test_owner_and_active_project_checks_prevent_offline_import(case):
    service, bundle, project_id, old, *_ = case
    before = deepcopy(service.get('developer', project_id))
    with pytest.raises(ServiceError):
        import_case(case, owner='another-owner')
    assert service.get('developer', project_id) == before
    service.next_experiment('developer', project_id, dict(request_id=uuid4().hex, mode='retry'))
    active = deepcopy(service.get('developer', project_id))
    assert active['state'] not in {'completed', 'failed', 'cancelled'}
    with pytest.raises(ServiceError):
        import_case(case)
    assert service.get('developer', project_id) == active
    no_paid_calls(case)


def test_media_claiming_twenty_four_fps_must_actually_have_that_rate(case):
    if not shutil.which('ffmpeg'):
        pytest.skip('actual FFmpeg required')
    service, bundle, project_id, old, *_ = case
    invalid = bundle / 'wrong-rate.mp4'
    result = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi',
        '-i', 'testsrc2=size=320x180:rate=12:duration=2', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', '-an', '-threads', '1', str(invalid)],
        capture_output=True, timeout=30)
    assert result.returncode == 0
    raw = invalid.read_bytes()
    invalid.unlink()
    (bundle / 'output.mp4').write_bytes(raw)
    receipt = read_json(bundle / 'render-receipt.json')
    receipt.update(output_sha256=sha(raw), output_bytes=len(raw))
    write_json(bundle / 'render-receipt.json', receipt)
    outcome = read_json(bundle / 'render-outcome.json')
    outcome['receipt'] = deepcopy(receipt)
    write_json(bundle / 'render-outcome.json', outcome)
    refresh_manifest(bundle)
    before = deepcopy(service.get('developer', project_id))
    with pytest.raises(ServiceError):
        import_case(case)
    assert service.get('developer', project_id) == before
    no_paid_calls(case)
