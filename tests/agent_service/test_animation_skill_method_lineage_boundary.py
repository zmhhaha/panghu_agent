"""Exercise initial plan/create and actual stored parent boundaries unchanged."""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_service.animation_skills_contract import build_skill_package
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_skill_method as contract
from video_workbench.animation_method_store import AnimationMethodStore
from test_animation_skill_method_profile import context, output


def package_version(method, version):
    package = method['skill_package']
    method['skill_package'] = build_skill_package(**{
        key: deepcopy(package[key]) for key in
        ('skill_id', 'kind', 'files', 'runtime_requirements')}, version=version)


def test_first_create_uses_unpersisted_plan_draft_at_version_one(tmp_path):
    planned_input = context(tmp_path, 'plan')
    planned = output(planned_input)
    contract.validate_result(planned, digest(planned_input), planned_input)
    creation = deepcopy(planned_input)
    creation.update(mode='create', methods=deepcopy(planned['methods']))
    assert all('version_id' not in row and 'method_id' not in row for row in creation['methods'])
    created = output(creation)
    assert contract.validate_result(created, digest(creation), creation) == created
    assert created['methods'][0]['skill_package']['version'] == 1
    assert 'method_id' not in created['methods'][0] and 'parent_version_id' not in created['methods'][0]
    package_version(created['methods'][0], 2)
    original = canonical_bytes(created)
    with pytest.raises(ServiceError, match='animation_skill_method_lineage_mismatch'):
        contract.validate_result(created, digest(creation), creation)
    assert canonical_bytes(created) == original


def test_only_exact_persisted_parent_permits_increment(tmp_path):
    creation = context(tmp_path)
    initial = output(creation)['methods'][0]
    store = AnimationMethodStore(tmp_path / 'methods.sqlite3')
    parent = store.create_method('fixture-owner', initial, 'fixture-first-method')
    creation['methods'] = [parent]
    successor = output(creation)
    method = successor['methods'][0]
    method.update(method_id=parent['method_id'], parent_version_id=parent['version_id'])
    package_version(method, parent['version'] + 1)
    assert contract.validate_result(successor, digest(creation), creation) == successor
    stored_next = store.create_method('fixture-owner', method, 'fixture-successor')
    assert stored_next['parent_version_id'] == parent['version_id']
    creation['methods'] = [stored_next]
    method.update(parent_version_id=stored_next['version_id'])
    package_version(method, stored_next['version'] + 1)
    successor['input_sha256'] = digest(creation)
    assert contract.validate_result(successor, digest(creation), creation) == successor
    for incorrect_parent in (parent['version_id'], parent['method_id'], 'invented-parent'):
        changed = deepcopy(successor)
        changed['methods'][0]['parent_version_id'] = incorrect_parent
        with pytest.raises(ServiceError):
            contract.validate_result(changed, digest(creation), creation)
    for incorrect_version in (stored_next['version'], stored_next['version'] + 2):
        changed = deepcopy(successor)
        package_version(changed['methods'][0], incorrect_version)
        with pytest.raises(ServiceError, match='animation_skill_method_lineage_mismatch'):
            contract.validate_result(changed, digest(creation), creation)


def test_actual_agent_prompt_explains_planning_draft_and_persisted_parent(tmp_path):
    prompt = contract.VideoAnimationSkillMethodProfile().build_prompt({'input_sha256': digest(context(tmp_path))})
    assert 'An input method without `version_id` is an unpersisted planning draft' in prompt
    assert 'at version=1 and omit both method_id and parent_version_id' in prompt
    assert "input parent's top-level version plus one" in prompt
    assert 'not its historical parent' in prompt
    assert 'exact input-backed lineage' in prompt


def test_published_v3_bytes_remain_frozen_and_new_guidance_has_new_identity():
    previous = contract.BUNDLE_ROOT.parent / 'animation-method-v3'
    raw = (previous / 'manifest.json').read_bytes()
    frozen_sha = 'e8be4336b8ba12a2e3af1fd8c6c350e349fafc25014e3d4e3cf5311eeb38dcc8'
    assert hashlib.sha256(raw).hexdigest() == frozen_sha
    manifest = json.loads(raw)
    assert manifest['bundle_revision'] == 'animation-method-v3'
    for row in manifest['local_files']:
        assert hashlib.sha256((previous / row['path']).read_bytes()).hexdigest() == row['sha256']
    assert contract.BUNDLE_REVISION == 'animation-method-v4'
    assert contract.BUNDLE_MANIFEST_SHA256 != frozen_sha
    assert json.loads((contract.BUNDLE_ROOT / 'manifest.json').read_bytes())['bundle_revision'] == contract.BUNDLE_REVISION
    assert set(contract.verified_bundle()) == contract.BUNDLE_FILES
