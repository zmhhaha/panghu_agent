"""Real clip decoding and durable learning orchestration, without model calls."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.profiles import video_reference_learning as protocol
from video_workbench.video_learning_service import VideoLearningService
from video_workbench.video_learning_media import VideoLearningMedia
from test_picture_learning_service import Gateway as PictureGateway, source


@pytest.fixture(scope='module')
def real_mp4(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('actual FFmpeg/FFprobe required')
    path = tmp_path_factory.mktemp('real-media') / 'reference.mp4'
    result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=320x180:rate=24:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-an', '-threads', '1', str(path)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    return path.read_bytes()


class Gateway(PictureGateway):
    def upload(self, document, files):
        protocol.validate_input(document)
        assert files[protocol.INPUT_NAME] == canonical_bytes(document)
        assert all(hashlib.sha256(files[row['file_name']]).hexdigest() == row['sha256']
            for row in document['observation']['frames'])
        self.uploads.append((deepcopy(document), deepcopy(files)))
        return []

    def collect(self, job_id, input_sha256, document):
        result = dict(schema=protocol.RESULT_SCHEMA, input_sha256=input_sha256,
            mode=document['mode'], source=None, review=None, transfer_task=None, method=None,
            evidence_scope='sampled_frames_only', sound_events=[])
        if document['mode'] == 'analyze':
            result['analysis'] = dict(summary='A sampled candidate for the visible sequence.',
                candidate_segments=[dict(start_seconds=0, end_seconds=2, title='Trajectory changes',
                    learning_goal='Learn how identities remain visible during motion.', difficulty='moderate',
                    evidence_frame_ids=[document['observation']['frames'][0]['frame_id']])],
                observed_scope='Only the supplied 24 measured samples.',
                limitations=['No continuous playback or audio listening.'])
        elif document['mode'] == 'create':
            result.update(source=source(), method=dict(technique='Individually timed trajectories',
                evidence_scope='sampled_frames_only', limitations=['Continuity not directly observed']))
        else:
            result['review'] = dict(score=85, summary='Compared measured reference and output frames.',
                issues=[], stop_recommended=True, motion='unverified', audio='unverified')
            if document['work_kind'] == 'reproduction':
                result['transfer_task'] = dict(goal='Change object count and spatial organization.',
                    criteria=['Keep identities traceable through the new trajectories.'])
        protocol.validate_result(result, input_sha256, document)
        return result


class Renderer:
    def __init__(self, root, raw):
        self.root, self.raw, self.results, self.calls = root, raw, {}, []
        self.fail, self.lose_result = False, False
        root.mkdir()

    def capabilities(self):
        return dict(allowed_engines=['html-svg', 'canvas', 'three'], assets={})

    def render(self, operation_id, source_document, document, *, deadline_at=None):
        assert operation_id not in self.calls
        self.calls.append(operation_id)
        protocol.validate_input(document)
        if self.fail:
            result = dict(status='failed', technical_passed=False, stop_proven=True)
        else:
            directory = self.root / operation_id
            directory.mkdir()
            (directory / 'output.mp4').write_bytes(self.raw)
            (directory / 'receipt.json').write_text('{"fixture":true}', encoding='utf-8')
            result = dict(status='rendered', technical_passed=True, stop_proven=True,
                media_path=str(directory / 'output.mp4'), receipt_path=str(directory / 'receipt.json'),
                sha256=hashlib.sha256(self.raw).hexdigest(), width=320, height=180,
                duration_seconds=2, fps=24)
        self.results[operation_id] = result
        if self.lose_result:
            raise ServiceError('result_lost', 503)
        return deepcopy(result)

    def recover(self, operation_id):
        return deepcopy(self.results.get(operation_id))

    lookup = recover


@pytest.fixture
def system(tmp_path, real_mp4):
    author, reviewer = Gateway(), Gateway()
    renderer = Renderer(tmp_path / 'renders', real_mp4)
    service = VideoLearningService(tmp_path / 'videos', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    return service, author, reviewer, renderer


def upload(service, raw, owner='developer', **overrides):
    body = dict(request_id=uuid4().hex, file_name='reference.mp4', mime_type='video/mp4',
        content_base64=base64.b64encode(raw).decode(), source_url='https://example.com/reference')
    body.update(overrides)
    return service.upload_reference(owner, body), body


def start(service, raw, **overrides):
    ref, _ = upload(service, raw)
    body = dict(request_id=uuid4().hex, title='Trajectory study', goal='Learn the visible motion sequence.',
        reference_ids=[ref['id']], width=320, height=180, start_seconds=0, end_seconds=2,
        learn_audio=True, use_wikiskill=False, max_rounds=1, max_seconds=900)
    body.update(overrides)
    return service.start('developer', body), body


def finish(service, project, limit=60):
    for _ in range(limit):
        project = service.get('developer', project['id'])
        if project['state'] in {'completed', 'cancelled', 'failed'}:
            return project
        service.advance('developer', project['latest_experiment_id'])
    raise AssertionError(project)


def test_real_decode_records_presentation_timestamps_aliases_and_original_bytes(system, real_mp4):
    service, author, reviewer, renderer = system
    project, body = start(service, real_mp4)
    frozen = service.inspect('developer', project['id'], project['latest_experiment_id'])['frozen']
    observed = frozen['reference_observation']
    assert len(observed['frames']) == 8
    assert observed['coverage']['requested_samples'] == 8
    assert observed['coverage']['unique_presentation_frames'] == 8
    assert observed['coverage']['continuous_motion_verified'] is False
    assert all(row['actual_pts'] >= row['requested_time'] for row in observed['frames'])
    assert all(row['media_sha256'] == hashlib.sha256(real_mp4).hexdigest() for row in observed['frames'])
    assert frozen['audio_observation']['status'] == 'unverified'
    path, mime = service.reference_artifact('developer', project['reference_ids'][0])
    assert path.read_bytes() == real_mp4 and mime == 'video/mp4'
    assert author.submissions == reviewer.submissions == renderer.calls == []
    assert service.start('developer', body)['id'] == project['id']


def test_reproduction_and_transfer_preserve_mp4_source_and_actual_visual_inputs(system, real_mp4):
    service, author, reviewer, renderer = system
    project, _ = start(service, real_mp4)
    result = finish(service, project)
    assert result['state'] == 'completed', result
    assert result['verification_scope'] == 'sampled_frames_only'
    assert len(result['candidates']) == 2
    assert [row['work_kind'] for row in result['candidates']] == ['reproduction', 'transfer']
    assert result['experiments'][0]['transfer_status'] == 'reviewed'
    assert result['audio_verified'] is result['continuous_motion_verified'] is False
    assert result['adopted_candidate_id'] is None
    assert all(row['media_url'].startswith('/api/video-lab/') for row in result['candidates'])
    for candidate in result['candidates']:
        path, mime = service.artifact('developer', result['id'], candidate['artifacts']['media']['id'])
        assert mime == 'video/mp4' and path.read_bytes() == real_mp4
        assert candidate['source_sha256']
    author_docs = [row[0] for row in author.uploads]
    assert any(frame['purpose'] == 'reference' for frame in author_docs[0]['observation']['frames'])
    transfer = next(doc for doc in author_docs if doc['work_kind'] == 'transfer')
    assert transfer['base_source'] is None and transfer['observation']['frames'] == []
    assert transfer['knowledge_snapshot'] is None and transfer['learned_method'] is not None
    review = reviewer.uploads[0][0]
    assert {row['purpose'] for row in review['observation']['frames']} == {'reference', 'output'}
    submissions = (len(author.submissions), len(reviewer.submissions), len(renderer.calls))
    assert service.catalogue('developer')['projects'][0]['preview_url']
    service.get('developer', project['id'])
    assert submissions == (len(author.submissions), len(reviewer.submissions), len(renderer.calls))


@pytest.mark.parametrize('overrides', [dict(start_seconds=-1), dict(end_seconds=3),
    dict(end_seconds=.5), dict(end_seconds=1.1), dict(width=1920), dict(learn_audio='yes')])
def test_clip_and_runtime_bounds_fail_before_paid_tasks(system, real_mp4, overrides):
    service, author, reviewer, renderer = system
    with pytest.raises(ServiceError):
        start(service, real_mp4, **overrides)
    assert author.submissions == reviewer.submissions == renderer.calls == []


def test_explicit_range_required_and_url_alone_is_not_a_reference(system, real_mp4):
    service, author, reviewer, renderer = system
    ref, _ = upload(service, real_mp4)
    with pytest.raises(ServiceError, match='video_learning_explicit_clip_required'):
        service.start('developer', dict(request_id=uuid4().hex, title='No silent trimming', goal='Learn',
            reference_ids=[ref['id']], use_wikiskill=False))
    with pytest.raises(ServiceError):
        service.upload_reference('developer', dict(request_id=uuid4().hex,
            source_url='https://example.com/video.mp4'))
    assert not author.submissions


def test_owner_idempotency_and_failed_render_technical_retry(system, real_mp4):
    service, author, reviewer, renderer = system
    reference, body = upload(service, real_mp4)
    assert service.upload_reference('developer', body)['id'] == reference['id']
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        service.upload_reference('developer', dict(body, source_url='https://example.com/different'))
    with pytest.raises(ServiceError) as err:
        service.reference_artifact('another-owner', reference['id'])
    assert err.value.status == 404
    project, _ = start(service, real_mp4)
    renderer.fail = True
    failed = finish(service, project)
    assert failed['state'] == 'failed'
    with pytest.raises(ServiceError):
        service.experiment('developer', project['id'], dict(request_id=uuid4().hex,
            mode='retry', feedback='cannot edit a technical retry'))
    renderer.fail = False
    retry = service.experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='retry'))
    finished = finish(service, retry)
    assert finished['state'] == 'completed'
    assert len([doc for doc, _ in author.uploads if doc['work_kind'] == 'reproduction']) == 1


def test_unknown_submission_is_reconciled_without_second_author_call(system, real_mp4):
    service, author, reviewer, renderer = system
    project, _ = start(service, real_mp4)
    author.lose_submit = True
    service.advance('developer', project['latest_experiment_id'])
    assert service.get('developer', project['id'])['state'] == 'submission_unknown'
    assert len(author.submissions) == 1
    author.lose_submit = False
    finished = finish(service, project)
    assert finished['state'] == 'completed'
    assert len([doc for doc, _ in author.uploads if doc['work_kind'] == 'reproduction']) == 1


def test_stop_preserves_intent_and_source_only_cannot_be_adopted(system, real_mp4):
    service, author, reviewer, renderer = system
    project, _ = start(service, real_mp4)
    service.advance('developer', project['latest_experiment_id'])
    service.advance('developer', project['latest_experiment_id'])
    candidate = service.get('developer', project['id'])['candidates'][0]
    service.stop('developer', project['id'])
    stopped = finish(service, project)
    assert stopped['state'] == 'cancelled' and stopped['experiments'][0]['stop_proven']
    assert renderer.calls == []
    with pytest.raises(ServiceError):
        service.adopt('developer', project['id'], dict(candidate_id=candidate['id']))


def test_manual_capture_keeps_unverified_motion_audio_and_never_publishes(system, real_mp4):
    service, author, reviewer, renderer = system
    project, _ = start(service, real_mp4)
    finish(service, project)
    calls = len(author.submissions) + len(reviewer.submissions)
    records = service.capture_wikiskill_records('developer')
    works = [row['content'] for row in records if row['kind'] == 'video_learning_work']
    assert len(works) == 2 and all(row['audio_verified'] is False for row in works)
    assert all(row['continuous_motion_verified'] is False for row in works)
    assert calls == len(author.submissions) + len(reviewer.submissions)
    assert service.get('developer', project['id'])['published'] is False


def test_analysis_is_durable_budgeted_idempotent_and_never_starts_creation(system, real_mp4):
    service, author, reviewer, renderer = system
    ref, _ = upload(service, real_mp4)
    body = dict(request_id=uuid4().hex, goal='Find a short visible effect worth learning.', max_seconds=600)
    analysis = service.analyze_reference('developer', ref['id'], body)
    assert analysis['state'] == 'queued' and analysis['creation_started'] is False
    assert service.analyze_reference('developer', ref['id'], body)['id'] == analysis['id']
    assert not author.submissions and not reviewer.submissions and not renderer.calls
    assert not service.catalogue('developer')['projects']
    assert service.pending() == [dict(owner='developer', id=analysis['id'])]
    service.advance('developer', analysis['id'])
    result = service.advance('developer', analysis['id'])
    assert result['state'] == 'completed', result
    assert result['candidates'][0]['title'] == 'Trajectory changes'
    assert result['observation']['coverage']['unique_presentation_frames'] <= 24
    assert result['continuous_motion_verified'] is result['audio_verified'] is False
    assert len(reviewer.submissions) == 1 and not author.submissions and not renderer.calls
    catalogue = service.catalogue('developer')
    assert not catalogue['projects'] and catalogue['analyses'][0]['id'] == analysis['id']
    assert catalogue['references'][0]['id'] == ref['id']
    service.get_analysis('developer', ref['id'], analysis['id'])
    assert len(reviewer.submissions) == 1
    with pytest.raises(ServiceError) as error:
        service.get_analysis('other-owner', ref['id'], analysis['id'])
    assert error.value.status == 404


def test_analysis_unknown_submit_reconciles_original_and_stop_waits_for_proof(system, real_mp4):
    service, author, reviewer, renderer = system
    ref, _ = upload(service, real_mp4)
    analysis = service.analyze_reference('developer', ref['id'], dict(request_id=uuid4().hex))
    reviewer.lose_submit = True
    service.advance('developer', analysis['id'])
    assert service.get_analysis('developer', ref['id'], analysis['id'])['state'] == 'submission_unknown'
    reviewer.lose_submit = False
    result = service.advance('developer', analysis['id'])
    assert result['state'] == 'completed' and len(reviewer.submissions) == 1
    second = service.analyze_reference('developer', ref['id'], dict(request_id=uuid4().hex))
    reviewer.active = True
    service.advance('developer', second['id'])
    stopping = service.stop_analysis('developer', ref['id'], second['id'])
    assert stopping['stop_proven'] is False
    service.advance('developer', second['id'])
    stopped = service.advance('developer', second['id'])
    assert stopped['state'] == 'cancelled' and stopped['stop_proven'] is True
    assert not author.submissions and not renderer.calls


def test_continue_compares_three_real_frame_sequences_while_fresh_excludes_old_source(system, real_mp4):
    service, author, reviewer, renderer = system
    project, _ = start(service, real_mp4)
    finished = finish(service, project)
    selected = finished['best_candidate_id']
    continued = service.experiment('developer', project['id'], dict(request_id=uuid4().hex,
        mode='continue', base_candidate_id=selected, feedback='Improve the approach phase.',
        use_wikiskill=False))
    finish(service, continued)
    doc = next(document for document, _ in reversed(reviewer.uploads)
        if document['work_kind'] == 'reproduction')
    assert len(doc['observation']['frames']) == 24
    assert {frame['purpose'] for frame in doc['observation']['frames']} == {'reference', 'output', 'baseline'}
    author_doc = next(document for document, _ in reversed(author.uploads)
        if document['work_kind'] == 'reproduction')
    assert author_doc['base_source'] is not None
    assert any(frame['purpose'] == 'baseline' for frame in author_doc['observation']['frames'])
    fresh = service.experiment('developer', project['id'], dict(request_id=uuid4().hex,
        mode='fresh', feedback='Try a new visual mechanism.', use_wikiskill=False))
    service.advance('developer', fresh['latest_experiment_id'])
    fresh_doc = author.uploads[-1][0]
    assert fresh_doc['base_source'] is None and fresh_doc['learned_method'] is None
    assert {frame['purpose'] for frame in fresh_doc['observation']['frames']} == {'reference'}


def test_selected_analysis_identity_and_exact_segment_are_frozen_in_project(system, real_mp4):
    service, author, reviewer, renderer = system
    ref, _ = upload(service, real_mp4)
    analysis = service.analyze_reference('developer', ref['id'], dict(request_id=uuid4().hex))
    service.advance('developer', analysis['id'])
    service.advance('developer', analysis['id'])
    body = dict(request_id=uuid4().hex, title='Selected segment', goal='Learn a selected effect.',
        reference_ids=[ref['id']], width=320, height=180, start_seconds=0, end_seconds=2,
        analysis_id=analysis['id'], segment_index=0, use_wikiskill=False)
    project = service.start('developer', body)
    selection = project['experiments'][0]['frozen']['analysis_selection']
    assert selection['analysis_id'] == analysis['id'] and selection['segment_index'] == 0
    assert selection['candidate']['title'] == 'Trajectory changes'
    with pytest.raises(ServiceError, match='selection_range_changed'):
        service.start('developer', dict(body, request_id=uuid4().hex, start_seconds=.5, end_seconds=1.5))
