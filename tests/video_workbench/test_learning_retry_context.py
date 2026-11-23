"""Technical retries retain the actual failed stage's comparison lineage."""
from copy import deepcopy
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.video_learning_service import VideoLearningService
from test_picture_learning_service import Gateway as PictureGateway, Renderer as PictureRenderer
from test_picture_learning_service import start as picture_start, finish as picture_finish
from test_video_learning_service import Gateway as VideoGateway, Renderer as VideoRenderer
from test_video_learning_service import start as video_start, finish as video_finish, real_mp4


@pytest.mark.parametrize('kind', ['picture', 'video'])
@pytest.mark.parametrize('failed_stage', ['review', 'render'])
def test_retry_freezes_effective_multiround_source_pixels_and_review_context(tmp_path, real_mp4, kind, failed_stage):
    if kind == 'picture':
        author, reviewer, renderer = PictureGateway(), PictureGateway(), PictureRenderer(tmp_path / 'renders')
        service = PictureLearningService(tmp_path / 'library', author_gateway=author,
            review_gateway=reviewer, renderer=renderer)
        start = lambda: picture_start(service, max_rounds=1)
        finish = lambda project: picture_finish(service, project)
    else:
        author, reviewer, renderer = VideoGateway(), VideoGateway(), VideoRenderer(tmp_path / 'renders', real_mp4)
        service = VideoLearningService(tmp_path / 'library', author_gateway=author,
            review_gateway=reviewer, renderer=renderer)
        start = lambda: video_start(service, real_mp4, max_rounds=1)
        finish = lambda project: video_finish(service, project)
        # Different actual pixels distinguish the frozen initial baseline from
        # later reviewed works. Container metadata changes alone are insufficient.
        variants = []
        for index in range(6):
            target = tmp_path / ('variant-%d.mp4' % index)
            response = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=320x180:rate=24:duration=2', '-vf', 'hue=h=%s' % (index * 41),
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', '-threads', '1', str(target)],
                capture_output=True, timeout=30)
            assert response.returncode == 0
            variants.append(target.read_bytes())
        base_video_render = renderer.render
        def variant_render(*args, **kwargs):
            renderer.raw = variants[min(len(renderer.calls), len(variants) - 1)]
            return base_video_render(*args, **kwargs)
        renderer.render = variant_render

    author_collect = author.collect
    def distinct_source(job_id, input_sha256, document):
        result = author_collect(job_id, input_sha256, document)
        result['source']['files']['scene.js'] += '\n// unique authored revision %d' % len(author.uploads)
        result['source']['source_sha256'] = source_sha256(result['source'])
        return result
    author.collect = distinct_source

    initial, _ = start()
    initial = finish(initial)
    root_candidate = initial['best_candidate_id']
    target = service.experiment('developer', initial['id'], dict(request_id=uuid4().hex,
        mode='continue', base_candidate_id=root_candidate, feedback='Improve the movement or brush structure.',
        use_wikiskill=False, max_rounds=3, max_seconds=900))
    target_id = target['latest_experiment_id']
    review_count, injected = 0, False
    reviewer_collect = reviewer.collect
    def fail_third_review(job_id, input_sha256, document):
        nonlocal review_count, injected
        if document['experiment_id'] == target_id and document['mode'] == 'review' and document['work_kind'] == 'reproduction':
            review_count += 1
            if failed_stage == 'review' and review_count == 3 and not injected:
                injected = True
                raise ServiceError('test_third_review_contract_rejected', 422)
        result = reviewer_collect(job_id, input_sha256, document)
        if document['work_kind'] == 'reproduction' and document['mode'] == 'review':
            result['review'].update(score=30 + review_count * 10, stop_recommended=False)
        return result
    reviewer.collect = fail_third_review
    if failed_stage == 'render':
        render_original = renderer.render
        def fail_third_render(operation_id, source, document, **kwargs):
            nonlocal injected
            if document['experiment_id'] == target_id and document['mode'] == 'create':
                # Render operation's input is frozen after the third author;
                # it already carries the prior two rounds' comparison context.
                run = service.store.read('experiments', 'developer', target_id)
                if run['round'] == 3 and not injected:
                    injected = True
                    renderer.fail = True
                    try:
                        return render_original(operation_id, source, document, **kwargs)
                    finally:
                        renderer.fail = False
            return render_original(operation_id, source, document, **kwargs)
        renderer.render = fail_third_render

    failed = finish(target)
    assert failed['state'] == 'failed' and injected
    original = service.store.read('experiments', 'developer', target_id)
    assert original['round'] == 3 and original['stage'] == failed_stage and original['stop_proven']
    prior_best = next(candidate for candidate in original['candidates'] if candidate['id'] == original['best_candidate_id'])
    assert prior_best['round'] == 2
    failed_input = next(service.store.read('operations', 'developer', identity)['input']
        for identity in reversed(original['operation_ids'])
        if service.store.read('operations', 'developer', identity)['stage'] == failed_stage)
    frozen_initial_baseline = original['frozen']['base_candidate_id']
    assert frozen_initial_baseline == root_candidate
    assert failed_input['selection']['base_candidate_id'] == prior_best['id'] != root_candidate
    expected_baseline = {row['sha256'] for row in failed_input['observation']['frames'] if row['purpose'] == 'baseline'}
    if kind == 'picture':
        assert expected_baseline == {prior_best['observed_sha256']}
    else:
        assert expected_baseline == {row['sha256'] for row in prior_best['observation']['frames']}
    active = next(row for row in original['candidates'] if row['id'] == original['active_candidate_id'])
    author_calls, render_calls = len(author.submissions), len(renderer.calls)
    retry = service.experiment('developer', initial['id'], dict(request_id=uuid4().hex, mode='retry'))
    retried = service.store.read('experiments', 'developer', retry['latest_experiment_id'])
    assert retried['frozen'] == original['frozen']
    assert len(retried['candidates']) == 1
    assert retried['candidates'][0]['source_sha256'] == active['source_sha256']
    assert prior_best['id'] in retried['inherited_candidate_ids']
    assert len(author.submissions) == author_calls
    service.advance('developer', retried['id'])
    current = service.store.read('experiments', 'developer', retried['id'])
    actual_input = service.store.read('operations', 'developer', current['operation_ids'][0])['input']
    assert actual_input == dict(failed_input, experiment_id=retried['id'])
    assert len(author.submissions) == author_calls
    if failed_stage == 'review':
        assert len(renderer.calls) == render_calls
    else:
        assert len(renderer.calls) == render_calls + 1
    # Complete the recovered technical step, then let the normal transfer
    # continue. Its method must still be selected from the effective lineage.
    result = finish(retry)
    assert result['state'] == 'completed', result
    retry_run = result['experiments'][-1]
    assert retry_run['transfer_status'] == 'reviewed'
    assert len(retry_run['candidates']) == 2
    assert retry_run['candidates'][1]['work_kind'] == 'transfer'
    assert service.store.read('experiments', 'developer', retried['id'])['technical_retry_context']['consumed'] is True
