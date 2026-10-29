"""Neutral research collectors validate fixed resources and stopped output bytes."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.engines import StopEvidence
from agent_service.profiles import research
from scientific_agent.pi_contracts import result_digest
from scientific_agent.pi_sections import select_section


TASK, ATTEMPT = (str(UUID(int=i)) for i in (51, 52))
REQUEST = 'd' * 64


def task(**extra):
    return dict(schema_version='1', business_task_id=TASK, topic='Controlled research', mode='review',
        created_at='2026-10-26T00:00:00Z', execution_deadline_at='2026-10-26T00:10:00Z', **extra)


def envelope(data, profile):
    return SimpleNamespace(task=data, profile=profile.name, profile_revision='v1', engine='hermes',
        runtime_revision='offline-runtime-v1', image_digest='sha256:' + 'a' * 64,
        attempt_id=ATTEMPT, fence=2, request_sha256=REQUEST,
        execution_binding=SimpleNamespace(sha256='b' * 64))


def observed_zero_search(directory):
    result = directory / 'result'
    result.mkdir()
    search = dict(source='crossref', query='controlled empty search', searched_at='2026-10-26T00:00:00Z',
        status='success', result_count=0, error=None)
    request = dict(tool='academic_search', payload={'query': search['query'], 'providers': ['crossref'], 'limit': 1})
    observation = dict(schema_version='1', attempt_id=ATTEMPT, request_sha256=REQUEST, observations=[
        dict(call_sha256=hashlib.sha256(canonical_bytes(request)).hexdigest(), observed_at=123.0,
            state='complete', request=request, response=dict(schema_version='1', searches=[search],
                papers=[], database_total=None, notes=[]))])
    (result / 'searches.jsonl').write_bytes(canonical_bytes(search) + b'\n')
    for kind in ('papers', 'screening', 'evidence', 'claims'):
        (result / (kind + '.jsonl')).write_bytes(b'')
    (result / 'references.json').write_bytes(b'[]')
    (result / 'report.md').write_bytes(b'# Scope\nControlled fixture.\n# Methods\nOne empty search.\n'
        b'# Findings\nInsufficient evidence.\n# Limitations\nNo scientific truth verified.\n# References\nNone.\n')
    (result / 'academic-observations.json').write_bytes(canonical_bytes(observation))
    return result


def test_research_prompt_uses_original_fixed_bundle_and_real_schemas():
    profile = research.ResearchProfile()
    data = task(source_policy={'providers': ['crossref'], 'max_results': 1})
    prompt = profile.build_prompt(data)
    assert 'local-procedures-v2' in prompt
    assert 'none was selected or installed' in prompt
    assert 'academic_search' in prompt
    assert 'Do not write academic-observations.json' in prompt
    assert 'python -m pi_worker.research_tools' not in prompt
    assert 'Run the offline `validate` CLI' not in prompt
    assert 'academic_search({"query":"exact query","providers":["crossref"],"limit":10})' in prompt
    assert 'academic_verify({"kind":"doi","identifier":"10.1234/example"})' in prompt
    assert 'controller runs offline validation after you stop' in prompt
    assert 'relative to result/, not the attempt root' in prompt
    assert 'bash' not in profile.required_tools
    with pytest.raises(ServiceError):
        profile.validate_task({**data, 'engine': 'pi'})


def test_research_tool_prompt_render_does_not_rewrite_untrusted_job_text():
    profile = research.ResearchProfile()
    topic = 'Compare python -m pi_worker.research_tools search and academic_search semantics'
    data = task(source_policy={'providers': ['crossref'], 'max_results': 1})
    data['topic'] = topic
    prompt = profile.build_prompt(data)
    job = prompt.split('<research-job-json>\n', 1)[1].split('\n</research-job-json>', 1)[0]
    assert json.loads(job) == {'topic': topic, 'mode': 'review'}
    # The hash-pinned source resources are unchanged; only host prompt rendering differs.
    resource = research.verified_bundle()['task-template.md']
    assert 'python -m pi_worker.research_tools search' in resource


@pytest.mark.parametrize('proof', [None, StopEvidence('another-attempt', True, ('writer',)),
    StopEvidence(ATTEMPT, False, ('writer',)), StopEvidence(ATTEMPT, True, ())])
def test_collector_cannot_read_output_until_matching_all_writer_stop(tmp_path, proof):
    profile = research.ResearchProfile()
    execution = envelope(task(source_policy={'providers': ['crossref'], 'max_results': 1}), profile)
    with pytest.raises(ServiceError, match='writers_not_stopped'):
        profile.collect_output(tmp_path, execution, proof)


def test_stopped_collector_builds_engine_neutral_candidate_from_measured_bytes(tmp_path, monkeypatch):
    observed_zero_search(tmp_path)
    profile = research.ResearchProfile()
    execution = envelope(task(source_policy={'providers': ['crossref'], 'max_results': 1}), profile)
    # Simulate only the already authenticated root artifact read on Windows.
    # Every other file read, actual validator, package checksum and envelope is real.
    original = research._read
    monkeypatch.setattr(research, '_read', lambda root, name, limit, **kwargs:
        (Path(root) / name).read_bytes() if kwargs.get('protected') else original(root, name, limit))
    artifacts = profile.collect_output(tmp_path, execution, StopEvidence(ATTEMPT, True, ('offline-writer',)))
    indexed = {item.name: item.content() for item in artifacts}
    result = json.loads(indexed['research-result.json'])
    assert result['attempt'] == 2
    assert result['result_digest'] == result_digest(result)
    assert result['validation']['valid'] is True
    assert result['provenance']['engine'] == 'hermes'
    assert result['provenance']['execution_request_sha256'] == REQUEST
    assert result['provenance']['execution_binding_sha256'] == 'b' * 64
    assert any(item['artifact_id'] == 'academic_observations' for item in result['artifacts'])


def test_collector_does_not_accept_agent_owned_observation_file(tmp_path):
    result = observed_zero_search(tmp_path)
    profile = research.ResearchProfile()
    execution = envelope(task(source_policy={'providers': ['crossref'], 'max_results': 1}), profile)
    if os.name == 'posix':
        (result / 'academic-observations.json').chmod(0o644)
    with pytest.raises(ServiceError, match='research_observation_not_protected'):
        profile.collect_output(tmp_path, execution, StopEvidence(ATTEMPT, True, ('offline-writer',)))


def revision_material():
    path = Path(__file__).resolve().parents[1] / 'scientific_pi/revisions/test_revision_validation.py'
    spec = importlib.util.spec_from_file_location('offline_research_revision_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.revision_fixture()


@pytest.fixture
def revision(tmp_path):
    data = revision_material()
    material = dict(report=data['report'], items=data['items'],
        artifacts={key: base64.b64encode(raw).decode('ascii') for key, raw in data['artifacts'].items()})
    section = select_section(data['report'], data['section_id'], data['base_sha256'])
    inputs, result = tmp_path / 'inputs', tmp_path / 'result'
    inputs.mkdir()
    result.mkdir()
    files = {'revision-materials.json': canonical_bytes(material), 'input-report.md': data['report'].encode(),
        'input-section.md': section['text'].encode(), **{research.FILES[name][0]: raw for name, raw in data['artifacts'].items()}}
    for name, raw in files.items():
        (inputs / name).write_bytes(raw)
    (result / 'revision-output.json').write_bytes(canonical_bytes(data['output']))
    request = task(intent_id=str(UUID(int=53)), parent_version_id=str(UUID(int=54)), root_report_id=str(UUID(int=55)),
        base_sha256=data['base_sha256'], section_id=data['section_id'], section_sha256=section['sha256'],
        instruction='Revise the fixed section.', input_sha256=hashlib.sha256(canonical_bytes(material)).hexdigest(),
        material_policy='recorded_evidence_only')
    return research.ResearchRevisionProfile(), request, data


def test_revision_collector_composes_only_selected_section_and_keeps_original_evidence(tmp_path, revision):
    profile, request, fixed = revision
    result = {item.name: item.content() for item in profile.collect_output(tmp_path,
        envelope(request, profile), StopEvidence(ATTEMPT, True, ('offline-writer',)))}
    candidate = json.loads(result['research-result.json'])
    assert candidate['validation']['valid'] is True
    assert candidate['provenance']['retrieval_performed'] is False
    assert candidate['provenance']['full_text_restored'] is False
    assert 'academic-observations.json' not in result
    assert result['report.md'] != fixed['report'].encode()
    for name in ('papers', 'evidence', 'references'):
        assert result[research.FILES[name][0]] == fixed['artifacts'][name]


@pytest.mark.parametrize('name', ['revision-materials.json', 'input-report.md', 'input-section.md', 'papers.jsonl'])
def test_revision_collector_rejects_changed_readable_input(tmp_path, revision, name):
    profile, request, _ = revision
    target = tmp_path / 'inputs' / name
    target.write_bytes(target.read_bytes() + b' tampering')
    with pytest.raises(ServiceError):
        profile.collect_output(tmp_path, envelope(request, profile), StopEvidence(ATTEMPT, True, ('offline-writer',)))
