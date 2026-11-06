"""Offline process2 production admission; synthetic fixtures are not media proof."""
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import wave

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import media
from video_workbench.continuity import (build_continuity_plan, compile_continuity_project,
    resolve_continuity, validate_continuity_plan)
from video_workbench.continuity_snapshot import freeze_continuity_snapshot
from video_workbench.motion import estimated_alignment, make_beats, manual_alignment
from video_workbench.motion_dispatch import scene_compiler
from video_workbench.motion_process import compile_process_project
from video_workbench.motion_render_k8s import validate_artifacts
from video_workbench.motion_render_service import MixedMotionRenderer
from video_workbench.motion_renderer.job import freeze_input
from video_workbench.motion_snapshot import verify_snapshot_v2
from test_continuity_store import state
from test_motion_render_execution import (IMAGE, OWNER, operation_key, scene_renderer,
    FakeKube, successful_kube)
from test_motion_snapshot import RUNTIME, adopt, freeze, motion_scene, resign
from test_process_review import fixture_v2


def pcm(seconds, value=4096):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(value.to_bytes(2, 'little', signed=True) * round(seconds * 24000))
    return stream.getvalue()


def process_scene(*, seconds=18, reviewed=False, minimal=False):
    plan, _, _ = fixture_v2()
    beats = make_beats('请求输入。请求处理。请求完成。')
    raw = pcm(seconds)
    checksum = hashlib.sha256(raw).hexdigest()
    alignment = estimated_alignment(beats, checksum, seconds)
    if minimal:
        plan['actions'] = plan['actions'][:4]
    if reviewed:
        alignment = manual_alignment(beats, checksum, seconds, alignment['segments'],
            previous_alignment=alignment, reviewed_by='fixture-reviewer', reviewed_at='2026-11-05T12:00:00+08:00')
    return dict(id='scene-process', title='过程图解', narration=beats['source_text'], visual_mode='motion',
        audio=dict(id='process-audio', sha256=checksum, data=raw, duration_seconds=seconds,
                   spoken_text=beats['spoken_text'], origin='tts', model='kokoro-rk3588', voice='zm_yunjian'),
        motion=dict(id='process-motion', plan=plan, beats=beats, alignment=alignment,
                    compiler_version='motion-process-compiler-2'))


def project_files(scene):
    motion = scene['motion']
    return compile_process_project(motion['plan'], motion['beats'], motion['alignment'],
                                   runtime_fingerprint=RUNTIME, compiler_version='motion-process-compiler-2')


def write_project(path, scene):
    files = project_files(scene)
    for name, data in {**files, 'audio.wav': scene['audio']['data']}.items():
        (path / name).write_bytes(data)
    return files


def test_process2_snapshot_rebuild_keeps_exact_runtime_plan_audio_and_compiler():
    source = process_scene()
    before = deepcopy(source)
    snapshot = freeze([source])
    frozen = snapshot['scenes'][0]
    assert source == before
    assert frozen['motion']['timeline']['compiler'] == 'motion-process-compiler-2'
    assert frozen['motion']['project_files_sha256'] == {
        name: hashlib.sha256(data).hexdigest() for name, data in project_files(source).items()}
    assert base64.b64decode(frozen['audio']['data_base64']) == source['audio']['data']
    assert verify_snapshot_v2(snapshot) == snapshot
    assert frozen['motion']['alignment']['review_status'] == 'pending'


@pytest.mark.parametrize('compiler', ['motion-process-compiler-1', 'motion-compiler-5', None, 'unknown'])
def test_process_frozen_compiler_is_explicit_never_upgraded(compiler):
    source = process_scene()
    source['motion']['timeline'] = dict(compiler=compiler)
    with pytest.raises(ServiceError, match='motion_compiler_schema_mismatch'):
        freeze([source])


@pytest.mark.parametrize('compiler', ['motion-process-compiler-1', 'motion-compiler-5', None, 'unknown'])
def test_new_process_candidate_rejects_nonproduction_or_missing_explicit_compiler(compiler):
    source = process_scene()
    source['motion']['compiler_version'] = compiler
    assert 'timeline' not in source['motion']
    with pytest.raises(ServiceError, match='motion_compiler_schema_mismatch'):
        freeze([source])


def test_legacy_plan_cannot_claim_process_compiler_or_rehash_a_process_snapshot():
    source = motion_scene()
    source['motion']['timeline'] = dict(compiler='motion-process-compiler-2')
    with pytest.raises(ServiceError, match='motion_compiler_schema_mismatch'):
        freeze([source])
    snapshot = freeze([process_scene()])
    snapshot['scenes'][0]['motion']['timeline']['compiler'] = 'motion-process-compiler-1'
    with pytest.raises(ServiceError, match='motion_compiler_schema_mismatch'):
        verify_snapshot_v2(resign(snapshot))
    for plan in [None, {}, {'schema': 'unknown'}]:
        with pytest.raises(ServiceError, match='motion_compiler_schema_mismatch'):
            scene_compiler(plan)


def test_process_delivery_still_requires_verified_timing_current_heads_and_original_audio():
    source = process_scene()
    with pytest.raises(ServiceError, match='motion_timing_needs_review'):
        freeze([source], purpose='delivery', current_adoptions=adopt([source]))
    source = process_scene(reviewed=True)
    heads = adopt([source])
    heads[source['id']]['visual_version_id'] = 'other'
    with pytest.raises(ServiceError, match='motion_adoption_conflict'):
        freeze([source], purpose='delivery', current_adoptions=heads)
    result = freeze([source], purpose='delivery', current_adoptions=adopt([source]))
    assert verify_snapshot_v2(result) == result
    source['audio']['data'] = pcm(18, value=2048)
    source['audio']['sha256'] = hashlib.sha256(source['audio']['data']).hexdigest()
    with pytest.raises(ServiceError, match='motion_audio_binding_mismatch'):
        freeze([source])


def test_process2_bundle_is_byte_recompiled_with_complete_runtime_and_image(tmp_path):
    files = write_project(tmp_path, process_scene())
    frozen = freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])
    assert frozen['manifest_sha256'] == hashlib.sha256(files['manifest.json']).hexdigest()
    assert freeze_input(tmp_path, image_digest=RUNTIME['image_digest']) == frozen
    with pytest.raises(ValueError, match='runtime_image_digest_mismatch'):
        freeze_input(tmp_path, image_digest='sha256:' + '2' * 64)
    malicious = b'<script>arbitrary()</script>'
    (tmp_path / 'index.html').write_bytes(malicious)
    manifest = json.loads(files['manifest.json'])
    manifest['files']['index.html'] = hashlib.sha256(malicious).hexdigest()
    (tmp_path / 'manifest.json').write_bytes(canonical_bytes(manifest))
    with pytest.raises(ValueError, match='input_not_compiler_output'):
        freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])


@pytest.mark.parametrize('compiler,template', [
    ('motion-process-compiler-1', 'controlled-process-1'),
    ('motion-process-compiler-2', 'controlled-process-1'),
    ('motion-compiler-5', 'controlled-process-2'),
    ('motion-process-compiler-2', 'technical-diagrams-5'),
])
def test_process1_and_crossed_production_pairs_rejected(tmp_path, compiler, template):
    files = write_project(tmp_path, process_scene())
    manifest = json.loads(files['manifest.json'])
    manifest.update(compiler=compiler, template=template)
    (tmp_path / 'manifest.json').write_bytes(canonical_bytes(manifest))
    with pytest.raises(ValueError, match='project_contract_mismatch'):
        freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])


def test_process_context_builds_isolated_job_and_reuses_frozen_files(tmp_path):
    frozen = freeze([process_scene()])['scenes'][0]
    renderer = scene_renderer(tmp_path, FakeKube())
    files, _, _, _, _, configs, job = renderer._context(frozen, operation_key(OWNER, 'process'), 0, 'draft')
    assert json.loads(files['manifest.json'])['compiler'] == 'motion-process-compiler-2'
    assert {name: hashlib.sha256(files[name]).hexdigest() for name in frozen['motion']['project_files_sha256']} == frozen['motion']['project_files_sha256']
    assert files['audio.wav'] == base64.b64decode(frozen['audio']['data_base64'])
    assert configs and job['spec']['backoffLimit'] == 0
    assert not job['spec']['template']['spec']['automountServiceAccountToken']
    assert job['spec']['template']['spec']['containers'][0]['image'] == IMAGE
    frozen['motion']['project_files_sha256']['index.html'] = '0' * 64
    with pytest.raises(ServiceError, match='motion_frozen_project_mismatch'):
        renderer._context(frozen, operation_key(OWNER, 'process'), 0, 'draft')


def test_process_scene_runs_existing_job_lifecycle_without_continuity(tmp_path, monkeypatch):
    # This mocks artifact collection, so it proves wiring and cleanup only.
    kube = successful_kube(monkeypatch)
    renderer = scene_renderer(tmp_path, kube)
    # Keep the nested operation SHA and evidence names under Windows MAX_PATH.
    renderer.root = tmp_path.parent / 'r'
    result = renderer.render(freeze([process_scene()])['scenes'][0],
                             key=operation_key(OWNER, 'process-lifecycle'), scene_index=0, quality='draft')
    assert Path(result['path']).read_bytes() == b'mp4'
    assert not kube.resources
    assert sum(method == 'POST' and resource == 'jobs' for method, resource, _ in kube.calls) == 1


def process_artifacts():
    scene = process_scene()
    files = project_files(scene)
    _, _, observations = fixture_v2()
    observations['timeline_sha256'] = hashlib.sha256(files['timeline.json']).hexdigest()
    entries = {**files, 'output.mp4': b'synthetic-media'}
    entries['experiment.json'] = canonical_bytes(dict(status='pending_validation', stages={'render': 0, 'observe': 0}))
    for row in observations['windows']:
        for label in ('before', 'middle', 'after'):
            data = f"synthetic-{row['ordinal']}-{label}".encode()
            entries[f"observations/a{row['ordinal']}-{label}.jpg"] = data
            row[label]['frame_sha256'] = hashlib.sha256(data).hexdigest()
    for name, field, sample in [('seek-first', 'first_sha256', 'initial'),
                              ('seek-last', 'last_sha256', None),
                              ('seek-repeat', 'repeated_first_sha256', 'repeated_initial')]:
        data = b'synthetic-final' if sample is None else b'synthetic-initial'
        entries[f'observations/{name}.jpg'] = data
        checksum = hashlib.sha256(data).hexdigest()
        observations['reverse_seek'][field] = checksum
        if sample:
            observations[sample]['frame_sha256'] = checksum
    entries['observations.json'] = canonical_bytes(observations)
    for name, status in [('render-receipt.json', 'encoded_pending_validation'),
                         ('observe-receipt.json', 'observed_pending_validation')]:
        entries[name] = canonical_bytes(dict(schema='video.motion-render-receipt.v1', status=status,
            input_manifest_sha256=hashlib.sha256(files['manifest.json']).hexdigest(),
            timeline_sha256=hashlib.sha256(files['timeline.json']).hexdigest(),
            runtime={k: v for k, v in RUNTIME.items() if k != 'image_digest'},
            runtime_binding=dict(status='matched', image_digest=RUNTIME['image_digest']),
            quality='draft', cli_quality='draft', decode_verified=True,
            output_sha256=hashlib.sha256(entries['output.mp4']).hexdigest()))
    return entries, files


def test_process_artifact_reviewer_checks_state_ports_docking_and_frame_bindings():
    entries, files = process_artifacts()
    result = validate_artifacts(entries, files, RUNTIME, 'draft')
    review = result['technical_review']
    assert review['schema'] == 'video.process-review.v1' and review['technical_status'] == 'passed'
    assert 'docking_bay_ownership' in review['checks']
    assert review['timing_review_status'] == 'pending' and review['voice_naturalness'] == 'pending_user_review'
    entries['observations/a2-middle.jpg'] = b'tampered'
    with pytest.raises(ServiceError, match='motion_observation_frame_mismatch'):
        validate_artifacts(entries, files, RUNTIME, 'draft')
    entries, files = process_artifacts()
    observations = json.loads(entries['observations.json'])
    observations['windows'][2]['middle']['relations']['forward']['sourcePort']['x'] -= 20
    entries['observations.json'] = canonical_bytes(observations)
    with pytest.raises(ServiceError, match='process_'):
        validate_artifacts(entries, files, RUNTIME, 'draft')


def test_mixed_process_continuity_and_legacy_compiler_boundaries_explicitly_reject():
    scenes = freeze([process_scene(), motion_scene()])['scenes']
    plan = dict(schema='video.continuity-plan.v1', links=[])
    for action in [lambda: build_continuity_plan(scenes), lambda: validate_continuity_plan(plan, scenes),
                   lambda: resolve_continuity(scenes, plan)]:
        with pytest.raises(ServiceError, match='process_continuity_mixed_not_supported'):
            action()
    for action in [lambda: compile_continuity_project(scenes[0], {}),
                   lambda: freeze_continuity_snapshot(freeze([process_scene(), motion_scene()]), dict(id='candidate', plan=plan))]:
        with pytest.raises(ServiceError, match='process_continuity_not_supported'):
            action()


def test_process_continuity_store_reports_issue_and_blocks_suggestion_adoption_render(state):
    base, motion, continuity, project_id = state
    owner = 'alice'
    revision = lambda: base.project(owner, project_id)['revision']
    candidate = continuity.propose(owner, project_id, revision())
    continuity.adopt(owner, project_id, candidate['id'], revision(), None)
    with base.transaction() as db:
        scene_id = base._project(db, owner, project_id)['scenes'][0]['id']
        version_id = motion._head(db, owner, project_id, scene_id)['version_id']
        version = motion._version(db, owner, version_id)
        version['plan'] = process_scene()['motion']['plan']
        version['compiler_version'] = 'motion-process-compiler-2'
        version['plan_sha256'] = digest(version['plan'])
        db.execute('UPDATE motion_versions SET document=? WHERE id=?', (canonical_bytes(version).decode(), version_id))
    projection = continuity.project_projection(owner, project_id)
    assert any(issue['reason'] == 'process_continuity_mixed_not_supported' for issue in projection['issues'])
    assert projection['head_stale'] and not projection['versions'][0]['adoptable']
    for action in [lambda: continuity.propose(owner, project_id, revision()),
                   lambda: continuity.propose(owner, project_id, revision(), []),
                   lambda: continuity.adopt(owner, project_id, candidate['id'], revision(), candidate['id']),
                   lambda: continuity.render_selection(owner, project_id),
                   lambda: continuity.render_selection(owner, project_id, candidate['id'])]:
        with pytest.raises(ServiceError, match='process_continuity_mixed_not_supported'):
            action()
    with base.transaction() as db:
        db.execute('DELETE FROM continuity_heads WHERE owner=? AND project_id=?', (owner, project_id))
    assert continuity.render_selection(owner, project_id) is None


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg unavailable')
def test_real_ffmpeg_independent_process_and_legacy_concat_preserves_original_pcm(tmp_path, monkeypatch):
    # Real local encoding/composition, synthetic color clips and PCM. This is
    # not a HyperFrames process render or true narration quality acceptance.
    sources = [process_scene(seconds=1.5, minimal=True), motion_scene(value=-4096)]
    snapshot = freeze(sources)
    ffmpeg, compilers, masters = shutil.which('ffmpeg'), [], []
    class Scene:
        runtime = RUNTIME
        def render(self, scene, **kwargs):
            compilers.append(scene['motion']['timeline']['compiler'])
            path = tmp_path / f"clip-{len(compilers)}.mp4"
            media._run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                'color=c=blue:s=1280x720:r=24:d=1.5', '-f', 'lavfi', '-i',
                'anullsrc=r=24000:cl=mono:d=1.5', '-c:v', 'libx264', '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-t', '1.5', str(path)], cwd=tmp_path, timeout=30)
            return dict(path=str(path), technical_review={'technical_status': 'passed'})
    original = media._run
    def capture(args, **kwargs):
        if args[-1] == 'combined.mp4':
            with wave.open(str(Path(kwargs['cwd']) / 'master.wav'), 'rb') as wav:
                masters.append(wav.readframes(wav.getnframes()))
        return original(args, **kwargs)
    monkeypatch.setattr(media, '_run', capture)
    result = MixedMotionRenderer(Scene()).render(snapshot, tmp_path / 'result', key='local-process-test')
    expected = b''
    for source in sources:
        with wave.open(io.BytesIO(source['audio']['data']), 'rb') as wav:
            expected += wav.readframes(wav.getnframes())
    assert masters == [expected]
    assert compilers == ['motion-process-compiler-2', 'motion-compiler-5']
    assert Path(result['path']).is_file() and result['expected_duration_seconds'] == 3
    assert result['audio_codec'] == 'aac' and result['snapshot_sha256'] == snapshot['sha256']
