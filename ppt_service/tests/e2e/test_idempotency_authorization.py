"""Cached HTTP writes must still authorize their current route resource.

Planning/checkpoint records and the historical Version are explicit ACL fixtures.
They only make each write route reachable; no publisher, model, export tool or
PowerPoint acceptance is bypassed or represented as verified by these tests.
"""
from __future__ import annotations

import uuid

from ppt_service.server.models import Checkpoint, Plan, Project, now
from ppt_service.server.repository import Repository

from .backup_helpers import metadata_snapshot
from .harness import E2ECase
from .test_security import seed_synthetic_historical_version


class IdempotencyAuthorizationTests(E2ECase):
    def seed_acl_plan(self, pid, *, confirmed=False):
        """Synthetic prerequisite for HTTP ACL/replay testing only."""
        marker = {'fixture': 'synthetic-idempotency-acl-only',
                  'publication_verified': False, 'powerpoint_acceptance': 'pending'}
        with self.sessions.begin() as session:
            project = session.get(Project, pid)
            checkpoint = Checkpoint(
                project_id=pid, issuer=project.issuer, subject=project.subject,
                storage_key=f'projects/{pid}/checkpoints/{uuid.uuid4()}', manifest=marker)
            session.add(checkpoint)
            session.flush()
            plan = Plan(
                project_id=pid, revision=1,
                phase='plan_options' if confirmed else 'plan_initial',
                input_snapshot_hash=Repository(session).snapshot(project)['hash'],
                outline=marker, design={'directions': [{'id': 'B'}]} | marker,
                checkpoint_id=checkpoint.id,
                confirmed_at=now() if confirmed else None,
                confirmation_selection={'direction_id': 'B'} if confirmed else None)
            session.add(plan)
            if confirmed:
                project.confirmed_plan_revision = plan.revision

    def request_case(self, kind):
        project = self.create_project(title='SYNTHETIC ' + kind + ' replay ACL')
        pid = project['id']
        route = f'/api/v1/projects/{pid}/'
        expected_status = 202
        if kind == 'plans':
            body = {'expected_revision': project['revision'], 'phase': 'plan_initial'}
            changed = body | {'feedback': 'Changed request must not reveal a deleted resource'}
            route += 'plans'
        elif kind == 'confirm':
            self.seed_acl_plan(pid)
            current = self.project(pid)
            body = {'expected_revision': current['revision'],
                    'input_snapshot_hash': current['plans'][0]['input_snapshot_hash'],
                    'selection': {'design_mode': 'free_design', 'template_id': None}}
            changed = body | {'selection': {'design_mode': 'free_design'}}
            route += 'plans/1/confirm'
            expected_status = 200
        elif kind == 'generations':
            self.seed_acl_plan(pid, confirmed=True)
            body = {'expected_revision': project['revision'], 'plan_revision': 1}
            changed = body | {'plan_revision': 2}
            route += 'generations'
        elif kind == 'revisions':
            version, _folder = seed_synthetic_historical_version(self, pid)
            body = {'expected_revision': project['revision'], 'base_version_id': version,
                    'feedback': 'SYNTHETIC ACL fixture edit', 'pages': [1]}
            changed = body | {'feedback': 'SYNTHETIC different request'}
            route += 'revisions'
        elif kind == 'cancel':
            job = self.queue_plan(pid)
            route = f'/api/v1/jobs/{job["id"]}/cancel'
            body = changed = None  # This route has no request body/hash fields.
            expected_status = 200
        else:
            raise AssertionError(kind)
        return pid, route, body, changed, expected_status

    def exercise_route(self, kind):
        pid, route, body, changed, status = self.request_case(kind)
        key = 'same-intent-after-authorization-' + kind
        headers = self.headers() | {'Idempotency-Key': key}
        first = self.client.post(route, json=body, headers=headers)
        self.assertEqual(first.status_code, status, first.text)
        original = first.json()
        before = metadata_snapshot(self.engine)

        # First execution advances revisions/events/quotas. Replaying must keep
        # the original response without another action or another side effect.
        replay = self.client.post(route, json=body, headers=headers)
        self.assertEqual(replay.status_code, status, replay.text)
        self.assertEqual(replay.json(), original)
        self.assertEqual(metadata_snapshot(self.engine), before)
        if changed is not None:
            conflict = self.client.post(route, json=changed, headers=headers)
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual(conflict.json()['detail']['code'], 'idempotency_conflict')
            self.assertEqual(metadata_snapshot(self.engine), before)

        # Even an invited authenticated account with the same key cannot read
        # or execute another owner's route; failed quota creation rolls back.
        for request_body in ([body, changed] if changed is not None else [body]):
            other = self.client.post(route, json=request_body,
                headers=self.headers('bob') | {'Idempotency-Key': key})
            self.assertEqual(other.status_code, 404, other.text)
            self.assertEqual(other.json()['detail']['code'], 'not_found')
            self.assertEqual(metadata_snapshot(self.engine), before)

        deleted = self.client.delete(f'/api/v1/projects/{pid}', headers=self.headers())
        self.assertEqual(deleted.status_code, 202, deleted.text)
        after_deletion = metadata_snapshot(self.engine)
        for request_body in ([body, changed] if changed is not None else [body]):
            for who in ('alice', 'bob'):
                response = self.client.post(route, json=request_body,
                    headers=self.headers(who) | {'Idempotency-Key': key})
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()['detail']['code'], 'not_found')
                self.assertEqual(metadata_snapshot(self.engine), after_deletion)

    def test_plan_replay_authorizes_before_cache_and_request_hash(self):
        self.exercise_route('plans')

    def test_confirmation_replay_authorizes_without_confirming_twice(self):
        self.exercise_route('confirm')

    def test_generation_replay_authorizes_without_reserving_quota_twice(self):
        self.exercise_route('generations')

    def test_revision_replay_authorizes_without_creating_second_job(self):
        self.exercise_route('revisions')

    def test_cancel_replay_authorizes_without_adding_second_event(self):
        self.exercise_route('cancel')
