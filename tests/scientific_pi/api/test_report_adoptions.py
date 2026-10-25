"""Selection CAS and idempotency commit in one independently executed SQL."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

import test_revision_quotas as quota_fixture
from scientific_agent.pi_projection import sql
from scientific_agent.pi_report_versions import ReportVersionStore, VersionError


FIELDS = ('expected_current_version_id','expected_selection_revision',
          'base_sha256','candidate_sha256','comparison_token')


class ReportAdoptionStoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = quota_fixture.RevisionReservationQuotaTests('test_reserved_source_cannot_be_imported_as_an_independent_root')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store,self.db,self.clock = self.fixture.store,self.fixture.db,self.fixture.clock
        self.store.owner_version_count = 100
        self.store.owner_version_bytes = 64*1024*1024
        self.store.global_version_bytes = 256*1024*1024
        self.parent = self.fixture.parent
        self.root = self.parent['root_report_id']
        self.db._execute('CREATE TABLE scientific_reports(id TEXT PRIMARY KEY, task_id TEXT, content TEXT)')

    def candidate(self, key):
        job = self.fixture.job(key)
        self.fixture.reserve(job)
        candidate = self.fixture.candidate(job)
        self.store.update_revision_job('owner',job['task_id'],'succeeded',
            candidate_version_id=candidate['version_id'],result_digest='a'*64)
        content = self.store.get_version('owner',candidate['version_id'],True)['content']
        self.db._execute('INSERT INTO scientific_reports(id,task_id,content) VALUES ('+
            ','.join(map(sql,[job['task_id'],job['task_id'],content]))+')')
        return candidate

    def adopt(self, candidate, key, comparison=None):
        comparison = comparison or self.store.compare_candidate('owner',candidate['version_id'])
        return self.store.adopt_candidate('owner',candidate['version_id'],
            **{field:comparison['adoption'][field] for field in FIELDS},key=key)

    def state(self):
        return self.store.writing_state('owner',self.root)

    def test_no_events_default_to_baseline_without_writing(self):
        before = len(self.db.sql)
        state = self.state()
        self.assertEqual((state['current_version_id'],state['selection_revision']),(self.parent['version_id'],0))
        self.assertEqual(len(self.db.sql),before)
        empty = self.store.writing_state('other',self.root,self.parent['report_sha256'])
        self.assertIsNone(empty['current_version_id'])
        self.assertEqual(empty['versions'],[])
        self.assertEqual(len(self.db.sql),before)

    def test_concurrent_candidates_from_one_head_cannot_both_be_adopted(self):
        candidates = [self.candidate('candidate-'+str(n)) for n in range(2)]
        comparisons = [self.store.compare_candidate('owner',c['version_id']) for c in candidates]
        barrier = threading.Barrier(2)
        def before_execute(query):
            if query.startswith('INSERT INTO scientific_report_adoptions'):
                barrier.wait(timeout=5)
        self.db.before_execute = before_execute
        def attempt(index):
            try:
                return self.adopt(candidates[index],'concurrent-'+str(index),comparisons[index])['target_version_id']
            except VersionError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt,range(2)))
        self.assertEqual(result.count('selection_conflict'),1)
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_adoptions')[0]['n'],1)
        self.assertEqual(self.state()['selection_revision'],1)

    def test_concurrent_same_key_recovers_one_receipt(self):
        candidate = self.candidate('same-key')
        comparison = self.store.compare_candidate('owner',candidate['version_id'])
        barrier = threading.Barrier(2)
        def before_execute(query):
            if query.startswith('INSERT INTO scientific_report_adoptions'):
                barrier.wait(timeout=5)
        self.db.before_execute = before_execute
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:self.adopt(candidate,'one-key',comparison),range(2)))
        self.assertEqual(results[0]['adoption_id'],results[1]['adoption_id'])
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_adoptions')[0]['n'],1)
        self.assertEqual(self.state()['current_version_id'],candidate['version_id'])

    def test_commit_response_loss_retries_after_restart_without_second_selection_change(self):
        candidate = self.candidate('lost-response')
        comparison = self.store.compare_candidate('owner',candidate['version_id'])
        self.db.commit_error_prefix = 'INSERT INTO scientific_report_adoptions'
        with self.assertRaises(OSError):
            self.adopt(candidate,'lost-key',comparison)
        self.assertEqual(self.state()['current_version_id'],candidate['version_id'])
        restarted = ReportVersionStore(self.db,clock=self.clock)
        restarted.initialize()
        result = restarted.adopt_candidate('owner',candidate['version_id'],
            **{field:comparison['adoption'][field] for field in FIELDS},key='lost-key')
        self.assertTrue(result['replayed'])
        self.assertEqual(self.state()['selection_revision'],1)
        self.assertEqual(self.db._query('SELECT COUNT(*) AS n FROM scientific_report_adoptions')[0]['n'],1)

    def test_key_conflict_cannot_adopt_different_candidate(self):
        first,second = self.candidate('first'),self.candidate('second')
        self.adopt(first,'fixed-key')
        with self.assertRaises(VersionError) as caught:
            self.adopt(second,'fixed-key')
        self.assertEqual(caught.exception.code,'adoption_key_conflict')
        self.assertEqual(self.state()['current_version_id'],first['version_id'])

    def test_source_removed_between_check_and_insert_cannot_create_an_adoption(self):
        candidate = self.candidate('source-race')
        comparison = self.store.compare_candidate('owner',candidate['version_id'])
        def remove_source(query):
            if query.startswith('INSERT INTO scientific_report_adoptions'):
                self.db.before_execute = None
                self.db._execute('DELETE FROM scientific_reports WHERE id='+sql(candidate['source_report_id']))
        self.db.before_execute = remove_source
        with self.assertRaises(VersionError) as caught:
            self.adopt(candidate,'missing-source',comparison)
        self.assertEqual(caught.exception.code,'selection_conflict')
        self.assertEqual(self.state()['selection_revision'],0)

    def test_binding_completion_changed_between_check_and_insert_is_atomic(self):
        candidate = self.candidate('binding-race')
        comparison = self.store.compare_candidate('owner',candidate['version_id'])
        def remove_completion(query):
            if query.startswith('INSERT INTO scientific_report_adoptions'):
                self.db.before_execute = None
                self.db._execute("UPDATE scientific_report_revision_jobs SET status='publishing' WHERE task_id="+sql(candidate['source_report_id']))
        self.db.before_execute = remove_completion
        with self.assertRaises(VersionError):
            self.adopt(candidate,'incomplete-source',comparison)
        self.assertEqual(self.state()['selection_revision'],0)

    def test_owner_and_global_event_quotas_are_part_of_the_cas_insert(self):
        candidate = self.candidate('adoption-quota')
        comparison = self.store.compare_candidate('owner',candidate['version_id'])
        for limit in ('owner_adoption_count','global_adoption_count'):
            previous = getattr(self.store,limit)
            setattr(self.store,limit,0)
            with self.assertRaises(VersionError) as caught:
                self.adopt(candidate,limit,comparison)
            self.assertEqual((caught.exception.code,caught.exception.status),('adoption_quota_exceeded',429))
            self.assertEqual(self.state()['selection_revision'],0)
            setattr(self.store,limit,previous)


if __name__ == '__main__':
    unittest.main()
