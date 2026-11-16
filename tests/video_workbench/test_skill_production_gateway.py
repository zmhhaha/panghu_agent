"""Real HTTP client, synthetic stopped handoffs; no model, rendering or approval."""
from copy import deepcopy
import hashlib
import json
from uuid import UUID

import httpx
import pytest

from agent_service.animation_skills_contract import build_skill_package, bundle_files
from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_skill_production import (
    PROFILE, INPUT_NAME, INPUT_ROLE, RESULT_NAME, EVIDENCE_NAME,
)
from agent_service.profiles.video_animation_skill_production_contract import CORE_FIELDS, RESULT_SCHEMA
from agent_service.skill_tool_evidence import SkillToolObserver
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.creative_skill_contract import build_scene_input, collect_scene_result
from video_workbench.skill_production_gateway import (
    RESOURCE_ROLE, UPLOAD_SCHEMA, SkillProductionAgentGateway,
)
from test_creative_film_renderer import film_scene


JOB, ATTEMPT = (str(UUID(int=i)) for i in (240, 241))
TOKEN = 'offline-skill-production-token-' + 'a' * 32


def method(index=1):
    package = build_skill_package(skill_id=f'identity-flow-{index}', version=1, kind='expression',
        files={'SKILL.md': f'---\nname: identity-flow-{index}\ndescription: Follow identity through change.\n---\n'
            'Keep the subject recognizable. Read [checks](references/checks.md) when needed.',
            'references/checks.md': 'Observe the transition itself, not only its endpoints.'},
        runtime_requirements={'engines': ['html-svg']})
    value = dict(schema='video.animation-method.v2', id=f'fixture-method-{index}', status='published',
                 content_sha256=digest({'fixture': index}), skill_package=package)
    value['publication'] = dict(schema='video.animation-method-publication.v2',
        method_version_id=value['id'], method_content_sha256=value['content_sha256'],
        skill_package_sha256=package['sha256'], reference_validation_ids=['fixture-reference'],
        transfer_validation_ids=['fixture-transfer'])
    return value


class Peer:
    def __init__(self):
        self.calls, self.uploads, self.job, self.request = [], {}, None, None
        self.profile = dict(profile=PROFILE, profile_revision='1', configured=True, ready=True,
            supports_inputs=True, max_timeout_seconds=600, required_model_capabilities=['text', 'tool_calls'])
        self.profiles = [self.profile]
        self.upload_change, self.lose_response, self.malformed_submission = None, False, False
        self.http_errors = {}
        self.contents = {}

    def __call__(self, request):
        self.calls.append(request)
        method_name, path = request.method, request.url.path
        if (method_name, path) in self.http_errors:
            return httpx.Response(self.http_errors[method_name, path], json={'detail': {'code': 'fixture_outage'}})
        if path == '/v1/capabilities':
            return httpx.Response(200, json={'profiles': self.profiles})
        if path == '/v1/inputs' and method_name == 'POST':
            name = request.url.params['name']
            self.uploads[name] = request.content
            receipt = dict(input_id=str(UUID(int=300 + len(self.uploads))), name=name,
                           size=len(request.content), sha256=hashlib.sha256(request.content).hexdigest())
            if self.upload_change:
                receipt.update(self.upload_change)
            return httpx.Response(201, json=receipt)
        if path == '/v1/jobs' and method_name == 'POST':
            self.request = json.loads(request.content)
            self.job = dict(job_id=JOB, state='queued', business_ref=self.request['business_ref'],
                            publication_authorized=False)
            if self.lose_response:
                raise httpx.ReadTimeout('Offline lost response', request=request)
            return httpx.Response(202, json={} if self.malformed_submission else self.job)
        if path.startswith('/v1/jobs/by-idempotency/') or path == '/v1/jobs/' + JOB:
            return httpx.Response(200, json=self.job) if self.job else httpx.Response(404, json={'detail': {'code': 'not_found'}})
        if path == '/v1/jobs/' + JOB + '/artifact-manifest':
            return httpx.Response(200, json=self.manifest)
        for identity, raw in self.contents.items():
            if path == '/v1/artifacts/' + identity + '/content':
                return httpx.Response(200, content=raw)
        raise AssertionError((method_name, path))

    def complete(self, document, delivery, source_result, *, read=True):
        observer = SkillToolObserver(delivery['catalog'], delivery['packages'], input_sha256=digest(document))
        decisions = []
        if read:
            package_sha = next(iter(delivery['packages']))
            params = {'package_sha256': package_sha}
            observer.observe(dict(type='tool_execution_start', toolName='skill_read', toolCallId='fixture-read', args=params))
            observer.observe(dict(type='tool_execution_end', toolName='skill_read', toolCallId='fixture-read',
                isError=False, result=observer.tools.tool_result('skill_read', params)))
            target = source_result['handoff']['objects'][0]
            decisions = [dict(package_sha256=package_sha, action='adapt', reason='Keeps the same subject recognizable.',
                scene_targets=[dict(target_id='tracked-object', description='Track the same moving subject.',
                    source_file=target['source_file'], source_anchor=target['source_anchor'])])]
        evidence = observer.finish(ATTEMPT)
        result = dict(schema=RESULT_SCHEMA, input_sha256=digest(document),
            source=deepcopy(source_result['source']), skill_decisions=decisions,
            handoff=deepcopy(source_result['handoff']), skill_tool_evidence_sha256=evidence['sha256'])
        self.artifact_ids = {RESULT_NAME: str(UUID(int=245)), EVIDENCE_NAME: str(UUID(int=246))}
        self.documents = {RESULT_NAME: result, EVIDENCE_NAME: evidence}
        self.contents = {self.artifact_ids[name]: canonical_bytes(value) for name, value in self.documents.items()}
        self.manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=self.artifact_ids[name], name=name,
            size=len(self.contents[self.artifact_ids[name]]), sha256=hashlib.sha256(
                self.contents[self.artifact_ids[name]]).hexdigest()) for name in (RESULT_NAME, EVIDENCE_NAME)])
        self.job = dict(job_id=JOB, state='completed', business_ref='video-skill-production:operation-1',
            publication_authorized=False, artifact_manifest_sha256=self.manifest['sha256'],
            pins=dict(profile=PROFILE, profile_revision='1'), attempt=dict(attempt_id=ATTEMPT, fence=1),
            stop_proven=True, stop_proof=dict(stop_proven=True, attempt_id=ATTEMPT, fence=1,
                request_sha256='a' * 64, result_sha256='b' * 64, resource_ids=['fixture-stopped-writer'], observed_at=1000.0))
        return result, evidence

    def rehash_manifest(self):
        self.manifest['sha256'] = digest({key: value for key, value in self.manifest.items() if key != 'sha256'})
        self.job['artifact_manifest_sha256'] = self.manifest['sha256']

    def rewrite(self, name, value=None, *, raw=None):
        content = canonical_bytes(value) if raw is None else raw
        self.contents[self.artifact_ids[name]] = content
        item = next(row for row in self.manifest['files'] if row['name'] == name)
        item.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
        self.manifest['total_bytes'] = sum(row['size'] for row in self.manifest['files'])
        self.rehash_manifest()

    def count(self, method_name, path):
        return sum(row.method == method_name and row.url.path == path for row in self.calls)


@pytest.fixture
def wire(tmp_path, monkeypatch):
    monkeypatch.setattr('video_workbench.skill_production_gateway.time.time', lambda: 1000.0)
    source_result = film_scene('html-svg')['creative']['package']['result']
    old = film_scene('html-svg')['creative']['package']['input_document']
    core = {key: old[key] for key in CORE_FIELDS}
    methods = [method(i) for i in range(5)]
    adapter = SkillProductionAdapter(lambda owner: deepcopy(methods), None, tmp_path / 'skill-freezes.sqlite3')
    delivery = adapter.freeze('alice', 'fixture-production', core, core['constraints']['allowed_engines'])
    document = build_scene_input(core, delivery)
    peer = Peer()
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        yield SkillProductionAgentGateway(client, adapter), peer, document, delivery, source_result, adapter


def test_uploads_every_frozen_file_then_submits_exact_normal_refs(wire):
    gateway, peer, document, delivery, _, _ = wire
    receipt = gateway.upload(document)
    expected = {INPUT_NAME: canonical_bytes(document), **{file.name: file.content()
        for file in bundle_files(delivery['catalog'], delivery['packages'])}}
    assert peer.uploads == expected and len(document['skill_catalog']['skills']) == 5
    assert receipt['schema'] == UPLOAD_SCHEMA and receipt['input_sha256'] == digest(document)
    assert receipt['skill_delivery_sha256'] == delivery['sha256']
    assert all(ref['role'] == (INPUT_ROLE if ref['path'] == INPUT_NAME else RESOURCE_ROLE) for ref in receipt['inputs'])
    assert all('Keep the subject recognizable' not in text for text in [peer.uploads[INPUT_NAME].decode()])
    admit = gateway.prepare_submission('operation-1', document, 1250, receipt)
    assert peer.count('POST', '/v1/jobs') == 0
    receipt['inputs'][0]['input_id'] = str(UUID(int=999))  # The prepared request is frozen.
    admit()
    assert peer.request['profile'] == PROFILE and peer.request['business_ref'] == 'video-skill-production:operation-1'
    assert peer.request['task'] == {'input_sha256': digest(document)}
    assert len(peer.request['inputs']) == len(expected) and peer.request.get('checkpoint_ref') is None
    assert peer.request['inputs'][0]['input_id'] != str(UUID(int=999))
    submit = next(row for row in peer.calls if row.method == 'POST' and row.url.path == '/v1/jobs')
    assert submit.headers['idempotency-key'] == 'skill-production-operation-1'


@pytest.mark.parametrize('change', [{'input_id': 'invalid'}, {'name': 'wrong.json'}, {'size': True}, {'sha256': '0' * 64},
                                  {'input_id': str(UUID(int=1))}])
def test_wrong_upload_receipts_and_reused_input_ids_never_submit(wire, change):
    gateway, peer, document, *_ = wire
    peer.upload_change = change
    with pytest.raises(ServiceError):
        gateway.upload(document)
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('change', ['missing', 'extra', 'hash', 'id', 'role', 'path', 'duplicate-id', 'delivery', 'catalog'])
def test_upload_ref_membership_is_rechecked_before_paid_admission(wire, change):
    gateway, peer, document, *_ = wire
    receipt = gateway.upload(document)
    if change == 'missing': receipt['inputs'].pop()
    elif change == 'extra': receipt['inputs'].append(deepcopy(receipt['inputs'][0]))
    elif change == 'hash': receipt['inputs'][-1]['sha256'] = '0' * 64
    elif change == 'id': receipt['inputs'][0]['input_id'] = 'invalid'
    elif change == 'role': receipt['inputs'][0]['role'] = RESOURCE_ROLE
    elif change == 'path': receipt['inputs'][-1]['path'] = '../escape'
    elif change == 'duplicate-id': receipt['inputs'][-1]['input_id'] = receipt['inputs'][0]['input_id']
    elif change == 'delivery': receipt['skill_delivery_sha256'] = '0' * 64
    else: receipt['catalog_sha256'] = '0' * 64
    receipt['sha256'] = digest({key: value for key, value in receipt.items() if key != 'sha256'})
    with pytest.raises(ServiceError):
        gateway.submit('operation-1', document, 1250, receipt)
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('change', ['disabled', 'not-ready', 'wrong-profile', 'duplicate', 'model', 'outage', 'deadline'])
def test_preflight_failure_posts_no_job(wire, change):
    gateway, peer, document, *_ = wire
    receipt = gateway.upload(document)
    if change == 'disabled': peer.profile['configured'] = False
    elif change == 'not-ready': peer.profile['ready'] = False
    elif change == 'wrong-profile': peer.profile['profile'] = 'video.animation-production.v2'
    elif change == 'duplicate': peer.profiles *= 2
    elif change == 'model': peer.profile['required_model_capabilities'] = ['text']
    elif change == 'outage': peer.http_errors['GET', '/v1/capabilities'] = 503
    with pytest.raises(ServiceError):
        gateway.submit('operation-1', document, 1000 if change == 'deadline' else 1250, receipt)
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('mode', ['timeout', 'malformed'])
def test_ambiguous_admission_uses_same_lookup_key_without_a_second_job(wire, mode):
    gateway, peer, document, *_ = wire
    receipt = gateway.upload(document)
    peer.lose_response, peer.malformed_submission = mode == 'timeout', mode == 'malformed'
    with pytest.raises(ServiceError, match='service_'):
        gateway.submit('operation-1', document, 1250, receipt)
    assert gateway.lookup('operation-1')['job_id'] == JOB and gateway.poll(JOB)['job_id'] == JOB
    assert peer.count('POST', '/v1/jobs') == 1


@pytest.mark.parametrize('read', [False, True])
def test_collect_returns_exact_host_evidence_delivery_and_valid_business_package(wire, read):
    gateway, peer, document, delivery, source_result, _ = wire
    result, evidence = peer.complete(document, delivery, source_result, read=read)
    collected = gateway.collect(JOB, digest(document), document)
    assert collected == dict(result=result, evidence=evidence, skill_delivery=delivery)
    package = collect_scene_result(document, **collected)
    assert package['schema'] == 'video.creative-scene.v5'
    assert len(collected['evidence']['events']) == (1 if read else 0)


@pytest.mark.parametrize('change', ['stop', 'attempt', 'fence', 'writers', 'profile', 'revision', 'publication',
    'manifest-binding', 'manifest-attempt', 'missing-evidence', 'duplicate-name', 'duplicate-id', 'total',
    'bytes', 'wrong-input', 'evidence-attempt', 'evidence-owner', 'evidence-source', 'evidence-hash',
    'self-reported-read', 'result-evidence-hash', 'duplicate-json'])
def test_untrusted_stop_manifest_or_self_report_cannot_replace_host_evidence(wire, change):
    gateway, peer, document, delivery, source_result, _ = wire
    result, evidence = peer.complete(document, delivery, source_result, read=change != 'self-reported-read')
    if change == 'stop': peer.job['stop_proven'] = False
    elif change == 'attempt': peer.job['stop_proof']['attempt_id'] = str(UUID(int=999))
    elif change == 'fence': peer.job['stop_proof']['fence'] = 2
    elif change == 'writers': peer.job['stop_proof']['resource_ids'] *= 2
    elif change == 'profile': peer.job['pins']['profile'] = 'video.animation-production.v2'
    elif change == 'revision': peer.job['pins']['profile_revision'] = '2'
    elif change == 'publication': peer.job['publication_authorized'] = True
    elif change == 'manifest-binding': peer.job['artifact_manifest_sha256'] = '0' * 64
    elif change == 'manifest-attempt': peer.manifest['attempt_id'] = str(UUID(int=999)); peer.rehash_manifest()
    elif change == 'missing-evidence': peer.manifest['files'].pop(); peer.rehash_manifest()
    elif change in {'duplicate-name', 'duplicate-id'}:
        key = 'name' if change == 'duplicate-name' else 'artifact_id'
        peer.manifest['files'][1][key] = peer.manifest['files'][0][key]
        peer.rehash_manifest()
    elif change == 'total': peer.manifest['total_bytes'] += 1; peer.rehash_manifest()
    elif change == 'bytes': peer.contents[peer.artifact_ids[EVIDENCE_NAME]] += b' '
    elif change.startswith('evidence-'):
        key = {'evidence-attempt': 'attempt_id', 'evidence-owner': 'recipient_owner',
               'evidence-source': 'source', 'evidence-hash': 'sha256'}[change]
        evidence[key] = str(UUID(int=999)) if change == 'evidence-attempt' else 'untrusted'
        if change != 'evidence-hash': evidence['sha256'] = digest({k: v for k, v in evidence.items() if k != 'sha256'})
        result['skill_tool_evidence_sha256'] = evidence['sha256']
        peer.rewrite(EVIDENCE_NAME, evidence); peer.rewrite(RESULT_NAME, result)
    else:
        if change == 'wrong-input': result['input_sha256'] = '0' * 64
        elif change == 'self-reported-read':
            result['skill_decisions'] = [dict(package_sha256=next(iter(delivery['packages'])), action='reject',
                reason='The model claims it read this root; no native read occurred.', scene_targets=[])]
        elif change == 'result-evidence-hash': result['skill_tool_evidence_sha256'] = '0' * 64
        raw = canonical_bytes(result)
        if change == 'duplicate-json': raw = b'{"schema":"fake",' + raw[1:]
        peer.rewrite(RESULT_NAME, raw=raw)
    with pytest.raises(ServiceError):
        gateway.collect(JOB, digest(document), document)


def test_missing_frozen_bundle_is_not_replaced_by_empty_skills_or_refreshed(wire, monkeypatch):
    gateway, peer, document, *_ = wire
    def missing(*args):
        raise ServiceError('production_skill_bundle_not_found', 404)
    monkeypatch.setattr(gateway.skill_adapter, 'get_bundle', missing)
    with pytest.raises(ServiceError, match='bundle_not_found'):
        gateway.upload(document)
    assert peer.calls == []
