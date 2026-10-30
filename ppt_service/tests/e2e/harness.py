"""Real HTTP/auth/database/controller with explicitly synthetic worker boundaries.

The worker never runs a model. Planning uses the real parser, source projection,
checkpoint collector and publisher. Prepare simulates the external quality tool;
generation can only return the real publication gate's pending result. These
fixtures must never be described as generated decks or official quality proof.
"""
from __future__ import annotations

from datetime import timedelta
from contextlib import closing
import os
from pathlib import Path
import re
import shutil
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

from ppt_service.control.dispatcher import Dispatcher
from ppt_service.control.execution import Handle, Observation, RuntimeConfig, RuntimeState, StopProof
from ppt_service.control.parsing import parse_inputs
from ppt_service.control.publisher import complete_index, publication_readiness, validate_private
from ppt_service.control.staging import atomic_json, read_json
from ppt_service.server.app import create_app
from ppt_service.server.auth import Identity, Settings
from ppt_service.server.migrate import migrate
from ppt_service.server.models import now
from ppt_service.worker.execute import read_task

ORIGIN = 'https://ppt-e2e.test'
ISSUER = 'https://identity.ppt-e2e.test'
AUDIENCE = 'ppt-e2e'
SYNTHETIC_SOURCE = b'# SYNTHETIC P5 FIXTURE\nThree pages; no charts; no external source or model.\n'


def test_database_url():
    """Reject arbitrary connection strings before connecting or creating schemas."""
    value = os.environ.get('PPT_E2E_DATABASE_URL')
    if not value:
        return None
    url = make_url(value)
    if os.environ.get('PPT_E2E_TEST_ONLY') != 'isolated-p5':
        raise RuntimeError('P5 PostgreSQL requires PPT_E2E_TEST_ONLY=isolated-p5')
    if url.get_backend_name() != 'postgresql' or not re.fullmatch(r'ppt_p5_[a-z0-9_]+', url.database or ''):
        raise RuntimeError('P5 requires a dedicated ppt_p5_* PostgreSQL database')
    socket = url.query.get('host')
    if socket is not None:
        if socket != '/pgsocket' or url.host not in (None, ''):
            raise RuntimeError('P5 permits only the isolated /pgsocket socket')
    elif url.host not in ('127.0.0.1', 'localhost', '::1'):
        raise RuntimeError('P5 PostgreSQL must use loopback or /pgsocket')
    # libpq options/services could redirect the connection or its search path.
    if set(url.query) - {'host', 'port', 'connect_timeout'}:
        raise RuntimeError('P5 PostgreSQL connection options are restricted')
    return value


class FakeWorker:
    """Finite synthetic worker; records real staged tasks, never spawns a process."""
    ready = True

    def __init__(self):
        self.creates = 0
        self.stops = 0
        self.hold = False
        self.stop_provable = True
        self.running = set()
        self.tasks = {}
        self.layouts = {}
        self.attempts = {}
        self.needs_input = False

    def reconcile(self, attempt):
        return Handle(attempt.uid, attempt.job_id, attempt.attempt, attempt.token,
                      'synthetic-worker-' + attempt.uid, 'synthetic-proxy-' + attempt.uid)

    def create(self, attempt, layout):
        self.creates += 1
        self.tasks[attempt.job_id] = read_task(layout.task / 'task.json')
        self.layouts[attempt.job_id] = layout
        self.attempts[attempt.job_id] = attempt
        if self.hold:
            self.running.add(attempt.uid)
        project = layout.project / 'synthetic-deck'
        project.mkdir(exist_ok=True)
        (project / 'source.md').write_bytes(SYNTHETIC_SOURCE)
        with closing(sqlite3.connect(layout.home / 'synthetic-session.db')) as db:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY)')
                db.execute('INSERT OR IGNORE INTO sessions VALUES (?)', (attempt.session_id,))
        pages = {'pages': [{'ordinal': n, 'title': f'SYNTHETIC page {n}',
                            'purpose': 'Exercise P5 state transitions only.'} for n in range(1, 4)]}
        plan = {'outline': pages, 'design': {'mode': 'free_design'}}
        if attempt.phase == 'plan_options':
            plan = {'outline': pages, 'design': {'selected': 'B', 'directions': [
                {'id': direction, 'name': 'SYNTHETIC direction ' + direction,
                 'page_plan': pages, 'production_parameters': {'language': 'zh'},
                 'confirmed': True} for direction in 'ABC']}}
        if attempt.phase == 'prepare':
            for name in ('design_spec.md', 'spec_lock.md'):
                (project / name).write_text('# SYNTHETIC P5 PREPARE\nNo delivery acceptance.\n', encoding='utf-8')
        status = ('awaiting_confirmation' if attempt.phase.startswith('plan') else
                  'prepared' if attempt.phase == 'prepare' else 'candidate')
        if self.needs_input:
            status = 'needs_input'
            plan = {'approval_status': 'denied', 'tool_name': 'synthetic-tool'}
        atomic_json(layout.result / 'manifest.json', {
            'schema_version': 1, 'phase': attempt.phase, 'status': status,
            'input_snapshot_sha256': attempt.snapshot['hash'], 'project_root': 'synthetic-deck',
            'plan': plan, 'artifacts': [], 'slides': [], 'quality': {}})
        atomic_json(layout.result / 'execution.json', {
            'job_id': attempt.job_id, 'attempt': attempt.attempt, 'phase': attempt.phase,
            'session_id': attempt.session_id, 'status': 'completed',
            'gateway_stopped': True, 'preview_stopped': True})
        atomic_json(layout.result / 'actor.private.json', {'synthetic': True, 'message': 'P5 test fixture'})
        return self.reconcile(attempt)

    def observe(self, handle):
        running = handle.uid in self.running
        return Observation(running, None if running else 0)

    def stop(self, handle):
        self.stops += 1
        if self.stop_provable:
            self.running.discard(handle.uid)

    def prove_stopped(self, handle):
        return StopProof(handle.uid, self.stop_provable and handle.uid not in self.running,
                         ('synthetic-worker', 'synthetic-proxy'))

    def cleanup(self, handle):
        if handle.uid in self.running:
            raise AssertionError('cleanup before writers stopped')


class _SyntheticPrepareGate:
    """Explicit substitute for an unavailable external tool, prepare ONLY."""
    def run(self, project, phase, pages):
        if phase != 'prepare' or pages != 3:
            raise AssertionError('synthetic prepare gate cannot validate a delivery')
        for name in ('design_spec.md', 'spec_lock.md'):
            if not (project / name).read_text(encoding='utf-8').startswith('# SYNTHETIC P5 PREPARE'):
                raise AssertionError('non-synthetic source passed to test gate')
        return {'official_gates_verified': True, 'synthetic_test_double': True}


class FakeSandbox:
    """Real parser/planning validation; simulated external prepare/delivery boundary."""
    def __init__(self):
        self.validations = []

    def parse(self, root, inventory):
        return parse_inputs(root, inventory)

    def validate(self, snapshot, request):
        if request['phase'] in ('generate', 'revise'):
            # No rendered deck is fabricated. Exercise the final deny boundary
            # with a clearly synthetic report, never issue runtime evidence.
            manifest = read_json(snapshot / 'result', 'manifest.json')
            complete, collection = complete_index(snapshot, manifest)
            result = publication_readiness(request['phase'], {
                'status': 'candidate', 'manifest': complete, 'collection': collection,
                'synthetic_test_double': True})
        else:
            result = validate_private(snapshot, request, gate_runner=_SyntheticPrepareGate())
        self.validations.append((request['phase'], result))
        return result


class E2ECase(unittest.TestCase):
    """No authentication dependency overrides, live credentials or shared DBs."""
    database_backend = None

    def setUp(self):
        super().setUp()
        self.temp_root = Path(tempfile.mkdtemp(prefix='ppt-p5-e2e-')).resolve()
        self.addCleanup(self._remove_temp)
        self.data_root = self.temp_root / 'data'
        self.data_root.mkdir()
        self.db_path = self.temp_root / 'state.sqlite3'
        pg_url = None if self.database_backend == 'sqlite' else test_database_url()
        self.schema = None
        if pg_url:
            self.schema = 'p5_' + uuid.uuid4().hex
            self._schema_engine = create_engine(pg_url)
            self.addCleanup(self._drop_schema)
            with self._schema_engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            self.engine = create_engine(pg_url, connect_args={'options': '-csearch_path=' + self.schema})
        else:
            self.engine = create_engine('sqlite:///' + self.db_path.as_posix(),
                                        connect_args={'check_same_thread': False, 'timeout': 20})
            @event.listens_for(self.engine, 'connect')
            def foreign_keys(connection, _record):
                connection.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.engine.dispose)
        self.database_kind = self.engine.dialect.name
        migrate(self.engine)
        self.settings = Settings(str(self.engine.url), str(self.data_root), oidc_issuer=ISSUER,
                                 oidc_audience=AUDIENCE, trusted_proxy_cidrs=('127.0.0.1/32',),
                                 allowed_origins=(ORIGIN,))
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.app = create_app(self.settings, self.engine)
        self.app.state.jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=self.key.public_key()))
        self.sessions = self.app.state.sessions
        self.client = TestClient(self.app, base_url=ORIGIN, client=('127.0.0.1', 12345))
        self.addCleanup(self.client.close)
        me = self.client.get('/api/v1/me', headers={'Authorization': 'Bearer ' + self.token()})
        self.assertEqual(me.status_code, 200, me.text)
        self.csrf = me.json()['csrf_token']
        self.worker = FakeWorker()
        self.sandbox = FakeSandbox()
        self.config = RuntimeConfig(self.data_root, state=RuntimeState.CONFIGURED)
        # The synthetic fixture declares no charts, from its uploaded source.
        self.dispatcher = self.new_dispatcher()

    def _remove_temp(self):
        if not self.temp_root.exists():
            return
        # The only removal target is the exact per-case mkdtemp result.
        for folder, dirs, files in os.walk(self.temp_root):
            Path(folder).chmod(0o700)
            for name in dirs:
                path = Path(folder) / name
                if not path.is_symlink():
                    path.chmod(0o700)
            for name in files:
                path = Path(folder) / name
                if not path.is_symlink():
                    path.chmod(0o600)
        shutil.rmtree(self.temp_root)

    def _drop_schema(self):
        if not re.fullmatch(r'p5_[a-f0-9]{32}', self.schema):
            raise AssertionError('invalid owned schema')
        try:
            with self._schema_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        finally:
            self._schema_engine.dispose()

    def token(self, who='alice', **claims):
        payload = {'iss': ISSUER, 'sub': who, 'aud': AUDIENCE,
                   'email': who + '@ppt.test', 'email_verified': True,
                   'iat': now(), 'exp': now() + timedelta(minutes=5)} | claims
        return jwt.encode(payload, self.key, algorithm='RS256')

    def headers(self, who='alice'):
        return {'Authorization': 'Bearer ' + self.token(who), 'Origin': ORIGIN, 'X-CSRF-Token': self.csrf}

    def owner(self, who='alice'):
        return Identity(ISSUER, who)

    def new_dispatcher(self, *, oracle=True):
        return Dispatcher(self.sessions, self.config, self.worker, self.sandbox,
                          oracle_provider=(lambda attempt, _checkpoint: {
                              'expected_charts': [], 'source_sha256': [a['sha256'] for a in attempt.snapshot['assets']]}) if oracle else None)

    def create_project(self, who='alice', **changes):
        response = self.client.post('/api/v1/projects', headers=self.headers(who), json={
            'title': 'SYNTHETIC P5 project', 'page_count': 3, 'external_model_ack': True} | changes)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def project(self, pid, who='alice'):
        response = self.client.get('/api/v1/projects/' + pid, headers=self.headers(who))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def upload(self, pid, who='alice', content=SYNTHETIC_SOURCE, filename='synthetic.md'):
        response = self.client.post(f'/api/v1/projects/{pid}/assets', headers=self.headers(who),
                                    files={'file': (filename, content, 'text/markdown')})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def queue_plan(self, pid, phase='plan_initial', who='alice', key=None):
        response = self.client.post(f'/api/v1/projects/{pid}/plans',
                                    headers=self.headers(who) | {'Idempotency-Key': key or str(uuid.uuid4())},
                                    json={'expected_revision': self.project(pid, who)['revision'], 'phase': phase})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def complete_fake_phase(self, job_id, hold=False):
        self.worker.hold = hold
        start = self.dispatcher.tick()
        self.assertEqual(start, {'status': 'started', 'job_id': job_id})
        return start if hold else self.dispatcher.tick()

    def confirm(self, pid, selection, who='alice', key=None):
        project = self.project(pid, who)
        plan = project['plans'][-1]
        response = self.client.post(f"/api/v1/projects/{pid}/plans/{plan['revision']}/confirm",
                                    headers=self.headers(who) | {'Idempotency-Key': key or str(uuid.uuid4())},
                                    json={'expected_revision': project['revision'],
                                          'input_snapshot_hash': plan['input_snapshot_hash'], 'selection': selection})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def prepare_project(self):
        pid = self.create_project()['id']
        self.upload(pid)
        initial = self.queue_plan(pid)
        self.assertEqual(self.complete_fake_phase(initial['id'])['status'], 'succeeded')
        self.confirm(pid, {'design_mode': 'free_design', 'template_id': None})
        options = self.queue_plan(pid, 'plan_options')
        self.assertEqual(self.complete_fake_phase(options['id'])['status'], 'succeeded')
        self.confirm(pid, {'direction_id': 'B'})
        return pid
