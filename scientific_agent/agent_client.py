"""Scientific business adapter for the neutral Agent Jobs API.

Business IDs, admission intents and publication receipts live in the scientific
database. No model/engine credentials or native session IDs cross this client.
Imports and construction perform no I/O. HTTP mutations are never auto-retried.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

from .pi_client import WorkerError
from .pi_contracts import (ResearchJob, canonical_json, parse_timestamp, report_sha256,
                           result_digest, utc_now, validate_task_id)
from .pi_projection import sql


MIB = 1024 * 1024
LOGICAL_RUNTIME = 'agent-task-contract-v1'
FILES = {'report': ('report.md', 'text/markdown'),
    'searches': ('searches.jsonl', 'application/x-ndjson'),
    'papers': ('papers.jsonl', 'application/x-ndjson'),
    'screening': ('screening.jsonl', 'application/x-ndjson'),
    'evidence': ('evidence.jsonl', 'application/x-ndjson'),
    'claims': ('claims.jsonl', 'application/x-ndjson'),
    'references': ('references.json', 'application/json'),
    'validation': ('validation.json', 'application/json'),
    'academic_observations': ('academic-observations.json', 'application/json'),
    'revision_output': ('revision-output.json', 'application/json')}


def _require(condition, code='agent_protocol_invalid', status=502):
    if not condition:
        raise WorkerError(status, code, 'Agent execution could not be verified', status >= 500)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WorkerError(502, 'agent_protocol_invalid', 'Invalid Agent service response', True) from None


def _wire(value):
    # Neutral Jobs include a finite epoch deadline; the older scientific
    # CanonicalJSON dialect deliberately excludes all floating-point values.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ScientificAgentClient:
    backend = 'agent-service'

    def __init__(self, database, base_url='', token='', *, timeout=15, transport=None,
                 generation_revision='v1', revision_revision='v1', legacy_worker=None,
                 providers=('crossref', 'pubmed', 'arxiv'), max_results=10):
        if base_url:
            parsed = urlsplit(base_url)
            _require(parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
                and not parsed.username and not parsed.password and not parsed.query
                and not parsed.fragment and parsed.path in {'', '/'}
                and not any(ord(c) <= 32 for c in base_url), 'configuration_missing', 503)
        _require(type(timeout) in (int, float) and 0 < timeout <= 60, 'configuration_missing', 503)
        for revision in (generation_revision, revision_revision):
            _require(type(revision) is str and re.fullmatch('[A-Za-z0-9_.-]{1,100}', revision),
                     'configuration_missing', 503)
        self.db, self.base_url, self.token = database, base_url.rstrip('/'), token
        self.timeout, self.transport = timeout, transport
        self.generation_revision, self.revision_revision = generation_revision, revision_revision
        self.legacy_worker, self.versions = legacy_worker, None
        self.providers, self.max_results = tuple(providers), max_results
        _require(self.providers and len(set(self.providers)) == len(self.providers)
            and set(self.providers) <= {'crossref', 'pubmed', 'arxiv', 'openalex', 'semantic_scholar'}
            and type(max_results) is int and 1 <= max_results <= 50, 'configuration_missing', 503)

    def initialize(self):
        self.db._execute('''CREATE TABLE IF NOT EXISTS scientific_agent_dispatch (
            task_id TEXT PRIMARY KEY, business_job TEXT NOT NULL, task TEXT NOT NULL,
            profile TEXT NOT NULL, profile_revision TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
            input_refs TEXT, request_json TEXT, job_id TEXT UNIQUE, admission_started INTEGER NOT NULL DEFAULT 0,
            admission_claim TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0, local_cancelled INTEGER NOT NULL DEFAULT 0,
            terminal_state TEXT,
            result_digest TEXT, report_sha256 TEXT, result_attempt INTEGER,
            artifact_manifest_sha256 TEXT,
            publication_ack TEXT, report_id TEXT, delivery_ack TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')

    def _request(self, method, path, payload=None, *, raw=False, limit=MIB, headers=None):
        _require(self.base_url and type(self.token) is str and 32 <= len(self.token) <= 512
            and all(33 <= ord(c) <= 126 for c in self.token), 'configuration_missing', 503)
        request_headers = {'Authorization': 'Bearer ' + self.token, 'Accept-Encoding': 'identity',
                           'Content-Type': 'application/json'}
        request_headers.update(headers or {})
        data = payload if isinstance(payload, bytes) else (
            None if payload is None else _wire(payload).encode('utf-8'))
        try:
            if self.transport is not None:
                status, response_headers, body = self.transport(method, self.base_url + path,
                    request_headers, data, self.timeout, limit)
            else:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
                request = urllib.request.Request(self.base_url + path, data=data,
                                                  headers=request_headers, method=method)
                try:
                    response = opener.open(request, timeout=self.timeout)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    status, response_headers = response.code, dict(response.headers)
                    body = response.read((65536 if status >= 300 else limit) + 1)
        except (OSError, urllib.error.URLError, TimeoutError):
            raise WorkerError(503, 'agent_unavailable', 'Agent service is temporarily unreachable', True) from None
        _require(type(body) is bytes and len(body) <= (65536 if status >= 300 else limit),
                 'agent_response_too_large')
        normalized_headers = {str(k).lower(): str(v) for k, v in response_headers.items()}
        _require(normalized_headers.get('content-encoding', 'identity').lower() == 'identity')
        _require(not 300 <= status < 400, 'agent_redirect_rejected')
        if not 200 <= status < 300:
            error = _json(body)
            detail = error.get('detail', {}) if isinstance(error, dict) else {}
            code = detail.get('code') if isinstance(detail, dict) else None
            if not isinstance(code, str) or re.fullmatch('[a-z][a-z0-9_]{0,79}', code) is None:
                code = 'agent_request_failed'
            raise WorkerError(status, code, 'Agent service request failed', status >= 500)
        if raw:
            return body, normalized_headers
        value = _json(body)
        _require(type(value) is dict)
        return value

    def _row(self, task_id):
        validate_task_id(task_id)
        rows = self.db._query('SELECT * FROM scientific_agent_dispatch WHERE task_id=' + sql(task_id))
        return rows[0] if rows else None

    def _update(self, task_id, **values):
        values['updated_at'] = utc_now()
        self.db._execute('UPDATE scientific_agent_dispatch SET ' +
            ','.join(key + '=' + sql(value) for key, value in values.items()) + ' WHERE task_id=' + sql(task_id))

    def _legacy(self, operation, task_id, *args, **kwargs):
        if self.legacy_worker is None:
            raise WorkerError(404, 'task_not_found', 'No execution mapping exists')
        return getattr(self.legacy_worker, operation)(task_id, *args, **kwargs)

    def _task(self, job):
        revision = job.get('task_kind') == 'revision'
        task = dict(schema_version='1', business_task_id=job['task_id'], topic=job['topic'], mode=job['mode'],
                    created_at=job['created_at'], execution_deadline_at=job['execution_deadline_at'])
        if revision:
            task.update({key: job[key] for key in ('intent_id', 'parent_version_id', 'root_report_id',
                'base_sha256', 'section_id', 'section_sha256', 'instruction', 'input_sha256', 'material_policy')})
        else:
            task['source_policy'] = {'providers': list(self.providers), 'max_results': self.max_results}
        return task

    def _prepare(self, job):
        revision = job.get('task_kind') == 'revision'
        if revision:
            from .pi_revision_contracts import RevisionJob
            job = RevisionJob.from_dict(job).to_dict()
        else:
            job = ResearchJob.from_dict(job).to_dict()
        _require(job['runtime_revision'] == LOGICAL_RUNTIME, 'task_conflict', 409)
        task_id, now = job['task_id'], utc_now()
        task, encoded = self._task(job), canonical_json(job)
        profile = 'research.revise.v1' if revision else 'research.generate.v1'
        profile_revision = self.revision_revision if revision else self.generation_revision
        values = [task_id, encoded, canonical_json(task), profile, profile_revision,
                  'scientific:' + task_id, now, now]
        self.db._execute('INSERT INTO scientific_agent_dispatch '
            '(task_id,business_job,task,profile,profile_revision,idempotency_key,created_at,updated_at) VALUES ('
            + ','.join(map(sql, values)) + ') ON CONFLICT(task_id) DO NOTHING')
        row = self._row(task_id)
        _require(row is not None and row['business_job'] == encoded, 'task_conflict', 409)
        # Existing tasks preserve their original source policy and route revision.
        return row

    def _upload(self, name, raw):
        _require(type(raw) is bytes and len(raw) <= 16 * MIB, 'revision_input_too_large', 413)
        value = self._request('POST', '/v1/inputs?' + urlencode({'name': name}), raw,
                              headers={'Content-Type': 'application/octet-stream'})
        validate_task_id(value.get('input_id'))
        digest = hashlib.sha256(raw).hexdigest()
        _require(value.get('name') == name and value.get('size') == len(raw) and value.get('sha256') == digest)
        return {'input_id': value['input_id'], 'sha256': digest}

    def _inputs(self, row, material):
        if row['input_refs'] is not None:
            return _json(row['input_refs'])
        refs = []
        if material is not None:
            job = _json(row['business_job'])
            from .pi_revision_contracts import validate_revision_materials
            from .pi_sections import select_section
            checked = validate_revision_materials(job, material)
            section = select_section(checked['report'], job['section_id'], job['base_sha256'])
            files = {'revision-materials.json': canonical_json(material).encode('utf-8'),
                'input-report.md': checked['report'].encode('utf-8'),
                'input-section.md': section['text'].encode('utf-8')}
            files.update({FILES[name][0]: raw for name, raw in checked['artifacts'].items()})
            _require(sum(map(len, files.values())) <= 32 * MIB, 'revision_input_too_large', 413)
            refs = [self._upload(name, raw) for name, raw in sorted(files.items())]
        encoded = canonical_json(refs)
        self.db._execute('UPDATE scientific_agent_dispatch SET input_refs=' + sql(encoded) +
            ',updated_at=' + sql(utc_now()) + ' WHERE task_id=' + sql(row['task_id']) + ' AND input_refs IS NULL')
        return _json(self._row(row['task_id'])['input_refs'])

    def _remote(self, row):
        if row['job_id']:
            value = self._request('GET', '/v1/jobs/' + row['job_id'])
        else:
            # A read-only lookup is essential after a lost POST response,
            # especially when cancellation is already durable.
            value = self._request('GET', '/v1/jobs/by-idempotency/' + quote(row['idempotency_key'], safe=''))
            self._bind(row, value)
        self._check_remote(row, value)
        return value

    def _check_remote(self, row, value):
        validate_task_id(value.get('job_id'))
        pins = value.get('pins', {})
        _require(value.get('business_ref') == row['task_id'] and type(pins) is dict
            and pins.get('profile') == row['profile'] and pins.get('profile_revision') == row['profile_revision']
            and (not row['job_id'] or row['job_id'] == value['job_id']))

    def _bind(self, row, value):
        self._check_remote(row, value)
        self.db._execute('UPDATE scientific_agent_dispatch SET job_id=' + sql(value['job_id'])
            + ',updated_at=' + sql(utc_now()) + ' WHERE task_id=' + sql(row['task_id']) + ' AND job_id IS NULL')
        _require(self._row(row['task_id'])['job_id'] == value['job_id'], 'task_conflict', 409)

    def _submit(self, row, material=None):
        row = self._row(row['task_id'])
        if row['terminal_state']:
            return self._snapshot(row)
        if row['local_cancelled'] or row['cancel_requested']:
            return self.cancel(row['task_id'])
        if row['job_id']:
            return self.get(row['task_id'])
        if row['admission_started']:
            # The original POST may still be in flight, may have committed, or
            # may never have reached the service. Recovery observes its durable
            # idempotency record and never submits another mutation by guessing.
            return self.get(row['task_id'])
        if row['request_json'] is None:
            job = _json(row['business_job'])
            request = dict(api_version='1', profile=row['profile'], profile_revision=row['profile_revision'],
                business_ref=row['task_id'], task=_json(row['task']), inputs=self._inputs(row, material),
                timeout_seconds=job['timeout_seconds'],
                execution_deadline_at=float(parse_timestamp(job['execution_deadline_at']).timestamp()))
            self.db._execute('UPDATE scientific_agent_dispatch SET request_json=' + sql(_wire(request))
                + ',updated_at=' + sql(utc_now()) + ' WHERE task_id=' + sql(row['task_id']) + ' AND request_json IS NULL')
            row = self._row(row['task_id'])
        claim = str(uuid4())
        self.db._execute('UPDATE scientific_agent_dispatch SET admission_started=1,admission_claim=' + sql(claim)
            + ',updated_at=' + sql(utc_now()) + ' WHERE task_id=' + sql(row['task_id'])
            + ' AND admission_started=0 AND cancel_requested=0 AND local_cancelled=0')
        row = self._row(row['task_id'])
        if row['cancel_requested'] or row['local_cancelled']:
            return self.cancel(row['task_id'])
        if row['admission_claim'] != claim:
            return self._submit(row, material)
        response = self._request('POST', '/v1/jobs', _json(row['request_json']),
                                  headers={'Idempotency-Key': row['idempotency_key']})
        self._bind(row, response)
        row = self._row(row['task_id'])
        if row['cancel_requested']:
            return self.cancel(row['task_id'])
        return self._snapshot(row, response)

    def put(self, job):
        if job.get('runtime_revision') != LOGICAL_RUNTIME:
            _require(self.legacy_worker is not None, 'legacy_execution_unavailable', 503)
            return self.legacy_worker.put(job)
        return self._submit(self._prepare(job))

    def put_revision(self, job, material):
        if job.get('runtime_revision') != LOGICAL_RUNTIME:
            _require(self.legacy_worker is not None, 'legacy_execution_unavailable', 503)
            return self.legacy_worker.put_revision(job, material)
        return self._submit(self._prepare(job), material)

    def _snapshot(self, row, remote=None):
        job = _json(row['business_job'])
        if row['terminal_state']:
            state = row['terminal_state']
            attempt = (remote.get('attempt') or {}).get('fence', 0) if remote else 0
        elif row['local_cancelled']:
            state, attempt = 'cancelled', 0
        elif remote is None:
            state, attempt = 'cancel_requested', 0
        else:
            mapping = {'queued': 'queued', 'starting': 'queued', 'running': 'running',
                'cancelling': 'cancel_requested', 'cancelled': 'cancelled', 'failed': 'failed',
                'timed_out': 'timed_out', 'quarantined': 'running', 'needs_input': 'failed',
                'completed': 'succeeded' if row['publication_ack'] else 'publishing'}
            _require(remote.get('state') in mapping)
            state = mapping[remote['state']]
            attempt = (remote.get('attempt') or {}).get('fence', 0)
            if state in ('failed', 'cancelled', 'timed_out') and row['terminal_state'] != state:
                self._update(row['task_id'], terminal_state=state)
        return dict(id=row['task_id'], topic=job['topic'], engine='agent-service', mode=job['mode'],
            state=state, attempt=attempt, revision=0, schema_version='1', created_at=job['created_at'],
            updated_at=row['updated_at'], execution_deadline_at=job['execution_deadline_at'],
            error_code=remote.get('error_code') if remote else None,
            error='Execution needs additional input' if remote and remote.get('state') == 'needs_input' else None)

    def get(self, task_id):
        row = self._row(task_id)
        if row is None:
            # A new prepared business record is not a legacy worker task.
            index = self.db._query('SELECT job FROM scientific_pi_index WHERE id=' + sql(task_id))
            if index and _json(index[0]['job']).get('runtime_revision') == LOGICAL_RUNTIME:
                raise WorkerError(404, 'task_not_found', 'Execution has not been admitted')
            return self._legacy('get', task_id)
        if row['local_cancelled'] or row['terminal_state']:
            return self._snapshot(row)
        try:
            remote = self._remote(row)
        except WorkerError as exc:
            if exc.status == 404 and row['cancel_requested']:
                job = _json(row['business_job'])
                if parse_timestamp(job['execution_deadline_at']) <= parse_timestamp(utc_now()):
                    self._update(task_id, local_cancelled=1)
                return self._snapshot(self._row(task_id))
            if exc.status == 404:
                if parse_timestamp(_json(row['business_job'])['execution_deadline_at']) <= parse_timestamp(utc_now()):
                    self._update(task_id, terminal_state='timed_out')
                    return self._snapshot(self._row(task_id))
                return self._snapshot(row, {'state': 'queued', 'error_code': 'admission_uncertain'})
            raise
        row = self._row(task_id)
        if row['cancel_requested'] and remote['state'] not in ('completed', 'failed', 'cancelled', 'timed_out'):
            remote = self._request('POST', '/v1/jobs/' + row['job_id'] + '/cancel', {})
        return self._snapshot(row, remote)

    def cancel(self, task_id):
        row = self._row(task_id)
        if row is None:
            index = self.db._query('SELECT job FROM scientific_pi_index WHERE id=' + sql(task_id))
            if not index or _json(index[0]['job']).get('runtime_revision') != LOGICAL_RUNTIME:
                return self._legacy('cancel', task_id)
            row = self._prepare(_json(index[0]['job']))
        self._update(task_id, cancel_requested=1)
        row = self._row(task_id)
        if not row['admission_started']:
            self._update(task_id, local_cancelled=1)
            return self._snapshot(self._row(task_id))
        return self.get(task_id)

    def _files(self, row, remote):
        manifest = self._request('GET', '/v1/jobs/' + remote['job_id'] + '/artifact-manifest')
        entries = manifest.get('files')
        body = {key: value for key, value in manifest.items() if key != 'sha256'}
        manifest_hash = hashlib.sha256(_wire(body).encode('utf-8')).hexdigest()
        attempt_id = (remote.get('attempt') or {}).get('attempt_id')
        _require(manifest.get('schema_version') == 'agent-artifacts.v1'
            and manifest.get('job_id') == remote['job_id'] and manifest.get('attempt_id') == attempt_id
            and manifest.get('sha256') == manifest_hash and manifest.get('publication_authorized') is False
            and remote.get('artifact_manifest_sha256') == manifest_hash)
        _require(type(entries) is list and 1 <= len(entries) <= 128)
        result, total = {}, 0
        for item in entries:
            _require(type(item) is dict and item.get('name') not in result
                and type(item.get('name')) is str and '/' not in item['name'] and '\\' not in item['name']
                and type(item.get('size')) is int and 0 <= item['size'] <= 16 * MIB
                and type(item.get('sha256')) is str and re.fullmatch('[0-9a-f]{64}', item['sha256']))
            validate_task_id(item.get('artifact_id'))
            total += item['size']
            result[item['name']] = item
        _require(total <= 50 * MIB and manifest.get('total_bytes') == total
            and [item['name'] for item in entries] == sorted(result))
        if row['artifact_manifest_sha256'] is not None:
            _require(row['artifact_manifest_sha256'] == manifest_hash, 'publication_conflict', 409)
        else:
            self.db._execute('UPDATE scientific_agent_dispatch SET artifact_manifest_sha256=' + sql(manifest_hash)
                + ',updated_at=' + sql(utc_now()) + ' WHERE task_id=' + sql(row['task_id'])
                + ' AND artifact_manifest_sha256 IS NULL')
            _require(self._row(row['task_id'])['artifact_manifest_sha256'] == manifest_hash,
                     'publication_conflict', 409)
        return result

    def _download(self, item):
        raw, headers = self._request('GET', '/v1/artifacts/' + item['artifact_id'] + '/content',
                                      raw=True, limit=item['size'])
        _require(len(raw) == item['size'] and hashlib.sha256(raw).hexdigest() == item['sha256'],
                 'artifact_size_mismatch')
        return raw, headers

    def _candidate(self, row):
        remote = self._remote(row)
        _require(remote.get('state') == 'completed', 'result_not_ready', 409)
        proof = remote.get('stop_proof') or {}
        _require(type(proof) is dict and remote.get('stop_proven') is True and proof.get('stop_proven') is True
            and proof.get('attempt_id') == remote['attempt']['attempt_id']
            and proof.get('fence') == remote['attempt']['fence']
            and type(proof.get('request_sha256')) is str
            and re.fullmatch('[0-9a-f]{64}', proof['request_sha256'])
            and remote.get('publication_authorized') is False)
        entries = self._files(row, remote)
        _require('research-result.json' in entries)
        result = _json(self._download(entries['research-result.json'])[0])
        revision = _json(row['business_job']).get('task_kind') == 'revision'
        definitions = {key: value for key, value in FILES.items()
            if key not in {'revision_output', 'academic_observations'}}
        extra = 'revision_output' if revision else 'academic_observations'
        definitions[extra] = FILES[extra]
        _require(type(result) is dict and result.get('task_id') == row['task_id']
            and result.get('attempt') == remote['attempt']['fence']
            and type(result.get('artifacts')) is list
            and len(result['artifacts']) == len(definitions)
            and result_digest(result) == result.get('result_digest'))
        indexed = {}
        for item in result['artifacts']:
            role = item.get('artifact_id')
            _require(role in definitions and role not in indexed)
            name, media = definitions[role]
            actual = entries.get(name)
            _require(actual is not None and item.get('size') == actual['size']
                and item.get('sha256') == actual['sha256'] and item.get('media_type') == media)
            indexed[role] = actual
        provenance = result.get('provenance')
        binding = remote.get('pins', {}).get('execution_binding') or {}
        _require(type(provenance) is dict and provenance.get('profile') == row['profile']
            and provenance.get('profile_revision') == row['profile_revision']
            and all(provenance.get(key) == remote['pins'].get(key)
                    for key in ('engine', 'runtime_revision', 'image_digest'))
            and provenance.get('execution_binding_sha256') == binding.get('sha256')
            and provenance.get('execution_attempt_id') == remote['attempt']['attempt_id']
            and provenance.get('execution_request_sha256') == proof.get('request_sha256')
            and bool(binding.get('sha256')))
        return result, indexed, entries

    def result(self, task_id):
        row = self._row(task_id)
        if row is None:
            return self._legacy('result', task_id)
        result, indexed, _ = self._candidate(row)
        contents = {role: self._download(item)[0] for role, item in indexed.items()}
        _require(len(contents['report']) <= 2 * MIB)
        try:
            report = contents['report'].decode('utf-8')
        except UnicodeError:
            raise WorkerError(502, 'agent_output_invalid', 'Report is not valid UTF-8') from None
        _require(report_sha256(report) == result['report_sha256'])
        _require(_json(contents['validation']) == result.get('validation'))
        from .pi_citations import build_citation_trace
        build_citation_trace(report, result['artifacts'],
            {name: contents[name] for name in ('papers', 'evidence', 'claims', 'references')})
        job = _json(row['business_job'])
        if job.get('task_kind') != 'revision':
            from .academic_observations import AcademicObservationError, validate_academic_observations
            try:
                validate_academic_observations(contents['academic_observations'], contents['searches'],
                    contents['papers'], attempt_id=result['provenance']['execution_attempt_id'],
                    request_sha256=result['provenance']['execution_request_sha256'],
                    source_policy=_json(row['task'])['source_policy'])
            except AcademicObservationError:
                raise WorkerError(409, 'publication_conflict', 'Academic observations do not match the candidate') from None
            for line in contents['papers'].decode('utf-8').splitlines():
                if line.strip():
                    paper = _json(line)
                    _require(paper.get('full_text_path') is None
                        and paper.get('availability', {}).get('full_text', {}).get('available') is False,
                        'publication_conflict', 409)
            from pi_worker.research_tools.validation import validate_research_outputs
            with tempfile.TemporaryDirectory(prefix='scientific-publication-') as path:
                staged = Path(path)
                for role, raw in contents.items():
                    if role != 'validation':
                        (staged / FILES[role][0]).write_bytes(raw)
                independent = validate_research_outputs(staged)
            _require(independent.get('valid') is True, 'publication_conflict', 409)
        else:
            _require(self.versions is not None, 'revision_job_conflict', 409)
            from .pi_revision_contracts import validate_revision_materials
            from .pi_revision_validation import validate_revision_candidate
            fixed = validate_revision_materials(job, self.versions.revision_materials(job['user_id'], task_id))
            validate_revision_candidate(fixed['report'], fixed['items'], fixed['artifacts'],
                job['section_id'], job['base_sha256'], report, result['artifacts'],
                {key: contents[key] for key in ('papers', 'evidence', 'claims', 'references')},
                output=contents['revision_output'])
        if row['result_digest']:
            _require(row['result_digest'] == result['result_digest'], 'publication_conflict', 409)
        else:
            self.db._execute('UPDATE scientific_agent_dispatch SET result_digest=' + sql(result['result_digest'])
                + ',report_sha256=' + sql(result['report_sha256']) + ',result_attempt=' + str(result['attempt'])
                + ',updated_at=' + sql(utc_now()) + ' WHERE task_id=' + sql(task_id) + ' AND result_digest IS NULL')
            _require(self._row(task_id)['result_digest'] == result['result_digest'], 'publication_conflict', 409)
        return dict(result, report_markdown=report)

    def artifacts(self, task_id):
        row = self._row(task_id)
        if row is None:
            return self._legacy('artifacts', task_id)
        result, _, _ = self._candidate(row)
        return {'items': result['artifacts']}

    def artifact(self, task_id, artifact_id, max_bytes=None):
        row = self._row(task_id)
        if row is None:
            return self._legacy('artifact', task_id, artifact_id, max_bytes=max_bytes)
        _, indexed, entries = self._candidate(row)
        if artifact_id == 'manifest':
            item, media = entries['research-result.json'], 'application/json'
        else:
            _require(artifact_id in indexed, 'artifact_not_found', 404)
            item, media = indexed[artifact_id], FILES[artifact_id][1]
        _require(max_bytes is None or type(max_bytes) is int and 0 <= item['size'] <= max_bytes,
                 'artifact_size_mismatch')
        raw, _ = self._download(item)
        return raw, {'Content-Type': media}

    def ack(self, task_id, payload):
        row = self._row(task_id)
        if row is None:
            return self._legacy('ack', task_id, payload)
        _require(type(payload) is dict and set(payload) == {'attempt', 'result_digest', 'report_id'}
            and payload['report_id'] == task_id and payload['attempt'] == row['result_attempt']
            and payload['result_digest'] == row['result_digest'] and bool(row['result_digest']),
            'publication_conflict', 409)
        reports = self.db._query('SELECT task_id,content FROM scientific_reports WHERE id=' + sql(task_id))
        _require(reports and reports[0]['task_id'] == task_id
            and report_sha256(reports[0]['content']) == row['report_sha256'], 'publication_conflict', 409)
        encoded = canonical_json(payload)
        self.db._execute('UPDATE scientific_agent_dispatch SET publication_ack=' + sql(encoded)
            + ',report_id=' + sql(task_id) + ',updated_at=' + sql(utc_now())
            + ' WHERE task_id=' + sql(task_id) + ' AND publication_ack IS NULL')
        _require(self._row(task_id)['publication_ack'] == encoded, 'publication_conflict', 409)
        # This durable local receipt is the business publication authority.
        # Generic delivery acknowledgement never makes a report public.
        try:
            self._delivery_ack(self._row(task_id))
        except WorkerError:
            # The report is already committed. A failed/ambiguous remote receipt
            # is replayed from this durable outbox without rerunning the model.
            pass
        return {'acknowledged': True, 'report_id': task_id}

    def _delivery_ack(self, row):
        if row['delivery_ack'] is not None:
            return
        _require(row['publication_ack'] and row['job_id'] and row['artifact_manifest_sha256'],
                 'publication_conflict', 409)
        document = dict(manifest_sha256=row['artifact_manifest_sha256'], decision='candidate_received',
            delivery_ref='scientific-report:' + row['task_id'] + ':' + row['result_digest'])
        value = self._request('POST', '/v1/jobs/' + row['job_id'] + '/publication-ack', document,
            headers={'Idempotency-Key': 'scientific-delivery:' + row['task_id']})
        _require(value.get('job_id') == row['job_id'] and value.get('recorded') is True
            and value.get('publication_authorized') is False)
        self._update(row['task_id'], delivery_ack=_wire(value))

    def replay_delivery_acknowledgements(self):
        rows = self.db._query('SELECT * FROM scientific_agent_dispatch WHERE publication_ack IS NOT NULL'
                              + ' AND delivery_ack IS NULL ORDER BY updated_at,task_id LIMIT 50')
        for row in rows:
            try:
                self._delivery_ack(row)
            except WorkerError:
                self._update(row['task_id'])

    def pending(self, cursor=None):
        if cursor and cursor.startswith('legacy:'):
            return self._legacy_pending(cursor.removeprefix('legacy:') or None)
        if cursor:
            validate_task_id(cursor)
        condition = '(job_id IS NOT NULL OR admission_started=1) AND publication_ack IS NULL'
        condition += ' AND terminal_state IS NULL AND local_cancelled=0'
        if cursor:
            condition += ' AND task_id>' + sql(cursor)
        rows = self.db._query('SELECT * FROM scientific_agent_dispatch WHERE ' + condition
                              + ' ORDER BY task_id LIMIT 50')
        items = []
        for row in rows:
            try:
                snapshot = self.get(row['task_id'])
                if snapshot['state'] == 'publishing':
                    items.append(snapshot)
            except WorkerError as exc:
                if exc.status not in (404, 409):
                    raise
        if len(rows) == 50:
            return {'items': items, 'next_cursor': rows[-1]['task_id']}
        legacy = self._legacy_pending(None)
        return {'items': items + legacy['items'], 'next_cursor': legacy.get('next_cursor')}

    def _legacy_pending(self, cursor):
        if self.legacy_worker is None:
            return {'items': [], 'next_cursor': None}
        page = self.legacy_worker.pending(cursor)
        return dict(page, next_cursor='legacy:' + page['next_cursor'] if page.get('next_cursor') else None)

    def health(self):
        value = self._request('GET', '/v1/capabilities')
        profiles = value.get('profiles', [])
        _require(type(profiles) is list)
        configured = {item.get('profile'): item for item in profiles if isinstance(item, dict)}
        generate = configured.get('research.generate.v1', {})
        revise = configured.get('research.revise.v1', {})
        generate_ready = generate.get('ready') is True and generate.get('profile_revision') == self.generation_revision
        revise_ready = revise.get('ready') is True and revise.get('profile_revision') == self.revision_revision
        available = generate_ready or revise_ready
        return {'alive': True, 'queue_writable': available, 'runtime_ready': available,
            'worker_ready': available, 'backend': self.backend,
            'capabilities': {'profiles': profiles, 'revision_job_versions': ['1'] if revise_ready else [],
                'revision_input_scope': 'recorded_evidence_only' if revise_ready else None,
                'max_revision_input_bytes': 16 * MIB, 'native_readiness_checked_at_execution': True}}


AgentClient = ScientificAgentClient
