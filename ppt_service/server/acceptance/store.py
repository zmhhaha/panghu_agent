"""Separate SQL metadata and append-only record service; no core migrations."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..auth import Identity
from .contracts import ReviewCommand, ReviewRecord, SourceBinding
from .domain import AcceptanceError, ContextResolver, ReviewContext, project_snapshot, validate_command


class AcceptanceBase(DeclarativeBase):
    pass


class AcceptanceStream(AcceptanceBase):
    __tablename__ = "ppt_acceptance_streams"
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(256))
    project_id: Mapped[str] = mapped_column(String(80))
    version_id: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer, default=0)


class AcceptanceEntry(AcceptanceBase):
    __tablename__ = "ppt_acceptance_entries"
    __table_args__ = (
        UniqueConstraint("scope", "revision"),
        UniqueConstraint("scope", "request_key"),
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scope: Mapped[str] = mapped_column(ForeignKey("ppt_acceptance_streams.scope"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(128))
    request_sha256: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[str] = mapped_column(String(40))
    issuer: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(256))
    binding: Mapped[dict] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _scope(owner: Identity, project_id: str, version_id: str):
    return _digest([owner.issuer, owner.sub, project_id, version_id])


class AcceptanceService:
    """All writes commit with the resolver's project/version locks held.

    ``sessions`` must be a SQLAlchemy sessionmaker. ``resolve`` is a trusted host
    port, not a dependency receiving browser data beyond the requested IDs.
    Tables are installed explicitly by the host; constructing this service does
    not create/alter schema. Independent SQLite tests are not PostgreSQL rollout.
    """

    def __init__(self, sessions, resolve: ContextResolver):
        self.sessions = sessions
        self.resolve = resolve

    def _context(self, session, owner, project_id, version_id, *, for_update):
        if type(owner) is not Identity:
            raise AcceptanceError("authentication_required", 401)
        if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) for value in (project_id, version_id)):
            raise AcceptanceError("version_not_found", 404)
        context = self.resolve(session, owner, project_id, version_id, for_update=for_update)
        if type(context) is not ReviewContext:
            raise AcceptanceError("trusted_context_unavailable", 503)
        if context.owner != owner or (context.binding.project_id, context.binding.version_id) != (project_id, version_id):
            raise AcceptanceError("version_not_found", 404)
        return context

    @staticmethod
    def _records(session, scope):
        rows = session.scalars(select(AcceptanceEntry).where(AcceptanceEntry.scope == scope).order_by(AcceptanceEntry.revision)).all()
        return [ReviewRecord(
            id=row.id, revision=row.revision, kind=row.kind, created_at=row.created_at,
            reviewer={"issuer": row.issuer, "sub": row.subject}, binding=SourceBinding.model_validate(row.binding),
            payload=row.payload, revoked=False, stale=False,
        ) for row in rows]

    def get(self, owner: Identity, project_id: str, version_id: str):
        with self.sessions.begin() as session:
            # The host locks authoritative source rows even for GET so source
            # invalidation cannot race the context/history projection. GET never
            # inserts a stream or changes rows. Derive revision from the SAME
            # ordered-record SELECT: separate stream/history reads can disagree
            # under PostgreSQL READ COMMITTED when another append commits.
            context = self._context(session, owner, project_id, version_id, for_update=True)
            scope = _scope(owner, project_id, version_id)
            records = self._records(session, scope)
            return project_snapshot(context, records[-1].revision if records else 0, records)

    def append(self, owner: Identity, project_id: str, version_id: str, command: ReviewCommand, request_key: str):
        if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_key):
            raise AcceptanceError("idempotency_key_required", 422)
        try:
            with self.sessions.begin() as session:
                context = self._context(session, owner, project_id, version_id, for_update=True)
                scope = _scope(owner, project_id, version_id)
                digest = _digest(command.model_dump(mode="json"))
                stream = session.scalar(select(AcceptanceStream).where(AcceptanceStream.scope == scope).with_for_update())
                if stream is None:
                    try:
                        with session.begin_nested():
                            stream = AcceptanceStream(scope=scope, issuer=owner.issuer, subject=owner.sub, project_id=project_id, version_id=version_id, revision=0)
                            session.add(stream)
                            session.flush()
                    except IntegrityError:
                        stream = session.scalar(select(AcceptanceStream).where(AcceptanceStream.scope == scope).with_for_update())
                if stream is None:
                    raise AcceptanceError("acceptance_busy", 503)
                prior = session.scalar(select(AcceptanceEntry).where(AcceptanceEntry.scope == scope, AcceptanceEntry.request_key == request_key))
                if prior is not None:
                    if prior.request_sha256 != digest:
                        raise AcceptanceError("idempotency_key_reused")
                    # A lost-response retry remains idempotent even after source
                    # changes or revocation. Return the current view, never append.
                    return project_snapshot(context, stream.revision, self._records(session, scope))
                validate_command(context, command)
                if stream.revision != command.expected_revision:
                    raise AcceptanceError("acceptance_revision_changed")
                records = self._records(session, scope)
                if command.kind == "revoke":
                    target = next((r for r in records if r.id == command.record_id and r.kind != "revoke"), None)
                    if target is None:
                        raise AcceptanceError("review_record_not_found", 404)
                    if any(r.kind == "revoke" and r.payload["record_id"] == target.id for r in records):
                        raise AcceptanceError("review_already_revoked")
                changed = session.execute(update(AcceptanceStream).where(
                    AcceptanceStream.scope == scope, AcceptanceStream.revision == command.expected_revision,
                ).values(revision=command.expected_revision + 1))
                if changed.rowcount != 1:
                    raise AcceptanceError("acceptance_revision_changed")
                row = AcceptanceEntry(
                    id=str(uuid.uuid4()), scope=scope, revision=command.expected_revision + 1,
                    request_key=request_key, request_sha256=digest, kind=command.kind,
                    created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    issuer=owner.issuer, subject=owner.sub, binding=context.binding.model_dump(mode="json"),
                    payload=command.model_dump(mode="json", exclude={"kind", "expected_revision", "binding"}),
                )
                session.add(row)
                session.flush()
                return project_snapshot(context, row.revision, self._records(session, scope))
        except IntegrityError:
            raise AcceptanceError("acceptance_revision_changed") from None
        except OperationalError as exc:
            # Contention is retryable with the SAME key; unknown DB faults remain
            # real server errors rather than being disguised as user conflicts.
            if any(word in str(exc.orig).lower() for word in ("database is locked", "deadlock", "serialize access")):
                raise AcceptanceError("acceptance_busy", 503) from None
            raise
