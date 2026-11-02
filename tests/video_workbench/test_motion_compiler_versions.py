"""Frozen v1 recordings remain reproducible when compact v2 becomes default."""
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace
import wave

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.motion import (
    COMPILER_VERSION, TEMPLATE_VERSION, compile_project, estimated_alignment,
    example_plan, make_beats, resolve_timeline,
)
from video_workbench.motion_render_k8s import production_resources
from video_workbench import motion_render_k8s
from video_workbench.motion_renderer.job import freeze_input
from video_workbench.motion_snapshot import freeze_snapshot_v2, verify_snapshot_v2


TEXT = '请求到达。进入队列。执行节点开始工作。'
V1, V2 = 'motion-compiler-1', 'motion-compiler-2'
RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='2905162d925fc87fb8242e24ed0bbf025d307ea1',
    node='v22.20.0', browser='HeadlessChrome/141.0.7390.54',
    ffmpeg='ffmpeg version 5.1.7-0+deb12u1', gsap='3.14.2',
    gsap_sha256='a' * 64, font_sha256='f' * 64, dpkg_sha256='d' * 64,
    npm_lock_sha256='e' * 64, image_digest='sha256:' + '1' * 64)

# Captured from the original compiler before introducing v2. Every generated
# byte is covered, including executable HTML, the timeline and the manifest.
V1_GOLDEN = {
    'flow': {
        'index.html': '8bb164ea0d5f69b86a472c0635e02a45bcbe2d3fa5be811d60ef13886bd8de1c',
        'plan.json': 'eca6d25d23a51a9975732c78adc3d81ce80cb46a0db0c5df3506007bee842021',
        'beats.json': 'f2554954f85093f930e87d66d21ea560fcb6757fddfc43a95186f2757adb9ca1',
        'alignment.json': '0e0a99eeb59363992660e441afa0885a06c5fe2275d32abffda09913ba603349',
        'timeline.json': '673c3ebdeac7e8e1c94f6a50a71f877b0daef197a6c4bc4e8d731e48d161c2e2',
        'manifest.json': 'b663155b8b57eb08d3fb4342d3e2a8c8c8640ad948d4e323e148e0a052687e9a',
    },
    'compare': {
        'index.html': '71d15361a9aedc8d9291b5243a52a3bcca53615cc6a6d6da5fd713b81016062b',
        'plan.json': 'ae176a19fa0cdb4d6e10f473fc091c48f5dfad49effba2cd97404853b3fd1754',
        'beats.json': 'f2554954f85093f930e87d66d21ea560fcb6757fddfc43a95186f2757adb9ca1',
        'alignment.json': '0e0a99eeb59363992660e441afa0885a06c5fe2275d32abffda09913ba603349',
        'timeline.json': 'de311e29e9ec10f96a23da714d4d51756b14a80a1324aec4746d503d14ce882d',
        'manifest.json': 'e1b4dbed1adae63a62c593cab75f5eeeb98c642afe5e51fbc90c04cddf409b0d',
    },
    'focus': {
        'index.html': '3a0fcd3d19577740088381c2bb47643228059485906f51594d5675ab05e51099',
        'plan.json': '422a8b788b849b86e7c2b0adf086104ac013dfb30ad2a7df278fa228968d707a',
        'beats.json': 'f2554954f85093f930e87d66d21ea560fcb6757fddfc43a95186f2757adb9ca1',
        'alignment.json': '0e0a99eeb59363992660e441afa0885a06c5fe2275d32abffda09913ba603349',
        'timeline.json': '59db574b4fa7ffa930ed7f6a42a6c1de45920038b40f8156c8d895a123401d00',
        'manifest.json': 'ace19420ebd39ee135e42d92f84562f7efb41929da687cb2c59a3ce710d8e59f',
    },
}
V1_SNAPSHOT_SHA = '48e40c60f2670ebac034cbc1894bdf45f3e9796f3a322d4a6fa41eff6c477dc4'


def arguments(template='focus', *, audio_sha='a' * 64, seconds=15):
    beats = make_beats(TEXT)
    return (example_plan(beats, template=template), beats,
            estimated_alignment(beats, audio_sha, seconds))


def recording():
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes((1).to_bytes(2, 'little', signed=True) * 36000)
    return output.getvalue()


def scene(*, compiler=None):
    data = recording()
    audio = dict(id='audio-candidate', sha256=hashlib.sha256(data).hexdigest(),
        data=data, duration_seconds=1.5, spoken_text=TEXT, origin='uploaded')
    plan, beats, alignment = arguments(audio_sha=audio['sha256'], seconds=1.5)
    motion = dict(id='motion-candidate', plan=plan, beats=beats, alignment=alignment)
    if compiler is not None:
        motion['timeline'] = resolve_timeline(plan, beats, alignment, compiler_version=compiler)
    return dict(id='scene-focus', title='聚焦', narration=TEXT,
                visual_mode='motion', audio=audio, motion=motion)


def snapshot(source):
    return freeze_snapshot_v2('compiler-project', 7, '版本兼容', [source],
                              runtime_fingerprint=RUNTIME)


@pytest.mark.parametrize('template', ['flow', 'compare', 'focus'])
def test_explicit_v1_reproduces_all_pre_upgrade_bytes(template):
    args = arguments(template)
    files = compile_project(*args, compiler_version=V1)
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()} == V1_GOLDEN[template]
    assert json.loads(files['timeline.json']) == resolve_timeline(*args, compiler_version=V1)


@pytest.mark.parametrize('template', ['flow', 'compare', 'focus'])
def test_default_v2_preserves_candidate_text_audio_and_actions(template):
    args = arguments(template)
    original = deepcopy(args)
    legacy = compile_project(*args, compiler_version=V1)
    current = compile_project(*args)
    assert COMPILER_VERSION == V2
    assert TEMPLATE_VERSION == 'technical-diagrams-2'
    assert compile_project(*args) == current
    assert args == original
    for name in ('plan.json', 'beats.json', 'alignment.json'):
        assert current[name] == legacy[name]
    old_timeline, new_timeline = (json.loads(files['timeline.json']) for files in (legacy, current))
    assert old_timeline.pop('compiler') == V1
    assert new_timeline.pop('compiler') == V2
    assert new_timeline == old_timeline
    assert current['index.html'] != legacy['index.html']
    old_manifest, new_manifest = (json.loads(files['manifest.json']) for files in (legacy, current))
    assert (old_manifest['compiler'], old_manifest['template']) == (V1, 'technical-diagrams-1')
    assert (new_manifest['compiler'], new_manifest['template']) == (V2, 'technical-diagrams-2')
    assert new_manifest['audio_sha256'] == old_manifest['audio_sha256']
    assert new_manifest['semantic_sha256'] == old_manifest['semantic_sha256']


@pytest.mark.parametrize('compiler', ['motion-compiler-99', 'motion-demo-compiler-1', None])
def test_generic_compiler_cannot_select_unknown_or_demo_implementations(compiler):
    args = arguments()
    for function in (compile_project, resolve_timeline):
        with pytest.raises(ServiceError):
            function(*args, compiler_version=compiler)


def test_old_snapshot_keeps_pre_upgrade_hash_and_new_snapshot_has_new_identity():
    source = scene(compiler=V1)
    original = deepcopy(source)
    legacy = snapshot(source)
    assert source == original
    assert legacy['sha256'] == V1_SNAPSHOT_SHA
    assert verify_snapshot_v2(legacy) == legacy
    current = snapshot(scene())
    assert verify_snapshot_v2(current) == current
    assert current['scenes'][0]['motion']['timeline']['compiler'] == V2
    assert current['sha256'] != legacy['sha256']
    for name in ('plan', 'beats', 'alignment', 'id', 'semantic_sha256'):
        assert current['scenes'][0]['motion'][name] == legacy['scenes'][0]['motion'][name]
    assert current['scenes'][0]['audio'] == legacy['scenes'][0]['audio']


@pytest.mark.parametrize('original,replacement', [(V1, V2), (V2, V1), (V1, 'unknown-compiler')])
def test_resigning_only_frozen_compiler_cannot_relabel_an_existing_snapshot(original, replacement):
    frozen = snapshot(scene(compiler=original))
    frozen['scenes'][0]['motion']['timeline']['compiler'] = replacement
    frozen['sha256'] = digest({key: value for key, value in frozen.items() if key != 'sha256'})
    with pytest.raises(ServiceError):
        verify_snapshot_v2(frozen)


@pytest.mark.parametrize('compiler', [V1, V2])
def test_production_job_admits_each_exact_compiler_pair_without_rewriting_files(tmp_path, compiler):
    source = scene(compiler=compiler)
    motion = source['motion']
    files = compile_project(motion['plan'], motion['beats'], motion['alignment'],
                            compiler_version=compiler, runtime_fingerprint=RUNTIME)
    files['audio.wav'] = source['audio']['data']
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    frozen = freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])
    configs, job = production_resources(tmp_path,
        image='registry:5000/video-motion-renderer:latest@' + RUNTIME['image_digest'],
        release_id='compiler-compatibility', namespace='video-motion-production',
        name='compatibility-test', binding='b' * 64)
    assert frozen['manifest_sha256'] == hashlib.sha256(files['manifest.json']).hexdigest()
    assert configs and job['spec']['backoffLimit'] == 0
    assert {name: (tmp_path / name).read_bytes() for name in files} == files


@pytest.mark.parametrize('compiler', [V1, V2])
def test_executor_prepares_the_frozen_version_before_any_kubernetes_submission(tmp_path, monkeypatch, compiler):
    frozen = snapshot(scene(compiler=compiler))['scenes'][0]

    class SubmissionBoundaryReached(Exception):
        pass

    def inspect_bundle(directory, **options):
        actual = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                  for name in frozen['motion']['project_files_sha256']}
        assert actual == frozen['motion']['project_files_sha256']
        assert hashlib.sha256((directory / 'audio.wav').read_bytes()).hexdigest() == frozen['audio']['sha256']
        assert json.loads((directory / 'timeline.json').read_bytes())['compiler'] == compiler
        raise SubmissionBoundaryReached

    monkeypatch.setattr(motion_render_k8s, 'production_resources', inspect_bundle)
    renderer = motion_render_k8s.KubeSceneRenderer(None,
        SimpleNamespace(namespace='video-motion-production'),
        image='registry:5000/video-motion-renderer:latest@' + RUNTIME['image_digest'],
        release_id='compiler-compatibility', runtime=RUNTIME, root=tmp_path)
    with pytest.raises(SubmissionBoundaryReached):
        renderer.render(frozen, key='compatibility', scene_index=0, quality='draft')


@pytest.mark.parametrize('compiler,template', [
    (V1, 'technical-diagrams-2'), (V2, 'technical-diagrams-1'),
    (V2, 'record-demonstration-1'), ('motion-demo-compiler-1', 'technical-diagrams-2'),
])
def test_freezing_rejects_crossed_legacy_new_and_demo_pairs(tmp_path, compiler, template):
    source = scene()
    motion = source['motion']
    files = compile_project(motion['plan'], motion['beats'], motion['alignment'])
    files['audio.wav'] = source['audio']['data']
    manifest = json.loads(files['manifest.json'])
    manifest.update(compiler=compiler, template=template)
    files['manifest.json'] = json.dumps(manifest).encode()
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    with pytest.raises(ValueError, match='project_contract_mismatch'):
        freeze_input(tmp_path, allow_unbound_experiment=True)
