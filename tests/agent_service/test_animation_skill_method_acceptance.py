"""Synthetic Skill-role captures exercise admission; they grant no live readiness."""
from copy import deepcopy

import pytest

from agent_service.animation_skills_contract import build_skill_package
from agent_service.contracts import ServiceError
from agent_service.deploy import animation_method_acceptance as admission
from agent_service.deploy.animation_method_catalog import (
    measure_animation_method_bundle, prepare_animation_method_catalog)
from agent_service.profiles import video_animation_skill_method as skill
from agent_service.worker_entry import HostConfig, accepted_host
from video_workbench.animation_knowledge import AnimationKnowledgeStore
import test_animation_method_acceptance as captures
from test_animation_review_catalog import IMAGE, model, public_config


@pytest.fixture
def skill_capture(tmp_path, monkeypatch):
    original_document, original_output = captures.document, captures.output
    knowledge = AnimationKnowledgeStore(tmp_path / 'synthetic-knowledge.sqlite3')
    knowledge.record_experience('synthetic-operator', 'brief', 'synthetic-admission',
        {'goal': 'SYNTHETIC request: retain visible object identity during filtering.'})
    snapshot = knowledge.snapshot('synthetic-operator')

    def document(review=True):
        return dict(original_document(review), knowledge_snapshot=deepcopy(snapshot), research_records=[])

    def output(value):
        result = dict(original_output(value), knowledge_proposals=[])
        for method in result['methods']:
            method.update(schema='video.animation-method.v2', research_record_ids=[],
                skill_package=build_skill_package(skill_id='synthetic-retained-identity', version=1,
                    kind='expression', runtime_requirements={'engines': ['html-svg']},
                    files={'SKILL.md': '---\nname: retained-identity\ndescription: Preserve visual identity during filtering.\n---\n'
                        'Use for a visible collection. Adapt geometry and structure to the new content. '
                        'Keep retained objects recognizable; avoid teleporting or premature connectors.'}))
        return result

    def prepare():
        manifests, _ = measure_animation_method_bundle(skills=True)
        return prepare_animation_method_catalog(public_config(), worker_image=IMAGE,
            source_pool_id='existing-pool', review_model_policy=model(),
            resource_manifests=manifests, skills=True)

    monkeypatch.setattr(captures, 'method', skill)
    monkeypatch.setattr(captures, 'document', document)
    monkeypatch.setattr(captures, 'output', output)
    monkeypatch.setattr(captures, 'prepare', prepare)
    # Windows cannot attest actual root-owned mount permissions. Reuse the
    # explicitly synthetic reader; no admission returned here is deployable.
    from agent_service import worker_entry
    monkeypatch.setattr(worker_entry, '_read_operator_json', lambda path, **kwargs: captures.read(path))
    return captures.capture


@pytest.mark.parametrize('review', [False, True])
def test_skill_roles_require_their_own_capture_and_create_only_disabled_v2_approval(tmp_path, skill_capture, review):
    directory, root, document, result = skill_capture(tmp_path, review=review)
    kwargs = {}
    if not review:
        renderer = captures.renderer_capture(tmp_path / 'render', document, result)
        kwargs.update(renderer_dir=renderer, renderer_image=captures.RENDER_IMAGE)
    summary = admission.review_candidate(directory, tmp_path / 'admitted', **kwargs)
    approval = captures.read(tmp_path / 'admitted/binding-approvals.json')[0]
    target = tmp_path / 'admitted' / approval['path']
    report = captures.read(target / 'acceptance-evidence.json')
    config = HostConfig.model_validate(captures.read(target / 'config.json'))
    assert accepted_host(config, target / 'acceptance.json')
    role = 'review' if review else 'author'
    assert report['profile'] == f'video.animation-method-{role}.v2'
    assert approval['acceptance']['suite'] == f'video.animation-method-{role}-contract.v2'
    assert summary['enabled'] is False and not config.profile.enabled
    assert summary['model_requests'] == summary['uid_allocations'] == 0
    assert report['technical_host_admission_only'] is True
    if review:
        assert report['model_frame_transport']['evidence_scope'] == 'completed_upstream_transport'
        assert report['model_frame_transport']['motion_observed'] is False
        assert report['renderer_proof'] is None
    else:
        assert report['renderer_proof']['container_absent_verified'] is True
        assert report['renderer_proof']['subjective_quality_approved'] is False
    assert 'business_user_approval' in report['not_claimed']


def test_skill_author_cannot_be_admitted_from_generated_source_without_stopped_render(tmp_path, skill_capture):
    directory, _, _, _ = skill_capture(tmp_path, review=False)
    with pytest.raises(ServiceError, match='actual_render_required'):
        admission.review_candidate(directory, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()


def test_skill_review_requires_exact_completed_image_transport(tmp_path, skill_capture):
    directory, root, _, _ = skill_capture(tmp_path)
    captures.edit(root, 'candidate-evidence.json',
        lambda value: value['model_image_evidence'].update(evidence_scope='declared_only'))
    with pytest.raises(ServiceError, match='image_evidence_missing'):
        admission.review_candidate(directory, tmp_path / 'rejected')
    assert not (tmp_path / 'rejected').exists()
