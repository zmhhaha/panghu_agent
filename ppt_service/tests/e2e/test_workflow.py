"""Composed HTTP → auth → DB → dispatcher → private checkpoint workflows."""
import hashlib
import uuid

from sqlalchemy import select

from ppt_service.server.models import Asset, Checkpoint, Job, Plan, UserQuota, Version
from ppt_service.control.staging import read_json
from .harness import E2ECase, SYNTHETIC_SOURCE


class WorkflowTests(E2ECase):
    def generation_request(self, pid, *, key=None):
        project = self.project(pid)
        return self.client.post(f'/api/v1/projects/{pid}/generations', headers=self.headers() | {
            'Idempotency-Key': key or str(uuid.uuid4())}, json={
                'expected_revision': project['revision'], 'plan_revision': project['plans'][-1]['revision']})

    def test_two_http_confirmations_prepare_checkpoint_and_generation_gate_pending(self):
        pid = self.prepare_project()
        with self.sessions() as session:
            plans = session.scalars(select(Plan).where(Plan.project_id == pid).order_by(Plan.revision)).all()
            baseline_ids = {p.checkpoint_id for p in plans}
            self.assertEqual(len(baseline_ids), 2)
            self.assertTrue(all(p.confirmed_at for p in plans))
            self.assertEqual(plans[1].design['recommendation']['selection_is'],
                             'agent_recommendation_only_not_user_confirmation')
            self.assertNotIn('confirmed', plans[1].design['directions'][0])
        before_generation = self.project(pid)
        request = self.generation_request(pid, key='generation-intent')
        self.assertEqual(request.status_code, 202, request.text)
        prepare_id = request.json()['id']
        # Browser retry uses the original payload, even after prepare completes.
        original_body = {'expected_revision': before_generation['revision'],
                         'plan_revision': before_generation['plans'][-1]['revision']}
        self.assertEqual(self.complete_fake_phase(prepare_id)['status'], 'succeeded')
        duplicate = self.client.post(f'/api/v1/projects/{pid}/generations',
            headers=self.headers() | {'Idempotency-Key': 'generation-intent'}, json=original_body)
        self.assertEqual(duplicate.status_code, 202, duplicate.text)
        self.assertEqual(duplicate.json(), request.json())
        staged = self.worker.tasks[prepare_id]
        self.assertEqual(staged['stage1_confirmation']['channel'], 'website')
        self.assertEqual(staged['stage2_confirmation']['selection'], {'direction_id': 'B'})
        self.assertEqual(staged['stage2_confirmation']['selected_direction']['id'], 'B')
        parsed = read_json(self.worker.layouts[prepare_id].inputs, 'parsed.json')
        self.assertIn('SYNTHETIC P5 FIXTURE', parsed['sources'][0]['text'])
        with self.sessions() as session:
            generate = session.scalar(select(Job).where(Job.project_id == pid, Job.kind == 'generate'))
            generate_id = generate.id
            prepared = session.get(Checkpoint, generate.checkpoint_id)
            self.assertNotIn(prepared.id, baseline_ids)
            self.assertEqual(generate.input_snapshot, session.get(Job, prepare_id).input_snapshot)
            self.assertIn('design_spec.md_sha256', prepared.manifest)
            self.assertEqual(prepared.manifest['control_context']['stage2_confirmation']['selection'], {'direction_id': 'B'})
            checkpoint_files = {c.storage_key: (self.data_root / c.storage_key / 'checkpoint.json').read_bytes()
                                for c in session.scalars(select(Checkpoint).where(Checkpoint.project_id == pid))}
        result = self.complete_fake_phase(generate_id)
        self.assertEqual(result['status'], 'failed')
        gate = self.sandbox.validations[-1][1]
        self.assertTrue(gate['synthetic_test_double'])
        self.assertFalse(gate['accepted'])
        self.assertEqual(gate['validation_status'], 'gate_pending')
        self.assertEqual(gate['completed_programmatic_gates'], [])
        self.assertIn('independent_visual_review', gate['required_pending_gates'])
        self.assertEqual(self.client.get(f'/api/v1/projects/{pid}/versions', headers=self.headers()).json(), [])
        with self.sessions() as session:
            self.assertEqual(session.get(Job, generate_id).error_code, 'quality_gate_failed')
            self.assertEqual(len(session.scalars(select(Checkpoint)).all()), 3)
            self.assertEqual(session.scalars(select(Version)).all(), [])
            self.assertEqual(session.get(UserQuota, (self.owner().issuer, self.owner().sub)).generation_count, 1)
        for key, original in checkpoint_files.items():
            self.assertEqual((self.data_root / key / 'checkpoint.json').read_bytes(), original)
        self.assertIsNone(self.project(pid)['latest_version_id'])
        self.assertFalse(self.config.allow_paid_execution)
        self.assertFalse(self.config.policy_approved)
        self.assertIsNone(self.config.upstream_env_file)

    def test_recommendation_cannot_skip_either_http_confirmation(self):
        pid = self.create_project()['id']
        self.upload(pid)
        initial = self.queue_plan(pid)
        self.assertEqual(self.complete_fake_phase(initial['id'])['status'], 'succeeded')
        project = self.project(pid)
        options = self.client.post(f'/api/v1/projects/{pid}/plans', headers=self.headers() | {
            'Idempotency-Key': 'skip-first'}, json={'expected_revision': project['revision'], 'phase': 'plan_options'})
        self.assertEqual(options.status_code, 409)
        self.assertEqual(options.json()['detail']['code'], 'initial_confirmation_required')
        self.assertEqual(self.generation_request(pid).json()['detail']['code'], 'options_confirmation_required')
        self.confirm(pid, {'design_mode': 'free_design'})
        options = self.queue_plan(pid, 'plan_options')
        self.assertEqual(self.complete_fake_phase(options['id'])['status'], 'succeeded')
        blocked = self.generation_request(pid)
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['detail']['code'], 'options_confirmation_required')
        with self.sessions() as session:
            self.assertEqual([j.kind for j in session.scalars(select(Job).order_by(Job.created_at))],
                             ['plan_initial', 'plan_options'])

    def test_repeated_intent_and_conflict_do_not_duplicate_job_or_event(self):
        pid = self.create_project()['id']
        self.upload(pid)
        body = {'expected_revision': self.project(pid)['revision'], 'phase': 'plan_initial'}
        headers = self.headers() | {'Idempotency-Key': 'durable-intent'}
        first = self.client.post(f'/api/v1/projects/{pid}/plans', headers=headers, json=body)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(self.complete_fake_phase(first.json()['id'])['status'], 'succeeded')
        repeated = self.client.post(f'/api/v1/projects/{pid}/plans', headers=headers, json=body)
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(repeated.json(), first.json())
        conflict = self.client.post(f'/api/v1/projects/{pid}/plans', headers=headers, json=body | {'feedback': 'changed'})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()['detail']['code'], 'idempotency_conflict')
        with self.sessions() as session:
            self.assertEqual(len(session.scalars(select(Job)).all()), 1)
            self.assertEqual(len(session.scalars(select(Plan)).all()), 1)
        self.assertEqual(self.worker.creates, 1)

    def test_upload_invalidates_confirmation_without_erasing_old_checkpoint(self):
        pid = self.prepare_project()
        before = self.project(pid)
        with self.sessions() as session:
            baseline = {c.storage_key: (self.data_root / c.storage_key / 'checkpoint.json').read_bytes()
                        for c in session.scalars(select(Checkpoint))}
        self.upload(pid, content=b'# SYNTHETIC changed material\nNew confirmed input required.\n')
        current = self.project(pid)
        self.assertIsNone(current['confirmed_plan_revision'])
        self.assertNotEqual(current['input_snapshot_hash'], before['input_snapshot_hash'])
        response = self.generation_request(pid)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'options_confirmation_required')
        stale = self.client.post(f"/api/v1/projects/{pid}/plans/2/confirm", headers=self.headers() | {
            'Idempotency-Key': 'stale-input'}, json={'expected_revision': current['revision'],
                'input_snapshot_hash': before['input_snapshot_hash'], 'selection': {'direction_id': 'B'}})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['detail']['code'], 'stale_input_snapshot')
        for key, original in baseline.items():
            self.assertEqual((self.data_root / key / 'checkpoint.json').read_bytes(), original)

    def test_removed_material_retains_snapshot_bytes_and_rejects_old_confirmation(self):
        pid = self.prepare_project()
        project = self.project(pid)
        asset = project['assets'][0]
        with self.sessions() as session:
            source = self.data_root / session.get(Asset, asset['id']).storage_key
        response = self.client.delete(f"/api/v1/projects/{pid}/assets/{asset['id']}",
            headers=self.headers(), params={'expected_revision': project['revision']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(source.read_bytes(), SYNTHETIC_SOURCE)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), asset['sha256'])
        self.assertEqual(self.project(pid)['assets'], [])
        self.assertEqual(self.generation_request(pid).json()['detail']['code'], 'options_confirmation_required')

    def test_missing_oracle_rejects_before_generation_worker_creation(self):
        pid = self.prepare_project()
        prepare = self.generation_request(pid)
        self.assertEqual(prepare.status_code, 202)
        self.assertEqual(self.complete_fake_phase(prepare.json()['id'])['status'], 'succeeded')
        created = self.worker.creates
        self.dispatcher = self.new_dispatcher(oracle=False)
        result = self.dispatcher.tick()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.worker.creates, created)
        with self.sessions() as session:
            self.assertEqual(session.get(Job, result['job_id']).status, 'failed')
            self.assertEqual(session.scalars(select(Version)).all(), [])

    def test_source_hash_tampering_cannot_create_plan_checkpoint(self):
        pid = self.create_project()['id']
        asset = self.upload(pid)
        job = self.queue_plan(pid)
        with self.sessions() as session:
            source = self.data_root / session.get(Asset, asset['id']).storage_key
        source.write_bytes(b'# SYNTHETIC unauthorized rewrite\n')
        result = self.dispatcher.tick()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'parse_failed')
        self.assertEqual(self.worker.creates, 0)
        with self.sessions() as session:
            self.assertEqual(session.get(Job, job['id']).status, 'failed')
            self.assertEqual(session.scalars(select(Checkpoint)).all(), [])
