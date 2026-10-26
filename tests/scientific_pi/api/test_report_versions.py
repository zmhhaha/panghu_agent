"""Durable version/snapshot admissions on independent SQLite connections.

All report and evidence facts are synthetic. Connections commit per operation,
matching the shared HTTP SQL service rather than a process-local transaction.
"""
import concurrent.futures
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid

from scientific_agent.pi_report_versions import ReportVersionStore, VersionError
from scientific_agent.pi_sections import index_sections


REPORT = '# Synthetic fixture\r\n\r\n<!-- claim:c1 -->\r\nSynthetic result [1].\r\n'
SOURCE = '00000000-0000-4000-8000-000000000001'
CANDIDATE_SOURCE = '00000000-0000-4000-8000-000000000002'


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 25, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class SQLAdapter:
    def __init__(self, path):
        self.path, self.sql = str(path), []
        self.commit_error_prefix = None
        self.before_execute = None
        self.lock = threading.Lock()

    def _execute(self, query):
        if self.before_execute:
            self.before_execute(query)
        with self.lock:
            self.sql.append(query)
        with closing(sqlite3.connect(self.path, timeout=15)) as connection:
            connection.execute(query)
            connection.commit()
        if self.commit_error_prefix and query.startswith(self.commit_error_prefix):
            self.commit_error_prefix = None
            raise OSError('Synthetic response lost after commit')

    def _query(self, query):
        with closing(sqlite3.connect(self.path, timeout=15)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query)]


def fixture_records(report=REPORT):
    acquired = dict(available=True, obtained_at='2026-10-25T00:00:00Z')
    absent = dict(available=False, obtained_at=None)
    paper = dict(paper_id='p1', title='Synthetic paper 科研', authors=['Fixture Author'], year=2025,
                 doi=None, pmid=None, url='https://example.org/synthetic', sources=['fixture'],
                 retrieved_at='2026-10-25T00:00:00Z', abstract='Synthetic abstract', full_text_path=None,
                 availability=dict(metadata=acquired, abstract=acquired, full_text=absent),
                 verification=dict(status='unknown', checked_at=None, source=None, error=None))
    records = dict(papers=[paper],
                   evidence=[dict(evidence_id='e1', paper_id='p1', finding='Synthetic finding', method='Fixture',
                                  limitations='Synthetic abstract only', access_level='abstract', locator='abstract')],
                   claims=[dict(claim_id='c1', marker='<!-- claim:c1 -->', evidence_ids=['e1'],
                                assessment='supported', explanation='Synthetic abstract record only')],
                   references=[dict(n=1, paper_id='p1')])
    artifacts = {kind: (json.dumps(rows, ensure_ascii=False) if kind == 'references' else
                       ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)).encode('utf-8')
                 for kind, rows in records.items()}
    all_files = dict(artifacts, report=report.encode('utf-8'))
    items = [dict(artifact_id=kind, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                  media_type='text/markdown' if kind == 'report' else 'application/json' if kind == 'references' else 'application/x-ndjson')
             for kind, raw in all_files.items()]
    return items, artifacts


class ReportVersionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = SQLAdapter(Path(self.temporary.name) / 'reports.db')
        self.clock = Clock()
        self.store = ReportVersionStore(self.db, clock=self.clock)
        self.store.initialize()
        self.items, self.artifacts = fixture_records()

    def base(self, owner='owner', source=SOURCE, report=REPORT):
        return self.store.ensure_base(owner, source, report, 'Synthetic topic')

    def snapshot(self, owner='owner', key='snapshot-1', base=None):
        base = base or self.base(owner)
        return self.store.save_snapshot(owner, base['version_id'], self.items, self.artifacts, key)

    def candidate(self, owner='owner', parent=None, key='candidate-1', source=CANDIDATE_SOURCE,
                  content=REPORT + '\r\nCandidate revision 科研.\r\n', **changes):
        parent = parent or self.base(owner)
        arguments = dict(owner=owner, parent_version_id=parent['version_id'],
                         base_sha256=parent['report_sha256'], content=content,
                         key=key, source_report_id=source)
        arguments.update(changes)
        return self.store.append_candidate(**arguments)

    def intent(self, owner='owner', key='intent-1', base=None, snapshot=None, **changes):
        base = base or self.base(owner)
        snapshot = snapshot or self.snapshot(owner, base=base)
        section = index_sections(REPORT)['sections'][0]
        arguments = dict(owner=owner, parent_version_id=base['version_id'], base_sha256=base['report_sha256'],
                         section_id=section['section_id'], section_sha256=section['sha256'], instruction='Explain limits',
                         key=key, snapshot_id=snapshot['snapshot_id'], timeout_seconds=1800)
        arguments.update(changes)
        return self.store.prepare_intent(**arguments)

    def assertError(self, code, operation):
        with self.assertRaises(VersionError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_exact_base_roundtrip_is_idempotent_and_original_table_untouched(self):
        report = REPORT + '\r\nUnicode 科研 and NUL:\x00\r\n'
        base = self.base(report=report)
        again = self.base(report=report)
        self.assertEqual(base, again)
        restored = self.store.get_version('owner', base['version_id'], include_content=True)
        self.assertEqual(restored['content'].encode('utf-8'), report.encode('utf-8'))
        self.assertEqual(restored['report_sha256'], hashlib.sha256(report.encode('utf-8')).hexdigest())
        self.assertNotIn('owner', restored)
        self.assertNotIn('content', self.store.get_version('owner', base['version_id']))
        self.assertTrue(all('scientific_reports ' not in query for query in self.db.sql))

    def test_same_source_changed_content_cannot_overwrite_base(self):
        base = self.base()
        self.assertError('base_report_conflict', lambda: self.base(report=REPORT + 'changed'))
        self.assertEqual(self.store.get_version('owner', base['version_id'], True)['content'], REPORT)

    def test_shared_legacy_source_has_independent_owner_roots(self):
        first, second = self.base('alice'), self.base('bob')
        self.assertNotEqual(first['version_id'], second['version_id'])
        self.assertEqual(first['root_report_id'], second['root_report_id'])
        self.assertEqual(len(self.store.list_versions('alice', SOURCE)), 1)
        self.assertEqual(len(self.store.list_versions('bob', SOURCE)), 1)
        self.assertError('version_not_found', lambda: self.store.get_version('alice', second['version_id']))
        self.assertEqual(self.store.list_versions('other', SOURCE), [])

    def test_base_count_and_utf8_byte_quotas_are_atomic(self):
        self.store = ReportVersionStore(self.db, clock=self.clock, owner_version_count=1)
        self.base()
        self.assertError('version_quota_exceeded', lambda: self.base(source=str(uuid.uuid4())))
        limited = ReportVersionStore(self.db, clock=self.clock, owner_version_bytes=4)
        self.assertError('version_quota_exceeded', lambda: limited.ensure_base('new-owner', SOURCE, '科研'))
        global_limited = ReportVersionStore(self.db, clock=self.clock, global_version_bytes=len(REPORT.encode('utf-8')))
        self.assertError('version_quota_exceeded', lambda: global_limited.ensure_base('third-owner', SOURCE, REPORT))

    def test_concurrent_base_admission_does_not_exceed_count(self):
        self.store = ReportVersionStore(self.db, clock=self.clock, owner_version_count=1)
        def attempt(number):
            try:
                return self.base(source=str(uuid.uuid5(uuid.NAMESPACE_DNS, str(number))))['version_id']
            except VersionError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        self.assertEqual(results.count('version_quota_exceeded'), 5)
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_versions')[0]['n'], 1)

    def test_ambiguous_base_commit_recovers_after_restart(self):
        self.db.commit_error_prefix = 'INSERT INTO scientific_report_versions'
        with self.assertRaises(OSError):
            self.base()
        restarted = ReportVersionStore(self.db, clock=self.clock)
        restarted.initialize()
        result = restarted.ensure_base('owner', SOURCE, REPORT)
        self.assertEqual(restarted.get_version('owner', result['version_id'], True)['content'], REPORT)

    def test_candidate_roundtrip_preserves_parent_root_and_published_source(self):
        parent = self.base()
        candidate = self.candidate(parent=parent, content=REPORT + '\r\n科研 NUL:\x00\r\n')
        self.assertEqual(candidate['parent_version_id'], parent['version_id'])
        self.assertEqual(candidate['root_report_id'], SOURCE)
        self.assertEqual(candidate['source_report_id'], CANDIDATE_SOURCE)
        self.assertEqual(candidate['topic'], parent['topic'])
        self.assertEqual(self.store.get_version('owner', parent['version_id'], True)['content'], REPORT)
        self.assertEqual(self.store.get_version('owner', candidate['version_id'], True)['content'], REPORT + '\r\n科研 NUL:\x00\r\n')
        reopened = self.store.ensure_base('owner', CANDIDATE_SOURCE, REPORT + '\r\n科研 NUL:\x00\r\n')
        self.assertEqual(reopened, candidate)
        grandchild = self.candidate(parent=candidate, key='candidate-2', source=str(uuid.uuid4()))
        self.assertEqual(grandchild['parent_version_id'], candidate['version_id'])
        self.assertEqual(grandchild['root_report_id'], SOURCE)
        self.assertEqual(len(self.store.list_versions('owner', SOURCE)), 3)
        self.assertEqual(self.store.list_versions('owner', CANDIDATE_SOURCE), self.store.list_versions('owner', SOURCE))
        self.assertEqual(self.store.list_versions('owner', grandchild['source_report_id']), self.store.list_versions('owner', SOURCE))
        self.assertEqual(self.store.list_versions('other', CANDIDATE_SOURCE), [])
        self.assertEqual(self.store.list_versions('owner', CANDIDATE_SOURCE, limit=1, offset=1), self.store.list_versions('owner', SOURCE)[1:2])
        self.assertTrue(all('UPDATE scientific_report_versions' not in query and 'scientific_reports ' not in query for query in self.db.sql))

    def test_candidate_key_is_stable_and_checks_all_request_arguments(self):
        parent = self.base()
        first = self.candidate(parent=parent)
        self.clock.advance(10)
        self.assertEqual(self.candidate(parent=parent), first)
        self.assertError('candidate_key_conflict', lambda: self.candidate(parent=parent, content=REPORT + 'different'))
        self.assertError('candidate_key_conflict', lambda: self.candidate(parent=parent, topic='Other topic'))
        self.assertError('candidate_key_conflict', lambda: self.candidate(parent=parent, source=str(uuid.uuid4())))
        second_parent = self.base(source=str(uuid.uuid4()))
        self.assertError('candidate_key_conflict', lambda: self.candidate(parent=second_parent))
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_versions')[0]['n'], 3)

    def test_candidate_source_cannot_be_reregistered_with_another_key(self):
        parent = self.base()
        self.candidate(parent=parent)
        self.assertError('candidate_source_conflict', lambda: self.candidate(parent=parent, key='different-key'))
        self.assertError('candidate_source_conflict', lambda: self.candidate(parent=parent, key='other-key', source=SOURCE))
        self.assertEqual(len(self.store.list_versions('owner', SOURCE)), 2)

    def test_candidate_parent_sha_and_owner_are_enforced(self):
        parent = self.base()
        self.assertError('stale_report', lambda: self.candidate(parent=parent, base_sha256='a' * 64))
        self.assertError('version_not_found', lambda: self.candidate('other', parent=parent))
        alice = self.candidate('alice')
        bob = self.candidate('bob')
        self.assertNotEqual(alice['version_id'], bob['version_id'])
        self.assertEqual(alice['source_report_id'], bob['source_report_id'])
        self.assertError('version_not_found', lambda: self.store.get_version('bob', alice['version_id']))

    def test_candidate_atomic_insert_rechecks_parent_root(self):
        parent = self.base()
        def change_parent_before_insert(query):
            if query.startswith('INSERT INTO scientific_report_versions'):
                self.db.before_execute = None
                self.db._execute("UPDATE scientific_report_versions SET root_report_id='" + str(uuid.uuid4()) + "' WHERE version_id='" + parent['version_id'] + "'")
        self.db.before_execute = change_parent_before_insert
        self.assertError('stale_report', lambda: self.candidate(parent=parent))
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_versions')[0]['n'], 1)

    def test_candidate_ambiguous_commit_recovers_after_restart(self):
        parent = self.base()
        self.db.commit_error_prefix = 'INSERT INTO scientific_report_versions'
        with self.assertRaises(OSError):
            self.candidate(parent=parent)
        self.clock.advance(20)
        self.store = ReportVersionStore(self.db, clock=self.clock)
        self.store.initialize()
        candidate = self.candidate(parent=parent)
        self.assertEqual(candidate['created_at'], '2026-10-25T00:00:00.000000Z')
        self.assertEqual(len(self.store.list_versions('owner', SOURCE)), 2)

    def test_candidate_concurrent_same_key_returns_one_version(self):
        parent = self.base()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.candidate(parent=parent), range(5)))
        self.assertEqual(len({result['version_id'] for result in results}), 1)
        self.assertEqual(len(self.store.list_versions('owner', SOURCE)), 2)

    def test_candidate_source_is_atomic_across_different_keys(self):
        parent = self.base()
        def attempt(number):
            try:
                return self.candidate(parent=parent, key='candidate-' + str(number))['version_id']
            except VersionError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        self.assertEqual(results.count('candidate_source_conflict'), 5)
        self.assertEqual(len(self.store.list_versions('owner', SOURCE)), 2)

    def test_candidate_registration_before_base_import_preserves_lineage(self):
        parent = self.base()
        candidate = None
        content = REPORT + 'Concurrent published candidate'
        def register_before_base_insert(query):
            nonlocal candidate
            if query.startswith('INSERT INTO scientific_report_versions'):
                self.db.before_execute = None
                candidate = self.candidate(parent=parent, content=content)
        self.db.before_execute = register_before_base_insert
        reopened = self.store.ensure_base('owner', CANDIDATE_SOURCE, content)
        self.assertEqual(reopened, candidate)
        self.assertEqual(reopened['root_report_id'], SOURCE)
        self.assertEqual(len(self.store.list_versions('owner', SOURCE)), 2)

    def test_candidate_count_quota_is_shared_with_bases_and_atomic(self):
        parent = self.base()
        self.store = ReportVersionStore(self.db, clock=self.clock, owner_version_count=2)
        def attempt(number):
            try:
                return self.candidate(parent=parent, key='candidate-' + str(number),
                                      source=str(uuid.uuid5(uuid.NAMESPACE_DNS, str(number))))['version_id']
            except VersionError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        self.assertEqual(results.count('version_quota_exceeded'), 5)
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_versions')[0]['n'], 2)

    def test_candidate_utf8_bytes_use_owner_and_global_quotas(self):
        parent = self.base()
        used = len(REPORT.encode('utf-8'))
        self.store = ReportVersionStore(self.db, clock=self.clock, owner_version_bytes=used + 5)
        self.assertError('version_quota_exceeded', lambda: self.candidate(parent=parent, content='科研'))
        self.store = ReportVersionStore(self.db, clock=self.clock, global_version_bytes=used + 5)
        self.assertError('version_quota_exceeded', lambda: self.candidate(parent=parent, content='科研'))
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_versions')[0]['n'], 1)

    def test_snapshot_fixes_report_manifest_and_raw_records(self):
        snapshot = self.snapshot()
        self.items[0]['sha256'] = 'a' * 64
        self.artifacts['papers'] = b'changed later'
        restored = self.store.get_snapshot('owner', snapshot['snapshot_id'])
        original_items, original_artifacts = fixture_records()
        self.assertEqual(restored['report'], REPORT)
        self.assertEqual(restored['artifacts'], original_artifacts)
        self.assertEqual({row['artifact_id']: row for row in restored['items']}, {row['artifact_id']: row for row in original_items})
        self.assertEqual(restored['scope'], 'recorded_evidence_only')
        self.assertNotIn('payload', restored)
        self.assertNotIn('owner', restored)

    def test_snapshot_key_idempotency_and_conflict(self):
        first = self.snapshot()
        self.clock.advance(10)
        self.assertEqual(self.snapshot(), first)
        second_base = self.base(source=str(uuid.uuid4()))
        self.assertError('snapshot_key_conflict', lambda: self.snapshot(base=second_base))
        self.assertEqual(self.store.get_snapshot_by_key('owner', 'snapshot-1')['snapshot_id'], first['snapshot_id'])
        self.assertIsNone(self.store.get_snapshot_by_key('other', 'snapshot-1'))

    def test_snapshot_ownership_is_required(self):
        snapshot = self.snapshot()
        self.assertError('snapshot_not_found', lambda: self.store.get_snapshot('other', snapshot['snapshot_id']))
        self.assertError('version_not_found', lambda: self.store.save_snapshot('other', snapshot['version_id'], self.items, self.artifacts, 'new-key'))

    def test_corrupt_manifest_and_record_bytes_never_persist(self):
        base = self.base()
        corrupted = dict(self.artifacts, papers=b'broken')
        self.assertError('artifact_mismatch', lambda: self.store.save_snapshot('owner', base['version_id'], self.items, corrupted, 'bad'))
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_snapshots')[0]['n'], 0)

    def test_snapshot_actual_encoded_bytes_enforce_quota(self):
        snapshot = self.snapshot()
        budget = snapshot['payload_bytes']
        self.assertGreater(budget, sum(len(value) for value in self.artifacts.values()))
        self.store = ReportVersionStore(self.db, clock=self.clock, owner_snapshot_bytes=budget)
        self.assertError('snapshot_quota_exceeded', lambda: self.snapshot(key='snapshot-2'))
        self.assertEqual(self.db._query('SELECT SUM(payload_bytes) AS n FROM scientific_report_snapshots WHERE payload IS NOT NULL')[0]['n'], budget)

    def test_concurrent_snapshot_admission_enforces_owner_count(self):
        base = self.base()
        self.store = ReportVersionStore(self.db, clock=self.clock, owner_snapshot_count=1)
        def attempt(number):
            try:
                return self.snapshot(key='snapshot-' + str(number), base=base)['snapshot_id']
            except VersionError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        self.assertEqual(results.count('snapshot_quota_exceeded'), 5)

    def test_global_snapshot_quota_is_shared_between_owners(self):
        first = self.snapshot('alice')
        second_base = self.base('bob')
        self.store = ReportVersionStore(self.db, clock=self.clock, global_snapshot_bytes=first['payload_bytes'])
        self.assertError('snapshot_quota_exceeded', lambda: self.snapshot('bob', base=second_base))

    def test_ambiguous_snapshot_commit_recovers_without_extending_ttl(self):
        base = self.base()
        self.db.commit_error_prefix = 'INSERT INTO scientific_report_snapshots'
        with self.assertRaises(OSError):
            self.snapshot(base=base)
        self.clock.advance(12)
        recovered = self.snapshot(base=base)
        self.assertEqual(recovered['created_at'], '2026-10-25T00:00:00.000000Z')

    def test_snapshot_corruption_detected_on_read(self):
        snapshot = self.snapshot()
        self.db._execute("UPDATE scientific_report_snapshots SET payload='{}' WHERE snapshot_id='" + snapshot['snapshot_id'] + "'")
        self.assertError('snapshot_integrity_error', lambda: self.store.get_snapshot('owner', snapshot['snapshot_id']))

    def test_version_corruption_detected_on_read(self):
        base = self.base()
        self.db._execute("UPDATE scientific_report_versions SET content_b64='Y2hhbmdlZA==' WHERE version_id='" + base['version_id'] + "'")
        self.assertError('version_integrity_error', lambda: self.store.get_version('owner', base['version_id'], True))

    def test_expired_snapshot_payload_is_cleared_without_reusing_key(self):
        self.store = ReportVersionStore(self.db, clock=self.clock, snapshot_ttl=5)
        snapshot = self.snapshot()
        self.clock.advance(6)
        self.assertError('snapshot_expired', lambda: self.store.get_snapshot('owner', snapshot['snapshot_id']))
        self.assertEqual(self.store.cleanup_expired()['cleared_snapshots'], 1)
        self.assertEqual(self.store.cleanup_expired()['cleared_snapshots'], 0)
        row = self.db._query('SELECT payload FROM scientific_report_snapshots')[0]
        self.assertIsNone(row['payload'])
        self.assertError('snapshot_expired', lambda: self.snapshot())
        self.assertError('snapshot_expired', lambda: self.store.get_snapshot_by_key('owner', 'snapshot-1'))
        self.assertEqual(self.store.get_version('owner', snapshot['version_id'], True)['content'], REPORT)

    def test_pinned_snapshot_survives_cleanup_until_intent_expiry(self):
        self.store = ReportVersionStore(self.db, clock=self.clock, snapshot_ttl=5, intent_ttl=20)
        base, snapshot = self.base(), self.snapshot()
        intent = self.intent(base=base, snapshot=snapshot)
        self.clock.advance(6)
        self.assertEqual(self.store.cleanup_expired()['cleared_snapshots'], 0)
        self.assertTrue(self.store.get_snapshot('owner', snapshot['snapshot_id'])['pinned'])
        self.assertError('snapshot_expired', lambda: self.intent(key='new-intent', base=base, snapshot=snapshot))
        self.assertEqual(self.store.find_intent('owner', 'intent-1')['intent_id'], intent['intent_id'])
        self.clock.advance(15)
        self.assertError('intent_expired', lambda: self.store.get_intent('owner', intent['intent_id']))
        self.assertEqual(self.store.cleanup_expired()['cleared_snapshots'], 1)
        self.assertError('intent_expired', lambda: self.intent(base=base, snapshot=snapshot))

    def test_intent_idempotency_full_fingerprint_and_owner(self):
        intent = self.intent()
        self.assertEqual(self.intent(), intent)
        self.assertEqual(self.store.find_intent('owner', 'intent-1'), intent)
        self.assertIsNone(self.store.find_intent('other', 'intent-1'))
        self.assertError('intent_key_conflict', lambda: self.intent(instruction='Different requirements'))
        self.assertError('intent_key_conflict', lambda: self.intent(timeout_seconds=900))
        self.assertError('intent_not_found', lambda: self.store.get_intent('other', intent['intent_id']))
        self.assertEqual(intent['status'], 'prepared')
        self.assertNotIn('owner', intent)

    def test_intent_verifies_base_section_and_snapshot_parent(self):
        self.assertError('stale_report', lambda: self.intent(base_sha256='a' * 64))
        self.assertError('section_conflict', lambda: self.intent(section_sha256='a' * 64))
        second = self.base(source=str(uuid.uuid4()))
        other_snapshot = self.snapshot(base=second, key='other-snapshot')
        self.assertError('snapshot_parent_conflict', lambda: self.intent(snapshot=other_snapshot))

    def test_intent_limits_total_and_live_counts(self):
        self.store = ReportVersionStore(self.db, clock=self.clock, owner_live_intent_count=1, owner_intent_count=2, intent_ttl=5)
        self.intent()
        self.assertError('intent_quota_exceeded', lambda: self.intent(key='intent-2'))
        self.clock.advance(6)
        self.intent(key='intent-2')
        self.clock.advance(6)
        self.assertError('intent_quota_exceeded', lambda: self.intent(key='intent-3'))
        self.assertError('invalid_version_request', lambda: self.intent(key='large', instruction='x' * 8001))

    def test_intent_instruction_storage_limit_is_utf8_bytes(self):
        exact = self.intent(instruction='🧪' * 2000)
        self.assertEqual(len(exact['instruction'].encode('utf-8')), 8000)
        self.assertEqual(len(self.intent(key='ascii-large', instruction='x' * 8000)['instruction']), 8000)
        self.assertError('invalid_version_request', lambda: self.intent(key='over-bytes', instruction='科研' * 1334))

    def test_ambiguous_intent_commit_restores_same_prepared_record(self):
        base, snapshot = self.base(), self.snapshot()
        self.db.commit_error_prefix = 'INSERT INTO scientific_report_revision_intents'
        with self.assertRaises(OSError):
            self.intent(base=base, snapshot=snapshot)
        self.clock.advance(10)
        recovered = self.intent(base=base, snapshot=snapshot)
        self.assertEqual(recovered['created_at'], '2026-10-25T00:00:00.000000Z')
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_revision_intents')[0]['n'], 1)

    def test_cleanup_race_at_intent_admission_cannot_create_dangling_intent(self):
        self.store = ReportVersionStore(self.db, clock=self.clock, snapshot_ttl=5)
        base, snapshot = self.base(), self.snapshot()
        def expire_before_insert(query):
            if query.startswith('INSERT INTO scientific_report_revision_intents'):
                self.db.before_execute = None
                self.clock.advance(6)
                self.store.cleanup_expired()
        self.db.before_execute = expire_before_insert
        self.assertError('snapshot_expired', lambda: self.intent(base=base, snapshot=snapshot))
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_revision_intents')[0]['n'], 0)

    def test_concurrent_same_key_intents_return_one_record(self):
        base, snapshot = self.base(), self.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.intent(base=base, snapshot=snapshot), range(5)))
        self.assertEqual(len({result['intent_id'] for result in results}), 1)
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_revision_intents')[0]['n'], 1)

    def test_invalid_arguments_are_rejected_before_sql(self):
        for owner in ('', '  ', 'x' * 201, 'owner\x00'):
            self.assertError('invalid_version_request', lambda owner=owner: self.base(owner))
        for key in ('', 'x' * 129, 'newline\n'):
            self.assertError('invalid_version_request', lambda key=key: self.snapshot(key=key))
        self.assertError('invalid_version_request', lambda: self.store.list_versions('owner', SOURCE, limit=True))
        self.assertError('invalid_version_request', lambda: self.store.ensure_base('owner', SOURCE, REPORT, 'invalid\x00topic'))


if __name__ == '__main__':
    unittest.main()
