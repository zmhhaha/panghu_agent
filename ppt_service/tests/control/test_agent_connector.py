"""Neutral PPT boundary checks with real DB transactions and ordinary artifacts.

The in-memory client is explicitly a test double. It never launches an engine,
claims a live acceptance record, or contacts a model/provider.
"""
from __future__ import annotations

import copy
import hashlib
import json
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
from ppt_service.server.models import AgentOutbox, Asset, Checkpoint, ExecutionSlot, Job, Plan, Project, Version, now
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


@pytest.mark.parametrize('changed', ['attempt', 'fence', 'resources', 'quarantine', 'pins'])
def test_terminal_remote_without_matching_stop_binding_retains_slot(database, changed):
    control, service = connector(database)
    assert control.tick()['status'] == 'uploading'
    assert control.tick()['status'] == 'submitted'
    remote = next(iter(service.jobs.values()))
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
    assert len(service.submissions) == 2 and service.submissions[0] == service.submissions[1]


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
