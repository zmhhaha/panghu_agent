"""Explicit adoption and precise comparison through the real local API."""
import hashlib
import unittest
import uuid
from unittest.mock import patch

import test_revision_execution as execution
from scientific_agent.pi_sections import index_sections


ADOPTION_FIELDS = ('expected_current_version_id','expected_selection_revision',
                   'base_sha256','candidate_sha256','comparison_token')


class RevisionEditorAPITests(unittest.TestCase):
    def setUp(self):
        self.fixture = execution.RevisionExecutionTests('test_revision_dispatch_reuses_task_and_publishes_candidate_without_changing_parent')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client,self.worker = self.fixture.client,self.fixture.worker
        self.database,self.versions = self.fixture.database,self.fixture.versions
        self.projection = self.fixture.projection
        self.root = self.fixture.parent_task

    def candidate(self, key='editor', source=None):
        source = source or self.root
        report = self.database.get_report(source)['content']
        section = next(row for row in index_sections(report)['sections'] if row['title']=='Analysis')
        items = self.fixture.items if source == self.root else self.worker.results[source]['artifacts']
        with patch.object(self.worker,'artifacts',return_value={'items':items}):
            preview = self.client.post('/scientific-reports/'+source+'/revision-previews',
                json=dict(base_sha256=hashlib.sha256(report.encode()).hexdigest(),section_id=section['section_id'],
                          instruction='Clarify limitations using only fixed records.',timeout_seconds=600),
                headers={'Idempotency-Key':key})
        self.assertEqual(preview.status_code,200,preview.text)
        task = self.client.post('/scientific-revision-previews/'+preview.json()['intent']['intent_id']+'/start',json={})
        self.assertEqual(task.status_code,200,task.text)
        task_id = task.json()['id']
        self.worker.candidate(task_id)
        self.projection.reconcile()
        data = self.client.get('/scientific-research/'+task_id).json()
        self.assertEqual(data['state'],'succeeded')
        return data['candidate_version_id'],task_id

    def compare(self, version):
        response = self.client.get('/scientific-report-versions/'+version+'/comparison')
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def adopt(self, version, comparison=None, key='adopt-editor'):
        comparison = comparison or self.compare(version)
        return self.client.post('/scientific-report-versions/'+version+'/adopt',
            json={k:comparison['adoption'][k] for k in ADOPTION_FIELDS},headers={'Idempotency-Key':key})

    def state(self, source=None):
        response = self.client.get('/scientific-reports/'+(source or self.root)+'/writing-state')
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_reading_unimported_legacy_or_pi_report_never_creates_a_version(self):
        task = self.client.post('/scientific-research',json={'topic':'Unimported report'}).json()['id']
        self.worker.complete(task,self.fixture.REPORT)
        self.projection.reconcile()
        before = len(self.database.sql)
        state = self.state(task)
        self.assertIsNone(state['current_version_id'])
        self.assertEqual(state['current_sha256'],hashlib.sha256(self.fixture.REPORT.encode()).hexdigest())
        self.assertEqual((state['selection_revision'],state['versions']),(0,[]))
        self.assertEqual(len(self.database.sql),before)
        legacy = str(uuid.uuid4())
        self.database._execute("INSERT INTO scientific_reports(id,task_id,topic,content) VALUES ('"+legacy+"','"+legacy+"','Legacy','Exact original')")
        before = len(self.database.sql)
        self.assertIsNone(self.state(legacy)['current_version_id'])
        self.assertEqual(len(self.database.sql),before)

    def test_comparison_contains_exact_selected_bytes_and_does_not_adopt(self):
        version,task = self.candidate()
        before = len(self.database.sql)
        data = self.compare(version)
        selection = self.fixture.section
        raw = self.fixture.REPORT.encode()
        self.assertEqual(data['section']['before'].encode(),raw[selection['start_byte']:selection['end_byte']])
        revised = self.database.get_report(task)['content'].encode()
        self.assertEqual(data['section']['after'].encode(),revised[data['section']['start_byte']:data['section']['candidate_end_byte']])
        self.assertTrue(data['diff']['available'])
        self.assertIn('-Synthetic finding',data['diff']['text'])
        self.assertIn('+Revised synthetic finding',data['diff']['text'])
        self.assertTrue(data['adoption']['can_adopt'])
        self.assertEqual(self.state()['current_version_id'],self.fixture.preview['parent']['version_id'])
        self.assertEqual(self.state()['selection_revision'],0)
        self.assertEqual(len(self.database.sql),before)

    def test_explicit_adoption_changes_only_private_selection_and_retains_original_download(self):
        version,task = self.candidate()
        response = self.adopt(version)
        self.assertEqual(response.status_code,200,response.text)
        data = response.json()
        self.assertEqual((data['current_version_id'],data['selection_revision']),(version,1))
        self.assertEqual(data['adoption']['target_version_id'],version)
        self.assertEqual(self.state()['current_version_id'],version)
        self.assertEqual(self.state(task)['root_report_id'],self.root)
        self.assertEqual(self.client.get('/scientific-download/'+self.root).content,self.fixture.REPORT.encode())
        self.assertEqual(self.client.get('/scientific-report-versions/'+version+'/download').content,self.database.get_report(task)['content'].encode())
        self.assertEqual(self.worker.put_count,2)

    def test_prepublication_version_cannot_be_compared_or_adopted(self):
        task = self.fixture.start()
        self.worker.candidate(task)
        self.database.skip_report_insert = True
        self.projection.reconcile()
        binding = self.versions.get_revision_job('owner',task)
        version = binding['candidate_version_id']
        self.assertIsNotNone(version)
        self.assertEqual(self.client.get('/scientific-report-versions/'+version+'/comparison').status_code,409)
        payload = dict(expected_current_version_id=self.fixture.preview['parent']['version_id'],
            expected_selection_revision=0,base_sha256=self.fixture.preview['parent']['report_sha256'],
            candidate_sha256=self.worker.results[task]['report_sha256'],comparison_token='0'*64)
        self.assertEqual(self.client.post('/scientific-report-versions/'+version+'/adopt',json=payload,
            headers={'Idempotency-Key':'unpublished'}).status_code,409)
        # A fabricated completion flag alone cannot stand in for a real source.
        self.versions.update_revision_job('owner',task,'succeeded')
        self.assertEqual(self.client.get('/scientific-report-versions/'+version+'/comparison').status_code,409)
        self.assertEqual(self.state()['selection_revision'],0)

    def test_source_report_digest_task_identity_and_index_must_match(self):
        version,task = self.candidate()
        comparison = self.compare(version)
        original = self.database.get_report(task)['content']
        self.database._execute("UPDATE scientific_reports SET content='Corrupt fixture' WHERE id='"+task+"'")
        self.assertEqual(self.adopt(version,comparison).status_code,409)
        self.database.connection.execute('UPDATE scientific_reports SET content=?,task_id=? WHERE id=?',(original,self.root,task))
        self.database.connection.commit()
        self.assertEqual(self.adopt(version,comparison).status_code,409)
        self.assertEqual(self.state()['selection_revision'],0)

    def test_late_branch_cannot_overwrite_current_and_old_comparison_conflicts(self):
        first,_ = self.candidate('first')
        second,_ = self.candidate('second')
        stale = self.compare(second)
        self.assertEqual(self.adopt(first,key='adopt-first').status_code,200)
        self.assertEqual(self.adopt(second,stale,key='stale-branch').status_code,409)
        fresh = self.compare(second)
        self.assertFalse(fresh['adoption']['can_adopt'])
        self.assertEqual(fresh['adoption']['reason'],'parent_not_current')
        self.assertEqual(self.adopt(second,fresh,key='fresh-branch').status_code,409)
        self.assertEqual(self.state()['current_version_id'],first)

    def test_second_revision_can_start_from_private_candidate_source_and_replay_old_adoption(self):
        first,source = self.candidate('first')
        comparison = self.compare(first)
        self.assertEqual(self.adopt(first,comparison,key='first-key').status_code,200)
        second,_ = self.candidate('second',source=source)
        self.assertEqual(self.adopt(second,key='second-key').status_code,200)
        replay = self.adopt(first,comparison,key='first-key')
        self.assertEqual(replay.status_code,200,replay.text)
        data = replay.json()
        self.assertEqual((data['current_version_id'],data['selection_revision']),(second,2))
        self.assertEqual((data['adoption']['target_version_id'],data['adoption']['selection_revision']),(first,1))
        self.assertTrue(data['adoption']['replayed'])
        self.assertEqual(self.adopt(second,key='first-key').status_code,409)

    def test_identity_invalid_input_stale_hash_and_comparison_fingerprint(self):
        version,_ = self.candidate()
        comparison = self.compare(version)
        for user,status in (('',401),('other',404)):
            self.assertEqual(self.client.get('/scientific-report-versions/'+version+'/comparison',headers={'X-Forwarded-User':user}).status_code,status)
            payload = {k:comparison['adoption'][k] for k in ADOPTION_FIELDS}
            self.assertEqual(self.client.post('/scientific-report-versions/'+version+'/adopt',json=payload,
                headers={'X-Forwarded-User':user,'Idempotency-Key':'private'}).status_code,status)
        for changes,status in ((dict(expected_selection_revision=True),422),(dict(path='/private'),422),
                               (dict(base_sha256='0'*64),409),(dict(candidate_sha256='0'*64),409),
                               (dict(comparison_token='0'*64),409)):
            payload = dict({k:comparison['adoption'][k] for k in ADOPTION_FIELDS},**changes)
            self.assertEqual(self.client.post('/scientific-report-versions/'+version+'/adopt',json=payload,
                headers={'Idempotency-Key':'invalid'}).status_code,status)
        payload = {k:comparison['adoption'][k] for k in ADOPTION_FIELDS}
        self.assertEqual(self.client.post('/scientific-report-versions/'+version+'/adopt',json=payload).status_code,422)
        self.assertEqual(self.state()['selection_revision'],0)

    def test_comparison_returns_literal_dynamic_text_and_bounded_diff_fallback(self):
        diff = self.versions._section_diff('<script>original</script>\r\n','<img src=x onerror=alert(1)>\r\n')
        self.assertIn('<img src=x onerror=alert(1)>',diff['text'])
        large = self.versions._section_diff('x'*(130*1024),'changed')
        self.assertEqual((large['available'],large['text'],large['reason']),(False,'','diff_too_large'))
        no_newline = self.versions._section_diff('before','after')
        self.assertIn('-before\n\\ No newline at end of file\n+after',no_newline['text'])


if __name__ == '__main__':
    unittest.main()
