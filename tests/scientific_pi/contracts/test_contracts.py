"""Wire-contract tests independent of any model, database, or network."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from uuid import uuid4

from scientific_agent.pi_contracts import (
    ContractError, ResearchJob, canonical_json, contract_schema, input_digest,
    parse_timestamp, public_status, report_sha256, result_digest,
)


class ContractTests(unittest.TestCase):
    def job(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = {"schema_version": "1", "task_id": str(uuid4()), "topic": "研究题目",
                   "user_id": "trusted-opaque-user", "engine": "pi", "mode": "review",
                   "timeout_seconds": 1800, "created_at": now.isoformat().replace("+00:00", "Z"),
                   "execution_deadline_at": (now + timedelta(seconds=1800)).isoformat().replace("+00:00", "Z"),
                   "skill_bundle_revision": "local-v1", "runtime_revision": "pinned"}
        payload["input_digest"] = input_digest(payload)
        return payload

    def test_input_vector_matches_documented_unicode_bytes(self):
        value = {"topic": "研究题目", "user_id": "trusted-opaque-user", "engine": "pi", "mode": "review", "timeout_seconds": 1800}
        self.assertEqual(input_digest(value), "a225a6308ec7a6a96463c5ea75fbfef814ec3ef4aa141cdf286a4e33cceb5851")
        self.assertEqual(canonical_json(value), '{"engine":"pi","mode":"review","timeout_seconds":1800,"topic":"研究题目","user_id":"trusted-opaque-user"}')

    def test_report_and_result_digest_vectors(self):
        digest = report_sha256("abc")
        self.assertEqual(digest, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        result = {"schema_version": "1", "task_id": "00000000-0000-4000-8000-000000000001", "attempt": 1,
                  "report_sha256": digest,
                  "artifacts": [{"artifact_id": "report", "media_type": "text/markdown", "size": 3, "sha256": digest}],
                  "provenance": {"runtime_revision": "pinned", "skill_bundle_revision": "pinned", "model": "fixture-model", "image_digest": "sha256:" + "0" * 64}}
        expected = "510fcd75b79cbd4357a361a1e2a4463bcf8ef00767f59f61424d7e2222b2bb7a"
        self.assertEqual(result_digest(result), expected)
        result.update({"state": "succeeded", "result_digest": "ignored", "report_markdown": "abc"})
        self.assertEqual(result_digest(result), expected)
        result["artifacts"][0]["path"] = "irrelevant/storage/location"
        self.assertEqual(result_digest(result), expected)
        self.assertNotEqual(report_sha256("abc\n"), digest)

    def test_job_round_trip_and_hash_tampering(self):
        payload = self.job()
        self.assertEqual(ResearchJob.from_dict(payload).to_dict(), payload)
        payload["topic"] = "changed"
        with self.assertRaises(ContractError) as caught:
            ResearchJob.from_dict(payload)
        self.assertEqual(caught.exception.code, "input_digest_mismatch")

    def test_replay_hash_ignores_server_metadata(self):
        payload = self.job()
        digest = input_digest(payload)
        payload.update({"runtime_revision": "changed", "created_at": "2020-01-01T00:00:00Z", "email": "other@example.test"})
        self.assertEqual(input_digest(payload), digest)

    def test_rejects_unsafe_canonical_types(self):
        for value in (float("nan"), 1.0, 2**53, {1: "integer-key"}, "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(ContractError):
                canonical_json(value)
        self.assertEqual(canonical_json({"\U00010000": 1, "\ue000": 2}), '{"\ue000":2,"\U00010000":1}')

    def test_job_rejects_unknown_fields_wrong_types_and_deadline(self):
        for mutation in ({"command": "untrusted"}, {"timeout_seconds": True}, {"user_id": ""},
                         {"execution_deadline_at": "2000-01-01T00:00:00Z"}, {"schema_version": "2"},
                         {"task_id": "../../queue.sqlite"}):
            payload = self.job()
            payload.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                ResearchJob.from_dict(payload)

    def test_no_ambiguous_terminal_status(self):
        for state in ("failed", "cancelled", "timed_out"):
            self.assertEqual(public_status(state), "failed")
        self.assertEqual(public_status("publishing"), "running")
        self.assertEqual(public_status("succeeded"), "done")
        with self.assertRaises(ContractError):
            public_status("unknown")

    def test_timestamps_need_utc_and_schema_is_serializable(self):
        for value in ("2026-10-21", "2026-10-21T00:00:00", "2026-10-21T00:00:00+08:00"):
            with self.assertRaises(ContractError):
                parse_timestamp(value)
        self.assertEqual(parse_timestamp("2026-10-21T00:00:00Z").utcoffset(), timedelta(0))
        self.assertFalse(contract_schema()["additionalProperties"])
        json.dumps(contract_schema())


if __name__ == "__main__":
    unittest.main()
