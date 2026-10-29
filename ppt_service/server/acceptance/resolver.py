"""Resolve human review from frozen, controller-owned delivery evidence.

This reader hashes bytes and checks database provenance. It never runs project
code, parses Office/XML, reconstructs programmatic certificates or publishes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from ..models import AgentOutbox, Checkpoint, Job, Project, Version
from ..repository import Repository
from ..storage import Storage, safe_key
from .contracts import PageDescriptor, RuntimeBinding, SourceBinding
from .domain import AcceptanceError, ReviewContext

SCHEMA = 'ppt-service.hermes-review-delivery.v1'
CACHE_ROOTS = frozenset(('.preview', '.review', 'live_preview', '.live_preview.lock', 'validation'))
PENDING = frozenset(('independent_export_build_contract', 'independent_visual_review',
                     'powerpoint_open_edit_save_reopen'))
COMPLETED = frozenset(('independent_raster_regeneration_and_binding',
                       'independent_delivery_audit', 'independent_native_ooxml_and_chart_data',
                       'independent_postflight_source_and_quality'))
SHA = re.compile(r'[a-f0-9]{64}\Z')
IMAGE = re.compile(r'[^@\s]+@sha256:([a-f0-9]{64})\Z')
AGENT_FORMAT = 'ppt-agent-artifacts-v1'
AGENT_PENDING = frozenset(('independent_visual_review', 'independent_postflight_and_delivery'))
AGENT_COMPLETED = COMPLETED - {'independent_postflight_source_and_quality'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def require(value):
    if not value:
        raise AcceptanceError('review_evidence_unavailable', 503)


class ReviewContextResolver:
    """Immutable review context, with core locks retained by the caller.

    Both reads and writes lock the terminal job before the project, as does the
    controller. Project.revision is a conservative monotonic source epoch. A
    project edit can invalidate observations even if artifact bytes stay equal.
    """

    def __init__(self, storage: Storage):
        self.storage = storage

    def _read(self, key, limit=4 * 1024**2):
        with self.storage.open(key, limit) as stream:
            before = os.fstat(stream.fileno())
            require(before.st_nlink == 1)
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        require(len(data) == before.st_size <= limit
                and before.st_mtime_ns == after.st_mtime_ns)
        return data

    def _json(self, key):
        def pairs(items):
            result = {}
            for name, value in items:
                require(name not in result)
                result[name] = value
            return result

        def invalid(_):
            require(False)

        data = self._read(key)
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
        require(isinstance(value, dict))
        return value, hashlib.sha256(data).hexdigest()

    def _hash(self, key, limit=64 * 1024**2):
        digest, size = hashlib.sha256(), 0
        with self.storage.open(key, limit) as stream:
            before = os.fstat(stream.fileno())
            require(before.st_nlink == 1)
            while chunk := stream.read(65536):
                size += len(chunk)
                require(size <= limit)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        require(size == before.st_size and before.st_mtime_ns == after.st_mtime_ns)
        return {'sha256': digest.hexdigest(), 'bytes': size}

    def _tree(self, prefix, expected, total_limit=512 * 1024**2, *, exclude=()):
        """Check complete bounded no-link rosters, not just declared files."""
        require(isinstance(expected, list) and len(expected) <= 8192)
        indexed = {}
        folded = set()
        total = 0
        for row in expected:
            require(isinstance(row, dict) and set(row) == {'path', 'sha256', 'bytes'})
            key = '/'.join(safe_key(row['path']))
            require(key.casefold() not in folded and isinstance(row['sha256'], str)
                    and SHA.fullmatch(row['sha256']) and type(row['bytes']) is int
                    and 0 <= row['bytes'] <= 64 * 1024**2)
            folded.add(key.casefold())
            total += row['bytes']
            require(total <= total_limit)
            indexed[key] = row
        target = self.storage.root
        for part in safe_key(prefix):
            target /= part
            require(not target.is_symlink() and not target.is_junction())
        actual = set()
        directories = 0

        def walk(folder, base=''):
            nonlocal directories
            info = folder.lstat()
            directories += 1
            require(directories <= 8192 and stat.S_ISDIR(info.st_mode)
                    and not folder.is_symlink() and not folder.is_junction())
            for entry in os.scandir(folder):
                key = base + entry.name
                if not base and entry.name in exclude:
                    continue
                safe_key(key)
                node = Path(entry.path)
                info = entry.stat(follow_symlinks=False)
                require(not entry.is_symlink() and not node.is_junction())
                if stat.S_ISDIR(info.st_mode):
                    walk(node, key + '/')
                else:
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                            and len(actual) < 8192 and key in indexed)
                    require(self._hash(prefix + '/' + key) ==
                            {name: indexed[key][name] for name in ('sha256', 'bytes')})
                    actual.add(key)

        walk(target)
        require(actual == set(indexed))
        return sorted(indexed.values(), key=lambda row: row['path'])

    def __call__(self, session, owner, project_id, version_id, *, for_update=True):
        # Check ownership before following any source IDs or storage references.
        v = session.scalar(select(Version).join(Project, Project.id == Version.project_id).where(
            Version.id == version_id, Version.project_id == project_id,
            Version.issuer == owner.issuer, Version.subject == owner.sub,
            Project.issuer == owner.issuer, Project.subject == owner.sub,
            Project.deletion_requested == False))
        if v is None:
            raise AcceptanceError('version_not_found', 404)
        try:
            job = session.scalar(select(Job).where(Job.id == v.job_id).with_for_update())
            project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            v = session.scalar(select(Version).where(Version.id == version_id).with_for_update())
            checkpoint = session.scalar(select(Checkpoint).where(Checkpoint.id == v.checkpoint_id).with_for_update())
            if project is None or project.deletion_requested or any(
                    row is None or row.issuer != owner.issuer or row.subject != owner.sub
                    for row in (project, v, job, checkpoint)):
                raise AcceptanceError('version_not_found', 404)
            require(v.project_id == job.project_id == checkpoint.project_id == project.id
                    and v.status == 'review' and job.status == 'succeeded' and job.stopped is True
                    and job.kind in ('generate', 'revise') and job.stage == 'review')
            return self._resolve(session, owner, project, v, job, checkpoint)
        except AcceptanceError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError, ValidationError, RecursionError):
            raise AcceptanceError('review_evidence_unavailable', 503) from None

    def _resolve(self, session, owner, project, version, job, checkpoint):
        public, meta = version.manifest, checkpoint.manifest
        # Only the explicit portable format enters this path. Historical Hermes
        # delivery/session checks below retain their complete native contract.
        if isinstance(meta, dict) and meta.get('checkpoint_format') == AGENT_FORMAT:
            return self._resolve_agent(session, owner, project, version, job, checkpoint)
        require(isinstance(public, dict) and isinstance(meta, dict)
                and public.get('status') == meta.get('status') == 'review'
                and public.get('publication_authorization') is False
                and public.get('eligible_as_revision_base') is meta.get('eligible_as_revision_base') is False
                and meta.get('schema_version') == 1 and meta.get('session_id') == job.session_id
                and meta.get('input_snapshot_sha256') == job.input_snapshot.get('hash'))
        cp_prefix = f'projects/{project.id}/checkpoints/'
        require(checkpoint.storage_key.startswith(cp_prefix)
                and len(safe_key(checkpoint.storage_key)) == 4)
        stored, _ = self._json(checkpoint.storage_key + '/checkpoint.json')
        require(stored == meta)
        for name in ('project', 'home'):
            require(isinstance(meta.get(name), dict))
            rows = self._tree(checkpoint.storage_key + '/' + name, meta[name].get('files'))
            require(sum(row['bytes'] for row in rows) == meta[name].get('bytes'))

        evidence = public['review_evidence']
        require(isinstance(evidence, dict))
        nonce = evidence['controller_nonce']
        require(isinstance(nonce, str) and re.fullmatch(r'[a-f0-9-]{36}', nonce))
        evidence_key = evidence['storage_key']
        require(evidence_key == f'projects/{project.id}/snapshots/validation-evidence/{nonce}')
        receipt, receipt_hash = self._json(evidence_key + '/delivery/candidate-delivery.json')
        require(receipt_hash == evidence['receipt_sha256'] and receipt.get('schema') == SCHEMA
                and receipt.get('engine') == 'hermes' and receipt.get('status') == 'review'
                and receipt.get('delivery_kind') == 'review_candidate'
                and receipt.get('original_candidate_status') == 'candidate'
                and receipt.get('review_artifacts_ready') is True
                and all(receipt.get(name) is False for name in (
                    'accepted', 'published', 'publication_authorization', 'revision_base_eligible',
                    'full_exporter_postflight_verified', 'independent_visual_review'))
                and PENDING.issubset(receipt.get('required_pending_gates', []))
                and COMPLETED.issubset(receipt.get('completed_programmatic_gates', [])))
        image = IMAGE.fullmatch(receipt['execution_image'])
        require(image and receipt['execution_image'] == evidence['execution_image']
                and re.fullmatch(r'[a-f0-9]{40}', receipt['ppt_master_commit'])
                and receipt['controller_nonce'] == nonce
                and receipt['source_manifest_sha256'] == evidence['source_manifest_sha256']
                and receipt['source_fingerprint_sha256'] == evidence['source_fingerprint_sha256']
                and receipt['input_snapshot_sha256'] == job.input_snapshot['hash']
                and all(receipt[name] == getattr(job, name) for name in ('attempt', 'session_id'))
                and receipt['job_id'] == job.id and receipt['phase'] == job.kind)

        transport, _ = self._json(evidence_key + '/transport-manifest.json')
        require(transport.get('schema_version') == 1 and transport.get('controller_nonce') == nonce
                and transport.get('source_manifest_sha256') == evidence['source_manifest_sha256']
                and transport.get('execution_image') == receipt['execution_image']
                and transport.get('transport_verified') is True and transport.get('writers_stopped') is True
                and transport.get('sandbox_exit_code') == 0 and transport.get('controller_error_code') is None
                and isinstance(transport.get('pod_uid'), str) and bool(transport['pod_uid']))
        retained = self._tree(evidence_key, transport['files'], exclude=('transport-manifest.json',))
        require(sum(row['bytes'] for row in retained) == transport['bytes'])
        delivery_rows = self._tree(evidence_key + '/delivery', receipt['retained_files'],
                                   exclude=('candidate-delivery.json',))
        require(sum(row['bytes'] for row in delivery_rows) == receipt['retained_bytes'])
        source = receipt['source']
        project_root = '/'.join(safe_key(meta['project_root']))
        source_rows = []
        for row in meta['project']['files']:
            if row['path'].startswith(project_root + '/'):
                key = row['path'][len(project_root) + 1:]
                if key.split('/', 1)[0] not in CACHE_ROOTS:
                    source_rows.append(dict(row, path=key))
        source_rows.sort(key=lambda row: row['path'])
        require(source_rows and fingerprint(source_rows) == receipt['source_fingerprint_sha256']
                == source.get('copied_source_fingerprint')
                and source.get('project_root') == project_root
                and source.get('source_manifest_sha256') == evidence['source_manifest_sha256']
                and source.get('input_snapshot_sha256') == job.input_snapshot['hash'])
        for folder, report_key, filename in (
                ('programmatic', 'programmatic_report_sha256', 'programmatic-evidence.json'),
                ('postflight', 'postflight_report_sha256', 'postflight-evidence.json')):
            report, _ = self._json(evidence_key + '/delivery/' + folder + '/' + filename)
            require(fingerprint(report) == receipt[report_key] and report.get('source') == source
                    and report.get('controller_nonce') == nonce)

        artifact_key = f'projects/{project.id}/versions/{version.id}/review'
        require(version.artifact_root == artifact_key and public['pptx_key'] == artifact_key + '/presentation.pptx'
                and public['pptx_sha256'] == receipt['artifacts'][0]['sha256'] == source['pptx']['sha256'])
        originals = {row['path']: row for row in source_rows}
        require(source['pptx']['path'] in originals and all(
            originals[source['pptx']['path']][name] == source['pptx'][name] for name in ('sha256', 'bytes')))
        artifacts = [{'path': 'presentation.pptx', **self._hash(public['pptx_key'])}]
        require(artifacts[0]['sha256'] == public['pptx_sha256']
                and artifacts[0]['bytes'] == receipt['artifacts'][0]['bytes'])
        pages = []
        require(3 <= len(public['slides']) <= 20 and len(public['slides']) == len(receipt['pages'])
                == len(source['svg_sources']) and len(receipt['artifacts']) == len(public['slides']) + 1)
        for ordinal, (slide, page, svg, artifact) in enumerate(zip(
                public['slides'], receipt['pages'], source['svg_sources'], receipt['artifacts'][1:]), 1):
            require(page['number'] == svg['ordinal'] == artifact['ordinal'] == ordinal
                    and slide['preview_key'] == artifact_key + f'/{ordinal}.png'
                    and slide['preview_sha256'] == page['preview_sha256'] == artifact['sha256']
                    and slide['source_sha256'] == page['source_sha256'] == svg['sha256']
                    and svg['path'] in originals and originals[svg['path']]['sha256'] == svg['sha256'])
            actual = {'path': f'{ordinal}.png', **self._hash(slide['preview_key'])}
            require(actual['sha256'] == page['preview_sha256'] and actual['bytes'] == artifact['bytes'])
            artifacts.append(actual)
            pages.append(PageDescriptor(number=ordinal, title=slide['title'],
                                        preview_sha256=actual['sha256'], source_sha256=svg['sha256']))
        self._tree(artifact_key, artifacts, 128 * 1024**2)
        require(type(receipt['native_chart_count']) is int and receipt['native_chart_count'] >= 0)
        binding = SourceBinding(
            project_id=project.id, version_id=version.id, source_revision=project.revision,
            manifest_sha256=evidence['source_manifest_sha256'], input_snapshot_sha256=job.input_snapshot['hash'],
            pptx_sha256=public['pptx_sha256'], source_fingerprint_sha256=receipt['source_fingerprint_sha256'],
            evidence_id=nonce, evidence_sha256=receipt_hash,
            runtime=RuntimeBinding(engine='hermes', runtime_revision=image[1],
                                   profile='ppt-master:' + receipt['ppt_master_commit'],
                                   checkpoint_format='hermes-home-project-v1'))
        return ReviewContext(owner=owner, binding=binding, source_revision=project.revision,
                             pages=tuple(pages), native_chart_count=receipt['native_chart_count'],
                             current_input_snapshot_sha256=Repository(session).snapshot(project)['hash'],
                             current_source_fingerprint_sha256=receipt['source_fingerprint_sha256'],
                             origin_status='candidate', phase=job.kind)

    def _resolve_agent(self, session, owner, project, version, job, checkpoint):
        """Bind portable source, remote stop proof and independent validator bytes.

        This web-only reader has no controller/Agent SDK dependency. No report,
        human observation or generic completed state can authorize publication.
        """
        public, meta = version.manifest, checkpoint.manifest
        require(isinstance(public, dict) and public.get('status') == meta.get('status') == 'review'
                and public.get('publication_authorization') is False
                and public.get('eligible_as_revision_base') is meta.get('eligible_as_revision_base') is False
                and meta.get('schema_version') == 1
                and meta.get('input_snapshot_sha256') == job.input_snapshot.get('hash'))
        require(checkpoint.storage_key.startswith(f'projects/{project.id}/checkpoints/')
                and len(safe_key(checkpoint.storage_key)) == 4)
        stored, _ = self._json(checkpoint.storage_key + '/checkpoint.json')
        require(stored == meta)
        cp = meta['agent_checkpoint']
        fields = {'schema_version', 'ppt_master_revision', 'project_id', 'source_business_job_id',
                  'source_job_id', 'source_attempt_id', 'phase', 'input_snapshot_sha256',
                  'project_root', 'files', 'total_bytes', 'publication_authorized', 'sha256'}
        require(isinstance(cp, dict) and set(cp) == fields
                and cp['schema_version'] == 'ppt-artifact-checkpoint.v1'
                and cp['ppt_master_revision'] == 'c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7'
                and cp['project_id'] == project.id and cp['source_business_job_id'] == job.id
                and cp['phase'] == job.kind and cp['input_snapshot_sha256'] == job.input_snapshot['hash']
                and cp['publication_authorized'] is False
                and fingerprint({key: value for key, value in cp.items() if key != 'sha256'}) == cp['sha256'])
        project_root = '/'.join(safe_key(cp['project_root']))
        require(project_root == meta['project_root'])
        for identifier in ('project_id', 'source_business_job_id', 'source_job_id', 'source_attempt_id'):
            require(isinstance(cp[identifier], str) and str(UUID(cp[identifier])) == cp[identifier])
        rows = self._tree(checkpoint.storage_key + '/project', cp['files'], 256 * 1024**2)
        require(0 < len(rows) <= 2046 and type(cp['total_bytes']) is int
                and sum(row['bytes'] for row in rows) == cp['total_bytes']
                and all(row['path'].startswith(project_root + '/') for row in rows))
        retained_cp, _ = self._json(checkpoint.storage_key + '/result/checkpoint.json')
        require(retained_cp == cp)
        checkpoint_rows = [dict(row, path='project/' + row['path']) for row in rows]
        checkpoint_rows.extend({'path': key, **self._hash(checkpoint.storage_key + '/' + key)}
                               for key in ('checkpoint.json', 'result/checkpoint.json'))
        self._tree(checkpoint.storage_key, checkpoint_rows, 256 * 1024**2 + 8 * 1024**2)

        out = session.get(AgentOutbox, job.id)
        require(out is not None and out.attempt == job.attempt and out.fencing_token == job.fencing_token
                and out.state == 'mapped' and isinstance(out.result, dict)
                and out.result.get('status') == 'review' and out.result.get('version_id') == version.id
                and out.remote_job_id == cp['source_job_id'] == job.run_id
                and out.checkpoint_ref == meta['service_checkpoint_ref'])
        pins = out.remote_pins
        require(isinstance(pins, dict) and pins.get('engine') in ('pi', 'hermes')
                and pins.get('profile') == 'ppt.' + job.kind + '.v1'
                and pins.get('profile_revision') == out.request.get('profile_revision')
                and isinstance(pins.get('runtime_revision'), str) and bool(pins['runtime_revision'])
                and isinstance(pins.get('image_digest'), str)
                and re.fullmatch(r'sha256:[a-f0-9]{64}', pins['image_digest']))
        task = out.request['task']
        require(out.request.get('profile') == pins['profile']
                and out.request.get('business_ref') == out.business_ref
                and task.get('phase') == job.kind and task.get('business_job_id') == job.id
                and task.get('project_id') == project.id
                and task.get('input_snapshot_sha256') == job.input_snapshot['hash']
                and fingerprint(task['input_snapshot']) == job.input_snapshot['hash'])
        source_ref = meta['agent_source']
        require(isinstance(source_ref, dict) and set(source_ref) == {
                    'storage_key', 'remote_job_sha256', 'artifact_manifest_sha256'})
        source_key = f'projects/{project.id}/agent-results/{job.id}/{out.fencing_token}'
        require(source_ref['storage_key'] == source_key)
        remote_record, remote_hash = self._json(source_key + '/remote-job.json')
        require(remote_hash == source_ref['remote_job_sha256']
                and isinstance(source_ref['artifact_manifest_sha256'], str)
                and SHA.fullmatch(source_ref['artifact_manifest_sha256'])
                and remote_record.get('artifact_manifest_sha256') == source_ref['artifact_manifest_sha256']
                == out.result.get('artifact_manifest_sha256'))
        remote = remote_record['remote']
        proof, attempt = remote.get('stop_proof'), remote.get('attempt')
        require(remote.get('job_id') == out.remote_job_id and remote.get('business_ref') == out.business_ref
                and remote.get('state') == 'completed' and remote.get('pins') == pins
                and isinstance(attempt, dict) and attempt.get('attempt_id') == cp['source_attempt_id']
                and isinstance(proof, dict) and proof.get('stop_proven') is True
                and proof.get('attempt_id') == attempt['attempt_id']
                and type(attempt.get('fence')) is int and proof.get('fence') == attempt['fence']
                and bool(proof.get('resource_ids')))
        self._tree(source_key + '/project', rows, 256 * 1024**2)
        original_cp, _ = self._json(source_key + '/result/checkpoint.json')
        manifest, manifest_hash = self._json(source_key + '/result/manifest.json')
        require(original_cp == cp and manifest.get('phase') == job.kind and manifest.get('status') == 'candidate'
                and manifest.get('input_snapshot_sha256') == job.input_snapshot['hash']
                and manifest.get('project_root') == project_root)
        source_files = [dict(row, path='project/' + row['path']) for row in rows]
        source_files.extend({'path': key, **self._hash(source_key + '/' + key)}
                            for key in ('result/manifest.json', 'result/checkpoint.json', 'remote-job.json'))
        self._tree(source_key, source_files, 256 * 1024**2 + 12 * 1024**2)
        originals = {row['path'][len(project_root) + 1:]: dict(
            row, path=row['path'][len(project_root) + 1:]) for row in rows}
        artifacts = manifest['artifacts']
        require(isinstance(artifacts, list) and len(artifacts) == len(originals)
                and len({row['path'] for row in artifacts}) == len(artifacts)
                and {row['path'] for row in artifacts} == set(originals))
        for artifact in artifacts:
            require(all(artifact[name] == originals[artifact['path']][name] for name in ('sha256', 'bytes')))

        evidence = public['review_evidence']
        require(isinstance(evidence, dict) and evidence.get('kind') == 'agent-validation.v1')
        nonce = evidence['controller_nonce']
        require(isinstance(nonce, str) and str(UUID(nonce)) == nonce)
        evidence_key = (f'projects/{project.id}/agent-results/{job.id}/'
                        f'agent-validation-{out.fencing_token}-{manifest_hash[:16]}')
        require(evidence['storage_key'] == evidence_key and evidence['source_manifest_sha256'] == manifest_hash)
        report, report_hash = self._json(evidence_key + '/validated.json')
        require(report_hash == evidence['receipt_sha256'] and report.get('manifest') == manifest
                and report.get('source_manifest_sha256') == manifest_hash
                and report.get('input_snapshot_sha256') == job.input_snapshot['hash']
                and report.get('phase') == job.kind and report.get('validation_nonce') == nonce
                and report.get('status') == 'candidate' and report.get('accepted') is False
                and report.get('publication_authorized') is False and report.get('programmatic_checks_passed') is True
                and report.get('evidence', {}).get('official_gates_verified') is True
                and AGENT_PENDING.issubset(report.get('required_pending_gates', []))
                and public.get('required_pending_gates') == report.get('required_pending_gates'))
        transport, _ = self._json(evidence_key + '/transport-manifest.json')
        require(transport.get('schema_version') == 'ppt-agent-validation-transport.v1'
                and transport.get('source_manifest_sha256') == manifest_hash
                and transport.get('validation_nonce') == nonce
                and transport.get('writers_stopped') is True and transport.get('transport_verified') is True
                and type(transport.get('sandbox_exit_code')) is int and transport['sandbox_exit_code'] == 0
                and isinstance(transport.get('sandbox_pod_uid'), str) and bool(transport['sandbox_pod_uid'])
                and isinstance(transport.get('validation_request_sha256'), str)
                and SHA.fullmatch(transport['validation_request_sha256'])
                and isinstance(transport.get('execution_image'), str) and IMAGE.fullmatch(transport['execution_image'])
                and transport['execution_image'] == evidence['execution_image'])
        retained = self._tree(evidence_key, transport['files'], exclude=('transport-manifest.json',))
        require(type(transport.get('bytes')) is int and sum(row['bytes'] for row in retained) == transport['bytes'])
        programmatic = report['independent_programmatic_evidence']
        require(isinstance(programmatic, dict))
        validation_id = programmatic['validation_id']
        require(isinstance(validation_id, str) and str(UUID(validation_id)) == validation_id)
        programmatic_key = evidence_key + '/independent/' + validation_id
        persisted, _ = self._json(programmatic_key + '/programmatic-evidence.json')
        require(persisted == programmatic and programmatic.get('schema_version') == 1
                and programmatic.get('issuer') == 'ppt-service-independent-programmatic-runtime-v1'
                and programmatic.get('controller_nonce') == nonce
                and programmatic.get('preview_group_stopped') is True and programmatic.get('command_groups_stopped') is True
                and type(programmatic.get('render_exit_code')) is int and programmatic['render_exit_code'] == 0
                and type(programmatic.get('delivery_exit_code')) is int and programmatic['delivery_exit_code'] == 0
                and programmatic.get('independent_visual_review') is False
                and programmatic.get('full_exporter_postflight_verified') is False
                and AGENT_COMPLETED.issubset(programmatic.get('completed_programmatic_gates', [])))
        source = programmatic['source']
        source_rows = sorted((row for row in originals.values()
                              if row['path'].split('/', 1)[0] not in CACHE_ROOTS), key=lambda row: row['path'])
        source_fingerprint = fingerprint(source_rows)
        require(source_rows and source.get('copied_source_fingerprint') == source_fingerprint
                == evidence['source_fingerprint_sha256'] and source.get('project_root') == project_root
                and source.get('source_manifest_sha256') == manifest_hash
                and source.get('input_snapshot_sha256') == job.input_snapshot['hash'])
        stored_source, _ = self._json(programmatic_key + '/source-binding.json')
        require(stored_source == source)
        pptxs = [row for row in artifacts if row.get('role') == 'pptx']
        require(len(pptxs) == 1 and source['pptx'] == originals[pptxs[0]['path']])
        artifact_key = f'projects/{project.id}/versions/{version.id}/review'
        require(version.artifact_root == artifact_key and public['pptx_key'] == artifact_key + '/presentation.pptx'
                and public['pptx_sha256'] == source['pptx']['sha256'])
        delivered = [{'path': 'presentation.pptx', **self._hash(public['pptx_key'])}]
        require(all(delivered[0][name] == source['pptx'][name] for name in ('sha256', 'bytes')))
        pages = []
        require(3 <= len(public['slides']) <= 20 and len(public['slides']) == len(manifest['slides'])
                == len(source['svg_sources']) == len(programmatic['pngs'])
                == task['input_snapshot']['settings'].get('page_count', 8))
        for ordinal, (slide, original, svg, png) in enumerate(zip(
                public['slides'], manifest['slides'], source['svg_sources'], programmatic['pngs']), 1):
            require(original['ordinal'] == svg['ordinal'] == png['ordinal'] == ordinal
                    and original['svg_path'] == svg['path'] == png['svg_path']
                    and all(svg[name] == originals[svg['path']][name] for name in ('sha256', 'bytes'))
                    and svg['sha256'] == png['svg_sha256'] == slide['source_sha256']
                    and slide['preview_key'] == artifact_key + f'/{ordinal}.png'
                    and slide['title'] == str(original.get('title', ''))[:200]
                    and slide['preview_sha256'] == png['sha256'])
            png_key = '/'.join(safe_key(png['relative_path']))
            actual_png = self._hash(programmatic_key + '/' + png_key)
            actual = {'path': f'{ordinal}.png', **self._hash(slide['preview_key'])}
            require(actual_png == {name: png[name] for name in ('sha256', 'bytes')}
                    and actual_png == {name: actual[name] for name in ('sha256', 'bytes')})
            delivered.append(actual)
            pages.append(PageDescriptor(number=ordinal, title=slide['title'],
                         preview_sha256=actual['sha256'], source_sha256=svg['sha256']))
        self._tree(artifact_key, delivered, 128 * 1024**2)
        native = programmatic['native']
        stored_native, _ = self._json(programmatic_key + '/native-inspection.json')
        require(stored_native == native)
        require(type(native.get('chart_count')) is int and native['chart_count'] >= 0
                and native.get('slide_count') == len(pages)
                and (not native['chart_count'] or native.get('chart_data_verified') is True))
        binding = SourceBinding(project_id=project.id, version_id=version.id, source_revision=project.revision,
            manifest_sha256=manifest_hash, input_snapshot_sha256=job.input_snapshot['hash'],
            pptx_sha256=public['pptx_sha256'], source_fingerprint_sha256=source_fingerprint,
            evidence_id=nonce, evidence_sha256=report_hash,
            runtime=RuntimeBinding(engine=pins['engine'], runtime_revision=fingerprint(pins),
                                   profile=pins['profile'], checkpoint_format=AGENT_FORMAT))
        return ReviewContext(owner=owner, binding=binding, source_revision=project.revision, pages=tuple(pages),
            native_chart_count=native['chart_count'],
            current_input_snapshot_sha256=Repository(session).snapshot(project)['hash'],
            current_source_fingerprint_sha256=source_fingerprint, origin_status='candidate', phase=job.kind)
