"""Upgrade recovery preserves reserved intent and persisted delivery receipts."""
from copy import deepcopy
import sqlite3

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.animation_method_store import AnimationMethodStore
from video_workbench.animation_skill_production import SkillProductionAdapter
from test_animation_skill_production import TASK, metadata, published


def test_legacy_batch_ack_loss_recovers_with_new_single_read_keys(tmp_path, monkeypatch):
    originals = [published(index) for index in range(18)]
    by_id = {row['id']: row for row in originals}
    path = tmp_path / 'delivery.sqlite3'
    server_path = tmp_path / 'publisher.sqlite3'
    server = AnimationMethodStore(server_path)

    def bind_synthetic_publications(store):
        # Publication validation is covered separately. Keep the real server's
        # SQLite request/receipt transactions here, with inert published bytes.
        def get_method(db, owner, version_id, *, published_only=False):
            assert owner == 'alice' and published_only
            return deepcopy(by_id[version_id])
        monkeypatch.setattr(store, '_get_method', get_method)

    bind_synthetic_publications(server)
    legacy_calls, legacy_receipts = [], []

    def receipt_saved_but_ack_lost(owner, body):
        legacy_calls.append(deepcopy(body))
        legacy_receipts.append(server.read_published(
            owner, body['method_version_ids'], body['consumer_id']))
        raise OSError('Synthetic connection loss after the server committed its receipt')

    class LegacyBatchAdapter(SkillProductionAdapter):
        def _complete(self, binding, intent):
            # Reproduce the previous deployed reader's first 16-version request.
            consumer = 'skill-packages:' + digest({key: binding[key] for key in (
                'recipient_owner', 'consumer_id')}) + ':0'
            return self.read(binding['recipient_owner'], dict(
                method_version_ids=[row['id'] for row in intent['methods'][:16]],
                consumer_id=consumer))

    legacy = LegacyBatchAdapter(lambda owner: [metadata(row) for row in originals],
                              receipt_saved_but_ack_lost, path)
    with pytest.raises(OSError, match='after the server committed'):
        legacy.freeze('alice', 'same-operation', TASK, ['html-svg'])
    assert len(legacy_calls) == 1 and len(legacy_calls[0]['method_version_ids']) == 16

    def ledger():
        with sqlite3.connect(path) as db:
            return db.execute('SELECT binding,intent,intent_sha256,snapshot FROM '
                              'animation_skill_production_freezes').fetchone()

    before = ledger()
    assert before[3] is None
    # Restart both sides, so recovery cannot depend on an in-memory receipt.
    server = AnimationMethodStore(server_path)
    bind_synthetic_publications(server)
    assert server.read_published('alice', legacy_calls[0]['method_version_ids'],
                                 legacy_calls[0]['consumer_id']) == legacy_receipts[0]
    # Reusing that old key for a smaller response would cause the original bug.
    with pytest.raises(ServiceError, match='animation_method_idempotency_conflict'):
        server.read_published('alice', [originals[0]['id']], legacy_calls[0]['consumer_id'])

    recovery_calls = []

    def recover(owner, body):
        recovery_calls.append(deepcopy(body))
        assert len(body['method_version_ids']) == 1
        assert body['consumer_id'].startswith('skill-packages-single-v1:')
        assert body['consumer_id'] != legacy_calls[0]['consumer_id']
        return server.read_published(owner, body['method_version_ids'], body['consumer_id'])

    def no_catalogue_refresh(owner):
        pytest.fail('Recovery must retain its reserved catalog, even after publications change')

    recovered = SkillProductionAdapter(no_catalogue_refresh, recover, path)
    result = recovered.freeze('alice', 'same-operation', TASK, ['html-svg'])
    assert len(recovery_calls) == 18
    assert len({body['consumer_id'] for body in recovery_calls}) == 18
    assert {row['method_version_id'] for row in result['catalog']['skills']} == set(by_id)
    assert len(result['retrieval_receipts']) == 18
    assert ledger()[:3] == before[:3] and ledger()[3] is not None
    assert server.read_published('alice', legacy_calls[0]['method_version_ids'],
                                 legacy_calls[0]['consumer_id']) == legacy_receipts[0]
    assert len(server.events('alice')) == 19

    def no_repeat_read(*args):
        pytest.fail('A completed freeze must not repeat metadata retrieval or other work')

    completed = SkillProductionAdapter(no_catalogue_refresh, no_repeat_read, path)
    assert completed.freeze('alice', 'same-operation', TASK, ['html-svg']) == result
    assert completed.get_bundle('alice', 'same-operation', result['catalog']['sha256']) == result
