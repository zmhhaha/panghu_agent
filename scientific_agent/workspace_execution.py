"""Durable stage admission, bounded artifact reception and restart recovery.

No mutation of /v1/jobs is retried after an ambiguous response. The persisted
claim is observed through the service's idempotency lookup instead. Cancelling
an unsubmitted intent consumes no worker; waiting for a user never starts a job.
"""
from __future__ import annotations

import base64
import copy
from datetime import timedelta
import hashlib
import json
import logging
import re
from urllib.parse import quote
from uuid import uuid4

from .pi_client import WorkerError
from .pi_contracts import parse_timestamp, utc_now, canonical_json, result_digest
from .pi_sections import index_sections
from .workspace import (WorkspaceError, require, digest, encode, TERMINAL, _new_job,
                        _event, _fill_selections)

log = logging.getLogger(__name__)
MIB = 1024 * 1024
TRACE_FILES = {'papers': 'papers.jsonl', 'evidence': 'evidence.jsonl',
               'claims': 'claims.jsonl', 'references': 'references.json'}


def revision_input(project, version, section, feedback):
    """Reuse the already accepted fixed-evidence revision contract unchanged."""
    from .pi_revision_contracts import (RevisionJob, revision_input_digest,
                                       revision_material_digest, validate_revision_materials)
    task_id, intent_id, now = str(uuid4()), str(uuid4()), utc_now()
    material = dict(report=version['report_markdown'], items=copy.deepcopy(version['trace_manifest']),
                    artifacts=copy.deepcopy(version['trace_artifacts']))
    job = dict(schema_version='1', task_kind='revision', task_id=task_id,
        input_digest='', topic=project['requirements']['question'].replace('\n', ' ').replace('\r', ' ')[:500],
        user_id=project['owner'], engine='pi', mode='review', timeout_seconds=1800,
        created_at=now, execution_deadline_at=(parse_timestamp(now) + timedelta(seconds=1800)
            ).isoformat().replace('+00:00', 'Z'),
        skill_bundle_revision='agent-task-contract-v1', runtime_revision='agent-task-contract-v1',
        intent_id=intent_id, parent_version_id=version['id'],
        root_report_id=version.get('root_version_id', version['id']), base_sha256=version['report_sha256'],
        section_id=section['section_id'], section_sha256=section['sha256'], instruction=feedback,
        input_sha256=revision_material_digest(material), material_policy='recorded_evidence_only')
    job['input_digest'] = revision_input_digest(job)
    job = RevisionJob.from_dict(job).to_dict()
    validate_revision_materials(job, material)
    task = dict(schema_version='1', business_task_id=task_id, topic=job['topic'], mode='review',
        created_at=now, execution_deadline_at=job['execution_deadline_at'],
        **{key: job[key] for key in ('intent_id', 'parent_version_id', 'root_report_id',
            'base_sha256', 'section_id', 'section_sha256', 'instruction', 'input_sha256', 'material_policy')})
    return dict(revision_job=job, revision_material=material, task=task,
        parent_version_id=version['id'], evidence_snapshot_id=version['evidence_snapshot_id'],
        confirmation_id=version['confirmation_id'], profile='research.revise.v1')


def _job(project, task_id):
    job = next((item for item in project['jobs'] if item['id'] == task_id), None)
    require(job is not None, 'job_not_found', 404)
    return job


class WorkspaceService:
    def __init__(self, store, worker):
        self.store, self.worker = store, worker
        self.source_policy = dict(providers=list(getattr(worker, 'providers',
            ('crossref', 'pubmed', 'arxiv'))), max_results=getattr(worker, 'max_results', 10),
            max_search_calls=3)

    def _set_job(self, owner, project_id, task_id, **fields):
        def change(project):
            job = _job(project, task_id)
            if all(job.get(key) == value for key, value in fields.items()):
                return False
            job.update(copy.deepcopy(fields), updated_at=utc_now())
            if job['state'] in TERMINAL and project['active_job_id'] == task_id:
                project['active_job_id'] = None
        return self.store.internal(owner, project_id, change)

    def action(self, owner, project_id, action, payload, key):
        self.store.action(owner, project_id, action, payload, key, source_policy=self.source_policy)
        # The HTTP request returns the durable project even when execution is
        # temporarily unreachable. Polling and the background reconciler resume.
        try:
            self.reconcile(owner, project_id)
        except (WorkerError, WorkspaceError):
            pass
        return self.store.get(owner, project_id)

    def _request(self, *args, **kwargs):
        if not hasattr(self.worker, '_request'):
            raise WorkerError(503, 'agent_service_required', 'Staged research requires agent-service', False)
        return self.worker._request(*args, **kwargs)

    def _inputs(self, job):
        if not hasattr(self.worker, '_upload'):
            raise WorkerError(503, 'agent_service_required', 'Staged research requires agent-service', False)
        if job['stage'] != 'revise':
            return [self.worker._upload('context.json', encode(job['frozen_context']).encode('utf-8'))]
        from .pi_revision_contracts import validate_revision_materials
        from .pi_sections import select_section
        checked = validate_revision_materials(job['revision_job'], job['revision_material'])
        section = select_section(checked['report'], job['revision_job']['section_id'],
                                 job['revision_job']['base_sha256'])
        files = {'revision-materials.json': canonical_json(job['revision_material']).encode('utf-8'),
            'input-report.md': checked['report'].encode('utf-8'),
            'input-section.md': section['text'].encode('utf-8')}
        files.update({TRACE_FILES[key]: raw for key, raw in checked['artifacts'].items()})
        return [self.worker._upload(name, raw) for name, raw in sorted(files.items())]

    def dispatch(self, owner, project_id, task_id):
        project = self.store.raw(owner, project_id)
        job = _job(project, task_id)
        if job['state'] in TERMINAL or job['admission_started'] or job['remote_job_id']:
            return
        if job['cancel_requested']:
            self._set_job(owner, project_id, task_id, state='cancelled')
            return
        if job['input_refs'] is None:
            try:
                refs = self._inputs(job)
            except WorkerError as exc:
                self._set_job(owner, project_id, task_id, error_code=exc.code)
                raise
            def save_inputs(value):
                saved = _job(value, task_id)
                if saved['input_refs'] is not None or saved['state'] in TERMINAL:
                    return False
                saved['input_refs'] = refs
            project = self.store.internal(owner, project_id, save_inputs)
            job = _job(project, task_id)
        if job['request'] is None:
            request = dict(api_version='1', profile=job['profile'], profile_revision=job['profile_revision'],
                business_ref=task_id, task=job['task'], inputs=job['input_refs'], timeout_seconds=1800,
                execution_deadline_at=parse_timestamp(job['execution_deadline_at']).timestamp())
            def save_request(value):
                saved = _job(value, task_id)
                if saved['request'] is not None or saved['state'] in TERMINAL:
                    return False
                saved['request'] = request
            project = self.store.internal(owner, project_id, save_request)
            job = _job(project, task_id)
        claim = str(uuid4())
        def claim_admission(value):
            saved = _job(value, task_id)
            if saved['admission_started'] or saved['cancel_requested'] or saved['state'] in TERMINAL:
                return False
            saved.update(admission_started=True, admission_claim=claim, state='admitting')
        project = self.store.internal(owner, project_id, claim_admission)
        job = _job(project, task_id)
        if job.get('admission_claim') != claim or job['cancel_requested']:
            return
        try:
            remote = self._request('POST', '/v1/jobs', job['request'],
                                   headers={'Idempotency-Key': job['idempotency_key']})
            self._bind(owner, project_id, job, remote)
        except WorkerError as exc:
            # Even a connection error can occur after the remote commit. An
            # explicit 4xx is a negative receipt; transport errors stay uncertain.
            fields = dict(error_code=exc.code)
            if 400 <= exc.status < 500:
                fields['state'] = 'failed'
            else:
                fields['state'] = 'admission_uncertain'
            self._set_job(owner, project_id, task_id, **fields)
            raise

    def _check_remote(self, job, remote):
        from .workspace import identifier
        identifier(remote.get('job_id'))
        pins = remote.get('pins') or {}
        require(remote.get('business_ref') == job['id'] and pins.get('profile') == job['profile']
            and pins.get('profile_revision') == job['profile_revision']
            and (job['remote_job_id'] is None or remote['job_id'] == job['remote_job_id']),
            'agent_job_binding_mismatch', 502)

    def _bind(self, owner, project_id, job, remote):
        self._check_remote(job, remote)
        self._set_job(owner, project_id, job['id'], remote_job_id=remote['job_id'])

    def _remote(self, owner, project_id, job):
        path = ('/v1/jobs/' + job['remote_job_id'] if job['remote_job_id'] else
            '/v1/jobs/by-idempotency/' + quote(job['idempotency_key'], safe=''))
        value = self._request('GET', path)
        self._check_remote(job, value)
        if not job['remote_job_id']:
            self._bind(owner, project_id, job, value)
        return value

    def _observe(self, owner, project_id, job, remote):
        """Persist real engine event times; a status poll is never progress."""
        from .workspace_diagnostics import observed_time
        cursor = job.get('observed_event_cursor', 0)
        if type(remote.get('event_cursor')) is not int or remote['event_cursor'] <= cursor:
            return
        def read_page(after):
            try:
                page = self._request('GET', '/v1/jobs/' + remote['job_id']
                    + '/events?after=' + str(after) + '&limit=100')
                items = page.get('items') if type(page) is dict else None
                return items[:100] if type(items) is list else None
            except Exception:
                # Timing is supplementary; a diagnostic transport/parser
                # failure cannot block reception of a valid stopped result.
                return None
        events = read_page(cursor)
        if events is None:
            return
        fields = {}
        def consume(items):
            nonlocal cursor
            for event in items:
                if type(event) is not dict:
                    continue
                seq, kind = event.get('seq'), event.get('type')
                stamp = observed_time(event.get('created_at'))
                if type(seq) is not int or seq <= cursor or not stamp:
                    continue
                cursor = seq
                if kind == 'job.queued':
                    fields['queued_at'] = stamp
                if kind == 'job.running' and not job.get('started_at') and 'started_at' not in fields:
                    fields['started_at'] = stamp
                if kind in {'job.running', 'engine.progress', 'job.completed',
                            'job.failed', 'job.timed_out', 'job.cancelled', 'job.needs_input'}:
                    fields.update(last_progress_at=stamp, last_event=kind)
                if kind in {'job.completed', 'job.failed', 'job.timed_out', 'job.cancelled', 'job.needs_input'}:
                    fields['finished_at'] = stamp
        consume(events)
        if remote.get('state') in {'completed', 'failed', 'timed_out', 'cancelled', 'needs_input'} \
                and remote['event_cursor'] > cursor:
            # Finished jobs will not be polled by the reconciler again. Bound
            # diagnostics to two pages while still observing their actual final
            # event. Missing start events in the skipped middle stay unknown.
            tail = read_page(max(cursor, remote['event_cursor'] - 100))
            if tail is not None:
                consume(tail)
        if cursor > job.get('observed_event_cursor', 0):
            try:
                self._set_job(owner, project_id, job['id'], observed_event_cursor=cursor, **fields)
            except Exception:
                log.warning('research task diagnostic timing deferred (task=%s)', job['id'])

    def job_diagnostic(self, owner, project_id, task_id):
        from .workspace_diagnostics import diagnostic_download
        project = self.store.raw(owner, project_id)
        job = _job(project, task_id)
        if job.get('remote_job_id'):
            try:
                self._observe(owner, project_id, job, self._remote(owner, project_id, job))
            except WorkerError:
                pass
        job = _job(self.store.raw(owner, project_id), task_id)
        return diagnostic_download(project_id, job)

    def _candidate(self, job, remote):
        """Recheck stopped attempt, manifest, bytes and business provenance."""
        require(remote.get('state') == 'completed', 'result_not_ready', 409)
        attempt, proof = remote.get('attempt') or {}, remote.get('stop_proof') or {}
        require(remote.get('stop_proven') is True and proof.get('stop_proven') is True
            and proof.get('attempt_id') == attempt.get('attempt_id')
            and proof.get('fence') == attempt.get('fence')
            and remote.get('publication_authorized') is False,
            'agent_stop_proof_mismatch', 502)
        manifest = self._request('GET', '/v1/jobs/' + remote['job_id'] + '/artifact-manifest')
        manifest_hash = digest({key: value for key, value in manifest.items() if key != 'sha256'})
        require(manifest.get('schema_version') == 'agent-artifacts.v1'
            and manifest.get('job_id') == remote['job_id']
            and manifest.get('attempt_id') == attempt.get('attempt_id')
            and manifest.get('publication_authorized') is False
            and manifest.get('sha256') == manifest_hash == remote.get('artifact_manifest_sha256'),
            'agent_artifact_manifest_mismatch', 502)
        entries, size = {}, 0
        files = manifest.get('files')
        require(type(files) is list and 1 <= len(files) <= 32, 'agent_artifact_manifest_mismatch', 502)
        for item in files:
            name = item.get('name')
            require(type(name) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name)
                and name not in entries and type(item.get('size')) is int
                and 0 <= item['size'] <= 16 * MIB
                and re.fullmatch('[0-9a-f]{64}', str(item.get('sha256'))),
                'agent_artifact_manifest_mismatch', 502)
            from .workspace import identifier
            identifier(item.get('artifact_id'))
            size += item['size']
            entries[name] = item
        require(size <= 24 * MIB and manifest.get('total_bytes') == size
            and [item['name'] for item in files] == sorted(entries), 'agent_artifact_manifest_mismatch', 502)
        downloaded = {}
        def read(name):
            require(name in entries, 'agent_result_missing', 502)
            item = entries[name]
            raw, _ = self._request('GET', '/v1/artifacts/' + item['artifact_id'] + '/content',
                                   raw=True, limit=item['size'])
            require(type(raw) is bytes and len(raw) == item['size']
                and hashlib.sha256(raw).hexdigest() == item['sha256'], 'agent_artifact_mismatch', 502)
            downloaded[name] = raw
            return raw
        if job['stage'] == 'revise':
            result = self._revision_candidate(job, remote, read)
        else:
            from .workflow_contracts import (decode, validate_stage_result, trace_artifacts,
                                             trace_manifest)
            result = decode(read('stage-result.json'))
            validate_stage_result(result, job['task'], job['frozen_context'])
            provenance = result['provenance']
            require(provenance.get('execution_attempt_id') == attempt.get('attempt_id')
                and provenance.get('execution_request_sha256') == proof.get('request_sha256')
                and provenance.get('profile_revision') == job['profile_revision'],
                'agent_result_provenance_mismatch', 502)
            if job['stage'] == 'draft':
                artifacts = trace_artifacts(job['frozen_context'], result['output'])
                require(read('report.md').decode('utf-8') == result['output']['report_markdown'],
                        'agent_artifact_mismatch', 502)
                for role, value in artifacts.items():
                    require(read(TRACE_FILES[role]) == value, 'agent_artifact_mismatch', 502)
        return result, manifest_hash

    def _revision_candidate(self, job, remote, read):
        from .workflow_contracts import decode
        from .pi_revision_contracts import validate_revision_materials
        from .pi_revision_validation import validate_revision_candidate
        result = decode(read('research-result.json'))
        require(result.get('task_id') == job['id']
            and result.get('attempt') == remote['attempt']['fence']
            and result.get('result_digest') == result_digest(result), 'agent_revision_result_mismatch', 502)
        provenance, proof = result.get('provenance') or {}, remote['stop_proof']
        require(provenance.get('profile') == job['profile']
            and provenance.get('profile_revision') == job['profile_revision']
            and provenance.get('execution_attempt_id') == remote['attempt']['attempt_id']
            and provenance.get('execution_request_sha256') == proof.get('request_sha256'),
            'agent_result_provenance_mismatch', 502)
        raw = {role: read(name) for role, name in TRACE_FILES.items()}
        report = read('report.md').decode('utf-8')
        require(hashlib.sha256(report.encode('utf-8')).hexdigest() == result.get('report_sha256'),
                'agent_artifact_mismatch', 502)
        fixed = validate_revision_materials(job['revision_job'], job['revision_material'])
        validate_revision_candidate(fixed['report'], fixed['items'], fixed['artifacts'],
            job['revision_job']['section_id'], job['revision_job']['base_sha256'],
            report, result.get('artifacts'), raw, output=read('revision-output.json'))
        return dict(result, report_markdown=report,
            trace_artifacts={k: base64.b64encode(v).decode('ascii') for k, v in raw.items()})

    def _ingest(self, owner, project_id, task_id, result, manifest_hash):
        def change(project):
            job = _job(project, task_id)
            if job['state'] == 'completed':
                require(job['result_digest'] == result['result_digest'], 'stage_publication_conflict', 409)
                return False
            stale = job['input_revision'] != project['input_revision'] or job['cancel_requested']
            job.update(state='completed', stale=stale, result=copy.deepcopy(result),
                result_digest=result['result_digest'], manifest_sha256=manifest_hash,
                updated_at=utc_now(), error_code=None)
            if project['active_job_id'] == task_id:
                project['active_job_id'] = None
            output = result.get('output', {})
            stage = job['stage']
            if stage == 'plan':
                plan = dict(id=str(uuid4()), revision=job['input_revision'], content=output,
                    output=output, task_id=task_id, created_at=utc_now(), stale=stale,
                    source_policy=copy.deepcopy(job['task']['source_policy']),
                    execution_timeout_seconds=1800)
                project['plan_history'].append(plan)
                if not stale:
                    project['plan'] = copy.deepcopy(plan)
                    project['confirmations'] = dict(plan=None, evidence=None)
                    project['stage'] = 'plan'
            elif stage == 'retrieve':
                retrieval = output['retrieval']
                batch = dict(batch_id=str(uuid4()), project_id=project_id,
                    attempt_id=retrieval['attempt_id'], request_sha256=retrieval['request_sha256'],
                    source_policy=retrieval['source_policy'], searches=output['searches'],
                    papers=output['papers'], observations=output['observations'])
                # Historical stale search results remain in the job result. They
                # cannot leak into the current snapshot through this collection.
                if not stale:
                    project['search_batches'].append(batch)
                    _fill_selections(project)
                    project['stage'] = 'evidence'
                    _new_job(project, 'outline', source_policy=self.source_policy)
                else:
                    project['search_batch_history'].append(dict(batch, stale=True, task_id=task_id))
            elif stage == 'outline':
                snapshot = job['frozen_context']['evidence_snapshot']
                from .workflow_contracts import source_papers
                evidence = dict(id=snapshot['snapshot_id'], snapshot=copy.deepcopy(snapshot),
                    sha256=digest(snapshot), content=output,
                    papers=source_papers(snapshot), created_at=utc_now(), task_id=task_id, stale=stale)
                project['evidence_history'].append(copy.deepcopy(evidence))
                if not stale:
                    project['evidence'] = evidence
                    project['outline'] = dict(id=str(uuid4()), revision=job['input_revision'],
                        content=output, output=output, snapshot_sha256=evidence['sha256'],
                        created_at=utc_now(), task_id=task_id)
                    project['confirmations']['evidence'] = None
                    project['stage'] = 'evidence'
            else:
                self._add_version(project, job, result, stale)
                if not stale:
                    project['stage'] = 'review'
            _event(project, 'stage_completed', dict(task_id=task_id, stage=stage, stale=stale))
        return self.store.internal(owner, project_id, change)

    def _add_version(self, project, job, result, stale):
        if job['stage'] == 'draft':
            from .workflow_contracts import trace_artifacts, trace_manifest
            report = result['output']['report_markdown']
            raw = trace_artifacts(job['frozen_context'], result['output'])
            items = trace_manifest(report, raw)
            artifacts = {role: base64.b64encode(value).decode('ascii') for role, value in raw.items()}
            snapshot_id = job['frozen_context']['evidence_snapshot']['snapshot_id']
            confirmation_id = job['frozen_context']['confirmation']['confirmation_id']
            parent_id = project['current_version_id']
        else:
            report, artifacts = result['report_markdown'], result['trace_artifacts']
            items = [item for item in result['artifacts'] if item['artifact_id'] in
                     {'report', 'papers', 'evidence', 'claims', 'references'}]
            snapshot_id, confirmation_id = job['evidence_snapshot_id'], job['confirmation_id']
            parent_id = job['parent_version_id']
        version_id = str(uuid4())
        sources = [json.loads(line) for line in
            base64.b64decode(artifacts['papers']).decode('utf-8').splitlines() if line]
        version = dict(id=version_id, task_id=job['id'], parent_version_id=parent_id,
            root_version_id=(next((v['root_version_id'] for v in project['versions'] if v['id'] == parent_id), version_id)),
            status='candidate' if parent_id else 'draft', stale=stale,
            input_revision=job['input_revision'], report_markdown=report,
            report_sha256=hashlib.sha256(report.encode('utf-8')).hexdigest(),
            section_index=index_sections(report), trace_manifest=items, trace_artifacts=artifacts,
            sources=sources, evidence_snapshot_id=snapshot_id, confirmation_id=confirmation_id,
            result_digest=result['result_digest'], created_at=utc_now())
        project['versions'].append(version)
        if project['current_version_id'] is None and not stale:
            project['current_version_id'] = version_id

    def _ack(self, owner, project_id, job):
        require(job['state'] == 'completed' and job['result_digest'] and job['manifest_sha256'],
                'stage_not_persisted', 409)
        value = self._request('POST', '/v1/jobs/' + job['remote_job_id'] + '/publication-ack',
            dict(manifest_sha256=job['manifest_sha256'], decision='candidate_received',
                 delivery_ref='scientific-project:' + project_id + ':' + job['id'] + ':' + job['result_digest']),
            headers={'Idempotency-Key': 'scientific-workspace-delivery:' + job['id']})
        require(value.get('recorded') is True and value.get('job_id') == job['remote_job_id'],
                'stage_delivery_ack_mismatch', 502)
        self._set_job(owner, project_id, job['id'], delivery_ack=True)

    def reconcile(self, owner, project_id):
        project = self.store.raw(owner, project_id)
        for initial in project['jobs']:
            task_id = initial['id']
            latest = self.store.raw(owner, project_id)
            job = _job(latest, task_id)
            if job['state'] == 'completed':
                if not job['delivery_ack']:
                    self._ack(owner, project_id, job)
                continue
            if job['state'] in TERMINAL:
                continue
            if not job['admission_started']:
                self.dispatch(owner, project_id, task_id)
                job = _job(self.store.raw(owner, project_id), task_id)
                if job['state'] in TERMINAL:
                    continue
            try:
                remote = self._remote(owner, project_id, job)
            except WorkerError as exc:
                if exc.status != 404:
                    raise
                elapsed = parse_timestamp(utc_now()) >= parse_timestamp(job['execution_deadline_at'])
                self._set_job(owner, project_id, task_id,
                    state=('cancelled' if job['cancel_requested'] else 'timed_out') if elapsed else 'admission_uncertain',
                    error_code='admission_uncertain')
                continue
            job = _job(self.store.raw(owner, project_id), task_id)
            state = remote.get('state')
            self._observe(owner, project_id, job, remote)
            if job['cancel_requested'] and state not in {'completed', 'failed', 'cancelled', 'timed_out'}:
                # The cancellation intent is already durable. Reissuing this
                # idempotent cancel operation never creates another execution.
                remote = self._request('POST', '/v1/jobs/' + remote['job_id'] + '/cancel', {})
                self._check_remote(job, remote)
                state = remote.get('state')
            if state == 'completed':
                try:
                    result, manifest_hash = self._candidate(job, remote)
                except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                    # The completed attempt is stopped; an invalid candidate is
                    # retained as a failure, never substituted into the project.
                    code = getattr(exc, 'code', 'stage_result_invalid')
                    self._set_job(owner, project_id, task_id, state='invalid_result', error_code=code)
                    continue
                try:
                    self._ingest(owner, project_id, task_id, result, manifest_hash)
                except Exception as exc:
                    # A valid stopped result that could not be committed is a
                    # storage failure, never a fabricated validation failure.
                    code = getattr(exc, 'code', 'workspace_storage_unavailable')
                    if code not in {'project_storage_limit', 'project_revision_conflict'}:
                        code = 'workspace_storage_unavailable'
                    def record_reception_failure(project):
                        saved = _job(project, task_id)
                        # A write may have committed before its response was
                        # lost. Never demote that completed receipt: doing so
                        # would publish the same output/version a second time.
                        if saved['state'] == 'completed':
                            require(saved['result_digest'] == result['result_digest']
                                and saved['manifest_sha256'] == manifest_hash,
                                'stage_publication_conflict', 409)
                            return False
                        saved.update(state='receiving', error_code=code, updated_at=utc_now())
                    recovered = self.store.internal(owner, project_id, record_reception_failure)
                    if _job(recovered, task_id)['state'] != 'completed':
                        continue
                self._ack(owner, project_id, _job(self.store.raw(owner, project_id), task_id))
            elif state in {'failed', 'cancelled', 'timed_out', 'needs_input'}:
                self._set_job(owner, project_id, task_id,
                    state='failed' if state == 'needs_input' else state,
                    error_code=remote.get('error_code') or ('stage_needs_input' if state == 'needs_input' else None))
            else:
                require(state in {'queued', 'starting', 'running', 'cancelling', 'quarantined'},
                        'agent_state_invalid', 502)
                self._set_job(owner, project_id, task_id, state=state, error_code=remote.get('error_code'))

    def reconcile_all(self):
        for owner, project_id in self.store.active_projects():
            try:
                self.reconcile(owner, project_id)
            except Exception as exc:
                # Never log request/context/materials, credentials or raw model errors.
                log.warning('research project reconciliation deferred (%s)',
                            getattr(exc, 'code', type(exc).__name__))
