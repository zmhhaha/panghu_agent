"""One separately budgeted running-cancellation attempt through the workbench.

Default preflight is read-only. --execute creates one marked temporary project,
one manual scene, and at most ONE generation POST. Only a real running Agent
snapshot plus job.running event permits one business cancellation POST. A race,
not_started, timeout, missing Pod observations or quarantine stays incomplete.
No resource deletion, retries, restarts, adoption, or fixture states.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from agent_service.contracts import ServiceError, digest
from .fault_evidence import PREFIX, observe_pods, pod_stop, public_job, stopped_job
from .verify_live import Runner, Settings, VerificationFailure, canonical_uuid, require


TERMINAL = {'completed', 'cancelled', 'timed_out', 'failed', 'needs_input', 'quarantined'}


class CancellationRunner(Runner):
    def __init__(self, settings, *, protect_project_id, kubectl=None, kubeconfig=None,
                 worker_namespace=None, observer=observe_pods, **kwargs):
        super().__init__(settings, **kwargs)
        self.protect_project_id = canonical_uuid(protect_project_id)
        require(not settings.pause_for_faults, 'cancellation_has_no_workflow_gates')
        require(all(v is not None for v in (kubectl, kubeconfig, worker_namespace))
                or all(v is None for v in (kubectl, kubeconfig, worker_namespace)), 'pod_observer_settings_incomplete')
        self.kubectl, self.kubeconfig, self.worker_namespace = kubectl, kubeconfig, worker_namespace
        self.observer = observer
        self.report.update(scope='one_running_cancellation_attempt', generation_limit=1,
            protected_project_id=self.protect_project_id, cancellation_posts=0,
            running_cancellation='not_run', worker_pod_stop='not_observed',
            slot_release_time='not_directly_observed', timeout_stop='not_run')

    def run(self):
        report = super().run()
        if self.settings.execute and report['status'] == 'passed' and report['worker_pod_stop'] != 'confirmed':
            report['status'] = 'pending_pod_evidence'
            self.flush()
        return report

    def events(self, job_id):
        cursor, items = 0, []
        for _ in range(11):
            page = self.agent.events(job_id, after=cursor, limit=100)
            require(type(page.get('items')) is list and type(page.get('has_more')) is bool
                    and type(page.get('next_cursor')) is int, 'job_events_invalid')
            for value in page['items']:
                require(type(value) is dict and type(value.get('seq')) is int and value['seq'] > cursor
                        and type(value.get('type')) is str, 'job_events_invalid')
                cursor = value['seq']
                items.append({k: value.get(k) for k in ('seq', 'type', 'created_at')})
            require(page['next_cursor'] == cursor, 'job_event_cursor_invalid')
            if not page['has_more']:
                return items
        raise VerificationFailure('job_event_limit')

    def observe(self, label, attempt_id):
        if self.kubectl is None:
            return None
        try:
            return self.observer(kubectl=self.kubectl, kubeconfig=self.kubeconfig,
                namespace=self.worker_namespace, selector=PREFIX + 'attempt=' + attempt_id,
                output=self.settings.output / (label + '-pods.json'))
        except Exception:
            # No stderr, credentials, command line or raw kubectl output in report.
            self.report[label + '_pod_observation'] = 'unavailable'
            self.flush()
            return None

    def execute(self):
        protected = self.request('GET', '/api/projects/' + self.protect_project_id)
        self.save('protected-project-before.json', protected)
        with self.step('create_cancellation_project'):
            self.project = self.request('POST', '/api/projects', body=dict(
                title='[LIVE CANCEL ' + self.settings.run_id + '] 运行取消验收',
                script='制作一幅关于文稿、拆镜、候选、采用、素材导出的详细静态流程图。',
                style='仅使用静态SVG，清楚呈现中文标签、流程关系与图例。'))
            self.report['project_id'] = canonical_uuid(self.project['id'])
            self.project = self.request('POST', '/api/projects/' + self.report['project_id'] + '/scenes', body=dict(
                title='素材制作流程', narration='文稿先拆镜，镜头生成候选，审阅采用后导出素材。',
                visual_description='绘制12个带中文说明的流程节点，包含分支关系、图例和一致的配色；1280乘720静态SVG。',
                duration_seconds=8, format='svg'))
            scene_id = canonical_uuid(self.project['scenes'][0]['id'])
        with self.step('one_generation_and_running_observation'):
            document = self.input_document('scene', scene_id)
            self.save('cancel-input.json', document)
            path = '/api/scenes/' + scene_id + '/generate'
            body = dict(expected_revision=self.project['revision'], base_version_id=None, instruction='')
            intent = dict(step='cancellation', path=path, input_sha256=digest(document), request_sha256=digest(body),
                idempotency_key='live-' + self.settings.run_id + '-cancel', state='submission_started',
                recovery_get='/api/projects/' + self.report['project_id'])
            self.report['intents'].append(intent)
            self.report['generation_posts'] = 1
            self.flush()
            operation = self.request('POST', path, body=body, expected=(202,), idem=intent['idempotency_key'])
            try:
                operation_id = canonical_uuid(operation.get('id'))
            except VerificationFailure:
                raise VerificationFailure('mutation_response_unknown', uncertain=True) from None
            intent.update(operation_id=operation_id, state='waiting_for_running')
            self.flush()
            deadline = self.clock() + self.settings.poll_timeout
            running = None
            for _ in range(math.ceil(self.settings.poll_timeout / self.settings.poll_interval) + 1):
                self.refresh()
                current = self.scene(scene_id)['operation']
                require(current['id'] == operation_id, 'cancellation_operation_mismatch')
                if current.get('job_id'):
                    job_id = canonical_uuid(current['job_id'])
                    intent['job_id'] = job_id
                    job = self.agent.get_job(job_id)
                    self.save('cancel-last-job.json', public_job(job))
                    require(job.get('job_id') == job_id and job.get('business_ref') == 'video:' + operation_id,
                            'cancellation_job_mismatch')
                    if job.get('state') in TERMINAL:
                        self.report['running_cancellation'] = 'completed_before_cancel' if job['state'] == 'completed' else 'not_running'
                        raise VerificationFailure('no_running_cancellation_window')
                    if job.get('state') == 'running':
                        attempt, pins = job.get('attempt'), job.get('pins')
                        require(type(attempt) is dict and canonical_uuid(attempt.get('attempt_id'))
                            and type(attempt.get('fence')) is int and attempt['fence'] >= 1
                            and type(pins) is dict and pins.get('profile') == 'video.scene.v1'
                            and pins.get('profile_revision') == '1' and job.get('not_started') is not True,
                            'running_job_identity_invalid')
                        events = self.events(job_id)
                        require(sum(v['type'] == 'job.starting' for v in events) == 1
                                and any(v['type'] == 'job.running' for v in events), 'running_event_missing')
                        self.save('cancel-running-events.json', dict(job_id=job_id, items=events))
                        self.save('cancel-running-job.json', public_job(job))
                        self.observe('cancel-running', attempt['attempt_id'])
                        # Observation can take time: recheck before sending cancel.
                        current_job = self.agent.get_job(job_id)
                        require(current_job.get('state') == 'running' and current_job.get('attempt') == attempt,
                                'completed_during_running_observation')
                        running = job
                        break
                if self.clock() >= deadline:
                    raise VerificationFailure('running_observation_timeout')
                self.sleep(min(self.settings.poll_interval, max(0, deadline - self.clock())))
            require(running is not None, 'running_observation_timeout')
        with self.step('single_business_cancellation'):
            self.report['cancellation_posts'] = 1
            self.report['cancel_requested_monotonic'] = self.clock()
            intent['state'] = 'cancel_submission_started'
            self.flush()
            cancellation = self.request('POST', '/api/operations/' + operation_id + '/cancel', body={})
            self.save('cancel-business-response.json', cancellation)
            require(cancellation.get('id') == operation_id and cancellation.get('status') == 'cancelling',
                    'cancellation_completion_race')
            deadline = self.clock() + self.settings.poll_timeout
            final = None
            for _ in range(math.ceil(self.settings.poll_timeout / self.settings.poll_interval) + 1):
                job = self.agent.get_job(job_id)
                self.save('cancel-last-job.json', public_job(job))
                require(job.get('attempt') == running['attempt'], 'cancellation_attempt_changed')
                self.refresh()
                if job.get('state') in TERMINAL:
                    if job['state'] != 'cancelled':
                        self.report['running_cancellation'] = 'completion_race' if job['state'] == 'completed' else job['state']
                        raise VerificationFailure('running_cancellation_not_confirmed')
                    stopped_job(job, operation_id)
                    if self.scene(scene_id)['operation']['status'] == 'cancelled':
                        final = job
                        break
                if self.clock() >= deadline:
                    raise VerificationFailure('cancellation_poll_timeout')
                self.sleep(min(self.settings.poll_interval, max(0, deadline - self.clock())))
            require(final is not None, 'cancellation_poll_timeout')
            self.save('cancel-stopped-job.json', public_job(final))
            self.save('cancel-final-events.json', dict(job_id=job_id, items=self.events(job_id)))
            # Cancelled Jobs intentionally have no frozen output manifest.
            try:
                manifest = self.agent.artifact_manifest(job_id)
            except ServiceError as error:
                require(error.status == 409 and error.code == 'artifact_manifest_unavailable', 'cancel_manifest_unknown')
                self.save('cancel-manifest-observation.json', dict(available=False, http_status=409,
                    code='artifact_manifest_unavailable', job_id=job_id))
            else:
                self.save('cancel-manifest-observation.json', manifest)
                raise VerificationFailure('cancelled_job_has_manifest')
            artifacts = self.agent.artifacts(job_id)
            self.save('cancel-artifacts.json', artifacts)
            require(artifacts == {'items': []} and final.get('artifact_manifest_sha256') is None,
                    'cancelled_job_published_artifacts')
            require(not self.scene(scene_id)['versions'] and self.scene(scene_id)['head_version_id'] is None,
                    'cancelled_task_created_candidate')
            after = self.request('GET', '/api/projects/' + self.protect_project_id)
            self.save('protected-project-after.json', after)
            require(after == protected, 'protected_project_changed')
            observed = self.observe('cancel-stopped', final['attempt']['attempt_id'])
            self.report['running_cancellation'] = 'http_stop_confirmed'
            self.report['cancel_to_stop_seconds'] = round(self.clock() - self.report['cancel_requested_monotonic'], 3)
            if observed:
                try:
                    linked = pod_stop(final, observed)
                except VerificationFailure:
                    self.report['worker_pod_stop'] = 'not_confirmed'
                else:
                    self.save('cancel-pod-association.json', dict(resources=linked))
                    self.report['worker_pod_stop'] = 'confirmed'
            intent['state'] = 'cancelled_stop_validated'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('workbench-url', 'origin', 'host', 'owner', 'agent-url', 'protect-project-id'):
        parser.add_argument('--' + name, required=True)
    for name in ('agent-token-file', 'output', 'kubectl', 'kubeconfig'):
        parser.add_argument('--' + name, type=Path, required=name in {'agent-token-file', 'output'})
    parser.add_argument('--worker-namespace')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--run-id', default='')
    parser.add_argument('--poll-interval', type=float, default=1)
    parser.add_argument('--poll-timeout', type=float, default=660)
    args = vars(parser.parse_args(argv))
    extra = {k: args.pop(k) for k in ('protect_project_id', 'kubectl', 'kubeconfig', 'worker_namespace')}
    try:
        report = CancellationRunner(Settings(**args), **extra).run()
        print('Cancellation verification ' + report['status'] + '; generation POSTs='
              + str(report['generation_posts']) + '; cancellation=' + report['running_cancellation']
              + '; Pod stop=' + report['worker_pod_stop'] + '; cost=unknown.')
        return 0 if report['status'] == 'passed' else 1
    except (VerificationFailure, ValueError, TypeError, OSError):
        print('Cancellation verification could not start; no submission was retried.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
