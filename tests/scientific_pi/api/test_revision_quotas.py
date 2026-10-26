"""Candidate capacity is reserved atomically on independently committed SQL."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from scientific_agent.pi_report_versions import MAX_REPORT_BYTES, ReportVersionStore, VersionError
from scientific_agent.pi_revision_contracts import revision_input_digest, revision_material_digest
from scientific_agent.pi_sections import index_sections
from test_report_versions import Clock, SQLAdapter, REPORT, SOURCE, fixture_records


class RevisionReservationQuotaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = SQLAdapter(Path(self.directory.name)/'versions.db')
        self.clock = Clock()
        self.store = ReportVersionStore(self.db,clock=self.clock,owner_version_count=2,
            owner_version_bytes=len(REPORT.encode())+MAX_REPORT_BYTES,
            global_version_bytes=len(REPORT.encode())+MAX_REPORT_BYTES)
        self.store.initialize()
        self.items,self.artifacts = fixture_records()
        self.parent = self.store.ensure_base('owner',SOURCE,REPORT,'Synthetic topic')
        self.snapshot = self.store.save_snapshot('owner',self.parent['version_id'],self.items,self.artifacts,'snapshot')

    def job(self, key):
        section = index_sections(REPORT)['sections'][0]
        intent = self.store.prepare_intent('owner',self.parent['version_id'],self.parent['report_sha256'],
            section['section_id'],section['sha256'],'Explain recorded limitations.',key,self.snapshot['snapshot_id'])
        fixed = self.store.get_snapshot('owner',self.snapshot['snapshot_id'])
        material = dict(report=fixed['report'],items=fixed['items'],
                        artifacts={name:base64.b64encode(raw).decode('ascii') for name,raw in fixed['artifacts'].items()})
        now = self.clock()
        job = dict(schema_version='1',task_kind='revision',task_id=str(uuid.uuid5(uuid.NAMESPACE_URL,key)),
            user_id='owner',topic='Synthetic topic',engine='pi',mode='review',
            created_at=now.isoformat(),execution_deadline_at=(now+timedelta(seconds=1800)).isoformat(),
            runtime_revision='runtime',skill_bundle_revision='skills',
            input_sha256=revision_material_digest(material),material_policy='recorded_evidence_only',
            **{k:intent[k] for k in ('intent_id','parent_version_id','root_report_id','base_sha256',
                                    'section_id','section_sha256','instruction','timeout_seconds')})
        job['input_digest'] = revision_input_digest(job)
        return job

    def reserve(self, job):
        return self.store.reserve_revision_job('owner',job['intent_id'],job)

    def candidate(self, job):
        return self.store.append_candidate('owner',self.parent['version_id'],self.parent['report_sha256'],
            REPORT+'\r\nSynthetic candidate.\r\n','revision:'+job['task_id'],source_report_id=job['task_id'])

    def assertQuota(self, function):
        with self.assertRaises(VersionError) as caught:
            function()
        self.assertEqual((caught.exception.code,caught.exception.status),('version_quota_exceeded',429))

    def test_version_count_owner_bytes_and_global_bytes_reject_before_reservation(self):
        job = self.job('quota')
        original = dict(owner_version_count=self.store.owner_version_count,
                        owner_version_bytes=self.store.owner_version_bytes,
                        global_version_bytes=self.store.global_version_bytes)
        for limit,value in (('owner_version_count',1),
                            ('owner_version_bytes',len(REPORT.encode())+MAX_REPORT_BYTES-1),
                            ('global_version_bytes',len(REPORT.encode())+MAX_REPORT_BYTES-1)):
            with self.subTest(limit=limit):
                setattr(self.store,limit,value)
                self.assertQuota(lambda:self.reserve(job))
                self.assertEqual(self.store.active_revision_jobs(),[])
                setattr(self.store,limit,original[limit])

    def test_concurrent_reservations_do_not_share_a_single_candidate_slot(self):
        jobs = [self.job('concurrent-'+str(i)) for i in range(6)]
        def attempt(job):
            try:
                return self.reserve(job)['task_id']
            except VersionError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt,jobs))
        self.assertEqual(results.count('version_quota_exceeded'),5)
        self.assertEqual(len(self.store.active_revision_jobs()),1)

    def test_base_import_and_reservation_race_for_the_same_remaining_slot(self):
        job = self.job('base-reserve-race')
        barrier = threading.Barrier(2)
        def before_execute(query):
            if query.startswith(('INSERT INTO scientific_report_versions','INSERT INTO scientific_report_revision_jobs')):
                barrier.wait(timeout=5)
        self.db.before_execute = before_execute
        def attempt(kind):
            try:
                if kind == 'base':
                    self.store.ensure_base('owner',str(uuid.uuid4()),'A small report')
                else:
                    self.reserve(job)
                return 'admitted'
            except VersionError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt,('base','revision')))
        self.assertCountEqual(result,['admitted','version_quota_exceeded'])
        actual = self.db._query('SELECT COUNT(*) AS n FROM scientific_report_versions')[0]['n']
        self.assertEqual(actual+len(self.store.active_revision_jobs()),2)

    def test_unrelated_base_and_candidate_cannot_take_reserved_count(self):
        job = self.job('reserved-count')
        self.reserve(job)
        self.assertQuota(lambda:self.store.ensure_base('owner',str(uuid.uuid4()),'Small original'))
        self.assertQuota(lambda:self.store.append_candidate('owner',self.parent['version_id'],
            self.parent['report_sha256'],'Unrelated small candidate','unrelated',source_report_id=str(uuid.uuid4())))
        candidate = self.candidate(job)
        self.assertEqual(candidate['parent_version_id'],self.parent['version_id'])
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_versions')[0]['n'],2)

    def test_unrelated_base_cannot_take_reserved_owner_or_global_bytes(self):
        self.store.owner_version_count = 100
        job = self.job('reserved-bytes')
        self.reserve(job)
        self.assertQuota(lambda:self.store.ensure_base('owner',str(uuid.uuid4()),'x'))
        # A different owner does not bypass the global reservation.
        self.assertQuota(lambda:self.store.ensure_base('other',str(uuid.uuid4()),'x'))
        self.candidate(job)

    def test_terminal_failure_and_cancel_release_unfulfilled_capacity(self):
        for state in ('failed','cancelled','rejected','timed_out'):
            job = self.job('terminal-'+state)
            self.reserve(job)
            self.store.update_revision_job('owner',job['task_id'],state)
        final = self.job('after-terminal')
        self.assertEqual(self.reserve(final)['task_id'],final['task_id'])

    def test_reserved_source_cannot_be_imported_as_an_independent_root(self):
        job = self.job('lineage')
        self.reserve(job)
        with self.assertRaises(VersionError) as caught:
            self.store.ensure_base('owner',job['task_id'],'Attempted root import')
        self.assertEqual((caught.exception.code,caught.exception.status),('reserved_source_conflict',409))
        candidate = self.candidate(job)
        reopened = self.store.ensure_base('owner',job['task_id'],REPORT+'\r\nSynthetic candidate.\r\n')
        self.assertEqual(reopened,candidate)

    def test_append_commit_loss_consumes_capacity_without_a_later_binding_update(self):
        self.store.owner_version_count = 3
        job = self.job('lost-append')
        self.reserve(job)
        self.db.commit_error_prefix = 'INSERT INTO scientific_report_versions'
        with self.assertRaises(OSError):
            self.candidate(job)
        binding = self.store.get_revision_job('owner',job['task_id'])
        self.assertIsNone(binding['candidate_version_id'])
        self.assertEqual(binding['status'],'reserved')
        # Each query opens its own SQLite connection. A restarted process sees
        # the committed version as the consumed reservation before metadata ack.
        restarted = ReportVersionStore(self.db,clock=self.clock,owner_version_count=3,
            owner_version_bytes=self.store.owner_version_bytes,global_version_bytes=self.store.global_version_bytes)
        restarted.initialize()
        restarted.ensure_base('owner',str(uuid.uuid4()),'Another small report')
        recovered = restarted.append_candidate('owner',self.parent['version_id'],self.parent['report_sha256'],
            REPORT+'\r\nSynthetic candidate.\r\n','revision:'+job['task_id'],source_report_id=job['task_id'])
        self.assertEqual(recovered['source_report_id'],job['task_id'])
        self.assertEqual(len(restarted.list_versions('owner',SOURCE)),2)

    def test_maximum_reserved_bytes_can_be_fully_consumed_at_publication(self):
        job = self.job('maximum-candidate')
        self.reserve(job)
        result = self.store.append_candidate('owner',self.parent['version_id'],self.parent['report_sha256'],
            'x'*MAX_REPORT_BYTES,'maximum-candidate',source_report_id=job['task_id'])
        self.assertEqual(result['content_bytes'],MAX_REPORT_BYTES)


if __name__ == '__main__':
    unittest.main()
