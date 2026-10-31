"""Neutral PPT boundary checks with real DB transactions and ordinary artifacts.

The in-memory client is explicitly a test double. It never launches an engine,
claims a live acceptance record, or contacts a model/provider.
"""
from __future__ import annotations

import copy
from datetime import timedelta, timezone
import hashlib
import json
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agent_service.artifacts import frozen_manifest
from agent_service.contracts import ExecutionEnvelope, JobRequest, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles.ppt import PPTProfile
from ppt_service.control.agent_connector import AgentConnector, AgentConnectorSettings, build_agent_task
from ppt_service.control.agent_validation import AgentValidationReceipt, validate_private_candidate
from ppt_service.control.execution import ControlError, RuntimeConfig
from ppt_service.server.auth import Identity
from ppt_service.server.migrate import migrate
from ppt_service.server.models import AgentOutbox, Asset, Checkpoint, ExecutionSlot, Job, JobEvent, Plan, Project, Version, now
from ppt_service.server.repository import Repository


def identifier():
    return str(uuid4())


def write(root, name, content):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def stage1_plan():
    return dict(stage='stage1', status='awaiting_confirmation',
                **{key: {'value': value} for key, value in {
                    'title': 'Public source guide', 'primary_language': 'zh-CN',
                    'audience': 'Developers', 'communication_intent': 'Explain the source',
                    'template_choice': 'free_design'}.items()})


def stage2_plan():
    pages = {'pages': [{'ordinal': n, 'title': f'Page {n}', 'purpose': 'Explain public source'}
                       for n in range(1, 4)]}
    return {'page_plan': pages, 'design': {'directions': [
        {'id': key, 'name': f'Direction {key}', 'note': f'Layout {key}',
         'page_plan': pages, 'production_parameters': {'language': 'zh-CN', 'aspect_ratio': '16:9'}}
        for key in ('A', 'B', 'C')]}}


def envelope(task, *, engine='pi'):
    profile = PPTProfile(task['phase'])
    return ExecutionEnvelope(job_id=identifier(), attempt_id=identifier(), fence=1,
        engine=engine, profile=profile.name, profile_revision='v1', runtime_revision='synthetic-v1',
        image_digest='sha256:' + 'a' * 64, deadline_at=time.time() + 600,
        lease_expires_at=time.time() + 30, task=task, task_schema=profile.task_schema,
        output_schema=profile.output_schema, **profile.limits.as_dict())


def plan_artifacts(workspace, task, *, checkpoint_files=(), engine='pi'):
    profile = PPTProfile(task['phase'])
    for item in checkpoint_files:
        write(workspace / 'base', item.name, item.content())
    (workspace / 'project').mkdir(parents=True)
    for asset in task['input_snapshot']['assets']:
        write(workspace, 'inputs/' + asset['id'] + '.md', b'# Public source\n')
    profile.prepare_project(workspace, task)
    write(workspace, 'project/deck/source.md', b'# Public source\n')
    content = (workspace / 'project/deck/source.md').read_bytes()
    manifest = {'schema_version': 1, 'phase': task['phase'], 'status': 'awaiting_confirmation',
        'input_snapshot_sha256': task['input_snapshot_sha256'], 'project_root': 'deck',
        'plan': stage1_plan() if task['phase'] == 'plan_initial' else stage2_plan(),
        'questions': [], 'slides': [], 'quality': {}, 'provenance': {},
        'artifacts': [{'path': 'source.md', 'role': 'source', 'media_type': 'text/markdown',
                       'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}]}
    write(workspace, 'result/manifest.json', canonical_bytes(manifest))
    request = envelope(task, engine=engine)
    files = profile.collect_output(workspace, request, StopEvidence(request.attempt_id, True, ('synthetic-pod',)))
    return request, files


@pytest.fixture
def database(tmp_path):
    engine = create_engine('sqlite://')
    migrate(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    owner = Identity('https://fixture.example', 'owner')
    with sessions.begin() as session:
        project = Project(issuer=owner.issuer, subject=owner.sub, title='Public source guide',
                          settings={'page_count': 3}, external_model_ack=True)
        session.add(project); session.flush()
        asset_id = identifier()
        key = f'projects/{project.id}/assets/{asset_id}/original'
        raw = b'# Public source\n'
        write(tmp_path, key, raw)
        session.add(Asset(id=asset_id, project_id=project.id, storage_key=key,
            display_name='source.md', media_type='text/markdown', bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(), parse_status='ready'))
        session.flush()
        job = Repository(session).enqueue(project, owner, 'plan_initial', project.revision)
        project_id, job_id = project.id, job.id
    yield SimpleNamespace(sessions=sessions, owner=owner, project_id=project_id, job_id=job_id, root=tmp_path)
    engine.dispose()


class PlanningService:
    """Idempotent synthetic transport backed by the real domain collector."""
    def __init__(self, root):
        self.root = root
        self.uploads, self.jobs, self.by_key, self.artifacts, self.checkpoints = {}, {}, {}, {}, {}
        self.submissions, self.acks = [], []
        self.lookups, self.cancellations = [], []

    def upload_input(self, name, content):
        result = dict(input_id=identifier(), name=name, sha256=hashlib.sha256(content).hexdigest())
        self.uploads[result['input_id']] = result
        return result

    def submit(self, body, *, idempotency_key):
        self.submissions.append(copy.deepcopy(body))
        JobRequest.model_validate(body)
        if idempotency_key in self.by_key:
            return copy.deepcopy(self.jobs[self.by_key[idempotency_key]])
        checkpoint = body.get('checkpoint_ref')
        base = () if checkpoint is None else self.checkpoints[checkpoint['checkpoint_id']]
        env, files = plan_artifacts(self.root / identifier(), body['task'], checkpoint_files=base)
        pins = {'profile': env.profile, 'profile_revision': 'v1', 'engine': env.engine,
                'runtime_revision': 'synthetic-v1', 'image_digest': env.image_digest}
        proof = dict(stop_proven=True, attempt_id=env.attempt_id, fence=1,
                     request_sha256=env.request_sha256, result_sha256='b' * 64,
                     resource_ids=['synthetic-pod'], observed_at=time.time())
        job = dict(job_id=env.job_id, business_ref=body['business_ref'], state='completed',
                   pins=pins, attempt={'attempt_id': env.attempt_id, 'fence': 1}, stop_proof=proof)
        self.jobs[env.job_id] = job
        self.by_key[idempotency_key] = env.job_id
        self.artifacts[env.job_id] = files
        return copy.deepcopy(job)

    def get_job(self, job_id):
        return copy.deepcopy(self.jobs[job_id])

    def lookup_job(self, idempotency_key):
        self.lookups.append(idempotency_key)
        if idempotency_key not in self.by_key:
            raise ServiceError('job_not_found', 404)
        return self.get_job(self.by_key[idempotency_key])

    def cancel(self, job_id):
        self.cancellations.append(job_id)
        return self.get_job(job_id)

    def events(self, job_id, *, after, limit):
        return {'items': [], 'next_cursor': after, 'has_more': False}

    def artifact_manifest(self, job_id):
        job = self.jobs[job_id]
        rows = [dict(artifact_id=f'{job_id}:{n}', name=f.name, sha256=f.sha256, size=len(f.content()))
                for n, f in enumerate(self.artifacts[job_id])]
        return frozen_manifest(job_id, job['attempt']['attempt_id'], rows)

    def download_artifact(self, artifact_id):
        job_id, n = artifact_id.split(':')
        return self.artifacts[job_id][int(n)].content()

    def create_checkpoint(self, job_id, *, idempotency_key):
        ref = {'checkpoint_id': identifier(), 'sha256': digest({'job': job_id}), 'format': 'workspace-tree.v1'}
        self.checkpoints[ref['checkpoint_id']] = self.artifacts[job_id]
        return ref

    def acknowledge_publication(self, job_id, **kwargs):
        self.acks.append(dict(job_id=job_id, **kwargs))


class PlanningValidator:
    def validate(self, snapshot, request):
        source = hashlib.sha256((snapshot / 'result/manifest.json').read_bytes()).hexdigest()
        report = validate_private_candidate(snapshot, dict(request,
            source_manifest_sha256=source, validation_nonce=identifier()))
        return AgentValidationReceipt._issue(report, source, snapshot)


def connector(database):
    service = PlanningService(database.root / 'synthetic-service')
    control = AgentConnector(database.sessions, RuntimeConfig(database.root), service, PlanningValidator(),
                             settings=AgentConnectorSettings(enabled=True))
    return control, service


def complete_plan(control):
    assert control.tick()['status'] == 'uploading'
    assert control.tick()['status'] == 'submitted'
    assert control.tick()['status'] == 'mapped'
    assert control.tick()['status'] == 'acknowledged'


def confirm_and_enqueue(database, phase):
    with database.sessions.begin() as session:
        project = session.get(Project, database.project_id)
        plan = session.scalar(select(Plan).where(Plan.project_id == project.id).order_by(Plan.revision.desc()))
        plan.confirmed_at = now()
        plan.confirmation_selection = ({'design_mode': 'free_design', 'template_id': None}
                                       if phase == 'plan_options' else {'direction_id': 'B'})
        project.confirmed_plan_revision = plan.revision
        job = Repository(session).enqueue(project, database.owner, phase, project.revision,
                                          {'confirmation_selection': plan.confirmation_selection})
        job.checkpoint_id = plan.checkpoint_id
        return job.id


def test_planning_roundtrip_uses_two_database_confirmations_and_artifact_replay(database):
    control, service = connector(database)
    complete_plan(control)
    confirm_and_enqueue(database, 'plan_options')
    complete_plan(control)
    prepare_id = confirm_and_enqueue(database, 'prepare')
    with database.sessions() as session:
        task, ref = build_agent_task(session, session.get(Job, prepare_id))
        assert set(task['confirmations']) == {'stage1', 'stage2'}
        assert task['confirmations']['stage2']['selected_direction']['id'] == 'B'
        assert task['confirmations']['stage2']['design']['production_parameters']['language'] == 'zh-CN'
        assert task['confirmations']['stage1']['channel'] == 'website_database'
        assert task['checkpoint']['phase'] == 'plan_options' and ref['checkpoint_id'] in service.checkpoints
        assert len(session.scalars(select(Checkpoint)).all()) == 2
        assert session.get(Project, database.project_id).latest_version_id is None
        assert session.get(ExecutionSlot, 1).job_id is None
    assert all('engine' not in body for body in service.submissions)
    assert all(row['decision'] == 'candidate_received' for row in service.acks)
    assert len(service.submissions) == 2


def test_prepare_preserves_rich_database_confirmations_above_default_task_bound(database, monkeypatch):
    original_stage1, original_stage2 = stage1_plan, stage2_plan

    def rich_stage1():
        plan = original_stage1()
        plan['audience']['value'] = ('结合上传资料区分主题背景、基础概念和适用场景，面向初次学习系统架构的工程师。' * 90)
        return plan

    def rich_stage2():
        plan = original_stage2()
        for direction in plan['design']['directions']:
            direction['note'] = ('按概念、机制、示例和局限组织讲解，逐页对应原始资料并标明证据来源。' * 120)
        return plan

    monkeypatch.setitem(globals(), 'stage1_plan', rich_stage1)
    monkeypatch.setitem(globals(), 'stage2_plan', rich_stage2)
    control, service = connector(database)
    complete_plan(control)
    confirm_and_enqueue(database, 'plan_options')
    complete_plan(control)
    prepare_id = confirm_and_enqueue(database, 'prepare')
    with database.sessions() as session:
        task, ref = build_agent_task(session, session.get(Job, prepare_id))
        assert 32768 < len(canonical_bytes(task)) < 256 * 1024
        confirmations = copy.deepcopy(task['confirmations'])
        assert confirmations['stage1']['outline']['communication'] == rich_stage1()
        assert confirmations['stage2']['selected_direction']['note'] == rich_stage2()['design']['directions'][1]['note']
        assert confirmations['stage2']['selection'] == {'direction_id': 'B'}

    # Claim plus upload uses the real connector and durable outbox, but does not
    # submit prepare to even the synthetic transport. Both wire validators must
    # accept the exact same, unabridged database confirmations.
    assert control.tick() == {'status': 'uploading', 'job_id': prepare_id}
    with database.sessions() as session:
        out = session.get(AgentOutbox, prepare_id)
        assert out.request['task'] == task
        assert out.request['checkpoint_ref'] == ref
        request = JobRequest.model_validate(out.request)
        assert envelope(request.task).task['confirmations'] == confirmations
        assert out.remote_job_id is None and out.submission_started is False
        assert session.get(Project, database.project_id).confirmed_plan_revision == confirmations['stage2']['plan_revision']
        assert len(session.scalars(select(Checkpoint)).all()) == 2
        assert not session.scalars(select(Version)).all()
    assert len(service.submissions) == 2


@pytest.mark.parametrize('failure', ['task_too_large', 'request_invalid'])
def test_prepare_rejection_before_dispatch_has_safe_error_and_retains_confirmations(database, monkeypatch, failure):
    if failure == 'task_too_large':
        original_stage2 = stage2_plan

        def oversized_stage2():
            plan = original_stage2()
            for page in plan['page_plan']['pages']:
                page['body'] = '详细说明资料中的概念与证据。' * 2500
            return plan

        monkeypatch.setitem(globals(), 'stage2_plan', oversized_stage2)
    control, service = connector(database)
    complete_plan(control)
    confirm_and_enqueue(database, 'plan_options')
    complete_plan(control)
    prepare_id = confirm_and_enqueue(database, 'prepare')
    with database.sessions() as session:
        task, _ = build_agent_task(session, session.get(Job, prepare_id))
        confirmations = copy.deepcopy(task['confirmations'])
        if failure == 'task_too_large':
            assert len(canonical_bytes(task)) > 256 * 1024
    if failure == 'request_invalid':
        def invalid_request(**fields):
            return JobRequest(**dict(fields, timeout_seconds=0))
        monkeypatch.setattr('ppt_service.control.agent_connector.JobRequest', invalid_request)

    assert control._claim() is None
    code = 'execution_task_too_large' if failure == 'task_too_large' else 'execution_request_invalid'
    with database.sessions() as session:
        job = session.get(Job, prepare_id)
        assert job.status == 'failed' and job.error_code == code
        assert job.run_id is None and job.job_uid is None and job.stopped is False
        assert session.get(AgentOutbox, prepare_id) is None
        assert session.get(ExecutionSlot, 1).job_id is None
        project = session.get(Project, database.project_id)
        assert project.active_job_id is None and project.stage == 'draft' and project.latest_version_id is None
        assert project.confirmed_plan_revision == confirmations['stage2']['plan_revision']
        rebuilt, _ = build_agent_task(session, job)
        assert rebuilt['confirmations'] == confirmations
        assert len(session.scalars(select(Checkpoint)).all()) == 2
        assert not session.scalars(select(Version)).all()
        failed = session.scalar(select(JobEvent).where(JobEvent.job_id == prepare_id, JobEvent.type == 'job.failed'))
        assert failed.payload == {'error_code': code}
    assert len(service.submissions) == 2


def test_interrupted_artifact_write_resumes_download_without_resubmitting_model_job(database, monkeypatch):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    assert control.tick()['status'] == 'submitted'
    with database.sessions() as session:
        out = session.get(AgentOutbox, database.job_id)
        remote_id = out.remote_job_id
        deadline = session.get(Job, database.job_id).deadline
        target = database.root / f'projects/{database.project_id}/agent-results/{database.job_id}/{out.fencing_token}'
    first = service.artifact_manifest(remote_id)['files'][0]
    downloaded = []
    download = service.download_artifact

    def observed_download(artifact_id):
        downloaded.append(artifact_id)
        return download(artifact_id)

    monkeypatch.setattr(service, 'download_artifact', observed_download)
    fsync = os.fsync

    def interrupted_write(descriptor):
        # Simulate process death after a partial local write reached storage.
        # No ordinary exception handler can turn this into a rejected result.
        os.ftruncate(descriptor, 1)
        fsync(descriptor)
        raise SystemExit('synthetic controller interruption')

    with monkeypatch.context() as interrupted:
        interrupted.setattr(os, 'fsync', interrupted_write)
        with pytest.raises(SystemExit, match='synthetic controller interruption'):
            control.tick()
    pending = target.with_name('.' + target.name + '-download')
    assert pending.is_file() and pending.stat().st_size == 1
    assert not (target / first['name']).exists()
    with database.sessions() as session:
        job = session.get(Job, database.job_id)
        assert job.stopped is True and job.status == 'collecting'
        assert session.get(ExecutionSlot, 1).job_id == job.id
        assert not session.scalars(select(Plan)).all()

    restarted = AgentConnector(database.sessions, RuntimeConfig(database.root), service, PlanningValidator(),
        settings=AgentConnectorSettings(enabled=True))
    assert restarted.tick()['status'] == 'mapped'
    assert restarted.tick()['status'] == 'acknowledged'
    assert not pending.exists()
    assert downloaded.count(first['artifact_id']) == 2
    assert len(service.submissions) == len(service.acks) == 1
    for row in service.artifact_manifest(remote_id)['files']:
        raw = (target / row['name']).read_bytes()
        assert len(raw) == row['size'] and hashlib.sha256(raw).hexdigest() == row['sha256']
    with database.sessions() as session:
        job = session.get(Job, database.job_id)
        out = session.get(AgentOutbox, job.id)
        assert job.status == 'succeeded' and job.deadline == deadline
        assert out.remote_job_id == remote_id and out.state == 'mapped' and out.acked is True
        assert session.get(ExecutionSlot, 1).job_id is None
        assert len(session.scalars(select(Plan)).all()) == len(session.scalars(select(Checkpoint)).all()) == 1


@pytest.mark.parametrize('changed', ['unconfirmed', 'selection', 'snapshot'])
def test_following_phase_cannot_fabricate_or_rebind_confirmation(database, changed):
    control, _ = connector(database); complete_plan(control)
    jid = confirm_and_enqueue(database, 'plan_options')
    with database.sessions.begin() as session:
        plan = session.scalar(select(Plan))
        if changed == 'unconfirmed':
            plan.confirmed_at = None
        elif changed == 'selection':
            plan.confirmation_selection = {'design_mode': 'templates', 'template_id': 'other'}
        else:
            plan.input_snapshot_hash = 'c' * 64
    with database.sessions() as session, pytest.raises(ControlError, match='confirmation_lineage_mismatch'):
        build_agent_task(session, session.get(Job, jid))


@pytest.mark.parametrize('state', ['completed', 'failed'])
@pytest.mark.parametrize('changed', ['attempt', 'fence', 'resources', 'quarantine', 'pins'])
def test_terminal_remote_without_matching_stop_binding_retains_slot(database, changed, state):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    assert control.tick()['status'] == 'submitted'
    remote = next(iter(service.jobs.values()))
    remote.update(state=state, error_code='model_error')
    if changed == 'attempt': remote['stop_proof']['attempt_id'] = identifier()
    elif changed == 'fence': remote['stop_proof']['fence'] = 2
    elif changed == 'resources': remote['stop_proof']['resource_ids'] = []
    elif changed == 'quarantine': remote['state'] = 'quarantined'
    else: remote['pins'] = dict(remote['pins'], runtime_revision='changed')
    expected = 'agent_execution_binding_changed' if changed == 'pins' else 'agent_remote_stop_unverified'
    with pytest.raises(ControlError, match=expected): control.tick()
    with database.sessions() as session:
        assert session.get(ExecutionSlot, 1).job_id == database.job_id
        assert session.get(Job, database.job_id).stopped is False
        assert not session.scalars(select(Checkpoint)).all()


@pytest.mark.parametrize(('state', 'remote_code', 'expected'), [
    ('failed', 'model_error', 'model_response_failed'),
    ('failed', 'model_http_transient', 'model_response_failed'),
    ('failed', 'engine_error', 'execution_interrupted'),
    ('failed', None, 'execution_interrupted'),
    ('failed', 503, 'execution_interrupted'),
    ('failed', ['model_error'], 'execution_interrupted'),
    ('failed', {'code': 'model_error'}, 'execution_interrupted'),
    ('failed', 'MODEL_ERROR', 'execution_interrupted'),
    ('failed', 'model_error private-material /work/private', 'execution_interrupted'),
    ('timed_out', 'model_error', 'execution_timeout'),
    ('timed_out', 'model_http_transient', 'execution_timeout'),
])
def test_remote_failure_persists_only_fixed_business_error_without_retry(
        database, monkeypatch, state, remote_code, expected):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    assert control.tick()['status'] == 'submitted'
    remote = next(iter(service.jobs.values()))
    remote.update(state=state, error_code=remote_code,
                  message='private-material /work/private', details={'token': 'private-token'})
    if remote_code is None:
        remote.pop('error_code')  # Older service responses may omit the code.
    with database.sessions() as session:
        deadline = session.get(Job, database.job_id).deadline
        request = copy.deepcopy(session.get(AgentOutbox, database.job_id).request)

    def forbidden_collection(*args, **kwargs):
        pytest.fail('failed execution must not collect or validate artifacts')

    monkeypatch.setattr(service, 'artifact_manifest', forbidden_collection)
    monkeypatch.setattr(control.validator, 'validate', forbidden_collection)
    assert control.tick() == {'status': 'failed', 'job_id': database.job_id}
    with database.sessions() as session:
        job = session.get(Job, database.job_id)
        out = session.get(AgentOutbox, database.job_id)
        project = session.get(Project, database.project_id)
        assert job.status == 'failed' and job.error_code == expected and job.stopped is True
        assert job.deadline == deadline and out.request == request
        assert out.remote_job_id == remote['job_id'] and out.state == 'mapped'
        assert out.result == {'status': 'failed', 'error_code': expected}
        assert project.active_job_id is None and project.stage == 'draft'
        assert session.get(ExecutionSlot, 1).job_id is None
        assert not session.scalars(select(Checkpoint)).all()
        assert not session.scalars(select(Version)).all()
        event = session.scalar(select(JobEvent).where(
            JobEvent.job_id == job.id, JobEvent.type == 'job.failed'))
        assert event.payload == {'error_code': expected}
    assert control.tick()['status'] == 'acknowledged'
    assert control.tick()['status'] == 'idle'
    assert len(service.submissions) == len(service.jobs) == 1
    assert not service.acks and not service.cancellations


def test_model_failure_retains_existing_confirmed_plan_and_checkpoint(database):
    control, service = connector(database)
    complete_plan(control)
    jid = confirm_and_enqueue(database, 'plan_options')
    assert control.tick()['status'] == 'uploading'
    assert control.tick()['status'] == 'submitted'
    with database.sessions() as session:
        out = session.get(AgentOutbox, jid)
        remote = service.jobs[out.remote_job_id]
        confirmations = copy.deepcopy(out.request['task']['confirmations'])
        checkpoints = [(row.id, copy.deepcopy(row.manifest)) for row in session.scalars(select(Checkpoint))]
        deadline = session.get(Job, jid).deadline
    remote.update(state='failed', error_code='model_error')
    assert control.tick() == {'status': 'failed', 'job_id': jid}
    with database.sessions() as session:
        job = session.get(Job, jid)
        rebuilt, _ = build_agent_task(session, job)
        assert job.error_code == 'model_response_failed' and job.deadline == deadline
        assert rebuilt['confirmations'] == confirmations
        assert session.get(Project, database.project_id).confirmed_plan_revision == confirmations['stage1']['plan_revision']
        assert [(row.id, row.manifest) for row in session.scalars(select(Checkpoint))] == checkpoints
        assert not session.scalars(select(Version)).all()
    assert len(service.submissions) == 2


def test_requested_cancellation_takes_precedence_over_remote_model_failure(database):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    assert control.tick()['status'] == 'submitted'
    next(iter(service.jobs.values())).update(state='failed', error_code='model_error')
    with database.sessions.begin() as session:
        Repository(session).cancel(session.get(Job, database.job_id))
        deadline = session.get(Job, database.job_id).deadline
    assert control.tick() == {'status': 'cancelled', 'job_id': database.job_id}
    with database.sessions() as session:
        job = session.get(Job, database.job_id)
        assert job.status == 'cancelled' and job.error_code is None and job.stopped is True
        assert job.deadline == deadline and session.get(ExecutionSlot, 1).job_id is None
        assert session.get(AgentOutbox, job.id).result == {'status': 'cancelled'}
        assert not session.scalars(select(JobEvent).where(
            JobEvent.job_id == job.id, JobEvent.type == 'job.failed')).all()
    assert len(service.submissions) == len(service.cancellations) == 1


def test_collector_requires_exact_stopped_attempt_before_inspecting_output(database):
    with database.sessions() as session:
        task, _ = build_agent_task(session, session.get(Job, database.job_id))
    env, files = plan_artifacts(database.root / 'collector', task)
    profile = PPTProfile('plan_initial')
    for proof in (StopEvidence(identifier(), True, ('pod',)),
                  StopEvidence(env.attempt_id, False, ('pod',)),
                  StopEvidence(env.attempt_id, True, ())):
        with pytest.raises(ServiceError, match='writers_not_stopped'):
            profile.collect_output(database.root / 'collector', env, proof)
    cp = json.loads(next(f.content() for f in files if f.name == 'result/checkpoint.json'))
    assert cp['source_job_id'] == env.job_id and cp['source_attempt_id'] == env.attempt_id
    assert cp['source_business_job_id'] == database.job_id
    assert cp['publication_authorized'] is False


def test_checkpoint_replay_rejects_modified_project_bytes_before_model_start(database):
    control, service = connector(database); complete_plan(control)
    jid = confirm_and_enqueue(database, 'plan_options')
    with database.sessions() as session:
        task, ref = build_agent_task(session, session.get(Job, jid))
    workspace = database.root / 'modified-replay'; (workspace / 'project').mkdir(parents=True)
    for item in service.checkpoints[ref['checkpoint_id']]: write(workspace / 'base', item.name, item.content())
    for asset in task['input_snapshot']['assets']: write(workspace, 'inputs/' + asset['id'] + '.md', b'# Public source\n')
    write(workspace, 'base/project/deck/source.md', b'Edited after the checkpoint was measured')
    with pytest.raises(ServiceError, match='ppt_checkpoint_integrity_invalid'):
        PPTProfile('plan_options').prepare_project(workspace, task)
    assert list((workspace / 'project').iterdir()) == []


def test_uncertain_submission_recovers_same_outbox_request_without_duplicate_execution(database, monkeypatch):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    submit = service.submit
    lost = False

    def lose_first_response(body, *, idempotency_key):
        nonlocal lost
        result = submit(body, idempotency_key=idempotency_key)
        if not lost:
            lost = True
            raise ServiceError('synthetic_response_lost', 503)
        return result

    monkeypatch.setattr(service, 'submit', lose_first_response)
    with pytest.raises(ControlError, match='agent_service_unavailable'):
        control.tick()
    with database.sessions() as session:
        out = session.get(AgentOutbox, database.job_id)
        assert out.submission_started and out.remote_job_id is None
        assert session.get(ExecutionSlot, 1).job_id == database.job_id
    assert control.tick()['status'] == 'submitted'
    assert control.tick()['status'] == 'mapped'
    assert len(service.jobs) == 1 and len(service.uploads) == 1
    assert len(service.submissions) == 1 and len(service.lookups) == 1


def test_claim_request_keeps_original_absolute_deadline_and_phase_budget(database):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    with database.sessions() as session:
        job = session.get(Job, database.job_id)
        cutoff = job.deadline.replace(tzinfo=timezone.utc).timestamp()
        request = copy.deepcopy(session.get(AgentOutbox, database.job_id).request)
        assert request['execution_deadline_at'] == cutoff
        assert request['timeout_seconds'] == 600
    assert control.tick()['status'] == 'submitted'
    assert service.submissions[0]['execution_deadline_at'] == cutoff
    assert control.tick()['status'] == 'mapped'
    assert control.tick()['status'] == 'acknowledged'
    following = confirm_and_enqueue(database, 'plan_options')
    assert control.tick()['status'] == 'uploading'
    with database.sessions() as session:
        job = session.get(Job, following)
        request = session.get(AgentOutbox, following).request
        assert request['timeout_seconds'] == 1200
        assert request['execution_deadline_at'] == job.deadline.replace(tzinfo=timezone.utc).timestamp()


def test_replay_after_unreached_post_keeps_same_deadline_and_request(database, monkeypatch):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    submit = service.submit
    attempts = []

    def lose_before_submission(body, *, idempotency_key):
        attempts.append((copy.deepcopy(body), idempotency_key))
        if len(attempts) == 1:
            raise ServiceError('synthetic_request_lost', 503)
        return submit(body, idempotency_key=idempotency_key)

    monkeypatch.setattr(service, 'submit', lose_before_submission)
    with pytest.raises(ControlError, match='agent_service_unavailable'):
        control.tick()
    assert control.tick()['status'] == 'submitted'
    assert attempts[0] == attempts[1]
    assert len(service.jobs) == 1 and len(service.lookups) == 1


def test_cancel_during_uncertain_unreached_submission_never_reposts(database, monkeypatch):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    attempts = []

    def unreachable(body, *, idempotency_key):
        attempts.append((copy.deepcopy(body), idempotency_key))
        raise ServiceError('synthetic_request_lost', 503)

    monkeypatch.setattr(service, 'submit', unreachable)
    with pytest.raises(ControlError, match='agent_service_unavailable'):
        control.tick()
    with database.sessions.begin() as session:
        Repository(session).cancel(session.get(Job, database.job_id))
    assert control.tick()['status'] == 'cancelling'
    with database.sessions() as session:
        assert session.get(ExecutionSlot, 1).job_id == database.job_id
        assert session.get(Job, database.job_id).stopped is False
    with database.sessions.begin() as session:
        session.get(Job, database.job_id).deadline = now() - timedelta(seconds=1)
    assert control.tick()['status'] == 'cancelled'
    with database.sessions() as session:
        assert session.get(ExecutionSlot, 1).job_id is None
        assert session.get(Job, database.job_id).stopped is False
    assert len(attempts) == 1 and not service.jobs


def test_cancel_after_lost_reply_recovers_and_cancels_existing_remote(database, monkeypatch):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    submit = service.submit

    def lose_reply(body, *, idempotency_key):
        submit(body, idempotency_key=idempotency_key)
        raise ServiceError('synthetic_response_lost', 503)

    monkeypatch.setattr(service, 'submit', lose_reply)
    with pytest.raises(ControlError, match='agent_service_unavailable'):
        control.tick()
    with database.sessions.begin() as session:
        Repository(session).cancel(session.get(Job, database.job_id))
    assert control.tick()['status'] == 'submitted'
    assert control.tick()['status'] == 'cancelled'
    assert len(service.submissions) == len(service.cancellations) == 1


def test_connector_uploads_supported_25_mib_source(database):
    raw = b'x' * (25 * 1024**2)
    with database.sessions.begin() as session:
        asset = session.scalar(select(Asset))
        write(database.root, asset.storage_key, raw)
        asset.bytes, asset.sha256 = len(raw), hashlib.sha256(raw).hexdigest()
        session.flush()
        job = session.get(Job, database.job_id)
        job.input_snapshot = Repository(session).snapshot(session.get(Project, database.project_id))
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    assert len(service.uploads) == 1
    assert next(iter(service.uploads.values()))['sha256'] == hashlib.sha256(raw).hexdigest()


def test_plain_accepted_dictionary_cannot_replace_independent_validator_receipt(database):
    control, _ = connector(database)
    control.validator = SimpleNamespace(validate=lambda *_: {'accepted': True})
    assert control.tick()['status'] == 'uploading'
    assert control.tick()['status'] == 'submitted'
    with pytest.raises(ControlError, match='agent_validation_receipt_required'):
        control.tick()
    with database.sessions() as session:
        assert not session.scalars(select(Checkpoint)).all()
        assert not session.scalars(select(Plan)).all()
        assert session.get(Job, database.job_id).status == 'validating'
        assert session.get(Job, database.job_id).stopped is True
        assert session.get(ExecutionSlot, 1).job_id == database.job_id


def test_prepare_rejects_stage1_checkpoint_even_with_supplied_stage2_confirmation(database):
    control, _ = connector(database); complete_plan(control)
    jid = confirm_and_enqueue(database, 'plan_options')
    with database.sessions() as session:
        task, _ = build_agent_task(session, session.get(Job, jid))
    task['phase'], task['schema_version'] = 'prepare', 'ppt-prepare.v1'
    direction = {'id': 'B', 'name': 'Selected B'}
    task['confirmations']['stage2'] = dict(task['confirmations']['stage1'],
        selection={'direction_id': 'B'}, outline=stage2_plan()['page_plan'],
        design={'directions': [direction], 'production_parameters': {'language': 'zh-CN'}},
        selected_direction=direction)
    with pytest.raises(ServiceError, match='invalid_ppt_checkpoint'):
        PPTProfile('prepare').validate_task(task)


def test_revision_requires_exact_published_base_checkpoint_and_owner(database):
    control, service = connector(database); complete_plan(control)
    confirm_and_enqueue(database, 'plan_options'); complete_plan(control)
    # A synthetic published base is inserted explicitly; no review candidate is
    # promoted by this fixture or by the production connector.
    with database.sessions.begin() as session:
        plan = session.scalar(select(Plan).order_by(Plan.revision.desc()))
        initial = session.scalar(select(Plan).where(Plan.phase == 'plan_initial'))
        plan.confirmed_at = now(); plan.confirmation_selection = {'direction_id': 'B'}
        cp = session.get(Checkpoint, plan.checkpoint_id)
        domain = copy.deepcopy(cp.manifest['agent_checkpoint'])
        domain['phase'] = 'generate'
        for name in ('design_spec.md', 'spec_lock.md'):
            domain['files'].append({'path': 'deck/' + name, 'sha256': 'd' * 64, 'bytes': 0})
        domain['files'].sort(key=lambda r: r['path'])
        domain['sha256'] = digest({k: v for k, v in domain.items() if k != 'sha256'})
        cp.manifest = dict(cp.manifest, agent_checkpoint=domain,
                           confirmation_plan_ids={'stage1': initial.id, 'stage2': plan.id})
        project = session.get(Project, database.project_id)
        version = Version(project_id=project.id, issuer=project.issuer, subject=project.subject,
                          job_id=database.job_id, checkpoint_id=cp.id,
                          artifact_root='synthetic-published', manifest={'publication': 'fixture'}, status='published')
        session.add(version); session.flush()
        project.latest_version_id = identifier()  # Must never be resolved as the requested base.
        job = Repository(session).enqueue(project, database.owner, 'revise', project.revision,
                                          {'feedback': 'Clarify page two', 'pages': [2]})
        job.base_version_id = version.id; job.checkpoint_id = cp.id
        jid, vid, cid = job.id, version.id, cp.id
    with database.sessions() as session:
        task, _ = build_agent_task(session, session.get(Job, jid))
        assert task['base_version']['version_id'] == vid
        assert task['base_version']['checkpoint_sha256'] == task['checkpoint']['sha256']
    for changed in ('review', 'owner', 'checkpoint'):
        with database.sessions.begin() as session:
            version = session.get(Version, vid)
            version.status, version.subject, version.checkpoint_id = 'published', database.owner.sub, cid
            if changed == 'review': version.status = 'review'
            elif changed == 'owner': version.subject = 'other-owner'
            else: version.checkpoint_id = identifier()
        with database.sessions() as session, pytest.raises(ControlError, match='base_version_mismatch'):
            build_agent_task(session, session.get(Job, jid))


def running_progress(database, monkeypatch, records):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    assert control.tick()['status'] == 'submitted'
    remote = next(iter(service.jobs.values()))
    remote['state'] = 'running'
    rows = [dict(seq=n, job_id=remote['job_id'], type=kind, payload=payload)
            for n, (kind, payload) in enumerate(records, 1)]

    def page(job_id, *, after, limit):
        assert job_id == remote['job_id']
        selected = [row for row in rows if row['seq'] > after][:limit]
        return dict(items=copy.deepcopy(selected), next_cursor=selected[-1]['seq'] if selected else after,
                    has_more=False)

    monkeypatch.setattr(service, 'events', page)
    return control, service, remote, rows


def test_agent_progress_is_sanitized_replayable_and_cannot_finish_a_business_job(database, monkeypatch):
    control, service, _, rows = running_progress(database, monkeypatch, [
        ('job.queued', {}), ('job.starting', {}),
        ('engine.progress', {'event': 'engine_started', 'phase': 'validating', 'count': 100,
                             'raw': 'secret model text /work/private'}),
        ('engine.progress', {'event': 'pi_event', 'phase': 'collecting'}),
        ('engine.progress', {'event': 'runtime_finished'}), ('job.completed', {}),
    ])
    assert control.tick()['status'] == 'running'
    with database.sessions() as session:
        job = session.get(Job, database.job_id)
        events = session.scalars(select(JobEvent).where(JobEvent.job_id == job.id,
                        JobEvent.type == 'stage.changed').order_by(JobEvent.seq)).all()
        assert [event.payload for event in events] == [
            {'stage': 'execution_queued'}, {'stage': 'execution_starting'},
            {'stage': 'execution_running'}, {'stage': 'execution_running'}]
        assert job.status == 'running' and job.stage == 'execution_running' and job.stopped is False
        assert session.get(AgentOutbox, job.id).event_cursor == len(rows)
        assert session.get(ExecutionSlot, 1).job_id == job.id
        assert not session.scalars(select(Plan)).all()
        cursor = job.event_cursor
    assert control.tick()['status'] == 'running'
    with database.sessions() as session:
        assert session.get(Job, database.job_id).event_cursor == cursor
    assert len(service.submissions) == 1


@pytest.mark.parametrize('fault', ['cursor', 'sequence', 'job', 'payload', 'empty_more'])
def test_malformed_agent_progress_page_commits_neither_cursor_nor_event(database, monkeypatch, fault):
    control, service, _, rows = running_progress(database, monkeypatch, [('job.running', {})])
    page = dict(items=copy.deepcopy(rows), next_cursor=1, has_more=False)
    if fault == 'cursor': page['next_cursor'] = True
    elif fault == 'sequence': page['items'][0]['seq'] = 2
    elif fault == 'job': page['items'][0]['job_id'] = identifier()
    elif fault == 'payload': page['items'][0]['payload'] = 'not an object'
    else: page.update(items=[], next_cursor=0, has_more=True)
    monkeypatch.setattr(service, 'events', lambda *args, **kwargs: page)
    with pytest.raises(ControlError, match='agent_event_cursor_invalid'):
        control.tick()
    with database.sessions() as session:
        job = session.get(Job, database.job_id)
        assert session.get(AgentOutbox, job.id).event_cursor == 0
        assert job.stage == 'plan_initial' and job.stopped is False
        assert not session.scalars(select(JobEvent).where(JobEvent.type == 'stage.changed')).all()


def test_agent_progress_rolls_back_business_events_with_its_upstream_cursor(database, monkeypatch):
    control, _, _, _ = running_progress(database, monkeypatch, [('job.queued', {}), ('job.starting', {})])
    original = Repository.event

    def fail_mid_page(repo, job, kind, payload=None):
        if kind == 'stage.changed' and payload == {'stage': 'execution_starting'}:
            raise RuntimeError('synthetic transaction interruption')
        return original(repo, job, kind, payload)

    monkeypatch.setattr(Repository, 'event', fail_mid_page)
    with pytest.raises(RuntimeError, match='synthetic transaction interruption'):
        control.tick()
    with database.sessions() as session:
        assert session.get(AgentOutbox, database.job_id).event_cursor == 0
        assert session.get(Job, database.job_id).stage == 'plan_initial'
        assert not session.scalars(select(JobEvent).where(JobEvent.type == 'stage.changed')).all()
    monkeypatch.setattr(Repository, 'event', original)
    assert control.tick()['status'] == 'running'
    with database.sessions() as session:
        assert session.get(AgentOutbox, database.job_id).event_cursor == 2
        assert len(session.scalars(select(JobEvent).where(JobEvent.type == 'stage.changed')).all()) == 2


def test_terminal_progress_pages_are_drained_before_trusted_collection_and_validation(database, monkeypatch):
    control, service, remote, rows = running_progress(database, monkeypatch,
        [('job.running', {}), ('engine.progress', {'event': 'runtime_finished'})])
    remote['state'] = 'completed'

    def page(job_id, *, after, limit):
        selected = [row for row in rows if row['seq'] > after][:1]
        return dict(items=selected, next_cursor=selected[-1]['seq'] if selected else after,
                    has_more=after == 0)

    monkeypatch.setattr(service, 'events', page)
    collect = service.artifact_manifest

    def collect_after_stop(job_id):
        with database.sessions() as session:
            job = session.get(Job, database.job_id)
            assert job.stopped is True and job.stage == job.status == 'collecting'
        return collect(job_id)

    monkeypatch.setattr(service, 'artifact_manifest', collect_after_stop)
    validate = control.validator.validate

    def validate_after_collection(snapshot, request):
        with database.sessions() as session:
            job = session.get(Job, database.job_id)
            assert job.stopped is True and job.stage == job.status == 'validating'
        return validate(snapshot, request)

    monkeypatch.setattr(control.validator, 'validate', validate_after_collection)
    assert control.tick()['status'] == 'running'
    with database.sessions() as session:
        assert session.get(Job, database.job_id).stopped is False
        assert not session.scalars(select(Plan)).all()
    assert control.tick()['status'] == 'mapped'
    with database.sessions() as session:
        stages = session.scalars(select(JobEvent).where(JobEvent.type == 'stage.changed').order_by(JobEvent.seq)).all()
        assert [event.payload['stage'] for event in stages] == ['execution_running', 'collecting', 'validating']
        assert session.get(Job, database.job_id).status == 'succeeded'
    assert len(service.submissions) == 1


def test_agent_activity_cannot_undo_cancellation_or_release_its_execution_slot(database, monkeypatch):
    control, service, _, _ = running_progress(database, monkeypatch,
        [('job.running', {}), ('engine.progress', {'event': 'engine_progress'})])
    with database.sessions.begin() as session:
        Repository(session).cancel(session.get(Job, database.job_id))
    assert control.tick()['status'] == 'running'
    with database.sessions() as session:
        job = session.get(Job, database.job_id)
        assert job.status == 'cancelling' and job.stopped is False and job.stage == 'plan_initial'
        assert session.get(ExecutionSlot, 1).job_id == job.id
        assert session.get(AgentOutbox, job.id).event_cursor == 2
        assert not session.scalars(select(JobEvent).where(JobEvent.type == 'stage.changed')).all()
    assert len(service.cancellations) == len(service.submissions) == 1
