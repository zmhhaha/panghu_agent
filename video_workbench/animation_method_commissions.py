"""Finite developer experiments, independent of finished-video round quotas.

The trusted host reserves work, calls claim immediately before external I/O and
submits only when submission_claimed is true. Recovered claims must reconcile
their original operation ID. This store never calls models or executes source.
Results are trusted adapter inputs, not browser-supplied observation evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest

STAGES = frozenset({"creator", "review", "render"})
TERMINAL = frozenset({"completed", "failed", "cancelled"})
OP_TERMINAL = TERMINAL | {"released"}
RETRY_RELATION_FIELDS = (
    "base_commission_id", "base_candidate_id", "method_versions", "role",
    "baseline_demonstration_id", "structural_changes",
)
STOP_REASONS = frozenset({
    "goal_met", "budget_exhausted", "no_progress", "developer_stopped", "failed", "cancelled",
})
OUTCOMES = frozenset({"improved", "regressed", "equal", "inconclusive", "goal_met"})
MAX_RECORD_BYTES = 4 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
_TABLES = frozenset({
    "animation_method_commissions", "animation_method_commission_requests",
    "animation_method_commission_operations", "animation_method_commission_events",
    "sqlite_sequence",
})


def _require(value, code="invalid_animation_method_commission", status=422):
    if not value:
        raise ServiceError(code, status)


def _text(value, maximum=200, *, empty=False):
    _require(type(value) is str and (empty or bool(value.strip())) and len(value) <= maximum
             and not any((ord(c) < 32 and c not in "\r\n\t") or ord(c) == 127
                         or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def _identifier(value):
    _text(value)
    _require(not any(ord(c) < 32 for c in value))
    return value


def _json(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False)
        _require(len(raw.encode("utf-8")) <= MAX_RECORD_BYTES,
                 "animation_method_commission_record_too_large", 413)
        return raw
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise ServiceError("invalid_animation_method_commission_json", 422) from None


def _copy(value):
    return json.loads(_json(value))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _budget(value):
    _require(type(value) is dict and set(value) == STAGES | {"seconds"},
             "animation_method_commission_budget_invalid")
    _require(all(type(value[k]) is int and 0 <= value[k] <= 32 for k in STAGES)
             and value["creator"] > 0
             and _number(value["seconds"]) and 1 <= value["seconds"] <= 86400,
             "animation_method_commission_budget_invalid")
    return _copy(value)


def _media(value):
    _require(type(value) is dict and type(value.get("sha256")) is str
             and _SHA.fullmatch(value["sha256"]) is not None
             and _number(value.get("duration_seconds")) and 0 < value["duration_seconds"] <= 3600,
             "animation_method_commission_media_invalid")
    return _copy(value)


class AnimationMethodCommissionStore:
    """Separate owner-scoped SQLite ledger; reads never drive execution."""

    def __init__(self, path, *, clock=time.time):
        target = Path(path).absolute()
        _require(not any(p.is_symlink() for p in (target, *target.parents)),
                 "animation_method_commission_path_invalid", 409)
        self.path, self.clock = str(target), clock
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            existing = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            _require(existing <= _TABLES, "animation_method_commission_database_not_isolated", 409)
            for statement in (
                """CREATE TABLE IF NOT EXISTS animation_method_commissions(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL,
                    document TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS animation_method_commission_requests(
                    owner TEXT NOT NULL, action TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(owner,action,request_id))""",
                """CREATE TABLE IF NOT EXISTS animation_method_commission_operations(
                    id TEXT PRIMARY KEY,
                    commission_id TEXT NOT NULL REFERENCES animation_method_commissions(id),
                    owner TEXT NOT NULL, stage TEXT NOT NULL, idem TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL,
                    UNIQUE(commission_id,idem))""",
                """CREATE TABLE IF NOT EXISTS animation_method_commission_events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    commission_id TEXT NOT NULL REFERENCES animation_method_commissions(id),
                    operation_id TEXT, event TEXT NOT NULL, document TEXT NOT NULL)""",
            ):
                db.execute(statement)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _transaction(self):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _commission(db, owner, commission_id):
        row = db.execute("SELECT document FROM animation_method_commissions WHERE id=? AND owner=?",
                         (_identifier(commission_id), _identifier(owner))).fetchone()
        _require(row is not None, "animation_method_commission_not_found", 404)
        result = json.loads(row["document"])
        _require(result["frozen_sha256"] == digest(result["frozen"]),
                 "animation_method_commission_frozen_input_changed", 409)
        return result

    @staticmethod
    def _operation(db, owner, operation_id):
        row = db.execute("SELECT document FROM animation_method_commission_operations "
                         "WHERE id=? AND owner=?",
                         (_identifier(operation_id), _identifier(owner))).fetchone()
        _require(row is not None, "animation_method_operation_not_found", 404)
        operation = json.loads(row["document"])
        _require(operation["input_sha256"] == digest(operation["input"])
                 and operation["request_sha256"] == digest({
                     "stage": operation["stage"], "input": operation["input"]}),
                 "animation_method_operation_frozen_input_changed", 409)
        if operation["status"] == "completed":
            _require(operation["result_sha256"] == digest(operation["result"]),
                     "animation_method_operation_result_changed", 409)
        if 'input_resource_manifest' in operation:
            expected = [dict(path='animation-method-input.json', sha256=operation['input_sha256'])]
            expected.extend(dict(path=row['file_name'], sha256=row['sha256'])
                            for row in operation['input']['observation']['frames'])
            _require(operation['input_resource_manifest'] == sorted(expected, key=lambda row: row['path'])
                     and operation.get('input_resource_manifest_sha256') == digest(operation['input_resource_manifest']),
                     'animation_method_operation_frozen_input_changed', 409)
        return operation

    def _event(self, db, commission_id, name, operation_id=None, **detail):
        document = dict(event=name, operation_id=operation_id, at=self.clock(), **detail)
        db.execute("INSERT INTO animation_method_commission_events"
                   "(commission_id,operation_id,event,document) VALUES(?,?,?,?)",
                   (commission_id, operation_id, name, _json(document)))

    def _save(self, db, commission):
        commission["updated_at"] = self.clock()
        db.execute("UPDATE animation_method_commissions SET state=?,document=? WHERE id=?",
                   (commission["state"], _json(commission), commission["id"]))
        return commission

    def _save_operation(self, db, operation, event, **detail):
        operation["updated_at"] = self.clock()
        db.execute("UPDATE animation_method_commission_operations SET status=?,document=? WHERE id=?",
                   (operation["status"], _json(operation), operation["id"]))
        self._event(db, operation["commission_id"], event, operation["id"], **detail)
        return operation

    @staticmethod
    def _replay(db, owner, action, request_id, request):
        row = db.execute("SELECT request_sha256,result FROM animation_method_commission_requests "
                         "WHERE owner=? AND action=? AND request_id=?",
                         (owner, action, _identifier(request_id))).fetchone()
        if row is not None:
            _require(row["request_sha256"] == digest(request),
                     "animation_method_commission_idempotency_conflict", 409)
            return json.loads(row["result"])
        return None

    @staticmethod
    def _record_request(db, owner, action, request_id, request, result):
        db.execute("INSERT INTO animation_method_commission_requests VALUES(?,?,?,?,?)",
                   (owner, action, request_id, digest(request), _json(result)))

    @staticmethod
    def _usage(db, commission_id):
        result = dict.fromkeys(sorted(STAGES), 0)
        for row in db.execute("SELECT stage,count(*) AS n FROM animation_method_commission_operations "
                              "WHERE commission_id=? AND status!='released' GROUP BY stage",
                              (commission_id,)):
            result[row["stage"]] = row["n"]
        return result

    @staticmethod
    def _candidates(commission):
        result = {row["id"]: row for row in commission["candidates"]}
        baseline = commission["frozen"].get("base_candidate")
        if baseline is not None:
            result.setdefault(baseline["id"], baseline)
        return result

    @staticmethod
    def _active(db, commission_id):
        return db.execute("SELECT 1 FROM animation_method_commission_operations "
                          "WHERE commission_id=? AND status NOT IN"
                          "('completed','failed','cancelled','released') LIMIT 1",
                          (commission_id,)).fetchone() is not None

    def _present(self, db, commission):
        result = _copy(commission)
        result["usage"] = self._usage(db, commission["id"])
        result["remaining"] = {k: result["budget"][k] - result["usage"][k] for k in STAGES}
        result["operations"] = [json.loads(row["document"]) for row in db.execute(
            "SELECT document FROM animation_method_commission_operations "
            "WHERE commission_id=? ORDER BY rowid", (commission["id"],))]
        return result

    def create(self, owner, body, request_id):
        _identifier(owner)
        _identifier(request_id)
        _require(type(body) is dict and {"goal", "budget"} <= set(body)
                 and set(body) <= {
                     "title", "goal", "budget", "reference", "criteria", "criteria_version",
                     "method_versions", "base_commission_id", "base_candidate_id", "feedback",
                     "duration_seconds", "constraints", "assets", "role",
                     "baseline_demonstration_id", "structural_changes",
                     "retry_of_commission_id",
                     "protocol_schema", "knowledge_snapshot_id", "research_record_ids", "content_changes",
                 })
        _text(body["goal"], 16000)
        limits = _budget(body["budget"])
        if "title" in body:
            _text(body["title"], 500)
        if "feedback" in body:
            _text(body["feedback"], 16000, empty=True)
        if "duration_seconds" in body:
            _require(_number(body["duration_seconds"]) and 0 < body["duration_seconds"] <= 600)
        if "method_versions" in body:
            _require(type(body["method_versions"]) is list and len(body["method_versions"]) <= 32)
        role = body.get("role", "reference")
        _require(type(role) is str and role in {"reference", "transfer"},
                 "animation_method_commission_role_invalid")
        if body.get("baseline_demonstration_id") is not None:
            _identifier(body["baseline_demonstration_id"])
        if role == "transfer":
            changes = body.get("structural_changes")
            _require(type(changes) is dict and bool(changes) and set(changes) <= {
                "object_count", "relations", "task", "duration", "staging", "timing",
                "data_structure", "explanation_problem",
            }, "animation_method_commission_structural_change_required")
            for value in changes.values():
                _require(type(value) is dict and set(value) == {"before", "after"}
                         and value["before"] is not None and value["after"] is not None
                         and _json(value["before"]) != _json(value["after"]),
                         "animation_method_commission_structural_change_required")
        request = _copy(body)
        with self._transaction() as db:
            replay = self._replay(db, owner, "create", request_id, request)
            if replay is not None:
                return self._present(db, self._commission(db, owner, replay["id"]))
            retry_id = body.get("retry_of_commission_id")
            if retry_id is not None:
                previous = self._commission(db, owner, retry_id)
                _require(previous["state"] in {"failed", "cancelled"}
                         and not self._active(db, retry_id),
                         "animation_method_retry_not_available", 409)
                _require(all(body.get(key) == previous["frozen"].get(key)
                             for key in RETRY_RELATION_FIELDS),
                         "animation_method_retry_relationship_invalid", 409)
            base_id = body.get("base_commission_id")
            base_candidate = body.get("base_candidate_id")
            _require(base_candidate is None or base_id is not None,
                     "animation_method_commission_baseline_invalid")
            frozen = _copy(body)
            if base_id is not None:
                base = self._commission(db, owner, base_id)
                selected = base_candidate or base["best_candidate_id"]
                _require(selected is not None, "animation_method_commission_baseline_required", 409)
                candidate = self._candidates(base).get(selected)
                _require(candidate is not None, "animation_method_candidate_not_found", 404)
                frozen["base_candidate_id"] = selected
                frozen["base_candidate"] = _copy(candidate)
            now = self.clock()
            commission = dict(schema="video.animation-method-commission.v1", id=str(uuid4()),
                owner=owner, request_id=request_id, title=body.get("title", body["goal"][:120]),
                frozen=frozen, frozen_sha256=digest(frozen), budget=limits,
                state="queued", cancel_requested=False, deadline_at=now + limits["seconds"],
                created_at=now, updated_at=now, candidates=[], reviews=[],
                best_candidate_id=frozen.get("base_candidate_id"), stop_reason=None, summary=None)
            db.execute("INSERT INTO animation_method_commissions VALUES(?,?,?,?)",
                       (commission["id"], owner, commission["state"], _json(commission)))
            self._record_request(db, owner, "create", request_id, request, {"id": commission["id"]})
            self._event(db, commission["id"], "commission_created", frozen_sha256=commission["frozen_sha256"])
            return self._present(db, commission)

    def get(self, owner, commission_id):
        with self._transaction() as db:
            return self._present(db, self._commission(db, owner, commission_id))

    def list(self, owner, limit=100):
        _identifier(owner)
        _require(type(limit) is int and 1 <= limit <= 1000)
        with self._transaction() as db:
            return [self._present(db, json.loads(row["document"])) for row in db.execute(
                "SELECT document FROM animation_method_commissions WHERE owner=? ORDER BY rowid DESC LIMIT ?",
                (owner, limit))]

    def list_all(self, owner):
        """Read the complete owner history for explicit relationship resolution.

        The bounded public listing is still available for execution callers.
        Grouping must not lose an ancestor outside its most recent window;
        it needs frozen records, not every operation and budget calculation.
        """
        _identifier(owner)
        with self._transaction() as db:
            result = []
            for row in db.execute('SELECT document FROM animation_method_commissions '
                                  'WHERE owner=? ORDER BY rowid DESC', (owner,)):
                commission = json.loads(row['document'])
                _require(commission['frozen_sha256'] == digest(commission['frozen']),
                         'animation_method_commission_frozen_input_changed', 409)
                result.append(commission)
            return result

    def pending(self, limit=100):
        _require(type(limit) is int and 1 <= limit <= 1000)
        with self._transaction() as db:
            return [dict(id=row["id"], owner=row["owner"], state=row["state"]) for row in db.execute(
                "SELECT id,owner,state FROM animation_method_commissions "
                "WHERE state IN('queued','running','cancelling') ORDER BY rowid LIMIT ?", (limit,))]

    def operations(self, owner, commission_id):
        return self.get(owner, commission_id)["operations"]

    def events(self, owner, commission_id):
        with self._transaction() as db:
            self._commission(db, owner, commission_id)
            return [dict(sequence=row["sequence"], **json.loads(row["document"])) for row in db.execute(
                "SELECT sequence,document FROM animation_method_commission_events "
                "WHERE commission_id=? ORDER BY sequence", (commission_id,))]

    def operation(self, owner, operation_id):
        with self._transaction() as db:
            return self._operation(db, owner, operation_id)

    def reserve(self, owner, commission_id, stage, input_document, idempotency_key):
        _require(type(stage) is str and stage in STAGES,
                 "animation_method_commission_stage_invalid")
        _identifier(idempotency_key)
        _require(type(input_document) is dict)
        if input_document.get('schema') == 'video.animation-method-input.v2':
            from agent_service.profiles.video_animation_skill_method import OUTPUT_MAX_BYTES
            _require(len(_json(input_document).encode('utf-8')) + OUTPUT_MAX_BYTES + 65536 <= MAX_RECORD_BYTES,
                     'animation_method_operation_budget_exceeded', 413)
        request = dict(stage=stage, input=_copy(input_document))
        request_sha256 = digest(request)
        with self._transaction() as db:
            commission = self._commission(db, owner, commission_id)
            row = db.execute("SELECT request_sha256,document FROM animation_method_commission_operations "
                             "WHERE commission_id=? AND idem=?", (commission_id, idempotency_key)).fetchone()
            if row is not None:
                _require(row["request_sha256"] == request_sha256,
                         "animation_method_commission_idempotency_conflict", 409)
                return json.loads(row["document"])
            _require(commission["state"] not in TERMINAL and not commission["cancel_requested"],
                     "animation_method_commission_stopped", 409)
            _require(self.clock() < commission["deadline_at"],
                     "animation_method_commission_deadline_exceeded", 409)
            _require(not self._active(db, commission_id), "animation_method_operation_in_progress", 409)
            usage = self._usage(db, commission_id)
            _require(usage[stage] < commission["budget"][stage],
                     "animation_method_commission_budget_exhausted", 409)
            now = self.clock()
            operation = dict(schema="video.animation-method-operation.v1", id=str(uuid4()),
                owner=owner, commission_id=commission_id, stage=stage,
                idempotency_key=idempotency_key, request_sha256=request_sha256,
                input_sha256=digest(request["input"]), input=request["input"],
                status="reserved", job_id=None, result=None, error=None,
                created_at=now, updated_at=now)
            db.execute("INSERT INTO animation_method_commission_operations VALUES(?,?,?,?,?,?,?,?)",
                       (operation["id"], commission_id, owner, stage, idempotency_key,
                        request_sha256, operation["status"], _json(operation)))
            commission["state"] = "running"
            self._save(db, commission)
            self._event(db, commission_id, "operation_reserved", operation["id"], stage=stage,
                        request_sha256=request_sha256)
            return operation

    def claim(self, owner, operation_id):
        with self._transaction() as db:
            operation = self._operation(db, owner, operation_id)
            if operation["status"] != "reserved":
                return dict(operation, submission_claimed=False)
            commission = self._commission(db, owner, operation["commission_id"])
            _require(commission["state"] not in TERMINAL and not commission["cancel_requested"],
                     "animation_method_commission_stopped", 409)
            _require(self.clock() < commission["deadline_at"],
                     "animation_method_commission_deadline_exceeded", 409)
            operation["status"] = "submitting"
            self._save_operation(db, operation, "operation_claimed")
            return dict(operation, submission_claimed=True)

    def record_input_manifest(self, owner, operation_id, manifest):
        """Freeze the actual validated upload inventory before paid admission."""
        _require(type(manifest) is list and 1 <= len(manifest) <= 25,
                 'animation_method_input_manifest_invalid')
        manifest = _copy(manifest)
        with self._transaction() as db:
            operation = self._operation(db, owner, operation_id)
            document = operation['input']
            _require(document.get('schema') == 'video.animation-method-input.v2',
                     'animation_method_input_manifest_invalid')
            expected = [{'path': 'animation-method-input.json', 'sha256': operation['input_sha256']}]
            expected.extend(dict(path=row['file_name'], sha256=row['sha256'])
                            for row in document['observation']['frames'])
            _require(manifest == sorted(expected, key=lambda row: row['path']),
                     'animation_method_input_manifest_invalid')
            if 'input_resource_manifest' in operation:
                _require(operation['input_resource_manifest'] == manifest,
                         'animation_method_operation_frozen_input_changed', 409)
                return operation
            _require(operation['status'] == 'reserved', 'animation_method_operation_not_reserved', 409)
            operation.update(input_resource_manifest=manifest, input_resource_manifest_sha256=digest(manifest))
            return self._save_operation(db, operation, 'input_manifest_frozen', manifest_sha256=digest(manifest))

    def mark_submitted(self, owner, operation_id, job_id):
        _identifier(job_id)
        with self._transaction() as db:
            operation = self._operation(db, owner, operation_id)
            if operation["job_id"] is not None:
                _require(operation["job_id"] == job_id,
                         "animation_method_operation_submission_conflict", 409)
                return operation
            _require(operation["status"] in {"submitting", "submission_unknown"},
                     "animation_method_operation_not_claimed", 409)
            operation.update(status="submitted", job_id=job_id)
            return self._save_operation(db, operation, "operation_submitted", job_id=job_id)

    def mark_unknown(self, owner, operation_id, reason="submission_acknowledgement_unknown"):
        _text(reason, 1000)
        with self._transaction() as db:
            operation = self._operation(db, owner, operation_id)
            if operation["status"] in OP_TERMINAL or operation["job_id"] is not None:
                return operation
            _require(operation["status"] in {"submitting", "submission_unknown"},
                     "animation_method_operation_not_claimed", 409)
            if operation["status"] == "submission_unknown":
                return operation
            operation.update(status="submission_unknown", error=reason)
            return self._save_operation(db, operation, "operation_submission_unknown")

    def complete(self, owner, operation_id, result):
        result = _copy(result)
        with self._transaction() as db:
            operation = self._operation(db, owner, operation_id)
            if operation["status"] == "completed":
                _require(operation["result"] == result,
                         "animation_method_operation_result_conflict", 409)
                return operation
            _require(operation["status"] in {"submitting", "submission_unknown", "submitted", "running"},
                     "animation_method_operation_not_active", 409)
            operation.update(status="completed", result=result, result_sha256=digest(result), error=None)
            return self._save_operation(db, operation, "operation_completed",
                                        result_sha256=operation["result_sha256"])

    def fail(self, owner, operation_id, code, *, cancelled=False):
        _identifier(code)
        with self._transaction() as db:
            operation = self._operation(db, owner, operation_id)
            status = "cancelled" if cancelled else "failed"
            if operation["status"] in OP_TERMINAL:
                _require(operation["status"] == status and operation["error"] == code,
                         "animation_method_operation_result_conflict", 409)
                return operation
            operation.update(status=status, error=code)
            return self._save_operation(db, operation, "operation_" + status, code=code)

    def release_unsubmitted(self, owner, operation_id, proof):
        _require(type(proof) is dict and bool(proof) and type(proof.get("reason")) is str,
                 "animation_method_unsubmitted_proof_required")
        proof = _copy(proof)
        with self._transaction() as db:
            operation = self._operation(db, owner, operation_id)
            if operation["status"] == "released":
                _require(operation.get("release_proof") == proof,
                         "animation_method_operation_result_conflict", 409)
                return operation
            _require(operation["status"] == "reserved" and operation["job_id"] is None,
                     "animation_method_operation_may_have_submitted", 409)
            operation.update(status="released", release_proof=proof)
            return self._save_operation(db, operation, "operation_released")

    def add_candidate(self, owner, commission_id, body, request_id):
        _require(type(body) is dict and {"operation_id", "media", "source", "strategy"} <= set(body)
                 and set(body) <= {"operation_id", "media", "source", "strategy", "method_versions",
                                   "criteria_version", "technical", "provenance", "title"})
        request = _copy(body)
        media = _media(body["media"])
        _require(type(body["source"]) is dict and bool(body["source"]),
                 "animation_method_candidate_source_required")
        _require(type(body["strategy"]) in (str, dict) and bool(body["strategy"]),
                 "animation_method_candidate_strategy_required")
        with self._transaction() as db:
            commission = self._commission(db, owner, commission_id)
            action = "candidate:" + commission_id
            replay = self._replay(db, owner, action, request_id, request)
            if replay is not None:
                return replay
            _require(commission["state"] not in TERMINAL,
                     "animation_method_commission_stopped", 409)
            operation = self._operation(db, owner, body["operation_id"])
            _require(operation["commission_id"] == commission_id and operation["stage"] == "render"
                     and operation["status"] == "completed", "animation_method_candidate_render_required", 409)
            if type(operation["result"]) is dict and "media" in operation["result"]:
                _require(operation["result"]["media"] == media,
                         "animation_method_candidate_media_mismatch", 409)
            _require(not any(row["operation_id"] == operation["id"] for row in commission["candidates"]),
                     "animation_method_candidate_render_already_imported", 409)
            candidate = dict(schema="video.animation-method-candidate.v1", id=str(uuid4()),
                commission_id=commission_id, **request, created_at=self.clock())
            candidate["media"] = media
            commission["candidates"].append(candidate)
            if commission["best_candidate_id"] is None:
                commission["best_candidate_id"] = candidate["id"]
            self._save(db, commission)
            self._record_request(db, owner, action, request_id, request, candidate)
            self._event(db, commission_id, "candidate_added", operation["id"], candidate_id=candidate["id"])
            return candidate

    def record_review(self, owner, commission_id, body, request_id):
        _require(type(body) is dict
                 and {"operation_id", "candidate_id", "outcome", "best_candidate_id", "remaining_issues"} <= set(body)
                 and set(body) <= {"operation_id", "candidate_id", "outcome", "best_candidate_id",
                    "remaining_issues", "compared_candidate_id", "criteria_version", "evidence",
                    "limitations", "criteria_results", "summary"})
        _require(type(body["outcome"]) is str and body["outcome"] in OUTCOMES
                 and type(body["remaining_issues"]) is list)
        _identifier(body["candidate_id"])
        _identifier(body["best_candidate_id"])
        if body.get("compared_candidate_id") is not None:
            _identifier(body["compared_candidate_id"])
        request = _copy(body)
        with self._transaction() as db:
            commission = self._commission(db, owner, commission_id)
            action = "review:" + commission_id
            replay = self._replay(db, owner, action, request_id, request)
            if replay is not None:
                return replay
            _require(commission["state"] not in TERMINAL,
                     "animation_method_commission_stopped", 409)
            operation = self._operation(db, owner, body["operation_id"])
            _require(operation["commission_id"] == commission_id and operation["stage"] == "review"
                     and operation["status"] == "completed", "animation_method_candidate_review_required", 409)
            candidate_ids = set(self._candidates(commission))
            _require(body["candidate_id"] in candidate_ids and body["best_candidate_id"] in candidate_ids
                     and (body.get("compared_candidate_id") is None
                          or body["compared_candidate_id"] in candidate_ids),
                     "animation_method_candidate_not_found", 404)
            _require(not any(row["operation_id"] == operation["id"] for row in commission["reviews"]),
                     "animation_method_review_already_imported", 409)
            review = dict(schema="video.animation-method-comparison.v1", id=str(uuid4()),
                          commission_id=commission_id, **request, created_at=self.clock())
            commission["reviews"].append(review)
            commission["best_candidate_id"] = body["best_candidate_id"]
            self._save(db, commission)
            self._record_request(db, owner, action, request_id, request, review)
            self._event(db, commission_id, "review_recorded", operation["id"], review_id=review["id"])
            return review

    def stop(self, owner, commission_id, reason, summary):
        _require(type(reason) is str and reason in STOP_REASONS,
                 "animation_method_commission_stop_reason_invalid")
        _require(type(summary) in (dict, str) and bool(summary),
                 "animation_method_commission_stop_summary_required")
        summary = _copy(summary)
        with self._transaction() as db:
            commission = self._commission(db, owner, commission_id)
            if commission["state"] in TERMINAL:
                _require(commission["stop_reason"] == reason and commission["summary"] == summary,
                         "animation_method_commission_stop_conflict", 409)
                return self._present(db, commission)
            _require(not self._active(db, commission_id),
                     "animation_method_operation_in_progress", 409)
            if reason == "goal_met":
                _require(any(row["outcome"] == "goal_met"
                             and row["best_candidate_id"] == commission["best_candidate_id"]
                             for row in commission["reviews"]),
                         "animation_method_commission_goal_evidence_required", 409)
            elif reason == "no_progress":
                candidates = {row["id"]: row for row in commission["candidates"]}
                strategies = set()
                for row in reversed(commission["reviews"]):
                    if row["outcome"] in {"improved", "goal_met"}:
                        break
                    if row["candidate_id"] in candidates:
                        strategies.add(digest(candidates[row["candidate_id"]]["strategy"]))
                _require(len(strategies) >= 2,
                         "animation_method_commission_stagnation_evidence_required", 409)
            elif reason == "budget_exhausted":
                usage = self._usage(db, commission_id)
                _require(self.clock() >= commission["deadline_at"]
                         or any(commission["budget"][k] > 0 and usage[k] >= commission["budget"][k]
                                for k in STAGES),
                         "animation_method_commission_budget_not_exhausted", 409)
            commission.update(state="failed" if reason == "failed" else
                              "cancelled" if reason == "cancelled" else "completed",
                              stop_reason=reason, summary=summary)
            self._save(db, commission)
            self._event(db, commission_id, "commission_stopped", reason=reason)
            return self._present(db, commission)

    def cancel(self, owner, commission_id):
        with self._transaction() as db:
            commission = self._commission(db, owner, commission_id)
            if commission["state"] in TERMINAL or commission["cancel_requested"]:
                return self._present(db, commission)
            commission["cancel_requested"] = True
            if self._active(db, commission_id):
                commission["state"] = "cancelling"
            else:
                commission.update(state="cancelled", stop_reason="cancelled",
                                  summary={"reason": "developer_cancelled"})
            self._save(db, commission)
            self._event(db, commission_id, "commission_cancel_requested")
            return self._present(db, commission)
