"""Strict revision wire boundaries; no model or network calls."""
import copy
import base64
from datetime import timedelta
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from scientific_agent.pi_contracts import (
    ContractError, ResearchJob, canonical_json, parse_timestamp,
)
from scientific_agent.pi_revision_contracts import (
    MAX_REVISION_INPUT_BYTES, RevisionJob, revision_contract_schema,
    revision_input_digest, revision_material_digest, validate_revision_materials,
)


def job(**changes):
    payload = dict(schema_version='1', task_kind='revision', task_id=str(uuid4()),
                   topic='科研综述', user_id='trusted-owner', engine='pi', mode='review',
                   timeout_seconds=600, created_at='2026-10-25T01:00:00Z',
                   execution_deadline_at='2026-10-25T01:10:00Z',
                   skill_bundle_revision='pinned-skills', runtime_revision='pinned-runtime',
                   intent_id=str(uuid4()), parent_version_id=str(uuid4()), root_report_id=str(uuid4()),
                   base_sha256='a' * 64, section_id='sec_utf8_01', section_sha256='b' * 64,
                   instruction='解释局限，保留引用。', input_sha256='c' * 64,
                   material_policy='recorded_evidence_only')
    payload.update(changes)
    payload['input_digest'] = revision_input_digest(payload)
    return payload


class RevisionContractTests(unittest.TestCase):
    def test_round_trip_is_independent_of_research_job(self):
        payload = job()
        self.assertEqual(RevisionJob.from_dict(payload).to_dict(), payload)
        with self.assertRaises(ContractError):
            ResearchJob.from_dict(payload)

    def test_all_fixed_input_and_runtime_pins_are_fingerprinted(self):
        payload = job()
        for key, value in dict(topic='New topic', user_id='other', instruction='Other instruction',
                               skill_bundle_revision='other-skills', runtime_revision='other-runtime',
                               intent_id=str(uuid4()), parent_version_id=str(uuid4()), root_report_id=str(uuid4()),
                               base_sha256='0' * 64, section_id='new_section', section_sha256='0' * 64,
                               input_sha256='0' * 64, timeout_seconds=601).items():
            changed = dict(payload, **{key: value})
            with self.subTest(key=key):
                self.assertNotEqual(revision_input_digest(changed), payload['input_digest'])
                with self.assertRaises(ContractError) as caught:
                    RevisionJob.from_dict(changed)
                self.assertEqual(caught.exception.code, 'input_digest_mismatch')

    def test_server_identity_and_time_do_not_change_request_fingerprint(self):
        payload = job()
        changed = dict(payload, task_id=str(uuid4()), created_at='2026-10-25T02:00:00Z',
                       execution_deadline_at='2026-10-25T02:10:00Z')
        self.assertEqual(revision_input_digest(changed), payload['input_digest'])
        self.assertEqual(RevisionJob.from_dict(changed).to_dict(), changed)

    def test_job_rejects_unknown_and_missing_fields(self):
        payload = job()
        for extra in ('command', 'input', 'report', 'callback_url', 'full_text_path', 'profile', 'api_key'):
            with self.subTest(extra=extra), self.assertRaises(ContractError):
                RevisionJob.from_dict(dict(payload, **{extra: 'untrusted'}))
        for missing in payload:
            changed = dict(payload)
            del changed[missing]
            with self.subTest(missing=missing), self.assertRaises(ContractError):
                RevisionJob.from_dict(changed)

    def test_rejects_wrong_types_policies_and_unbounded_text(self):
        mutations = [dict(timeout_seconds=True), dict(timeout_seconds=59), dict(timeout_seconds=3601),
                     dict(schema_version=1), dict(task_kind='research'), dict(mode='brief'), dict(engine='crewai'),
                     dict(material_policy='restored_full_text'), dict(topic=' x '), dict(topic='x' * 501),
                     dict(topic='line\nfeed'), dict(user_id=' '), dict(user_id='other\x00'),
                     dict(instruction='x' * 2001), dict(instruction='\x1bunsafe'), dict(instruction=' x '),
                     dict(section_id='../section'), dict(section_id='章节'), dict(section_id='x' * 201),
                     dict(skill_bundle_revision='x\x00'), dict(runtime_revision='')]
        for changes in mutations:
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                RevisionJob.from_dict(job(**changes))

    def test_digest_and_uuid_are_canonical(self):
        for key in ('task_id', 'intent_id', 'parent_version_id', 'root_report_id'):
            for bad in ('../../db', 'ABCDEFAB-CDEF-4000-8000-ABCDEFABCDEF', None):
                with self.subTest(key=key, bad=bad), self.assertRaises(ContractError):
                    RevisionJob.from_dict(job(**{key: bad}))
        for key in ('base_sha256', 'section_sha256', 'input_sha256'):
            for bad in ('A' * 64, '0' * 63, None):
                with self.subTest(key=key, bad=bad), self.assertRaises(ContractError):
                    RevisionJob.from_dict(job(**{key: bad}))

    def test_deadline_cannot_restart_or_exceed_execution_budget(self):
        for deadline in ('2026-10-25T01:00:00Z', '2026-10-25T01:10:01Z',
                         '2026-10-25T00:59:59Z', '2026-10-25T01:10:00',
                         '2026-10-25T09:10:00+08:00'):
            with self.subTest(deadline=deadline), self.assertRaises(ContractError):
                RevisionJob.from_dict(job(execution_deadline_at=deadline))
        payload = job(timeout_seconds=60, execution_deadline_at='2026-10-25T01:01:00Z')
        self.assertEqual(parse_timestamp(payload['execution_deadline_at']) - parse_timestamp(payload['created_at']), timedelta(seconds=60))
        RevisionJob.from_dict(payload)

    def test_job_size_remains_small_even_with_all_text_fields(self):
        payload = job(topic='题' * 500, user_id='人' * 200, instruction='😀' * 2000,
                      skill_bundle_revision='技' * 200, runtime_revision='版' * 200)
        self.assertLess(len(canonical_json(payload).encode('utf-8')), 16 * 1024)
        RevisionJob.from_dict(payload)
        # Pins have a character bound as well as the complete envelope byte cap.
        with self.assertRaises(ContractError):
            RevisionJob.from_dict(job(skill_bundle_revision='😀' * 201))

    def test_safe_unicode_and_multiline_instruction_round_trip(self):
        payload = job(instruction='比较材料。\r\n\t说明局限😀')
        self.assertEqual(json.loads(canonical_json(payload)), payload)
        RevisionJob.from_dict(payload)
        with self.assertRaises(ContractError):
            job(instruction='broken\ud800')

    def test_material_digest_is_exact_and_bounded_by_utf8_bytes(self):
        material = dict(report='中文\r\n', items=[], artifacts={})
        expected = revision_material_digest(material)
        self.assertEqual(expected, revision_material_digest(dict(reversed(list(material.items())))))
        self.assertNotEqual(expected, revision_material_digest(dict(material, report='中文\n')))
        with self.assertRaises(ContractError) as caught:
            revision_material_digest({'report': '研' * (MAX_REVISION_INPUT_BYTES // 3 + 1)})
        self.assertEqual(caught.exception.status_code, 413)

    def test_material_hash_and_outer_shape_checked_before_record_parsing(self):
        material = dict(report='fixture', items=[], artifacts={})
        payload = job(input_sha256=revision_material_digest(material))
        altered = dict(material, report='changed')
        with self.assertRaises(ContractError) as caught:
            validate_revision_materials(payload, altered)
        self.assertEqual(caught.exception.code, 'revision_input_mismatch')
        for invalid in (None, [], dict(material, filesystem_path='/private'), {'report': 'fixture'}):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                validate_revision_materials(payload, invalid)

    def test_material_requires_exact_four_canonical_base64_records(self):
        material = dict(report='fixture', items=[], artifacts=dict.fromkeys(('papers', 'evidence', 'claims', 'references'), 'e30='))
        for bad in ('e30=\n', 'e30', 'e31=', '-_==', 'not base64', 3, None):
            altered = copy.deepcopy(material)
            altered['artifacts']['papers'] = bad
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                validate_revision_materials(job(input_sha256=revision_material_digest(altered)), altered)

    def fixed_material(self):
        from test_revision_validation import revision_fixture
        from scientific_agent.pi_sections import select_section
        fixture = revision_fixture()
        material = dict(report=fixture['report'], items=fixture['items'],
                        artifacts={key: base64.b64encode(raw).decode('ascii')
                                   for key, raw in fixture['artifacts'].items()})
        section = select_section(material['report'], fixture['section_id'], fixture['base_sha256'])
        payload = job(base_sha256=fixture['base_sha256'], section_id=fixture['section_id'],
                      section_sha256=section['sha256'], input_sha256=revision_material_digest(material))
        return payload, material, fixture

    def test_material_round_trip_preserves_original_report_and_record_bytes(self):
        payload, material, fixture = self.fixed_material()
        restored = validate_revision_materials(payload, material)
        self.assertEqual(restored, {key: fixture[key] for key in ('report', 'items', 'artifacts')})
        self.assertIn('\r\n', restored['report'])
        self.assertIn('科研', payload['topic'])

    def test_section_hash_mismatch_rejected_even_with_valid_material_digest(self):
        payload, material, _ = self.fixed_material()
        payload['section_sha256'] = '0' * 64
        payload['input_digest'] = revision_input_digest(payload)
        with self.assertRaises(ContractError) as caught:
            validate_revision_materials(payload, material)
        self.assertEqual(caught.exception.code, 'revision_section_mismatch')
        self.assertEqual(caught.exception.status_code, 409)

    def test_historical_full_text_path_is_recorded_without_opening_the_file(self):
        from test_revision_validation import manifest
        payload, material, fixture = self.fixed_material()
        papers = [json.loads(line) for line in fixture['artifacts']['papers'].splitlines()]
        for paper in papers:
            paper['full_text_path'] = 'old-unavailable-workspace/full.txt'
            paper['availability']['full_text'] = dict(available=True, obtained_at='2026-10-25T00:00:00Z')
        fixture['artifacts']['papers'] = ''.join(json.dumps(p) + '\n' for p in papers).encode()
        material['artifacts']['papers'] = base64.b64encode(fixture['artifacts']['papers']).decode('ascii')
        material['items'] = manifest(material['report'], fixture['artifacts'])
        payload['input_sha256'] = revision_material_digest(material)
        payload['input_digest'] = revision_input_digest(payload)
        with patch('builtins.open', side_effect=AssertionError('No full-text filesystem read')):
            restored = validate_revision_materials(payload, material)
        self.assertEqual(restored['artifacts']['papers'], fixture['artifacts']['papers'])
        for altered_artifacts in ({}, dict(material['artifacts'], full_text='c2VjcmV0')):
            altered = dict(material, artifacts=altered_artifacts)
            with self.subTest(extra=altered_artifacts), self.assertRaises(ContractError):
                validate_revision_materials(job(input_sha256=revision_material_digest(altered)), altered)

    def test_schema_disallows_extra_fields_and_keeps_version_separate(self):
        schema = revision_contract_schema()
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']), set(job()))
        self.assertEqual(schema['properties']['task_kind'], {'const': 'revision'})
        self.assertEqual(schema['properties']['material_policy'], {'const': 'recorded_evidence_only'})
        canonical_json(schema)

    def test_client_uses_separate_authenticated_revision_route(self):
        from scientific_agent.pi_client import PiClient
        calls = []
        payload, material, _ = self.fixed_material()
        def transport(*args):
            calls.append(args)
            return 201, {}, json.dumps({'id': payload['task_id'], 'state': 'queued'}).encode()
        client = PiClient('https://worker.internal', 'test-control', transport=transport)
        self.assertEqual(client.put_revision(payload, material)['id'], payload['task_id'])
        self.assertEqual(calls, [('PUT', 'https://worker.internal/v1/revisions/' + payload['task_id'],
                                 {'Authorization': 'Bearer test-control', 'Content-Type': 'application/json'},
                                 {'job': payload, 'input': material}, 10)])

    def test_production_transport_keeps_unicode_payload_in_utf8(self):
        from scientific_agent.pi_client import urllib_transport
        payload, material, _ = self.fixed_material()
        envelope = {'job': payload, 'input': material}
        with patch('urllib.request.urlopen') as open_request:
            response = open_request.return_value.__enter__.return_value
            response.status, response.headers = 201, {}
            response.read.return_value = b'{}'
            urllib_transport('PUT', 'https://worker.internal/v1/revisions/' + payload['task_id'],
                             {'Content-Type': 'application/json'}, envelope, 10)
        request = open_request.call_args.args[0]
        self.assertIn('科研'.encode(), request.data)
        self.assertEqual(json.loads(request.data), envelope)
        self.assertLessEqual(len(request.data), len(canonical_json(envelope).encode('utf-8')))


if __name__ == '__main__':
    unittest.main()
