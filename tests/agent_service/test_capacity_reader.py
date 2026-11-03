"""Reader runner boundaries; real image parsing remains a server acceptance."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent_service.deploy.verify_capacity_reader import (
    ReaderError, ordinary_json, run_reader, verify,
)


IMAGE = 'registry.invalid/control:latest@sha256:' + 'a' * 64
LEGACY = 'registry.invalid/control:latest@sha256:' + 'b' * 64


def response(mode, hosts=1):
    value = dict(status='verified', mode=mode, current_binding_count=1,
        actual_hosts_verified=hosts, original_policy_pins_sha256='c' * 64,
        actual_host_pins_sha256='d' * 64)
    if mode == 'expanded':
        value.update(synthetic_binding_counts_verified=[105, 256], rejection_257=True,
            unknown_fields_rejected=True, original_records_routes_preserved=True,
            original_policy_pins_preserved=True, history_record_limit=256)
    else:
        value['rejection_105'] = True
    return value


def test_container_reads_only_image_owned_code_without_host_mounts_network_or_secrets(monkeypatch):
    import subprocess
    seen = []
    def fake(arguments, **kwargs):
        seen.append((arguments, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(response('expanded')).encode(), stderr=b'')
    monkeypatch.setattr(subprocess, 'run', fake)
    result = run_reader(IMAGE, {}, {'fixture': {}}, package_root='/app/agent_service',
                        expected_binding_count=1, mode='expanded')
    assert result['history_record_limit'] == 256
    arguments, kwargs = seen[0]
    assert arguments[:3] == ['docker', 'run', '--rm']
    assert arguments[arguments.index('--network') + 1] == 'none'
    assert arguments[arguments.index('--user') + 1] == '10000:10000'
    assert '--read-only' in arguments and '-I' in arguments and '-B' in arguments
    assert not set(arguments) & {'-v', '--volume', '--mount', '--env-file', '-e', '--env'}
    assert '--pull=never' in arguments and IMAGE in arguments
    program = arguments[-1]
    assert "importlib.import_module('agent_service.' + name)" in program
    assert 'path.is_relative_to(root)' in program
    assert 'database_url' not in json.loads(kwargs['input'])
    assert kwargs['timeout'] == 240


def test_incomplete_or_failed_image_result_cannot_create_reader_evidence(monkeypatch):
    import subprocess
    incomplete = response('expanded')
    incomplete.pop('rejection_257')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps(incomplete).encode(), stderr=b''))
    with pytest.raises(ReaderError, match='expanded_reader_evidence_incomplete'):
        run_reader(IMAGE, {}, {'fixture': {}}, package_root='/app/agent_service', expected_binding_count=1, mode='expanded')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=2,
        stdout=b'{"status":"failed","stage":"expanded_105"}', stderr=b'private-input-must-not-print'))
    with pytest.raises(ReaderError, match='image_reader_expanded_expanded_105') as error:
        run_reader(IMAGE, {}, {'fixture': {}}, package_root='/app/agent_service', expected_binding_count=1, mode='expanded')
    assert 'private-input' not in str(error.value)


def test_output_references_hash_of_exact_report_and_never_claims_worker_for_control(tmp_path, monkeypatch):
    import agent_service.deploy.verify_capacity_reader as runner
    current = tmp_path / 'current.json'
    current.write_text(json.dumps({'kubernetes_workers': [{'host_config_map': 'fixture-host'}]}), encoding='utf-8')
    folder = tmp_path / 'hosts/fixture-host'
    folder.mkdir(parents=True)
    (folder / 'config.json').write_text('{}', encoding='utf-8')
    calls = []
    def fake(image, current, hosts, **arguments):
        calls.append((image, arguments['mode']))
        return response(arguments['mode'])
    monkeypatch.setattr(runner, 'run_reader', fake)
    monkeypatch.setattr(runner, 'source_identity', lambda: dict(revision='e' * 40,
        script_sha256='f' * 64, image_program_sha256='1' * 64))
    result = verify(image=IMAGE, legacy_image=LEGACY, public_path=current,
        host_config_dir=tmp_path / 'hosts', output_dir=tmp_path / 'evidence', expected_binding_count=1)
    raw = (tmp_path / 'evidence/reader-verification.json').read_bytes()
    compatibility = json.loads((tmp_path / 'evidence/compatibility.json').read_bytes())
    assert calls == [(IMAGE, 'expanded'), (LEGACY, 'legacy')]
    assert result['evidence_sha256'] == hashlib.sha256(raw).hexdigest()
    assert {row['role'] for row in compatibility['consumers']} == {'control-api', 'control-dispatcher', 'model-proxy'}
    assert all(row['image'] == IMAGE and row['evidence_sha256'] == result['evidence_sha256']
               for row in compatibility['consumers'])
    report = json.loads(raw)
    assert report['publication_authorized'] is False and report['runtime_process_health_verified'] is False
    assert report['uid_ledger_accessed'] is False and report['repository_code_mounted'] is False


def test_private_or_duplicate_inputs_are_rejected_before_container_execution(tmp_path):
    path = tmp_path / 'public.json'
    path.write_text('{"nested":{"worker_token":"synthetic-private-value"}}', encoding='utf-8')
    with pytest.raises(ReaderError, match='private_reader_input_forbidden') as error:
        ordinary_json(path)
    assert 'synthetic-private-value' not in str(error.value)
    path.write_text('{"field":1,"field":2}', encoding='utf-8')
    with pytest.raises(ReaderError, match='duplicate_reader_input_key'):
        ordinary_json(path)
