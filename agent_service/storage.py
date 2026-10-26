"""Transactional file retention, portable snapshots and business acknowledgements.

All mutations run under the Store transaction/fence. Nothing in this module
launches an engine, treats an Agent manifest as stop proof, or publishes content.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import (Boolean, Column, Float, ForeignKey, JSON, String, Table,
                        UniqueConstraint, delete, insert, select, update)

from .artifacts import frozen_manifest, relative_path
from .checkpoints import CHECKPOINT_FORMAT, checkpoint_manifest, compatible_checkpoint
from .contracts import InlineFile, ServiceError, canonical_bytes, digest


EXECUTION_FIELDS = ('task_schema', 'output_schema', 'max_input_bytes', 'max_input_total_bytes',
                    'max_input_files', 'max_artifact_bytes', 'max_artifact_file_bytes',
                    'max_artifact_files')


def execution_fields(pins):
    return {key: pins[key] for key in EXECUTION_FIELDS if key in pins}


def define_tables(metadata):
    references = Table('agent_resource_references', metadata,
        Column('resource_kind', String(32), primary_key=True),
        Column('resource_id', String(36), primary_key=True),
        Column('owner_kind', String(32), primary_key=True),
        Column('owner_id', String(36), primary_key=True))
    tombstones = Table('agent_blob_tombstones', metadata,
        Column('kind', String(32), primary_key=True), Column('id', String(36), primary_key=True),
        Column('deleted_at', Float, nullable=False))
    manifests = Table('agent_artifact_manifests', metadata,
        Column('job_id', String(36), ForeignKey('agent_jobs.id'), primary_key=True),
        Column('manifest', JSON, nullable=False), Column('created_at', Float, nullable=False))
    checkpoints = Table('agent_checkpoints', metadata,
        Column('id', String(36), primary_key=True), Column('tenant', String(128), nullable=False),
        Column('caller', String(128), nullable=False),
        Column('idempotency_key', String(128), nullable=False),
        Column('request_sha256', String(64), nullable=False),
        Column('source_job_id', String(36), ForeignKey('agent_jobs.id'), nullable=False),
        Column('manifest', JSON, nullable=False), Column('created_at', Float, nullable=False),
        UniqueConstraint('tenant', 'caller', 'idempotency_key', name='agent_checkpoint_idempotency'))
    retention = Table('agent_job_retention', metadata,
        Column('job_id', String(36), ForeignKey('agent_jobs.id'), primary_key=True),
        Column('inputs_released', Boolean, nullable=False, default=False),
        Column('artifacts_released', Boolean, nullable=False, default=False))
    recoveries = Table('agent_business_recoveries', metadata,
        Column('job_id', String(36), ForeignKey('agent_jobs.id'), primary_key=True),
        Column('revision', String(36), nullable=False), Column('document', JSON, nullable=False),
        Column('updated_at', Float, nullable=False))
    acknowledgements = Table('agent_publication_acknowledgements', metadata,
        Column('id', String(36), primary_key=True),
        Column('job_id', String(36), ForeignKey('agent_jobs.id'), nullable=False),
        Column('idempotency_key', String(128), nullable=False),
        Column('request_sha256', String(64), nullable=False),
        Column('document', JSON, nullable=False), Column('created_at', Float, nullable=False),
        UniqueConstraint('job_id', 'idempotency_key', name='agent_publication_ack_idempotency'))
    return dict(references=references, tombstones=tombstones, manifests=manifests,
                checkpoints=checkpoints, retention=retention, recoveries=recoveries,
                acknowledgements=acknowledgements)


class FileStoreMixin:
    def _removed(self, conn, kind, identifier):
        table = self.file_tables['tombstones']
        return conn.execute(select(table.c.id).where(table.c.kind == kind,
                                                     table.c.id == identifier)).first() is not None

    def _require_blob(self, conn, kind, identifier):
        if self._removed(conn, kind, identifier):
            raise ServiceError('not_found', 404)

    def _retain(self, conn, kind, identifier, owner_kind, owner_id):
        refs = self.file_tables['references']
        existing = conn.execute(select(refs).where(refs.c.resource_kind == kind,
            refs.c.resource_id == identifier, refs.c.owner_kind == owner_kind,
            refs.c.owner_id == owner_id)).first()
        if existing is None:
            conn.execute(insert(refs).values(resource_kind=kind, resource_id=identifier,
                                             owner_kind=owner_kind, owner_id=owner_id))

    def _retain_submission(self, conn, row):
        for ref in row['request'].get('inputs', []):
            self._retain(conn, 'input', ref['input_id'], 'job_input', row['id'])
        checkpoint = row['request'].get('checkpoint_ref')
        if checkpoint:
            self._retain(conn, 'checkpoint', checkpoint['checkpoint_id'], 'job_checkpoint', row['id'])

    def _freeze_artifact_manifest(self, conn, row, files):
        table = self.file_tables['manifests']
        manifest = frozen_manifest(row['id'], row['attempt_id'],
            [dict(item, artifact_id=item['id']) for item in files])
        conn.execute(insert(table).values(job_id=row['id'], manifest=manifest, created_at=self.clock()))
        for item in files:
            self._retain(conn, 'artifact', item['id'], 'job_output', row['id'])
        return manifest

    def get_artifact_manifest(self, tenant, caller, job_id):
        table = self.file_tables['manifests']
        with self._tx() as conn:
            self._find(conn, job_id, tenant, caller)
            row = conn.execute(select(table).where(table.c.job_id == job_id)).mappings().first()
            if row is None:
                raise ServiceError('artifact_manifest_unavailable', 409)
            return row['manifest']

    def _checkpoint(self, conn, tenant, caller, checkpoint_id):
        table = self.file_tables['checkpoints']
        row = conn.execute(select(table).where(table.c.id == checkpoint_id,
            table.c.tenant == tenant, table.c.caller == caller)).mappings().first()
        if row is None or self._removed(conn, 'checkpoint', checkpoint_id):
            raise ServiceError('not_found', 404)
        return dict(row)

    @staticmethod
    def _checkpoint_metadata(row):
        return dict(checkpoint_id=row['id'], sha256=row['manifest']['sha256'],
                    format=row['manifest']['format'], source_job_id=row['source_job_id'],
                    created_at=row['created_at'], manifest=row['manifest'],
                    native_resume=False, publication_authorized=False)

    def _check_checkpoint(self, conn, tenant, caller, request, pins=None):
        reference = request.get('checkpoint_ref')
        if reference is None:
            return None
        if (type(reference) is not dict
                or set(reference) != {'checkpoint_id', 'sha256', 'format'}
                or reference['format'] != CHECKPOINT_FORMAT):
            raise ServiceError('invalid_checkpoint_reference', 422)
        row = self._checkpoint(conn, tenant, caller, reference['checkpoint_id'])
        if row['manifest']['sha256'] != reference['sha256']:
            raise ServiceError('checkpoint_hash_mismatch', 409)
        if pins is not None:
            compatible_checkpoint(row['manifest'], pins)
            self._checkpoint_files(conn, tenant, caller, row, pins)
        return row

    def _checkpoint_files(self, conn, tenant, caller, checkpoint, pins):
        from .store import artifacts, jobs
        files, total = [], 0
        manifest = checkpoint['manifest']
        if len(manifest['files']) > pins.get('max_input_files', 2048):
            raise ServiceError('checkpoint_size_limit', 413)
        for item in manifest['files']:
            self._require_blob(conn, 'artifact', item['artifact_id'])
            row = conn.execute(select(artifacts).join(jobs, jobs.c.id == artifacts.c.job_id).where(
                artifacts.c.id == item['artifact_id'], jobs.c.tenant == tenant,
                jobs.c.caller == caller)).mappings().first()
            if (row is None or row['sha256'] != item['sha256'] or row['size'] != item['size']
                    or hashlib.sha256(row['content']).hexdigest() != item['sha256']):
                raise ServiceError('checkpoint_integrity_invalid', 503)
            total += row['size']
            if (row['size'] > pins.get('max_artifact_file_bytes', self.max_blob_bytes)
                    or total > pins.get('max_input_total_bytes', self.max_blob_bytes)):
                raise ServiceError('checkpoint_size_limit', 413)
            files.append(InlineFile.from_bytes(relative_path(item['path']), row['content']))
        return files

    def checkpoint_files(self, tenant, caller, reference, pins):
        with self._tx() as conn:
            row = self._check_checkpoint(conn, tenant, caller, {'checkpoint_ref': reference}, pins)
            return [] if row is None else self._checkpoint_files(conn, tenant, caller, row, pins)

    def create_checkpoint(self, tenant, caller, job_id, idempotency_key, *, artifact_ids=None,
                          format=CHECKPOINT_FORMAT):
        from .store import artifacts
        if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128
                or any(not 33 <= ord(c) <= 126 for c in idempotency_key)
                or format != CHECKPOINT_FORMAT or artifact_ids is not None
                and (type(artifact_ids) is not list or not artifact_ids or len(artifact_ids) > 2048
                     or any(type(item) is not str for item in artifact_ids)
                     or len(set(artifact_ids)) != len(artifact_ids))):
            raise ServiceError('invalid_checkpoint_request', 422)
        request_hash = digest(dict(job_id=job_id, artifact_ids=sorted(artifact_ids) if artifact_ids else None,
                                   format=format))
        table = self.file_tables['checkpoints']
        with self._tx() as conn:
            job = self._find(conn, job_id, tenant, caller)
            old = conn.execute(select(table).where(table.c.tenant == tenant,
                table.c.caller == caller, table.c.idempotency_key == idempotency_key)).mappings().first()
            if old is not None:
                if old['request_sha256'] != request_hash:
                    raise ServiceError('idempotency_conflict', 409)
                self._require_blob(conn, 'checkpoint', old['id'])
                return self._checkpoint_metadata(old), False
            if job['state'] not in {'completed', 'needs_input'} or not job.get('attempt_id'):
                raise ServiceError('checkpoint_source_not_stopped', 409)
            frozen = self.file_tables['manifests']
            if conn.execute(select(frozen.c.job_id).where(frozen.c.job_id == job_id)).first() is None:
                raise ServiceError('artifact_manifest_unavailable', 409)
            rows = list(conn.execute(select(artifacts).where(artifacts.c.job_id == job_id)
                                     .order_by(artifacts.c.name)).mappings())
            if artifact_ids is not None:
                rows = [row for row in rows if row['id'] in artifact_ids]
                if len(rows) != len(artifact_ids):
                    raise ServiceError('not_found', 404)
            if not rows:
                raise ServiceError('checkpoint_has_no_files', 409)
            for row in rows:
                self._require_blob(conn, 'artifact', row['id'])
            binding = job['pins'].get('execution_binding') or {}
            parent = job['request'].get('checkpoint_ref')
            if parent:
                self._checkpoint(conn, tenant, caller, parent['checkpoint_id'])
            manifest = checkpoint_manifest(job_id=job_id, attempt_id=job['attempt_id'],
                profile=job['request']['profile'], profile_revision=job['request']['profile_revision'],
                contract_sha256=binding.get('contract_sha256'),
                execution_binding=binding or None,
                artifacts=[dict(row, artifact_id=row['id']) for row in rows],
                parent_checkpoint_id=parent['checkpoint_id'] if parent else None)
            record = dict(id=str(uuid.uuid4()), tenant=tenant, caller=caller,
                          idempotency_key=idempotency_key, request_sha256=request_hash,
                          source_job_id=job_id, manifest=manifest, created_at=self.clock())
            conn.execute(insert(table).values(**record))
            for row in rows:
                self._retain(conn, 'artifact', row['id'], 'checkpoint', record['id'])
            if parent:
                self._retain(conn, 'checkpoint', parent['checkpoint_id'], 'checkpoint_parent', record['id'])
            return self._checkpoint_metadata(record), True

    def get_checkpoint(self, tenant, caller, checkpoint_id):
        with self._tx() as conn:
            return self._checkpoint_metadata(self._checkpoint(conn, tenant, caller, checkpoint_id))

    def lookup_job(self, tenant, caller, idempotency_key):
        from .store import jobs, _job
        if (type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128
                or any(not 33 <= ord(c) <= 126 for c in idempotency_key)):
            raise ServiceError('invalid_idempotency_key', 422)
        with self._tx() as conn:
            row = conn.execute(select(jobs).where(jobs.c.tenant == tenant,
                jobs.c.caller == caller, jobs.c.idempotency_key == idempotency_key)).mappings().first()
            if row is None:
                raise ServiceError('not_found', 404)
            return _job(row)

    def _delete_resource(self, conn, kind, identifier):
        refs, tombstones = self.file_tables['references'], self.file_tables['tombstones']
        if conn.execute(select(refs).where(refs.c.resource_kind == kind,
                                          refs.c.resource_id == identifier)).first() is not None:
            raise ServiceError('resource_retained', 409)
        if not self._removed(conn, kind, identifier):
            conn.execute(insert(tombstones).values(kind=kind, id=identifier, deleted_at=self.clock()))
        return dict(deleted=True, kind=kind, resource_id=identifier)

    def delete_input(self, tenant, caller, input_id):
        from .store import inputs
        with self._tx() as conn:
            item = conn.execute(select(inputs.c.id).where(inputs.c.id == input_id,
                inputs.c.tenant == tenant, inputs.c.caller == caller)).first()
            if item is None:
                raise ServiceError('not_found', 404)
            result = self._delete_resource(conn, 'input', input_id)
            conn.execute(update(inputs).where(inputs.c.id == input_id).values(content=b''))
            return result

    def delete_artifact(self, tenant, caller, artifact_id):
        from .store import artifacts, jobs
        with self._tx() as conn:
            item = conn.execute(select(artifacts.c.id).join(jobs, jobs.c.id == artifacts.c.job_id).where(
                artifacts.c.id == artifact_id, jobs.c.tenant == tenant, jobs.c.caller == caller)).first()
            if item is None:
                raise ServiceError('not_found', 404)
            result = self._delete_resource(conn, 'artifact', artifact_id)
            conn.execute(update(artifacts).where(artifacts.c.id == artifact_id).values(content=b''))
            return result

    def delete_checkpoint(self, tenant, caller, checkpoint_id):
        refs = self.file_tables['references']
        with self._tx() as conn:
            table = self.file_tables['checkpoints']
            row = conn.execute(select(table).where(table.c.id == checkpoint_id,
                table.c.tenant == tenant, table.c.caller == caller)).first()
            if row is None:
                raise ServiceError('not_found', 404)
            result = self._delete_resource(conn, 'checkpoint', checkpoint_id)
            conn.execute(delete(refs).where(refs.c.owner_id == checkpoint_id,
                                          refs.c.owner_kind.in_(['checkpoint', 'checkpoint_parent'])))
            return result

    def release_job_resources(self, tenant, caller, job_id, *, inputs=True, artifacts=True):
        if type(inputs) is not bool or type(artifacts) is not bool:
            raise ServiceError('invalid_retention_request', 422)
        refs, retention = self.file_tables['references'], self.file_tables['retention']
        with self._tx() as conn:
            job = self._find(conn, job_id, tenant, caller)
            if job['state'] not in {'completed', 'needs_input', 'failed', 'cancelled', 'timed_out'}:
                raise ServiceError('job_resources_still_active', 409)
            existing = conn.execute(select(retention).where(retention.c.job_id == job_id)).mappings().first()
            values = dict(inputs_released=inputs or bool(existing and existing['inputs_released']),
                          artifacts_released=artifacts or bool(existing and existing['artifacts_released']))
            if existing is None:
                conn.execute(insert(retention).values(job_id=job_id, **values))
            else:
                conn.execute(update(retention).where(retention.c.job_id == job_id).values(**values))
            kinds = (['job_input', 'job_checkpoint'] if inputs else []) + (['job_output'] if artifacts else [])
            if kinds:
                conn.execute(delete(refs).where(refs.c.owner_id == job_id, refs.c.owner_kind.in_(kinds)))
            return dict(job_id=job_id, **values, deletion_performed=False)

    def recovery(self, tenant, caller, job_id):
        table = self.file_tables['recoveries']
        with self._tx() as conn:
            job = self._find(conn, job_id, tenant, caller)
            value = conn.execute(select(table).where(table.c.job_id == job_id)).mappings().first()
            return dict(job_id=job_id, state=job['state'],
                        revision=value['revision'] if value else None,
                        document=value['document'] if value else {},
                        updated_at=value['updated_at'] if value else None,
                        native_resume=False, publication_authorized=False)

    def save_recovery(self, tenant, caller, job_id, document, *, expected_revision=None):
        if (type(document) is not dict or len(canonical_bytes(document)) > 65536
                or set(document) - {'phase', 'business_ref', 'checkpoint_id', 'data'}
                or 'data' in document and type(document['data']) is not dict):
            raise ServiceError('invalid_recovery_document', 422)
        for key in ('phase', 'business_ref'):
            if key in document and (type(document[key]) is not str or not 1 <= len(document[key]) <= 200
                                     or any(ord(char) < 32 for char in document[key])):
                raise ServiceError('invalid_recovery_document', 422)
        table = self.file_tables['recoveries']
        with self._tx() as conn:
            self._find(conn, job_id, tenant, caller)
            if document.get('checkpoint_id') is not None:
                self._checkpoint(conn, tenant, caller, document['checkpoint_id'])
            old = conn.execute(select(table).where(table.c.job_id == job_id)).mappings().first()
            if (old['revision'] if old else None) != expected_revision:
                raise ServiceError('recovery_revision_conflict', 409)
            values = dict(revision=str(uuid.uuid4()), document=document, updated_at=self.clock())
            if old:
                conn.execute(update(table).where(table.c.job_id == job_id).values(**values))
            else:
                conn.execute(insert(table).values(job_id=job_id, **values))
            refs = self.file_tables['references']
            conn.execute(delete(refs).where(refs.c.owner_kind == 'business_recovery', refs.c.owner_id == job_id))
            if document.get('checkpoint_id'):
                self._retain(conn, 'checkpoint', document['checkpoint_id'], 'business_recovery', job_id)
            return dict(job_id=job_id, **values, native_resume=False, publication_authorized=False)

    def acknowledge_publication(self, tenant, caller, job_id, idempotency_key, document):
        import re
        if (type(document) is not dict
                or not {'manifest_sha256', 'delivery_ref'} <= set(document)
                or set(document) - {'manifest_sha256', 'delivery_ref', 'decision'}
                or type(document['manifest_sha256']) is not str or len(document['manifest_sha256']) != 64
                or type(document['delivery_ref']) is not str or not 1 <= len(document['delivery_ref']) <= 300
                or any(ord(char) < 32 for char in document['delivery_ref'])
                or type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 128
                or any(not 33 <= ord(c) <= 126 for c in idempotency_key)):
            raise ServiceError('invalid_publication_acknowledgement', 422)
        if ('decision' in document and (type(document['decision']) is not str
                or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}', document['decision']))):
            raise ServiceError('invalid_publication_acknowledgement', 422)
        table, manifests = self.file_tables['acknowledgements'], self.file_tables['manifests']
        with self._tx() as conn:
            job = self._find(conn, job_id, tenant, caller)
            old = conn.execute(select(table).where(table.c.job_id == job_id,
                table.c.idempotency_key == idempotency_key)).mappings().first()
            if old:
                if old['request_sha256'] != digest(document):
                    raise ServiceError('idempotency_conflict', 409)
                return dict(acknowledgement_id=old['id'], job_id=job_id, recorded=True,
                            publication_authorized=False, created_at=old['created_at']), False
            manifest = conn.execute(select(manifests.c.manifest).where(manifests.c.job_id == job_id)).first()
            if (job['state'] != 'completed' or manifest is None
                    or manifest.manifest['sha256'] != document['manifest_sha256']):
                raise ServiceError('publication_result_mismatch', 409)
            values = dict(id=str(uuid.uuid4()), job_id=job_id, idempotency_key=idempotency_key,
                          request_sha256=digest(document), document=document, created_at=self.clock())
            conn.execute(insert(table).values(**values))
            return dict(acknowledgement_id=values['id'], job_id=job_id, recorded=True,
                        publication_authorized=False, created_at=values['created_at']), True

    def publication_acknowledgements(self, tenant, caller, job_id):
        table = self.file_tables['acknowledgements']
        with self._tx() as conn:
            self._find(conn, job_id, tenant, caller)
            rows = conn.execute(select(table).where(table.c.job_id == job_id)
                                .order_by(table.c.created_at, table.c.id)).mappings()
            return [dict(acknowledgement_id=row['id'], document=row['document'],
                         created_at=row['created_at'], recorded=True, publication_authorized=False)
                    for row in rows]
