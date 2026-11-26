"""Typed review guidance validates and never rewrites historical retry input."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_reference_learning as protocol
from agent_service.profiles.video_learning_source_handoff import SOURCE_HANDOFF
from video_workbench.video_learning_service import VideoLearningService
from test_video_learning_service import system, real_mp4, start, finish, Gateway, Renderer


def test_review_and_transfer_plan_examples_bind_actual_input_and_reject_object_fields(system, real_mp4):
    service, author, reviewer, renderer = system
    project, _ = start(service, real_mp4)
    for _ in range(5):
        run = service.store.read('experiments', 'developer', project['latest_experiment_id'])
        if run['stage'] == 'review':
            break
        service.advance('developer', run['id'])
    assert run['stage'] == 'review'
    for mode in ('review', 'transfer_plan'):
        document = service._document(run, mode)
        protocol.validate_input(document)
        contract = document['constraints']['output_contract']
        assert document['feedback'] == run['frozen']['feedback']
        assert 'read_inputs.details.input_sha256' in contract['binding']['input_sha256']
        example = deepcopy(contract['result_example'])
        example['input_sha256'] = digest(document)
        assert example['mode'] == mode
        assert (example['transfer_task'] is not None) == (mode == 'transfer_plan')
        # The complete example is a real valid result once bound to the exact
        # frozen input, rather than only a prose description of a schema.
        protocol.validate_result(example, digest(document), document)
        for field, wrong in (
            ('issues', [{'description': 'An object is invalid here.'}]),
            ('comparison', {'reference': 'wrong object shape'}),
            ('reference_fidelity', {'score': 5}),
            ('work_quality', ['wrong array shape']),
            ('transfer', {'status': 'wrong object shape'}),
            ('motion', 'verified'), ('audio', 'verified')):
            malformed = deepcopy(example)
            malformed['review'][field] = wrong
            with pytest.raises(ServiceError):
                protocol.validate_result(malformed, digest(document), document)
        if mode == 'transfer_plan':
            invalid_plan = deepcopy(example)
            invalid_plan['transfer_task'] = None
            with pytest.raises(ServiceError):
                protocol.validate_result(invalid_plan, digest(document), document)
        contract['review_required']['issues'] = 'mutated caller copy'
        assert service._document(run, mode)['constraints']['output_contract']['review_required']['issues'] == 'string[]; NEVER object[]'
    author_input = author.uploads[0][0]
    assert author_input['source_handoff'] == SOURCE_HANDOFF
    instructions = author_input['constraints']['output_contract']['instructions'][0]
    assert 'output-source/scene.html' in instructions and 'source.file_names' in instructions


class OldVideoLearningService(VideoLearningService):
    """The historical behavior before generic review-format guidance existed."""
    def _document(self, run, mode):
        result = super()._document(run, mode)
        result['constraints'].pop('output_contract', None)
        return result


def test_historical_review_retry_does_not_add_new_output_contract(tmp_path, real_mp4):
    author, reviewer = Gateway(), Gateway()
    renderer = Renderer(tmp_path / 'renders', real_mp4)
    root = tmp_path / 'videos'
    old = OldVideoLearningService(root, author_gateway=author, review_gateway=reviewer, renderer=renderer)
    project, _ = start(old, real_mp4, max_rounds=1)
    original_collect = reviewer.collect
    def rejected_review(*args, **kwargs):
        raise ServiceError('historical_review_invalid_object_shape', 422)
    reviewer.collect = rejected_review
    failed = finish(old, project)
    assert failed['state'] == 'failed'
    original = old.store.read('experiments', 'developer', failed['latest_experiment_id'])
    failed_input = old.store.read('operations', 'developer', original['operation_ids'][-1])['input']
    assert original['stage'] == 'review' and 'output_contract' not in failed_input['constraints']
    reviewer.collect = original_collect
    current = VideoLearningService(root, author_gateway=author, review_gateway=reviewer, renderer=renderer)
    retry = current.experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='retry'))
    author_count, render_count = len(author.submissions), len(renderer.calls)
    current.advance('developer', retry['latest_experiment_id'])
    submitted = reviewer.uploads[-1][0]
    assert submitted == dict(failed_input, experiment_id=retry['latest_experiment_id'])
    assert 'output_contract' not in submitted['constraints']
    assert len(author.submissions) == author_count and len(renderer.calls) == render_count
    # New documents do get the guide; only replay of the historical failed
    # operation retains its original protocol data unchanged.
    retry_run = current.store.read('experiments', 'developer', retry['latest_experiment_id'])
    assert 'output_contract' in current._document(retry_run, 'review')['constraints']
