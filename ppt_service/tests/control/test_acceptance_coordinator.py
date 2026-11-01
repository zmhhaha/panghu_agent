"""Idle scheduling contracts, with synthetic stopped validation and no models."""
import copy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from ppt_service.control.acceptance_coordinator import AcceptanceCoordinator
from ppt_service.control.execution import ControlError, RuntimeConfig
from ppt_service.server.acceptance.resolver import fingerprint
from ppt_service.server.app import create_app
from ppt_service.server.auth import Identity, Settings
from ppt_service.server.migrate import migrate
from ppt_service.server.models import Asset, Checkpoint, ExecutionSlot, Job, Project, Version
from ppt_service.tests.server.agent_review_fixture import seed_agent_review


class Validator:
    def __init__(self, root):
        self.root = root
        self.transport = SimpleNamespace(settings=SimpleNamespace(
            worker_image="synthetic/office-validator:latest@sha256:" + "e" * 64))
        self.calls = []
        self.status = "acceptance_validated"
        self.on_validate = None

    def validate_acceptance(self, snapshot, original, request):
        self.calls.append((snapshot, copy.deepcopy(original), copy.deepcopy(request)))
        if self.on_validate:
            self.on_validate(snapshot, original, request)
        digest = fingerprint(request["binding"])
        target = self.root / "acceptance-attempts" / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"synthetic_only": True, "writers_stopped": True, "status": self.status}))
        return {"status": self.status, "binding_sha256": digest}


@pytest.fixture
def fixture(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    migrate(engine)
    app = create_app(Settings("sqlite://", str(tmp_path)), engine)
    owner = Identity("https://issuer", "alice")
    data = seed_agent_review(app, owner)
    config = RuntimeConfig(data_root=tmp_path)
    validator = Validator(tmp_path)
    coordinator = AcceptanceCoordinator(app.state.sessions, config, validator,
        oracle_provider=lambda task: {"expected_charts": [], "source_sha256": []}, enabled=True)
    yield app, data, validator, coordinator
    engine.dispose()


def test_idle_tick_reconstructs_bound_request_without_business_writes_or_open_transaction(fixture):
    app, data, validator, coordinator = fixture
    active = {"transactions": 0}
    engine = app.state.engine

    def begin(_): active["transactions"] += 1
    def finish(_): active["transactions"] -= 1
    event.listen(engine, "begin", begin)
    event.listen(engine, "commit", finish)
    event.listen(engine, "rollback", finish)

    def validate(snapshot, original, request):
        assert active["transactions"] == 0
        assert snapshot == app.state.storage.root / data["source_key"]
        assert original["source_manifest_sha256"] == request["binding"]["manifest_sha256"]
        assert original["validation_nonce"] == request["binding"]["evidence_id"]
        assert request["validation_nonce"] == request["evidence_id"] != original["validation_nonce"]
        assert request["original_request_sha256"] == fingerprint(original)
        assert original["expected_charts"] == [] and original["pages"] == 3

    validator.on_validate = validate
    result = coordinator.tick()
    assert result["status"] == "acceptance_validated" and result["version_id"] == data["vid"]
    assert len(validator.calls) == 1
    with app.state.sessions() as session:
        assert session.get(Version, data["vid"]).status == "review"
        assert session.get(Project, data["pid"]).revision == 8
        assert session.get(Project, data["pid"]).latest_version_id is None
        assert session.get(Checkpoint, data["cid"]).manifest["eligible_as_revision_base"] is False
        assert len(session.scalars(select(Job)).all()) == 1
    event.remove(engine, "begin", begin)
    event.remove(engine, "commit", finish)
    event.remove(engine, "rollback", finish)


@pytest.mark.parametrize("activity", ["queued", "running", "slot", "project", "parser_queued", "parser_active", "parser_stopping"])
def test_optional_acceptance_gives_all_execution_and_parser_activity_priority(fixture, activity):
    app, data, validator, coordinator = fixture
    with app.state.sessions.begin() as session:
        if activity in ("queued", "running"):
            session.get(Job, data["jid"]).status = activity
        elif activity == "slot":
            session.get(ExecutionSlot, 1).job_id = data["jid"]
        elif activity == "project":
            session.get(Project, data["pid"]).active_job_id = data["jid"]
        else:
            asset = session.scalar(select(Asset).where(Asset.project_id == data["pid"]))
            asset.parse_status = {"parser_queued": "queued", "parser_active": "parsing", "parser_stopping": "stopping"}[activity]
    assert coordinator.tick()["status"] == "idle"
    assert validator.calls == []


def test_stopped_failed_attempt_is_not_automatically_repeated_after_restart(fixture):
    app, _, validator, coordinator = fixture
    validator.status = "acceptance_failed"
    assert coordinator.tick()["status"] == "acceptance_failed"
    restarted = AcceptanceCoordinator(app.state.sessions, coordinator.config, validator,
        oracle_provider=coordinator.oracle_provider, enabled=True)
    for _ in range(3):
        assert restarted.tick()["status"] == "idle"
    assert len(validator.calls) == 1


def test_bounded_cursor_does_not_starve_newer_reviews_behind_cached_versions(fixture):
    app, data, validator, coordinator = fixture
    second = seed_agent_review(app, Identity("https://issuer", "alice"))
    coordinator.max_candidates = 1
    validator.status = "acceptance_failed"
    assert coordinator.tick()["version_id"] == data["vid"]
    assert coordinator.tick()["version_id"] == second["vid"]
    for _ in range(4):
        assert coordinator.tick()["status"] == "idle"
    assert len(validator.calls) == 2


def test_existing_bundle_and_bad_original_source_never_trigger_a_new_model_or_validation(fixture):
    app, data, validator, coordinator = fixture
    root = app.state.storage.root / "projects" / data["pid"] / "versions" / data["vid"] / "acceptance-evidence"
    root.mkdir(parents=True)
    assert coordinator.tick()["status"] == "idle" and validator.calls == []
    root.rmdir()
    (app.state.storage.root / data["source_key"] / "project/deck/spec_lock.md").write_bytes(b"changed frozen source")
    for _ in range(3):
        assert coordinator.tick()["status"] == "idle"
    assert validator.calls == []


def test_disabled_or_wrong_backend_cannot_enable_optional_acceptance(fixture):
    app, _, validator, coordinator = fixture
    disabled = AcceptanceCoordinator(app.state.sessions, coordinator.config, validator)
    assert disabled.tick() == {"status": "disabled"}
    with pytest.raises(ControlError, match="invalid_acceptance_configuration"):
        AcceptanceCoordinator(app.state.sessions, coordinator.config, validator,
            oracle_provider=coordinator.oracle_provider, enabled=True, backend_mode="docker")
    validator.transport.settings.worker_image = "synthetic/office-validator:latest"
    with pytest.raises(ControlError, match="invalid_acceptance_configuration"):
        AcceptanceCoordinator(app.state.sessions, coordinator.config, validator,
            oracle_provider=coordinator.oracle_provider, enabled=True)
    assert validator.calls == []


def test_chart_expectations_must_come_from_authorized_input_oracle(fixture):
    _, _, validator, coordinator = fixture
    coordinator.oracle_provider = lambda task: {"expected_charts": [{"synthetic": True}], "source_sha256": ["0" * 64]}
    with pytest.raises(ControlError, match="oracle_source_mismatch"):
        coordinator.tick()
    assert validator.calls == []


def test_new_activity_between_metadata_read_and_validation_gets_priority(fixture):
    app, data, validator, coordinator = fixture

    def oracle(task):
        with app.state.sessions.begin() as session:
            session.get(Job, data["jid"]).status = "queued"
        return {"expected_charts": [], "source_sha256": []}

    coordinator.oracle_provider = oracle
    assert coordinator.tick() == {"status": "idle", "reason": "busy"}
    assert validator.calls == []


def test_source_changes_during_unpaid_validation_keep_original_request_bound(fixture):
    app, data, validator, coordinator = fixture

    def change(snapshot, original, request):
        with app.state.sessions.begin() as session:
            session.get(Project, data["pid"]).revision += 1
        assert request["binding"]["source_revision"] == 8

    validator.on_validate = change
    assert coordinator.tick()["status"] == "acceptance_validated"
    with app.state.sessions() as session:
        assert session.get(Project, data["pid"]).revision == 9
        assert session.get(Version, data["vid"]).status == "review"
