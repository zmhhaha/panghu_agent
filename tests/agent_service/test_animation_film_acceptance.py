"""Synthetic records test the distinct film suite, never confer live admission."""
import hashlib

import pytest

from agent_service.contracts import ServiceError
from agent_service.deploy import animation_film_acceptance as film
from agent_service.deploy import animation_production_acceptance as old
from agent_service.profiles.video_animation_production import FILM_PROFILE
from test_animation_production_acceptance import make_capture, read, edit, SOURCE, RENDER_IMAGE


@pytest.fixture
def capture(tmp_path, monkeypatch):
    return make_capture(tmp_path, monkeypatch, film=True)


def assess(capture):
    return film.assess_candidate(capture['directory'], source_root=SOURCE,
        renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE)


def test_film_suite_binds_exact_context_handoff_native_capture_and_keeps_old_authority(capture):
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (capture['directory'], capture['renderer']) for path in folder.rglob('*') if path.is_file()}
    host, report = assess(capture)
    assert report['suite'] == film.SUITE == 'video.animation-production-contract.v2'
    assert report['profile'] == host['profile']['name'] == FILM_PROFILE
    assert report['schema'] == 'video-animation-film-production-independent-review.v1'
    assert report['film_context_binding']['film_sha256'] == capture['package']['input_document']['film_context']['film_sha256']
    assert report['film_context_binding']['current_scene_index'] == 0
    assert report['film_context_binding']['predecessor_sha256'] is None
    assert report['renderer_proof']['schema'] == 'video.animation-film-production-captured-render-proof.v1'
    assert 'original_v4_film_input' in report['renderer_proof']['scope']
    assert 'whole_film_continuity_observed' in report['not_claimed']
    assert report['model_requests'] == report['uid_allocations'] == 0
    assert report['acceptance_created'] is host['profile']['enabled'] is False
    assert before == {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (capture['directory'], capture['renderer']) for path in folder.rglob('*') if path.is_file()}
    with pytest.raises(ServiceError, match='profile_not_allowed'):
        old.assess_candidate(capture['directory'], source_root=SOURCE,
            renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE)


def test_new_suite_cannot_admit_old_profile(tmp_path, monkeypatch):
    capture = make_capture(tmp_path, monkeypatch)
    with pytest.raises(ServiceError, match='profile_not_allowed'):
        assess(capture)


@pytest.mark.parametrize('name,change,code', [
    ('creative-scene-v4.json', lambda doc: doc['result']['handoff'].update(exit_state='replaced'), 'package_mismatch'),
    ('operator-summary.json', lambda doc: doc.update(lease_expiry_rejected=False), 'operator_mismatch'),
    ('candidate-evidence.json', lambda doc: doc['model_calls'][0].update(status=500), 'model_calls_missing'),
])
def test_rehashed_context_or_failed_execution_cannot_bypass_exact_capture(capture, name, change, code):
    edit(capture['root'], name, change)
    with pytest.raises(ServiceError, match=code):
        assess(capture)


def test_new_admission_uses_only_film_suite_and_never_enables_routes(capture, tmp_path, monkeypatch):
    from agent_service import worker_entry
    monkeypatch.setattr(worker_entry, '_read_operator_json', lambda path, **kwargs: read(path))
    output = tmp_path / 'film-admitted'
    summary = film.review_candidate(capture['directory'], output, source_root=SOURCE,
        renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE)
    approval = read(output / 'binding-approvals.json')[0]
    target = output / approval['path']
    host = read(target / 'config.json')
    assert approval['acceptance']['suite'] == summary['suite'] == film.SUITE
    assert summary['profile'] == FILM_PROFILE and summary['enabled'] is False
    assert summary['schema'] == 'animation-film-production-technical-admission.v1'
    assert worker_entry.accepted_host(worker_entry.HostConfig.model_validate(host), target / 'acceptance.json')
    assert host['profile']['enabled'] is False
    assert all(row['enabled'] is False for row in host['execution_catalog']['routes'])


def test_rejected_film_capture_writes_no_partial_admission(capture, tmp_path):
    output = tmp_path / 'film-rejected'
    edit(capture['root'], 'operator-summary.json', lambda doc: doc.update(profile='video.animation-production.v1'))
    with pytest.raises(ServiceError, match='operator_mismatch'):
        film.review_candidate(capture['directory'], output, source_root=SOURCE,
            renderer_dir=capture['renderer'], renderer_image=RENDER_IMAGE)
    assert not output.exists()
