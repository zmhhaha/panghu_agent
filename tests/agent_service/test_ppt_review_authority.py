"""A review candidate needs specific website authority, never a published flag."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from agent_service.profiles.ppt import _revision_base


def base():
    return {'version_id': str(uuid4()), 'project_id': str(uuid4()),
            'checkpoint_sha256': 'c' * 64, 'manifest_sha256': 'a' * 64,
            'status': 'published', 'eligible_as_revision_base': True}


def review():
    return {**base(), 'status': 'review', 'eligible_as_revision_base': False,
            'review_authority': {'schema': 'ppt-review-authority.v1', 'target_status': 'review',
                'checkpoint_manifest_sha256': 'b' * 64, 'review_binding_sha256': 'd' * 64,
                'review_revision': 0}}


def validate(value):
    _revision_base(value, value['project_id'], value['checkpoint_sha256'])


def test_specific_review_authority_allows_feedback_before_any_human_records():
    value = review(); original = deepcopy(value)
    validate(value)
    assert value == original and value['eligible_as_revision_base'] is False
    value['review_authority']['review_revision'] = 3
    validate(value)


def test_published_base_remains_supported_and_does_not_accept_review_authority():
    value = base(); validate(value)
    value['review_authority'] = review()['review_authority']
    with pytest.raises(ServiceError):
        validate(value)


@pytest.mark.parametrize('change', ['authority_missing', 'promoted', 'target_published', 'negative_revision',
    'boolean_revision', 'missing_binding', 'altered_checkpoint', 'other_project', 'extra_authority'])
def test_review_cannot_gain_authority_from_status_or_unbound_metadata(change):
    value = review(); project, checkpoint = value['project_id'], value['checkpoint_sha256']
    if change == 'authority_missing': del value['review_authority']
    elif change == 'promoted': value['eligible_as_revision_base'] = True
    elif change == 'target_published': value['review_authority']['target_status'] = 'published'
    elif change == 'negative_revision': value['review_authority']['review_revision'] = -1
    elif change == 'boolean_revision': value['review_authority']['review_revision'] = True
    elif change == 'missing_binding': del value['review_authority']['review_binding_sha256']
    elif change == 'altered_checkpoint': value['checkpoint_sha256'] = 'e' * 64
    elif change == 'other_project': value['project_id'] = str(uuid4())
    else: value['review_authority']['published'] = True
    with pytest.raises(ServiceError):
        _revision_base(value, project, checkpoint)
