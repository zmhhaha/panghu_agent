"""Immutable full-video custody; synthetic fixtures do not establish artistry."""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_reference_learning as protocol
from video_workbench import animation_method_renderer as shared
from video_workbench.video_learning_renderer import VideoLearningRenderer
from test_animation_method_renderer import IMAGE, SOURCE, SyntheticExecution


def document():
    return dict(schema=protocol.INPUT_SCHEMA, mode='create', project_id='project',experiment_id='experiment',
        work_kind='reproduction', goal='Learn delayed causal expansion', criteria=['Expansion follows impact'],
        constraints=dict(width=320,height=180,fps=24,duration_seconds=1,allowed_engines=['html-svg']),
        observation=dict(frames=[dict(file_name='reference.png',sha256='a'*64,purpose='reference',
            media_sha256='b'*64,requested_time=0,actual_pts=0,clip_time=0)]),
        base_source=None,feedback='',knowledge_snapshot=None,prior_review=None,learned_method=None,assets={})


def source():
    value=deepcopy(SOURCE)
    return dict(value,source_sha256=protocol.source_sha256(value))


class VideoExecution(SyntheticExecution):
    def __call__(self, root):
        result=super().__call__(root)
        if self.failure:
            return result
        request=json.loads((root/'input/request.json').read_bytes())
        receipt=json.loads((root/'work/render-receipt.json').read_bytes())
        receipt.update(schema='video.animation-method-render-receipt.v3',
            project_runtime_sha256=request['project_runtime_sha256'],project_runtime=dict(
                schema='video.hyperframes-project-runtime-proof.v1',project_id=request['project']['project_id'],
                project_sha256=request['project_sha256'],runtime='hyperframes-core-project-1',version='0.8.149',
                core_sha256=shared.HYPERFRAMES_CORE_SHA256,seek_protocol='hyperframes-core-renderSeek-exact',
                legacy_bridge=request['legacy_bridge'],core_seek_count=24,dispatch_count=24))
        (root/'work/render-receipt.json').write_bytes(canonical_bytes(receipt))
        plan=json.loads((root/'render-plan.json').read_bytes())
        start=dict(schema='video.animation-method-render-start.v1',plan=plan,owner_token='d'*32,
            container_name='animation-method-synthetic')
        (root/'render-start.json').write_bytes(canonical_bytes(start))
        result.update(owner_token=start['owner_token'],container_name=start['container_name'],execution_timeout_seconds=60)
        (root/'render-outcome.json').write_bytes(canonical_bytes(result))
        return result


@pytest.fixture
def renderer(tmp_path,monkeypatch):
    runtime=shared.MethodAnimationRenderer(tmp_path/'existing',IMAGE,enabled=True,timeout=60,
        hyperframes=True,legacy_bridge=True)
    value=VideoLearningRenderer(tmp_path/'learning',runtime_renderer=runtime)
    execute=VideoExecution()
    monkeypatch.setattr(shared,'execute_method_render',execute)
    return value,execute


def test_complete_hyperframes_mp4_retains_original_timed_input_and_no_watch_claim(renderer):
    value,execute=renderer
    result=value.render('render',source(),document())
    assert result['status']=='rendered' and result['technical_passed'] and result['stop_proven']
    assert result['mime']=='video/mp4' and result['sha256']==result['media']['sha256']
    assert result['fps']==24 and result['duration_seconds']==1 and result['frame_count']==24
    assert result['evidence_scope']=='sampled_frames_only'
    assert result['whole_video_watched'] is result['audio_observed'] is False
    binding=json.loads((value._directory('render')/'binding.json').read_bytes())
    assert binding['document']==document() and binding['input_sha256']==digest(document())
    assert binding['learning_source_sha256']==source()['source_sha256']
    assert result==value.render('render',source(),document())==value.recover('render')
    assert execute.calls==1


def test_video_capture_defaults_to_one_hour_without_inheriting_the_method_timeout(tmp_path):
    runtime=shared.MethodAnimationRenderer(tmp_path/'existing',IMAGE,enabled=True,timeout=60,
        hyperframes=True,legacy_bridge=True)
    value=VideoLearningRenderer(tmp_path/'learning',runtime_renderer=runtime)
    binding=value._snapshot('hour-default',source(),document(),[])
    assert value.timeout==binding['timeout_seconds']==binding['configured_timeout_seconds']==3600
    assert runtime.timeout==60
    assert binding['document']==document()
    assert binding['source']['files']==source()['files']
    assert value.capabilities()['runtime_identity']['image_digest']==runtime.image_digest


def test_video_capture_accepts_an_explicit_shorter_budget_but_rejects_an_unbounded_one(tmp_path):
    runtime=shared.MethodAnimationRenderer(tmp_path/'existing',IMAGE,enabled=True,
        hyperframes=True,legacy_bridge=True)
    value=VideoLearningRenderer(tmp_path/'learning',runtime_renderer=runtime,timeout=120)
    binding=value._snapshot('explicit-shorter',source(),document(),[])
    assert binding['configured_timeout_seconds']==120
    assert shared.MethodAnimationRenderer(tmp_path/'method',IMAGE).timeout==360
    with pytest.raises(ServiceError,match='render_config_invalid'):
        VideoLearningRenderer(tmp_path/'unbounded',runtime_renderer=runtime,timeout=3601)


def test_lost_container_response_never_authorizes_second_render(renderer):
    value,execute=renderer
    execute.failure='unknown'
    unknown=dict(status='unknown',technical_passed=False,stop_proven=False)
    assert value.render('lost',source(),document())==unknown
    assert value.render('lost',source(),document())==unknown
    assert value.recover('lost')==unknown and execute.calls==1


def test_failed_container_keeps_original_stop_proof(renderer):
    value,execute=renderer
    execute.failure='known'
    result=value.render('failed',source(),document())
    assert result['status']=='failed' and result['stop_proven'] and not result['technical_passed']
    assert 'media_path' not in result


def with_native_progress(renderer, monkeypatch):
    """Complete native-output custody fixture; its media is synthetic."""
    value, execution = renderer

    def capture(root):
        outcome = execution(root)
        path = root / 'work/render-receipt.json'
        receipt = json.loads(path.read_bytes())
        receipt['diagnostics'] = dict(schema='video.method-render-diagnostics.v1', stage='media',
            substage=None, frame=dict(index=0, name='seek-initial-reload.jpg', timestamp_seconds=0),
            page_errors=[], blocked=[], crashed=False, history=[], elapsed_ms=123, stage_elapsed_ms=1,
            remaining_ms=30000)
        path.write_bytes(canonical_bytes(receipt))
        (root / 'work/render-progress.json').write_bytes(canonical_bytes(receipt['diagnostics']))
        return outcome

    monkeypatch.setattr(shared, 'execute_method_render', capture)
    return value, execution


def test_existing_completed_native_output_reconciles_without_rerender_or_evidence_rewrite(renderer, monkeypatch):
    value, execute = with_native_progress(renderer, monkeypatch)
    # Reproduce the older collector's failure after the one native execution.
    with monkeypatch.context() as previous:
        previous.setattr(value, '_diagnostic_outputs', lambda *args: {})
        with pytest.raises(ServiceError, match='render_extra_output'):
            value.render('native-progress', source(), document())
    root = value._directory('native-progress')
    row, _ = value._row('native-progress', historical=True)
    assert row['proof_sha256'] is None and row['result'] is None
    evidence = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file()}
    result = value.lookup('native-progress')
    assert result['status'] == 'rendered' and result['technical_passed'] is True and result['stop_proven'] is True
    assert value.lookup('native-progress') == result and execute.calls == 1
    assert {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in evidence} == evidence


@pytest.mark.parametrize('mutation', ['mismatch', 'schema', 'oversized', 'directory', 'extra'])
def test_native_progress_is_bounded_bound_to_receipt_and_does_not_allow_other_outputs(renderer, monkeypatch, mutation):
    value, execute = with_native_progress(renderer, monkeypatch)
    with monkeypatch.context() as previous:
        previous.setattr(value, '_diagnostic_outputs', lambda *args: {})
        with pytest.raises(ServiceError, match='render_extra_output'):
            value.render('invalid-progress', source(), document())
    root = value._directory('invalid-progress')
    progress = root / 'work/render-progress.json'
    receipt_path = root / 'work/render-receipt.json'
    receipt = json.loads(receipt_path.read_bytes())
    if mutation == 'mismatch':
        value_json = json.loads(progress.read_bytes());value_json['elapsed_ms'] += 1
        progress.write_bytes(canonical_bytes(value_json))
    elif mutation == 'schema':
        receipt['diagnostics']['schema'] = 'unbound-diagnostics'
        progress.write_bytes(canonical_bytes(receipt['diagnostics']))
        receipt_path.write_bytes(canonical_bytes(receipt))
    elif mutation == 'oversized':
        progress.write_bytes(b' ' * 65537)
    elif mutation == 'directory':
        progress.unlink();progress.mkdir()
    else:
        (root / 'work/arbitrary-output.json').write_bytes(b'{}')
    with pytest.raises(ServiceError):
        value.lookup('invalid-progress')
    assert execute.calls == 1
    assert value._row('invalid-progress', historical=True)[0]['proof_sha256'] is None


def test_progress_bytes_remain_inside_the_total_evidence_budget(renderer, monkeypatch):
    value, execute = with_native_progress(renderer, monkeypatch)
    with monkeypatch.context() as previous:
        previous.setattr(value, '_diagnostic_outputs', lambda *args: {})
        with pytest.raises(ServiceError, match='render_extra_output'):
            value.render('progress-budget', source(), document())
    root = value._directory('progress-budget')
    receipt = json.loads((root / 'work/render-receipt.json').read_bytes())
    media_bytes = (root / 'work/output.mp4').stat().st_size
    frame_bytes = sum(frame['bytes'] for frame in receipt['frames'] + receipt['seek_checks']['probes'])
    monkeypatch.setattr(shared, 'MAX_EVIDENCE_BYTES', media_bytes + frame_bytes)
    with pytest.raises(ServiceError, match='render_evidence_budget_exceeded'):
        value.lookup('progress-budget')
    assert execute.calls == 1


def test_input_rebind_and_tampered_source_cannot_render_under_existing_claim(renderer):
    value,execute=renderer
    value.render('same',source(),document())
    changed=deepcopy(document());changed['goal']='Another learning target'
    with pytest.raises(ServiceError,match='operation_conflict'):
        value.render('same',source(),changed)
    changed_source=source();changed_source['files']=dict(changed_source['files'],**{'scene.js':'changed'})
    with pytest.raises(ServiceError,match='source_changed'):
        value.render('new',changed_source,document())
    assert execute.calls==1


@pytest.mark.parametrize('file',['work/output.mp4','input/scene/scene.html','hyperframes_project.mjs','work/render-receipt.json'])
def test_retained_media_and_runtime_tampering_is_rejected(renderer,file):
    value,_=renderer
    value.render('sealed',source(),document())
    path=value._directory('sealed')/file
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ServiceError):
        value.lookup('sealed')


@pytest.mark.parametrize('capture_timeout',[60,3600])
def test_independent_admission_reconstructs_full_hyperframes_capture(renderer,capture_timeout):
    from agent_service.deploy.reference_learning_acceptance import verify_renderer_capture
    value,_=renderer
    value.timeout=capture_timeout
    value.render('admission',source(),document())
    result=dict(schema=protocol.RESULT_SCHEMA,input_sha256=digest(document()),mode='create',source=source(),
        review=None,transfer_task=None,method=None,sound_events=[],evidence_scope='sampled_frames_only')
    proof=verify_renderer_capture(value._directory('admission'),document(),result,renderer_image=IMAGE)
    assert proof['frame_count']==24 and proof['container_absent_verified']
    assert proof['whole_video_watched'] is proof['audio_observed'] is False
    assert proof['evidence_scope']=='sampled_frames_only'
