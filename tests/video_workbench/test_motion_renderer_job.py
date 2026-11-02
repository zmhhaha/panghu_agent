import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from video_workbench.motion import compile_project, example_plan, estimated_alignment, make_beats
from video_workbench.motion_demo import compile_demo_project
from video_workbench.motion_renderer import job as renderer_job
from video_workbench.motion_renderer.job import freeze_input, resources
from agent_service.contracts import ServiceError


@pytest.fixture
def project(tmp_path):
    audio = b'RIFF technical fixture bytes'
    beats = make_beats('首先提交请求。然后进入任务队列。最后执行并返回结果。')
    alignment = estimated_alignment(beats, hashlib.sha256(audio).hexdigest(), 15)
    for name, raw in compile_project(example_plan(beats), beats, alignment).items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(audio)
    return tmp_path


def test_configmap_freeze_is_deterministic(project):
    assert freeze_input(project, allow_unbound_experiment=True) == freeze_input(project, allow_unbound_experiment=True)


@pytest.mark.parametrize('template', ['flow', 'compare', 'focus'])
@pytest.mark.parametrize('compiler_version', ['motion-compiler-1', 'motion-compiler-2'])
def test_exact_compiler_versions_keep_all_input_bytes(tmp_path, monkeypatch, template, compiler_version):
    beats = make_beats('首先提交请求。然后进入任务队列。最后执行并返回结果。')
    audio = b'RIFF original template bytes'
    alignment = estimated_alignment(beats, hashlib.sha256(audio).hexdigest(), 15)
    originals = compile_project(example_plan(beats, template=template), beats, alignment,
                                compiler_version=compiler_version)
    for name, raw in originals.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(audio)
    monkeypatch.setattr(renderer_job, 'compile_demo_project', lambda *a, **kw: pytest.fail('wrong compiler'))
    freeze_input(tmp_path, allow_unbound_experiment=True)
    assert {name: (tmp_path / name).read_bytes() for name in originals} == originals


@pytest.fixture
def demo_project(tmp_path):
    beats = make_beats('先看一份日志。把日志保留在左侧。最后展示上下文。')
    audio = b'RIFF demonstration bytes'
    plan = dict(schema='video.motion-plan.v1', template='demonstration', title='记录怎样进入上下文？',
                takeaway='源记录仍然保留。', relations=[],
                elements=[dict(id='paper', kind='document', label='运行日志', detail='', slot='log_center')],
                actions=[dict(beat_id=beats['segments'][1]['id'], target='paper', kind='move',
                              placement='middle', pace='slow', to_slot='log_left')])
    for name, raw in compile_demo_project(plan, beats,
            estimated_alignment(beats, hashlib.sha256(audio).hexdigest(), 15)).items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(audio)
    return tmp_path


def test_demo_is_recompiled_only_by_explicit_demo_entry(demo_project, monkeypatch):
    monkeypatch.setattr(renderer_job, 'compile_project', lambda *a, **kw: pytest.fail('wrong compiler'))
    assert freeze_input(demo_project, allow_unbound_experiment=True)['manifest_sha256'] == hashlib.sha256(
        (demo_project / 'manifest.json').read_bytes()).hexdigest()


@pytest.mark.parametrize('compiler,template', [
    ('motion-demo-compiler-1', 'technical-diagrams-1'),
    ('motion-compiler-1', 'record-demonstration-1'),
    ('arbitrary-compiler', 'record-demonstration-1'),
    ('motion-compiler-1', 'technical-diagrams-2'),
    ('motion-compiler-2', 'technical-diagrams-1'),
])
def test_mixed_or_unknown_compiler_pairs_cannot_dispatch(project, compiler, template):
    manifest = json.loads((project / 'manifest.json').read_bytes())
    manifest.update(compiler=compiler, template=template)
    (project / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='project_contract_mismatch'):
        freeze_input(project, allow_unbound_experiment=True)


def test_demo_rehashed_arbitrary_page_is_still_rejected(demo_project):
    (demo_project / 'index.html').write_text('<script>arbitrary_code()</script>')
    manifest = json.loads((demo_project / 'manifest.json').read_bytes())
    manifest['files']['index.html'] = hashlib.sha256((demo_project / 'index.html').read_bytes()).hexdigest()
    (demo_project / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='input_not_compiler_output'):
        freeze_input(demo_project, allow_unbound_experiment=True)


def test_job_has_no_credentials_network_host_devices_or_automatic_retry(project):
    output = resources(project, image='registry:5000/video-motion-renderer:latest@sha256:' + 'a' * 64,
                       release_id='experiment-a', allow_unbound_experiment=True)
    ns, policy, config, job = output['items']
    assert policy['spec']['ingress'] == policy['spec']['egress'] == []
    assert config['immutable'] is True
    assert job['spec']['backoffLimit'] == 0
    assert job['spec']['activeDeadlineSeconds'] == 390
    spec = job['spec']['template']['spec']
    assert spec['automountServiceAccountToken'] is False
    assert spec['restartPolicy'] == 'Never'
    assert spec['enableServiceLinks'] is False
    assert {next(k for k in row if k != 'name') for row in spec['volumes']} == {'configMap', 'emptyDir'}
    assert [v['emptyDir']['sizeLimit'] for v in spec['volumes'] if 'emptyDir' in v] == ['4Gi']
    for container in spec['containers'] + spec['initContainers']:
        sec = container['securityContext']
        assert sec['runAsUser'] == 10001 and sec['runAsNonRoot'] is True
        assert sec['allowPrivilegeEscalation'] is False
        assert sec['readOnlyRootFilesystem'] is True
        assert sec['capabilities'] == {'drop': ['ALL']}
        assert sec['seccompProfile'] == {'type': 'RuntimeDefault'}
        assert all('valueFrom' not in env for env in container.get('env', []))
    renderer = spec['containers'][0]
    assert renderer['resources']['requests']['memory'] == '2Gi'
    assert renderer['resources']['limits']['memory'] == '4Gi'
    assert renderer['resources']['limits']['cpu'] == '2'


def test_does_not_execute_rehashed_arbitrary_html(project):
    (project / 'index.html').write_text('<script>fetch("https://bad.example")</script>')
    manifest = json.loads((project / 'manifest.json').read_bytes())
    manifest['files']['index.html'] = hashlib.sha256((project / 'index.html').read_bytes()).hexdigest()
    (project / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='input_not_compiler_output'):
        freeze_input(project, allow_unbound_experiment=True)


def test_audio_must_match_frozen_recording(project):
    (project / 'audio.wav').write_bytes(b'another recording')
    with pytest.raises(ValueError, match='audio_binding_mismatch'):
        freeze_input(project, allow_unbound_experiment=True)


@pytest.mark.parametrize('image', ['renderer:latest', 'registry/video:v1@sha256:' + 'a' * 64,
                                  'registry/video:latest@sha256:no'])
def test_rejects_mutable_or_nonlatest_image(project, image):
    with pytest.raises(ValueError, match='immutable_latest'):
        resources(project, image=image, release_id='a')


@pytest.mark.parametrize('seconds', [True, 1, 301, 3600])
def test_no_silent_budget_growth(project, seconds):
    with pytest.raises(ValueError, match='budget'):
        resources(project, image='r/video:latest@sha256:' + 'a' * 64, release_id='a', seconds=seconds)


def test_rejects_directory_symlink(project, tmp_path):
    link = tmp_path / 'link'
    try:
        link.symlink_to(project, target_is_directory=True)
    except OSError:
        pytest.skip('symlink privilege unavailable')
    with pytest.raises(ValueError, match='directory'):
        freeze_input(link)


def runtime_fingerprint():
    return dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
                hyperframes_source='2905162d925fc87fb8242e24ed0bbf025d307ea1',
                node='22.20.0', browser='HeadlessChrome/149', ffmpeg='ffmpeg version 5.1.8', gsap='3.14.2',
                gsap_sha256='a' * 64, font_sha256='b' * 64, dpkg_sha256='c' * 64,
                npm_lock_sha256='d' * 64, image_digest='sha256:' + 'e' * 64)


def recompile_with_runtime(project, runtime):
    read = lambda name: json.loads((project / name).read_bytes())
    timeline = read('timeline.json')
    files = compile_project(read('plan.json'), read('beats.json'), read('alignment.json'),
                            runtime_fingerprint=runtime, leading_seconds=timeline['leading_seconds'],
                            trailing_seconds=timeline['trailing_seconds'])
    for name, raw in files.items():
        (project / name).write_bytes(raw)


def test_null_runtime_requires_explicit_experiment(project):
    with pytest.raises(ValueError, match='runtime_fingerprint_required'):
        freeze_input(project)
    with pytest.raises(ValueError, match='runtime_fingerprint_required'):
        resources(project, image='r/video:latest@sha256:' + 'e' * 64, release_id='a')
    job = resources(project, image='r/video:latest@sha256:' + 'e' * 64, release_id='a',
                    allow_unbound_experiment=True)['items'][-1]
    env = {item['name']: item['value'] for item in job['spec']['template']['spec']['containers'][0]['env']}
    assert env['MOTION_ALLOW_UNBOUND_EXPERIMENT'] == '1'
    assert env['MOTION_IMAGE_DIGEST'] == 'sha256:' + 'e' * 64


def test_bound_runtime_matches_selected_image_and_never_uses_experiment_bypass(project):
    recompile_with_runtime(project, runtime_fingerprint())
    frozen = freeze_input(project)
    assert frozen['unbound_experiment'] is False
    job = resources(project, image='r/video:latest@sha256:' + 'e' * 64, release_id='a',
                    allow_unbound_experiment=True)['items'][-1]
    env = {item['name']: item['value'] for item in job['spec']['template']['spec']['containers'][0]['env']}
    assert 'MOTION_ALLOW_UNBOUND_EXPERIMENT' not in env
    assert env['MOTION_IMAGE_DIGEST'] == 'sha256:' + 'e' * 64
    with pytest.raises(ValueError, match='runtime_image_digest_mismatch'):
        resources(project, image='r/video:latest@sha256:' + 'f' * 64, release_id='a')


def test_incomplete_runtime_fingerprint_is_not_admitted(project):
    fingerprint = runtime_fingerprint()
    del fingerprint['font_sha256']
    recompile_with_runtime(project, fingerprint)
    with pytest.raises(ServiceError, match='motion_runtime_fingerprint_missing'):
        freeze_input(project)


@pytest.mark.parametrize('seconds', [15.013, 15 + 1 / 24000, 59.99, 15])
def test_real_compiler_fractional_timeline_roundtrips_into_node_mux_contract(seconds):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime unavailable')
    beats = make_beats('首先提交请求。然后进入任务队列。最后执行并返回结果。')
    alignment = estimated_alignment(beats, 'a' * 64, seconds)
    project = compile_project(example_plan(beats), beats, alignment)
    timeline = json.loads(project['timeline.json'])
    contract = (Path(__file__).resolve().parents[2] / 'video_workbench/motion_renderer/contract.mjs').as_uri()
    script = "import{readFileSync}from'node:fs';const m=await import(process.argv[1]);const t=JSON.parse(readFileSync(0,'utf8'));console.log(JSON.stringify({duration:m.encodedDuration(t),filter:m.muxAudioFilter(t)}));"
    result = subprocess.run([node, '--input-type=module', '-e', script, contract], input=json.dumps(timeline),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert abs(output['duration'] - timeline['duration_seconds']) < 1e-6
    assert output['duration'] >= seconds
    assert 'apad=whole_dur=' in output['filter']
