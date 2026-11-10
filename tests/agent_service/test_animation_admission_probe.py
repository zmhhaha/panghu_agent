"""Real loopback HTTP/SQLite admission checks, with no engine or model work."""
import hashlib
import json
import threading

import httpx
import pytest

from agent_service.contracts import ServiceError
from agent_service.deploy import animation_admission_probe as probe
from video_workbench.animation_revision_store import AnimationRevisionStore


def test_real_admission_lost_response_reconciles_once_and_cancel_never_refunds(tmp_path, monkeypatch):
    secret = 'a-unique-probe-credential-never-in-the-receipt-0123456789abcdef'
    monkeypatch.setattr(probe.secrets, 'token_hex', lambda size: secret)
    root = tmp_path / 'real-http'
    receipt = probe.run_probe(root)
    assert json.loads((root / 'receipt.json').read_bytes()) == receipt
    assert receipt['status'] == 'passed'
    assert receipt['queue_counts'] == dict(jobs=1, attempts=0, model_requests=0)
    assert receipt['before_post_ledger_status'] == 'submitting'
    assert receipt['exactly_one_claim_before_post'] is True
    assert receipt['business_ledger_reopened'] is True
    assert receipt['recovery_only_http_get'] is receipt['same_job_recovered'] is True
    assert receipt['cancellation_preserved_allowance'] is True
    assert receipt['another_key_rejected_before_cancel'] == receipt['another_key_rejected_after_cancel'] == 'animation_revision_allowance_consumed'
    assert receipt['terminal_replay_no_http'] is receipt['terminal_replay_ledger_unchanged'] is True
    assert receipt['response_loss_injection'] == 'client_transport_after_real_202'
    assert receipt['readiness_basis'] == 'synthetic_admission_gate_only'
    assert receipt['full_paid_chain_verified'] is receipt['full_workbench_verified'] is False
    assert receipt['production_acceptance_created'] is receipt['production_routes_changed'] is False
    assert receipt['dispatcher_started'] is receipt['actual_visual_review_performed'] is False
    assert receipt['control_api_stopped'] is True
    assert not any(thread.name == 'animation-admission-probe-http' for thread in threading.enumerate())
    encoded = json.dumps(receipt)
    assert secret not in encoded
    assert 'Authorization' not in encoded and 'Bearer ' not in encoded
    assert 'scene.html' not in encoded and 'input_sha256' not in encoded
    requests = [row for row in receipt['trace'] if row['event'] == 'http_request']
    assert [(row['method'], row['route']) for row in requests].count(('POST', '/v1/jobs')) == 1
    assert ('POST', '/v1/jobs/{job_id}/cancel') in [(row['method'], row['route']) for row in requests]
    ledger = AnimationRevisionStore(root / 'business-ledger.sqlite3')
    assert [row['status'] for row in ledger.operations(receipt['lineage_id'])] == ['cancelled']


def test_transport_refuses_job_post_when_the_business_claim_is_not_committed(tmp_path):
    ledger_path = tmp_path / 'reserved.sqlite3'
    ledger = AnimationRevisionStore(ledger_path)
    lineage = ledger.create_lineage('probe', 'project', 'source-r1', 'a' * 64, 'first')
    operation_id = lineage['id'] + ':review'
    ledger.reserve(lineage['id'], 'review', operation_id, operation_id, 'b' * 64, [])
    trace = []
    with probe._LossTransport(ledger_path, operation_id, trace, drop_response=True) as transport:
        with pytest.raises(ServiceError, match='animation_admission_probe_claim_missing'):
            # A refused local claim must fail before connecting to this unused port.
            transport.handle_request(httpx.Request('POST', 'http://127.0.0.1:1/v1/jobs'))
    assert trace == []
    assert ledger.operation(operation_id)['status'] == 'reserved'


def test_probe_will_not_replace_previous_evidence(tmp_path, capsys):
    root = tmp_path / 'retained'
    root.mkdir()
    retained = root / 'receipt.json'
    retained.write_bytes(b'{"status":"retained-original"}')
    expected = hashlib.sha256(retained.read_bytes()).hexdigest()
    assert probe.main(['--output', str(root)]) == 1
    assert json.loads(capsys.readouterr().out) == dict(status='failed', code='animation_admission_probe_no_overwrite')
    assert hashlib.sha256(retained.read_bytes()).hexdigest() == expected
    assert list(root.iterdir()) == [retained]
