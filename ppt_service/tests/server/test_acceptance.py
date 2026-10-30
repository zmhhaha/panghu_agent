"""Isolated human-review persistence/auth contracts; no real acceptance evidence."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
import unittest

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from ppt_service.server.auth import Identity, Settings
from ppt_service.server.acceptance import AcceptanceBase, AcceptanceError, AcceptanceService, ReviewContext, create_acceptance_router
from ppt_service.server.acceptance.contracts import PageDescriptor, ReviewCommand, SourceBinding
from ppt_service.server.acceptance.store import AcceptanceEntry, AcceptanceStream


OWNER = Identity("https://casdoor.test", "stable-alice")
ROOT = "/api/v1/projects/project-one/versions/version-one/acceptance"
PARSE = TypeAdapter(ReviewCommand).validate_python


def context():
    return ReviewContext(
        owner=OWNER,
        binding=SourceBinding(
            project_id="project-one", version_id="version-one", source_revision=5, manifest_sha256="1" * 64,
            input_snapshot_sha256="2" * 64, pptx_sha256="3" * 64, source_fingerprint_sha256="4" * 64,
            evidence_id="fixture-evidence-only", evidence_sha256="5" * 64,
            runtime={"engine": "pi", "runtime_revision": "test-only-revision", "profile": "ppt", "checkpoint_format": "artifact-replay-v1"},
        ),
        source_revision=5,
        pages=(PageDescriptor(number=1, title="Synthetic page", preview_sha256="6" * 64, source_sha256="7" * 64),
               PageDescriptor(number=2, title="Synthetic chart", preview_sha256="8" * 64, source_sha256="9" * 64)),
        native_chart_count=1, current_input_snapshot_sha256="2" * 64, current_source_fingerprint_sha256="4" * 64,
        origin_status="candidate", phase="generate",
    )


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="ppt-human-review-test-")
        self.addCleanup(self.temp.cleanup)
        self.db_url = "sqlite:///" + str(Path(self.temp.name) / "isolated.sqlite")
        self.engine = create_engine(self.db_url, connect_args={"check_same_thread": False, "timeout": 5})
        self.addCleanup(self.engine.dispose)
        AcceptanceBase.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.context = context()
        self.locks = []

        def resolve(session, owner, project_id, version_id, *, for_update):
            self.locks.append(for_update)
            return self.context

        self.resolve = resolve
        self.service = AcceptanceService(self.sessions, self.resolve)
        self.app = FastAPI()
        self.app.state.settings = Settings(
            self.db_url, self.temp.name, oidc_issuer=OWNER.issuer, oidc_audience="ppt",
            trusted_proxy_cidrs=("127.0.0.1/32",), allowed_origins=("https://ppt.test",),
        )
        self.app.state.jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=self.signing_key.public_key()))
        self.app.include_router(create_acceptance_router(self.service))
        self.client = TestClient(self.app, base_url="https://ppt.test", client=("127.0.0.1", 12345))
        self.addCleanup(self.client.close)
        self.client.cookies.set("ppt_csrf", "test-csrf", domain="ppt.test", path="/")

    def headers(self, **claims):
        payload = {"iss": OWNER.issuer, "sub": OWNER.sub, "aud": "ppt", "email": "alice@ppt.test", "email_verified": True, "iat": datetime.now(timezone.utc),
                   "exp": datetime.now(timezone.utc) + timedelta(minutes=5)} | claims
        return {"Authorization": "Bearer " + jwt.encode(payload, self.signing_key, algorithm="RS256"),
                "Origin": "https://ppt.test", "X-CSRF-Token": "test-csrf", "Idempotency-Key": "operation-one"}

    def visual(self, revision=0, verdict="pass"):
        return {"kind": "visual_review", "expected_revision": revision,
                "binding": self.context.binding.model_dump(), "note": "Synthetic human test assertion only",
                "pages": [{"number": page.number, "preview_sha256": page.preview_sha256, "verdict": verdict, "issues": []} for page in self.context.pages]}

    def powerpoint(self, revision=0):
        return {"kind": "powerpoint_review", "expected_revision": revision,
                "binding": self.context.binding.model_dump(), "application_version": "Microsoft PowerPoint test build 16.0",
                "platform": "Windows test fixture", "note": "No actual PowerPoint was run by this test",
                "checks": {"opened": "pass", "content_edited": "pass", "native_chart_edited": "pass", "saved": "pass", "reopened": "pass"}}

    def append(self, payload, key="operation-one"):
        return self.service.append(OWNER, "project-one", "version-one", PARSE(payload), key)

    def snapshot(self):
        return self.service.get(OWNER, "project-one", "version-one")

    def post(self, payload, **headers):
        return self.client.post(ROOT + "/records", json=payload, headers=self.headers() | headers)

    def count(self):
        with self.sessions() as session:
            return session.scalar(select(func.count()).select_from(AcceptanceEntry))

    def test_initial_snapshot_is_pending_and_read_does_not_create_rows(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot.revision, 0)
        self.assertTrue(snapshot.can_review)
        self.assertFalse(snapshot.can_revoke)
        self.assertFalse(snapshot.publication_authorized)
        self.assertEqual(snapshot.programmatic_status, "pending")
        self.assertEqual(snapshot.pages[0].preview_url, "/api/v1/versions/version-one/slides/1/preview")
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(AcceptanceStream)), 0)

    def test_http_auth_and_server_stamped_identity(self):
        response = self.post(self.visual(), **{"X-Auth-Request-User": "mallory"})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["records"][0]
        self.assertEqual(row["reviewer"], {"issuer": OWNER.issuer, "sub": OWNER.sub})
        self.assertTrue(row["created_at"].endswith("Z"))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertTrue(self.locks[-1])

    def test_missing_auth_or_untrusted_proxy_rejects(self):
        self.assertEqual(self.client.get(ROOT).status_code, 401)
        with TestClient(self.app, base_url="https://ppt.test", client=("192.0.2.3", 1)) as client:
            response = client.get(ROOT, headers=self.headers() | {"X-Forwarded-For": "127.0.0.1"})
            self.assertEqual(response.status_code, 401)

    def test_signed_claims_wrong_issuer_audience_expiry_reject(self):
        for claims in ({"iss": "https://wrong.test"}, {"aud": "wrong"}, {"email": "not-an-email"}, {"exp": datetime.now(timezone.utc) - timedelta(seconds=60)}):
            with self.subTest(claims=claims):
                self.assertEqual(self.client.get(ROOT, headers=self.headers(**claims)).status_code, 401)

    def test_csrf_origin_and_missing_cookie_reject(self):
        for overrides in ({"Origin": "https://evil.test"}, {"X-CSRF-Token": "wrong"}, {"Origin": ""}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.post(self.visual(), **overrides).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.post(self.visual()).status_code, 403)
        self.assertEqual(self.count(), 0)

    def test_cross_user_and_cross_issuer_are_not_found(self):
        response = self.client.get(ROOT, headers=self.headers(sub="stable-bob"))
        self.assertEqual(response.status_code, 404)
        self.app.state.settings.oidc_issuer = "https://second-issuer.test"
        response = self.client.get(ROOT, headers=self.headers(iss="https://second-issuer.test"))
        self.assertEqual(response.status_code, 404)
        response = self.client.post(ROOT + "/records", json=self.visual(), headers=self.headers(iss="https://second-issuer.test"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.count(), 0)

    def test_ids_are_bound_and_arbitrary_preview_urls_cannot_be_supplied(self):
        for address in (ROOT.replace("version-one", "version-other"), ROOT.replace("project-one", "project-other")):
            self.assertEqual(self.client.get(address, headers=self.headers()).status_code, 404)
        payload = self.visual()
        payload["pages"][0]["preview_url"] = "https://evil.test/pixel"
        self.assertEqual(self.post(payload).status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_unknown_fields_actor_flags_and_browser_timestamps_reject(self):
        for field, value in (("accepted", True), ("programmatic_status", "passed"), ("reviewer", {"sub": "mallory"}), ("created_at", "2000-01-01"), ("evidence", {"passed": True})):
            with self.subTest(field=field):
                self.assertEqual(self.post(self.visual() | {field: value}).status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_all_human_passes_still_cannot_publish_or_pass_programmatic_gate(self):
        self.append(self.visual())
        result = self.append(self.powerpoint(1), "powerpoint")
        self.assertEqual(result.visual_status, "passed")
        self.assertEqual(result.powerpoint_status, "passed")
        self.assertEqual(result.human_status, "passed")
        self.assertEqual(result.programmatic_status, "pending")
        self.assertEqual(result.acceptance_status, "pending")
        self.assertFalse(result.publication_authorized)
        self.assertEqual(result.pending_reasons, ["independent_postflight_integration_pending"])

    def test_partial_visual_and_powerpoint_are_retained_pending(self):
        payload = self.visual()
        payload["pages"][1]["verdict"] = "pending"
        result = self.append(payload)
        self.assertEqual(result.visual_status, "pending")
        pp = self.powerpoint(1)
        pp["checks"]["reopened"] = "pending"
        result = self.append(pp, "powerpoint")
        self.assertEqual(result.powerpoint_status, "pending")
        self.assertEqual(len(result.records), 2)

    def test_findings_and_failed_powerpoint_checks_block_human_completion(self):
        payload = self.visual()
        payload["pages"][0].update(verdict="needs_changes", issues=[{"category": "layout", "severity": "blocking", "description": "Title is clipped"}])
        result = self.append(payload)
        self.assertEqual(result.visual_status, "needs_changes")
        pp = self.powerpoint(1)
        pp["checks"]["reopened"] = "fail"
        result = self.append(pp, "powerpoint")
        self.assertEqual(result.powerpoint_status, "needs_changes")
        self.assertEqual(result.human_status, "needs_changes")

    def test_page_roster_duplicate_missing_reordered_or_wrong_hash_conflicts(self):
        for rows in (self.visual()["pages"][:1], list(reversed(self.visual()["pages"])), [self.visual()["pages"][0]] * 2,
                     [self.visual()["pages"][0] | {"preview_sha256": "f" * 64}, self.visual()["pages"][1]]):
            with self.subTest(rows=rows):
                response = self.post(self.visual() | {"pages": rows})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"]["code"], "preview_roster_changed")
        self.assertEqual(self.count(), 0)

    def test_pass_with_issues_and_empty_needs_changes_reject(self):
        payload = self.visual()
        payload["pages"][0]["issues"] = [{"category": "text", "severity": "minor", "description": "Typo"}]
        self.assertEqual(self.post(payload).status_code, 422)
        payload = self.visual("0")
        self.assertEqual(self.post(payload).status_code, 422)
        payload = self.visual()
        payload["pages"][0]["verdict"] = "needs_changes"
        self.assertEqual(self.post(payload).status_code, 422)

    def test_actual_powerpoint_version_and_order_required(self):
        for changes in ({"application_version": " "}, {"platform": ""}, {"checks": self.powerpoint()["checks"] | {"opened": "pending"}},
                        {"checks": self.powerpoint()["checks"] | {"saved": "pending"}}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post(self.powerpoint() | changes).status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_empty_powerpoint_progress_can_be_saved_without_false_completion(self):
        payload = self.powerpoint() | {"application_version": "", "platform": "", "checks": {key: "pending" for key in self.powerpoint()["checks"]}}
        result = self.append(payload)
        self.assertEqual(result.powerpoint_status, "pending")

    def test_native_chart_not_applicable_is_decided_by_trusted_context(self):
        payload = self.powerpoint()
        payload["checks"]["native_chart_edited"] = "not_applicable"
        self.assertEqual(self.post(payload).status_code, 422)
        self.context = replace(self.context, native_chart_count=0)
        result = self.append(payload)
        self.assertEqual(result.powerpoint_status, "passed")

    def test_chartless_powerpoint_still_requires_content_edit(self):
        self.context = replace(self.context, native_chart_count=0)
        payload = self.powerpoint()
        payload["checks"].update(native_chart_edited="not_applicable", content_edited="pending")
        result = self.append(payload)
        self.assertEqual(result.powerpoint_status, "pending")
        payload["expected_revision"] = 1
        payload["checks"]["content_edited"] = "pass"
        result = self.append(payload, "edited")
        self.assertEqual(result.powerpoint_status, "passed")

    def test_missing_content_edit_field_is_not_silently_grandfathered(self):
        payload = self.powerpoint()
        del payload["checks"]["content_edited"]
        self.assertEqual(self.post(payload).status_code, 422)

    def test_editing_nonexistent_chart_cannot_be_recorded_as_pass(self):
        self.context = replace(self.context, native_chart_count=0)
        self.assertEqual(self.post(self.powerpoint()).status_code, 422)

    def test_every_source_binding_dimension_prevents_reuse(self):
        for field in ("manifest_sha256", "input_snapshot_sha256", "pptx_sha256", "source_fingerprint_sha256", "evidence_sha256"):
            with self.subTest(field=field):
                payload = self.visual()
                payload["binding"][field] = "f" * 64
                self.assertEqual(self.post(payload).status_code, 409)
        for field in ("project_id", "version_id", "evidence_id"):
            with self.subTest(field=field):
                payload = self.visual()
                payload["binding"][field] = "different"
                self.assertEqual(self.post(payload).status_code, 409)
        for field in ("engine", "runtime_revision", "profile", "checkpoint_format"):
            with self.subTest(field=field):
                payload = self.visual()
                payload["binding"]["runtime"][field] = "different"
                self.assertEqual(self.post(payload).status_code, 409)
        self.assertEqual(self.count(), 0)

    def test_stale_input_or_artifact_marks_history_and_blocks_new_reviews(self):
        self.append(self.visual())
        original = self.context
        for field in ("current_input_snapshot_sha256", "current_source_fingerprint_sha256"):
            self.context = replace(original, **{field: "f" * 64})
            snapshot = self.snapshot()
            self.assertEqual(snapshot.acceptance_status, "stale")
            self.assertFalse(snapshot.can_review)
            self.assertEqual(snapshot.visual_status, "pending")
            self.assertTrue(snapshot.records[0].stale)
            self.assertEqual(self.post(self.visual(1), **{"Idempotency-Key": field}).status_code, 409)

    def test_runtime_or_evidence_change_requires_explicit_new_review(self):
        self.append(self.visual())
        runtime = self.context.binding.runtime.model_copy(update={"engine": "hermes"})
        self.context = replace(self.context, binding=self.context.binding.model_copy(update={"runtime": runtime, "evidence_id": "new-evidence"}))
        snapshot = self.snapshot()
        self.assertEqual(snapshot.visual_status, "pending")
        self.assertTrue(snapshot.records[0].stale)
        result = self.append(self.visual(1), "explicit-new-review")
        self.assertEqual(result.visual_status, "passed")
        self.assertTrue(result.records[0].stale)
        self.assertFalse(result.records[1].stale)

    def test_preview_roster_change_defensively_invalidates_prior_visual_pass(self):
        self.append(self.visual())
        # Deliberately broken host adapter: evidence binding was not advanced.
        # The projection still must not count a review of a different PNG.
        self.context = replace(self.context, pages=(self.context.pages[0].model_copy(update={"preview_sha256": "f" * 64}), self.context.pages[1]))
        result = self.snapshot()
        self.assertTrue(result.records[0].stale)
        self.assertEqual(result.visual_status, "pending")

    def test_changed_chart_applicability_invalidates_prior_not_applicable(self):
        self.context = replace(self.context, native_chart_count=0)
        payload = self.powerpoint()
        payload["checks"]["native_chart_edited"] = "not_applicable"
        self.append(payload)
        self.context = replace(self.context, native_chart_count=1)
        result = self.snapshot()
        self.assertTrue(result.records[0].stale)
        self.assertEqual(result.powerpoint_status, "pending")

    def test_source_change_then_restore_does_not_resurrect_old_review(self):
        payload = self.visual()
        self.append(payload)
        self.context = replace(self.context, source_revision=6, binding=self.context.binding.model_copy(update={"source_revision": 6}), current_input_snapshot_sha256="f" * 64)
        self.assertEqual(self.snapshot().acceptance_status, "stale")
        self.context = replace(self.context, source_revision=7, binding=self.context.binding.model_copy(update={"source_revision": 7}), current_input_snapshot_sha256="2" * 64)
        result = self.snapshot()
        self.assertEqual(result.acceptance_status, "pending")
        self.assertEqual(result.visual_status, "pending")
        self.assertTrue(result.records[0].stale)
        self.assertEqual(self.post(payload | {"expected_revision": 1}, **{"Idempotency-Key": "stale-ui"}).status_code, 409)
        result = self.append(self.visual(1), "explicit-rereview")
        self.assertEqual(result.visual_status, "passed")

    def test_needs_input_failed_and_planning_sources_cannot_be_retroaccepted(self):
        original = self.context
        for status in ("needs_input", "failed", "awaiting_confirmation"):
            with self.subTest(status=status):
                self.context = replace(original, origin_status=status)
                self.assertEqual(self.snapshot().acceptance_status, "unreviewable")
                self.assertEqual(self.post(self.visual()).status_code, 409)
        self.context = replace(original, phase="plan_options")
        self.assertEqual(self.post(self.visual()).status_code, 409)
        self.assertEqual(self.count(), 0)

    def test_idempotent_lost_response_retry_and_key_reuse(self):
        payload = self.visual()
        first = self.append(payload)
        again = self.append(payload)
        self.assertEqual(first.model_dump(), again.model_dump())
        self.assertEqual(self.count(), 1)
        response = self.post(payload | {"note": "different request"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "idempotency_key_reused")

    def test_idempotent_retry_after_source_change_returns_current_stale_state(self):
        payload = self.visual()
        self.append(payload)
        self.context = replace(self.context, current_input_snapshot_sha256="f" * 64)
        result = self.append(payload)
        self.assertEqual(result.acceptance_status, "stale")
        self.assertEqual(self.count(), 1)

    def test_revision_conflict_does_not_append_or_increment(self):
        self.append(self.visual())
        response = self.post(self.visual(), **{"Idempotency-Key": "different-key"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "acceptance_revision_changed")
        self.assertEqual(self.snapshot().revision, 1)
        self.assertEqual(self.count(), 1)

    def test_revoke_is_append_only_and_does_not_resurrect_previous_pass(self):
        self.append(self.visual())
        latest = self.append(self.visual(1), "second-review")
        before = [r.payload for r in latest.records]
        revoke = {"kind": "revoke", "expected_revision": 2, "binding": self.context.binding.model_dump(), "record_id": latest.records[-1].id, "reason": "Reviewed the wrong PowerPoint revision"}
        result = self.append(revoke, "revoke")
        self.assertEqual(result.visual_status, "pending")
        self.assertEqual(len(result.records), 3)
        self.assertEqual([r.payload for r in result.records[:2]], before)
        self.assertFalse(result.records[0].revoked)
        self.assertTrue(result.records[1].revoked)
        self.assertEqual(self.append(revoke, "revoke").revision, 3)
        response = self.post(revoke | {"expected_revision": 3}, **{"Idempotency-Key": "second-revoke"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.count(), 3)

    def test_explicit_review_after_revocation_is_required(self):
        initial = self.append(self.visual())
        revoke = {"kind": "revoke", "expected_revision": 1, "binding": self.context.binding.model_dump(), "record_id": initial.records[0].id, "reason": "Correction"}
        self.append(revoke, "revoke")
        result = self.append(self.visual(2), "explicit-new-review")
        self.assertEqual(result.visual_status, "passed")
        self.assertTrue(result.records[0].revoked)
        self.assertFalse(result.records[-1].revoked)

    def test_stale_record_can_be_revoked_but_cross_version_target_cannot(self):
        initial = self.append(self.visual())
        self.context = replace(self.context, current_input_snapshot_sha256="f" * 64)
        revoke = {"kind": "revoke", "expected_revision": 1, "binding": self.context.binding.model_dump(), "record_id": "foreign-record-id", "reason": "Correction"}
        self.assertEqual(self.post(revoke, **{"Idempotency-Key": "foreign-revoke"}).status_code, 404)
        result = self.append(revoke | {"record_id": initial.records[0].id}, "revoke")
        self.assertTrue(result.records[0].revoked)
        self.assertEqual(result.acceptance_status, "stale")

    def test_revoke_event_cannot_itself_be_revoked(self):
        initial = self.append(self.visual())
        revoke = {"kind": "revoke", "expected_revision": 1, "binding": self.context.binding.model_dump(), "record_id": initial.records[0].id, "reason": "Correction"}
        result = self.append(revoke, "revoke")
        response = self.post(revoke | {"record_id": result.records[-1].id, "expected_revision": 2}, **{"Idempotency-Key": "revoke-revoke"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.count(), 2)

    def test_records_survive_engine_and_service_restart(self):
        initial = self.append(self.visual())
        self.engine.dispose()
        reopened = create_engine(self.db_url)
        try:
            service = AcceptanceService(sessionmaker(reopened, expire_on_commit=False), self.resolve)
            self.assertEqual(service.get(OWNER, "project-one", "version-one").model_dump(), initial.model_dump())
        finally:
            reopened.dispose()

    def test_concurrent_writers_cannot_both_claim_same_revision(self):
        # Existing stream ensures the race tests CAS, not schema/bootstrap locks.
        self.append(self.visual())
        barrier = Barrier(2)

        def writer(key):
            barrier.wait(timeout=5)
            try:
                return self.append(self.visual(1), key).revision
            except AcceptanceError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(writer, "concurrent-a")
            b = pool.submit(writer, "concurrent-b")
            outcomes = [a.result(timeout=10), b.result(timeout=10)]
        self.assertEqual(outcomes.count(2), 1, outcomes)
        self.assertIn(next(value for value in outcomes if value != 2), ("acceptance_revision_changed", "acceptance_busy"))
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.snapshot().revision, 2)

    def test_read_revision_and_history_cannot_straddle_a_concurrent_append(self):
        # Deterministic READ COMMITTED interleaving: writer commits immediately
        # before the history SELECT. Old code fetched stream revision first and
        # returned revision=0 with a revision=1 record. Source locking is a host
        # responsibility; this mock additionally tests internal DTO consistency.
        original_records = self.service._records
        other_service = AcceptanceService(self.sessions, self.resolve)

        def append_before_history(session, scope):
            other_service.append(OWNER, "project-one", "version-one", PARSE(self.visual()), "racing-append")
            return original_records(session, scope)

        self.service._records = append_before_history
        result = self.snapshot()
        self.assertEqual(result.revision, 1)
        self.assertEqual([row.revision for row in result.records], [1])
        self.assertTrue(all(self.locks))

    def test_idempotency_key_required_bounded_and_owner_bound(self):
        headers = self.headers()
        headers.pop("Idempotency-Key")
        self.assertEqual(self.client.post(ROOT + "/records", json=self.visual(), headers=headers).status_code, 422)
        for key in ("", "x" * 129, "spaces invalid"):
            self.assertEqual(self.post(self.visual(), **{"Idempotency-Key": key}).status_code, 422)
        self.assertEqual(self.count(), 0)

    def test_source_identifiers_and_page_roster_are_not_untrusted_paths(self):
        for value in ("../secret", "https://evil.test", "version?token=x", "version%2fsecret"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                SourceBinding.model_validate(self.context.binding.model_dump() | {"version_id": value})
        with self.assertRaises(ValueError):
            replace(self.context, pages=(self.context.pages[1], self.context.pages[0]))
        with self.assertRaises(ValueError):
            replace(self.context, pages=())


if __name__ == "__main__":
    unittest.main()
