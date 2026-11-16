"""Exercise real loopback HTTP admission without launching a Worker or model.

This probe uses synthetic inputs and a probe-only readiness gate. It verifies
the durable business submit boundary and lost-response reconciliation, not
engine readiness, accepted production routing or a paid review/revision chain.
The response-loss injection discards one *real* successful HTTP response in the
client transport. It does not synthesize jobs, completed results or artifacts.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import socket
import threading
import time

import httpx
from sqlalchemy import func, select

from agent_service.api import ApiSettings, create_app
from agent_service.client import AgentClient
from agent_service.contracts import Principal, ProfilePolicy, ProfileRegistry, ServiceError, canonical_bytes, digest
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as visual
from agent_service.store import Store, attempts, jobs
from video_workbench.animation_polish import AnimationPolishCoordinator
from video_workbench.animation_revision_store import AnimationRevisionStore
from video_workbench.animation_review_gateway import AnimationCreateGateway, AnimationReviewGateway


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1sAAAAASUVORK5CYII=')
TENANT, CALLER = 'animation-admission-probe', 'isolated-operator'


def _require(value, code='animation_admission_probe_failed'):
    if not value:
        raise ServiceError(code, 409)


def _write(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical_bytes(value))


def _fixture(root):
    """Inert protocol material, never presented as decoded or observed media."""
    source = dict(source_revision='synthetic-admission-source', engine='html-svg',
        files={'scene.html': '<!doctype html><svg></svg>'})
    source['source_sha256'] = creation.source_sha256(source)
    context = dict(schema=creation.INPUT_SCHEMA, creation_id='synthetic-admission',
        goal='Synthetic admission-only fixture; no animation is rendered.',
        constraints=dict(width=16, height=16, fps=24, duration_seconds=2),
        assets={}, prompt_revision='admission-probe-r1', guidance='', base_source=None, review=None)
    creation.validate_creation_input(context)
    document = dict(schema=visual.REVIEW_INPUT_SCHEMA, clip_id='synthetic-admission',
        goal='Synthetic admission-only fixture, not an actual visual review.',
        media=dict(sha256=digest(dict(synthetic_not_decoded=True)), duration_seconds=2, width=1, height=1),
        source_sha256=source['source_sha256'], prompt_revision='admission-probe-r1',
        observation=dict(coverage='sampled_frames', audio_available=False,
            windows=[dict(start_seconds=0, end_seconds=2)],
            frames=[dict(frame_id='frame-' + str(index), file_name='frame-' + str(index) + '.png',
                time_seconds=index, sha256=hashlib.sha256(PNG).hexdigest()) for index in (0, 1)],
            limitations=['Synthetic one-pixel protocol inputs; no video was decoded or observed.']))
    visual.validate_review_input(document)
    bundle = root / 'synthetic-review-input'
    inputs = bundle / 'inputs'
    inputs.mkdir(parents=True)
    _write(inputs / visual.REVIEW_INPUT_NAME, document)
    for frame in document['observation']['frames']:
        with (inputs / frame['file_name']).open('xb') as stream:
            stream.write(PNG)
    _write(bundle / 'evidence.json', dict(schema='video.animation-observation-preparation.v1',
        profile=visual.REVIEW_PROFILE, input_name=visual.REVIEW_INPUT_NAME,
        input_sha256=digest(document), media_sha256=document['media']['sha256'],
        frames=2, total_image_bytes=2 * len(PNG), model_calls=0,
        observation_status='prepared_not_observed', input_directory='inputs'))
    return context, source, bundle


class ProbeAdmissionGate:
    """Synthetic readiness for queue admission only; never a Worker adapter."""
    provisioning = 'probe_only_no_dispatch'

    def ready(self, policy):
        return policy.name == visual.REVIEW_PROFILE and policy.runtime_revision == 'synthetic-not-executed'


def _route(path):
    if path.startswith('/v1/jobs/by-idempotency/'):
        return '/v1/jobs/by-idempotency/{key}'
    if path.startswith('/v1/jobs/'):
        return '/v1/jobs/{job_id}/cancel' if path.endswith('/cancel') else '/v1/jobs/{job_id}'
    return path if path in {'/v1/jobs', '/v1/inputs', '/v1/capabilities'} else 'other'


class _LossTransport(httpx.HTTPTransport):
    """Real HTTP, with one declared loss after reading an actual 202 response."""
    def __init__(self, ledger_path, operation_id, trace, *, drop_response):
        super().__init__(retries=0)
        self.ledger_path, self.operation_id, self.trace = ledger_path, operation_id, trace
        self.drop_response = drop_response

    def handle_request(self, request):
        route = _route(request.url.path)
        if request.method == 'POST' and route == '/v1/jobs':
            # A separate connection re-reads the committed business claim at
            # the last boundary before the real HTTP request can leave.
            ledger = AnimationRevisionStore(self.ledger_path)
            operation = ledger.operation(self.operation_id)
            claims = [event for event in ledger.events(operation['lineage_id'])
                if event['operation_id'] == self.operation_id and event['event'] == 'submission_claimed']
            _require(operation['status'] == 'submitting' and len(claims) == 1,
                     'animation_admission_probe_claim_missing')
            self.trace.append(dict(event='before_job_post', ledger_status=operation['status'], claim_events=len(claims)))
        self.trace.append(dict(event='http_request', method=request.method, route=route))
        response = super().handle_request(request)
        if request.method == 'POST' and route == '/v1/jobs' and self.drop_response:
            raw = response.read()
            status = response.status_code
            response.close()
            _require(status == 202, 'animation_admission_probe_expected_real_admission')
            job = json.loads(raw)
            _require(job.get('state') == 'queued', 'animation_admission_probe_expected_queued')
            self.trace.append(dict(event='injected_response_loss', actual_http_status=status,
                job_id=job['job_id'], injection='client_transport_after_real_response'))
            self.drop_response = False
            raise httpx.ReadError('Explicit admission-probe response-loss injection', request=request)
        return response


@contextmanager
def _loopback_server(app):
    import uvicorn
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    config = uvicorn.Config(app, host='127.0.0.1', port=port, access_log=False,
        log_config=None, log_level='critical', proxy_headers=False, server_header=False, lifespan='off')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True,
                              name='animation-admission-probe-http')
    try:
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        _require(server.started and thread.is_alive(), 'animation_admission_probe_server_unavailable')
        yield 'http://127.0.0.1:' + str(port)
    finally:
        server.should_exit = True
        if thread.ident is not None:
            thread.join(5)
            if thread.is_alive():
                server.force_exit = True
                thread.join(1)
        listener.close()
        _require(not thread.is_alive(), 'animation_admission_probe_server_not_stopped')


class _NeverRender:
    def render(self, *args, **kwargs):
        raise ServiceError('animation_admission_probe_render_forbidden', 409)

    lookup = render


def _expect_allowance_rejection(ledger, lineage_id):
    try:
        ledger.reserve(lineage_id, 'review', lineage_id + ':another-review',
                       lineage_id + ':another-key', 'c' * 64, [])
    except ServiceError as error:
        _require(error.code == 'animation_revision_allowance_consumed')
        return error.code
    raise ServiceError('animation_admission_probe_extra_slot_allocated', 409)


def run_probe(directory):
    """Write a fresh isolated result and return a credential-free receipt.

    No dispatcher, Kubernetes client, model proxy grant or renderer is created.
    Reusing an output directory is rejected rather than resetting evidence.
    """
    root = Path(directory).absolute()
    _require(not root.exists() and not any(path.is_symlink() for path in (root, *root.parents)),
             'animation_admission_probe_no_overwrite')
    root.mkdir(parents=True)
    _write(root / 'probe-start.json', dict(schema='video.animation-admission-probe-start.v1',
        model_tasks=0, dispatcher_started=False, synthetic_inputs=True))
    context, source, bundle = _fixture(root)
    ledger_path = root / 'business-ledger.sqlite3'
    ledger = AnimationRevisionStore(ledger_path)
    lineage = ledger.create_lineage('probe-operator', 'synthetic-admission-project',
        source['source_revision'], source['source_sha256'], 'synthetic-first-draft')
    lineage_id, operation_id = lineage['id'], lineage['id'] + ':review'
    queue = Store('sqlite:///' + str(root / 'queue.sqlite3'), allow_sqlite=True)
    queue.initialize()
    handler = implementation(visual.REVIEW_PROFILE)
    policy = ProfilePolicy(visual.REVIEW_PROFILE, '1', 'pi', 'synthetic-not-executed',
        'sha256:' + hashlib.sha256(b'synthetic-admission-only-no-worker-image').hexdigest(),
        enabled=True, supports_inputs=True, task_validator=handler.validate_task,
        task_schema=handler.task_schema, output_schema=handler.output_schema,
        **handler.limits.as_dict())
    token = secrets.token_hex(32)
    app = create_app(ApiSettings({token: Principal(TENANT, CALLER, frozenset({visual.REVIEW_PROFILE}))}),
        queue, ProfileRegistry([policy]), {'pi': ProbeAdmissionGate()})
    trace = []
    kwargs = dict(review_directory=bundle, creation_input=context, base_source=source,
                  deadline_at=time.time() + 120)

    def coordinator(client, store):
        return AnimationPolishCoordinator(store, AnimationReviewGateway(client),
                                         AnimationCreateGateway(client), _NeverRender())

    try:
        with _loopback_server(app) as origin:
            with AgentClient(origin, token, timeout=5, transport=_LossTransport(
                    ledger_path, operation_id, trace, drop_response=True)) as client:
                first = coordinator(client, ledger).step(lineage_id, **kwargs)
            _require(first['state'] == 'submission_unknown')
            lost = [row for row in trace if row['event'] == 'injected_response_loss']
            _require(len(lost) == 1)
            job_id = lost[0]['job_id']
            _require(queue.get_job(TENANT, CALLER, job_id)['state'] == 'queued')
            before_reopen = len(trace)
            # Recreate both the business store and HTTP client, rather than
            # retaining a fake gateway's in-memory accepted response.
            ledger = AnimationRevisionStore(ledger_path)
            with AgentClient(origin, token, timeout=5, transport=_LossTransport(
                    ledger_path, operation_id, trace, drop_response=False)) as client:
                recovered = coordinator(client, ledger).step(lineage_id, **kwargs)
                _require(recovered['state'] == 'reconciled' and recovered['job_id'] == job_id)
                reconciliation = trace[before_reopen:]
                _require(reconciliation == [dict(event='http_request', method='GET',
                    route='/v1/jobs/by-idempotency/{key}')])
                rejected_before_cancel = _expect_allowance_rejection(ledger, lineage_id)
                cancelled_job = client.cancel(job_id)
                _require(cancelled_job['state'] == 'cancelled' and cancelled_job['attempt'] is None)
                cancelled = coordinator(client, ledger).step(lineage_id, **kwargs)
                _require(cancelled['state'] == 'cancelled')
                rejected_after_cancel = _expect_allowance_rejection(ledger, lineage_id)
                frozen = digest(dict(operations=ledger.operations(lineage_id), events=ledger.events(lineage_id)))
                trace_before_repeat = len(trace)
                ledger = AnimationRevisionStore(ledger_path)
                repeated = coordinator(client, ledger).step(lineage_id, **kwargs)
                _require(repeated['state'] == 'cancelled' and len(trace) == trace_before_repeat)
                _require(frozen == digest(dict(operations=ledger.operations(lineage_id), events=ledger.events(lineage_id))))
            from agent_service.model_proxy import requests
            with queue.engine.connect() as connection:
                counts = {name: connection.execute(select(func.count()).select_from(table)).scalar_one()
                    for name, table in (('jobs', jobs), ('attempts', attempts), ('model_requests', requests))}
            operations = ledger.operations(lineage_id)
            posts = [row for row in trace if row.get('event') == 'http_request'
                     and row.get('method') == 'POST' and row.get('route') == '/v1/jobs']
            _require(counts == dict(jobs=1, attempts=0, model_requests=0)
                and len(posts) == 1 and len(operations) == 1 and operations[0]['status'] == 'cancelled')
        report = dict(schema='video.animation-admission-http-probe.v1', status='passed',
            execution_path='isolated_loopback_http_admission_only', synthetic_inputs=True,
            readiness_basis='synthetic_admission_gate_only', actual_worker_ready=False,
            production_configuration_loaded=False, production_routes_changed=False,
            production_acceptance_created=False, dispatcher_started=False,
            worker_attempts=0, model_requests=0, renders=0, actual_visual_review_performed=False,
            full_paid_chain_verified=False, full_workbench_verified=False,
            business_ledger_reopened=True, control_api_restarted=False,
            response_loss_injected=True, response_loss_injection='client_transport_after_real_202',
            before_post_ledger_status='submitting', exactly_one_claim_before_post=True,
            job_id=job_id, lineage_id=lineage_id, http_job_posts=1, queue_counts=counts,
            recovery_only_http_get=True, same_job_recovered=True,
            another_key_rejected_before_cancel=rejected_before_cancel,
            another_key_rejected_after_cancel=rejected_after_cancel,
            cancellation_preserved_allowance=True, terminal_replay_no_http=True,
            terminal_replay_ledger_unchanged=True, control_api_stopped=True,
            trace=trace, credentials_recorded=False)
        _write(root / 'receipt.json', report)
        return report
    finally:
        queue.engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New isolated evidence directory')
    args = parser.parse_args(argv)
    try:
        receipt = run_probe(args.output)
    except ServiceError as error:
        print(json.dumps(dict(status='failed', code=error.code)))
        return 1
    print(json.dumps(receipt, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
