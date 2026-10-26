"""Editor/adoption over the real API/client/worker chain; synthetic model output."""
import hashlib
import unittest

from harness import IntegrationHarness
import test_revision_end_to_end as fixture


class RevisionEditorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.h = IntegrationHarness(runtime=fixture.RevisionFixtureRuntime())
        self.addCleanup(self.h.close)
        self.root = self.h.submit().json()['id']
        self.h.execute_next()
        self.h.projection.reconcile()
        self.original = self.h.public_http.get('/scientific-download/' + self.root).content

    def revise(self, source, key):
        sections = self.h.public_http.get('/scientific-reports/' + source + '/sections').json()
        section = next(row for row in sections['sections'] if row['title'] == 'Findings')
        response = self.h.public_http.post('/scientific-reports/' + source + '/revision-previews',
            json=dict(base_sha256=sections['report_sha256'], section_id=section['section_id'],
                      instruction='Explain recorded abstract limitations. ' + key, timeout_seconds=600),
            headers={'Idempotency-Key': key})
        self.assertEqual(response.status_code, 200, response.text)
        intent = response.json()['intent']['intent_id']
        start = self.h.public_http.post('/scientific-revision-previews/' + intent + '/start', json={})
        self.assertEqual(start.status_code, 200, start.text)
        task_id = start.json()['id']
        self.assertEqual(self.h.execute_next()['state'], 'publishing')
        self.h.projection.reconcile()
        task = self.h.public_http.get('/scientific-research/' + task_id).json()
        self.assertEqual(task['state'], 'succeeded')
        return task

    def state(self):
        response = self.h.public_http.get('/scientific-reports/' + self.root + '/writing-state')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def compare(self, version):
        response = self.h.public_http.get('/scientific-report-versions/' + version + '/comparison')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def adopt(self, version, comparison, key):
        fields = ('expected_current_version_id', 'expected_selection_revision',
                  'base_sha256', 'candidate_sha256', 'comparison_token')
        return self.h.public_http.post('/scientific-report-versions/' + version + '/adopt',
            json={field: comparison['adoption'][field] for field in fields},
            headers={'Idempotency-Key': key})

    def test_candidate_compare_explicit_adopt_replay_and_exact_version_download(self):
        task = self.revise(self.root, 'first')
        version = task['candidate_version_id']
        before = self.state()
        self.assertEqual(before['selection_revision'], 0)
        comparison = self.compare(version)
        self.assertTrue(comparison['adoption']['can_adopt'])
        self.assertEqual(self.state(), before)  # Reading differences never adopts.
        response = self.adopt(version, comparison, 'adopt-first')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.state()['current_version_id'], version)
        self.assertEqual(self.state()['selection_revision'], 1)
        replay = self.adopt(version, comparison, 'adopt-first')
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(self.state()['selection_revision'], 1)
        candidate = self.h.public_http.get('/scientific-download/' + task['id']).content
        private = self.h.public_http.get('/scientific-report-versions/' + version + '/download').content
        self.assertEqual(private, candidate)
        self.assertEqual(hashlib.sha256(candidate).hexdigest(), comparison['candidate']['report_sha256'])
        self.assertEqual(self.h.public_http.get('/scientific-download/' + self.root).content, self.original)

    def test_old_branch_cannot_replace_adopted_version_and_owner_isolation_holds(self):
        a, b = self.revise(self.root, 'branch-a'), self.revise(self.root, 'branch-b')
        old_comparison = self.compare(b['candidate_version_id'])
        accepted = self.adopt(a['candidate_version_id'], self.compare(a['candidate_version_id']), 'adopt-a')
        self.assertEqual(accepted.status_code, 200, accepted.text)
        rejected = self.adopt(b['candidate_version_id'], old_comparison, 'adopt-stale-b')
        self.assertEqual(rejected.status_code, 409, rejected.text)
        fresh = self.compare(b['candidate_version_id'])
        self.assertFalse(fresh['adoption']['can_adopt'])
        self.assertEqual(fresh['adoption']['reason'], 'parent_not_current')
        for path in ('/scientific-reports/' + self.root + '/writing-state',
                     '/scientific-report-versions/' + b['candidate_version_id'] + '/comparison'):
            self.assertEqual(self.h.public_http.get(path, headers={'X-Forwarded-User':'another-owner'}).status_code, 404)
        self.assertEqual(self.state()['current_version_id'], a['candidate_version_id'])

    def test_new_revision_can_continue_from_adopted_private_version(self):
        first = self.revise(self.root, 'first')
        response = self.adopt(first['candidate_version_id'], self.compare(first['candidate_version_id']), 'first-adoption')
        self.assertEqual(response.status_code, 200, response.text)
        second = self.revise(first['id'], 'second')
        self.assertEqual(second['parent_version_id'], first['candidate_version_id'])
        comparison = self.compare(second['candidate_version_id'])
        self.assertTrue(comparison['adoption']['can_adopt'])
        response = self.adopt(second['candidate_version_id'], comparison, 'second-adoption')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.state()['selection_revision'], 2)
        self.assertEqual(self.h.public_http.get('/scientific-download/' + self.root).content, self.original)


if __name__ == '__main__':
    unittest.main()
