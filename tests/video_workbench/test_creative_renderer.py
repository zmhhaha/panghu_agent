"""Creative source custody and isolated-render admission, without rendering.

Artifact images/media below are synthetic bytes. These tests verify transport,
clock and receipt integrity; they do not claim a real decode or visual review.
"""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.creative_contract import collect_scene_result
from video_workbench.creative_renderer import compile_creative_project, resolve_creative_timeline
from video_workbench.creative_snapshot import freeze_snapshot_v4
from video_workbench.motion_render_k8s import (ANNOTATION, ExecutionJournal, KubeSceneRenderer,
    operation_key, production_resources, validate_artifacts)
from video_workbench.motion_renderer.job import freeze_input
from test_creative_production import result as source_result, source_scene
from test_motion_snapshot import RUNTIME


IMAGE = 'registry:5000/video-motion-renderer:latest@' + RUNTIME['image_digest']


def project_files(scene=None, **options):
    scene = scene or source_scene()
    value = scene['creative']
    return compile_creative_project(value['package'], value['beats'], value['alignment'],
                                    runtime_fingerprint=RUNTIME, **options)


def write_input(path, scene=None):
    scene = scene or source_scene()
    files = project_files(scene)
    for name, raw in files.items():
        (path / name).write_bytes(raw)
    (path / 'audio.wav').write_bytes(scene['audio']['data'])
    return files


def test_compile_preserves_freeform_sources_without_turning_them_into_fixed_plans():
    scene = source_scene()
    document = scene['creative']['package']['input_document']
    output = source_result(document)
    output['files'].update({'scene.js': 'window.customAnimation = true;', 'scene.css': 'svg { background: #124; }'})
    output['files_sha256'] = {name: hashlib.sha256(raw.encode()).hexdigest() for name, raw in output['files'].items()}
    scene['creative']['package'] = collect_scene_result(document, output)
    files = project_files(scene)
    assert set(files) == {'index.html', 'plan.json', 'beats.json', 'alignment.json', 'timeline.json', 'manifest.json'}
    assert files['index.html'] == output['files']['scene.html'].encode()
    assert json.loads(files['plan.json'])['result']['files'] == output['files']
    assert project_files(scene) == files
    timeline = json.loads(files['timeline.json'])
    assert timeline['actions'] == []
    assert timeline['creative']['capability_release_sha256'] == scene['creative']['package']['capability_release_sha256']
    assert timeline['audio_sha256'] == scene['audio']['sha256']


def test_padding_changes_the_outer_clock_without_stretching_the_authored_audio():
    scene = source_scene()
    files = project_files(scene, leading_seconds=.3, trailing_seconds=1.2)
    timeline = json.loads(files['timeline.json'])
    assert timeline['audio_seconds'] == scene['audio']['duration_seconds']
    assert timeline['duration_seconds'] == 3
    assert timeline['beats'][0]['start'] == .3
    assert timeline['creative']['input_sha256'] == scene['creative']['package']['input_sha256']


@pytest.mark.parametrize('field,value', [('leading_seconds', True), ('trailing_seconds', -1),
    ('leading_seconds', float('inf')), ('trailing_seconds', 10.1)])
def test_invalid_padding_is_not_silently_clamped(field, value):
    with pytest.raises(ServiceError, match='creative_render_binding_mismatch'):
        project_files(**{field: value})


def test_a_different_asset_pin_or_edited_source_cannot_reuse_the_frozen_package():
    scene = source_scene()
    document = deepcopy(scene['creative']['package']['input_document'])
    document['assets']['gsap.min.js'] = 'b' * 64
    scene['creative']['package'] = collect_scene_result(document, source_result(document))
    with pytest.raises(ServiceError, match='creative_render_asset_mismatch'):
        project_files(scene)
    scene = source_scene()
    scene['creative']['package']['result']['files']['scene.html'] += '<div>changed</div>'
    with pytest.raises(ServiceError, match='animation_experiment_source_integrity'):
        project_files(scene)


def test_review_may_confirm_the_clock_but_cannot_retime_existing_source():
    scene = source_scene()
    value = scene['creative']
    verified = deepcopy(value['alignment'])
    verified.update(review_status='verified', reviewed_by='developer', reviewed_at='2026-11-08T12:00:00+08:00')
    timeline = resolve_creative_timeline(value['package'], value['beats'], verified)
    assert timeline['review_status'] == 'verified'
    edited = deepcopy(value['alignment'])
    edited['segments'][0]['end'] -= .01
    with pytest.raises(ServiceError, match='creative_render_binding_mismatch'):
        resolve_creative_timeline(value['package'], value['beats'], edited)


def test_job_freezes_exact_generated_source_and_immutable_recording(tmp_path):
    files = write_input(tmp_path)
    frozen = freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])
    with tarfile.open(fileobj=io.BytesIO(frozen['payload']), mode='r:gz') as archive:
        restored = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
    assert restored == dict(files, **{'audio.wav': source_scene()['audio']['data']})
    assert frozen['unbound_experiment'] is False
    (tmp_path / 'audio.wav').write_bytes(b'another recording')
    with pytest.raises(ValueError, match='audio_binding_mismatch'):
        freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])


def test_rehashing_an_html_mutation_does_not_change_the_frozen_scene(tmp_path):
    write_input(tmp_path)
    (tmp_path / 'index.html').write_bytes(b'<html>different scene</html>')
    manifest = json.loads((tmp_path / 'manifest.json').read_bytes())
    manifest['files']['index.html'] = hashlib.sha256((tmp_path / 'index.html').read_bytes()).hexdigest()
    (tmp_path / 'manifest.json').write_bytes(canonical_bytes(manifest))
    with pytest.raises(ValueError, match='input_not_compiler_output'):
        freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])


def test_creative_job_inherits_no_credentials_and_is_pinned_to_source(tmp_path):
    files = write_input(tmp_path)
    binding = 'b' * 64
    configs, job = production_resources(tmp_path, image=IMAGE, release_id='creative-review',
        namespace='video-motion-render', name='creative-test', binding=binding, quality='final')
    assert all(config['immutable'] is True for config in configs)
    assert job['metadata']['annotations'][ANNOTATION] == binding
    assert job['spec']['backoffLimit'] == 0
    pod = job['spec']['template']['spec']
    assert pod['automountServiceAccountToken'] is False and pod['enableServiceLinks'] is False
    assert pod['restartPolicy'] == 'Never'
    assert pod.get('hostNetwork', False) is False and pod.get('hostPID', False) is False
    assert {next(key for key in item if key != 'name') for item in pod['volumes']} == {'projected', 'emptyDir'}
    for container in pod['containers'] + pod['initContainers']:
        assert container['image'] == IMAGE
        assert container['securityContext']['allowPrivilegeEscalation'] is False
        assert container['securityContext']['runAsNonRoot'] is True
        assert container['securityContext']['readOnlyRootFilesystem'] is True
        assert container['securityContext']['capabilities'] == {'drop': ['ALL']}
        assert not any('valueFrom' in row for row in container['env'])
    environment = {row['name']: row['value'] for row in pod['containers'][0]['env']}
    assert environment['MOTION_MANIFEST_SHA256'] == hashlib.sha256(files['manifest.json']).hexdigest()
    assert environment['MOTION_IMAGE_DIGEST'] == RUNTIME['image_digest']
    assert environment['MOTION_QUALITY'] == 'final'
    assert 'MOTION_ALLOW_UNBOUND_EXPERIMENT' not in environment
    with pytest.raises(ValueError, match='runtime_image_digest_mismatch'):
        production_resources(tmp_path, image=IMAGE[:-64] + 'f' * 64, release_id='creative-review',
            namespace='video-motion-render', name='creative-test', binding=binding)


@pytest.mark.parametrize('policies,accepted', [
    ([], False),
    ([dict(spec=dict(podSelector={}, policyTypes=['Ingress', 'Egress'], ingress=[], egress=[]))], True),
    ([dict(spec=dict(podSelector={}, policyTypes=['Ingress', 'Egress'], ingress=[], egress=[{}]))], False),
    ([dict(spec=dict(podSelector=dict(matchLabels={'only': 'some-pods'}),
                    policyTypes=['Ingress', 'Egress'], ingress=[], egress=[]))], False),
])
def test_creative_renderer_requires_a_namespace_wide_deny_all_policy(tmp_path, policies, accepted):
    kube = SimpleNamespace(namespace='video-motion-render',
                           request=lambda *args, **kwargs: dict(items=policies))
    renderer = KubeSceneRenderer(ExecutionJournal(tmp_path / 'journal.db'), kube,
        image=IMAGE, release_id='review', runtime=RUNTIME, root=tmp_path / 'runs')
    if accepted:
        assert renderer.check_policy() is None
    else:
        with pytest.raises(ServiceError, match='motion_namespace_not_isolated'):
            renderer.check_policy()


def test_creative_context_uses_frozen_source_and_keeps_operation_identity(tmp_path):
    scene = freeze_snapshot_v4('p1', 1, '生成动画', [source_scene()], runtime_fingerprint=RUNTIME)['scenes'][0]
    renderer = KubeSceneRenderer(ExecutionJournal(tmp_path / 'journal.db'), SimpleNamespace(namespace='video-motion-render'),
        image=IMAGE, release_id='review', runtime=RUNTIME, root=tmp_path / 'runs')
    key = operation_key('a' * 64, 'operation-one')
    context = renderer._context(scene, key, 0, 'draft')
    files, scene_key, binding, name, directory, configs, job = context
    assert scene_key == key + ':0'
    assert files['audio.wav'] == source_scene()['audio']['data']
    assert scene['creative']['project_files_sha256'] == {key: hashlib.sha256(raw).hexdigest()
                                                       for key, raw in files.items() if key != 'audio.wav'}
    assert job['metadata']['annotations'][ANNOTATION] == binding
    assert renderer._context(scene, key, 0, 'draft')[1:5] == context[1:5]
    assert renderer._context(scene, key, 0, 'final')[2] != binding
    altered = deepcopy(scene)
    altered['creative']['project_files_sha256']['index.html'] = 'f' * 64
    with pytest.raises(ServiceError, match='motion_frozen_project_mismatch'):
        renderer._context(altered, key, 0, 'draft')


def node_verify(path, expected):
    if not shutil.which('node'):
        pytest.skip('Node is not installed')
    module = (Path(__file__).resolve().parents[2] / 'video_workbench/motion_renderer/contract.mjs').as_uri()
    script = 'import {verifyInput} from ' + json.dumps(module) + ';verifyInput(process.argv[1],process.argv[2]);'
    return subprocess.run(['node', '--input-type=module', '-e', script, str(path), expected],
                          capture_output=True, text=True, timeout=10)


def test_node_contract_accepts_the_creative_package_and_rejects_changed_external_manifest(tmp_path):
    files = write_input(tmp_path)
    expected = hashlib.sha256(files['manifest.json']).hexdigest()
    process = node_verify(tmp_path, expected)
    assert process.returncode == 0, process.stderr
    manifest = json.loads(files['manifest.json'])
    manifest['width'] = 640
    (tmp_path / 'manifest.json').write_bytes(canonical_bytes(manifest))
    process = node_verify(tmp_path, expected)
    assert process.returncode != 0 and 'manifest_binding_mismatch' in process.stderr


def receipt_artifacts():
    files = project_files()
    timeline, package = (json.loads(files[name]) for name in ('timeline.json', 'plan.json'))
    count = round(timeline['duration_seconds'] * 24)
    times = [0, *[(count - 1) / 24 * (i + 1) / 18 for i in range(17)], (count - 1) / 24, 0]
    entries = dict(files)
    samples = []
    for index, time in enumerate(times):
        frame = b'synthetic-zero-frame' if time == 0 else ('synthetic-' + str(time)).encode()
        path = f'observations/creative-{index}.jpg'
        entries[path] = frame
        samples.append(dict(path=path, time=time, local_seconds=max(0, min(timeline['audio_seconds'], time)),
                            frame_sha256=hashlib.sha256(frame).hexdigest(), bytes=len(frame)))
    observations = dict(schema='video.creative-observations.v1',
        renderer='isolated-chromium-absolute-seek-ffmpeg-1', input_sha256=package['input_sha256'],
        source_sha256=package['result_sha256'], frame_count=count, samples=samples,
        blocked_requests=[], page_errors=[], visual_quality='not_evaluated')
    entries['observations.json'] = canonical_bytes(observations)
    entries['output.mp4'] = b'synthetic-media-only-for-custody-test'
    entries['experiment.json'] = canonical_bytes(dict(status='pending_validation', stages=dict(render=0, observe=0)))
    for name, status in [('render-receipt.json', 'encoded_pending_validation'), ('observe-receipt.json', 'observed_pending_validation')]:
        receipt = dict(schema='video.motion-render-receipt.v1', status=status,
            input_manifest_sha256=hashlib.sha256(files['manifest.json']).hexdigest(),
            timeline_sha256=hashlib.sha256(files['timeline.json']).hexdigest(),
            runtime={key: value for key, value in RUNTIME.items() if key != 'image_digest'},
            runtime_binding=dict(status='matched', image_digest=RUNTIME['image_digest']),
            quality='draft', cli_quality='draft')
        if name == 'render-receipt.json':
            receipt.update(decode_verified=True, output_sha256=hashlib.sha256(entries['output.mp4']).hexdigest(),
                creative_capture=dict(schema='video.creative-capture.v1', renderer=observations['renderer'],
                    frame_count=count, first_sha256=samples[0]['frame_sha256'],
                    last_sha256=samples[-2]['frame_sha256'], reverse_initial_sha256=samples[-1]['frame_sha256'],
                    probe_sha256=[sample['frame_sha256'] for sample in samples],
                    blocked_requests=[], page_errors=[]))
        entries[name] = canonical_bytes(receipt)
    return entries, files


def test_creative_receipt_validation_is_custody_not_aesthetic_approval():
    entries, files = receipt_artifacts()
    evidence = validate_artifacts(entries, files, RUNTIME, 'draft')
    assert evidence['technical_review']['status'] == 'passed'
    assert evidence['technical_review']['aesthetic_approval'] is False
    assert evidence['technical_review']['visual_quality'] == 'not_evaluated'
    assert evidence['media_sha256'] == hashlib.sha256(entries['output.mp4']).hexdigest()


@pytest.mark.parametrize('field,value,code', [
    ('input_sha256', 'f' * 64, 'creative_observation_binding_mismatch'),
    ('source_sha256', 'f' * 64, 'creative_observation_binding_mismatch'),
    ('frame_count', 1, 'creative_observation_binding_mismatch'),
    ('blocked_requests', ['external'], 'creative_observation_binding_mismatch'),
    ('page_errors', ['error'], 'creative_observation_binding_mismatch'),
])
def test_creative_observations_cannot_be_reused_for_another_source_or_failed_capture(field, value, code):
    entries, files = receipt_artifacts()
    observations = json.loads(entries['observations.json'])
    observations[field] = value
    entries['observations.json'] = canonical_bytes(observations)
    with pytest.raises(ServiceError, match=code):
        validate_artifacts(entries, files, RUNTIME, 'draft')


@pytest.mark.parametrize('kind', ['frame', 'time', 'reverse', 'capture', 'middle-probe', 'runtime', 'media'])
def test_creative_validation_rejects_tampered_frame_clock_reverse_runtime_and_media(kind):
    entries, files = receipt_artifacts()
    observations = json.loads(entries['observations.json'])
    if kind == 'frame':
        entries['observations/creative-1.jpg'] = b'changed frame'
        code = 'creative_observation_frame_mismatch'
    elif kind == 'time':
        observations['samples'][1]['time'] += .01
        code = 'creative_observation_frame_mismatch'
    elif kind == 'reverse':
        frame = b'a different zero pose'
        entries['observations/creative-19.jpg'] = frame
        observations['samples'][-1].update(frame_sha256=hashlib.sha256(frame).hexdigest(), bytes=len(frame))
        code = 'creative_reverse_seek_mismatch'
    else:
        receipt = json.loads(entries['render-receipt.json'])
        if kind == 'capture':
            receipt['creative_capture']['last_sha256'] = 'f' * 64
            code = 'creative_render_observation_mismatch'
        elif kind == 'middle-probe':
            # The endpoints still agree: inconsistent intermediate states from
            # a non-pure seek implementation must also prevent success.
            receipt['creative_capture']['probe_sha256'][10] = 'f' * 64
            code = 'creative_render_observation_mismatch'
        elif kind == 'runtime':
            receipt['runtime_binding']['image_digest'] = 'sha256:' + 'f' * 64
            code = 'motion_runtime_receipt_mismatch'
        else:
            entries['output.mp4'] += b'changed media'
            code = 'motion_media_receipt_invalid'
        entries['render-receipt.json'] = canonical_bytes(receipt)
    entries['observations.json'] = canonical_bytes(observations)
    with pytest.raises(ServiceError, match=code):
        validate_artifacts(entries, files, RUNTIME, 'draft')
