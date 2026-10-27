"""Synthetic frozen review data for integration tests; no acceptance evidence."""
import hashlib
import json
import uuid

from ppt_service.server.auth import Identity
from ppt_service.server.models import Checkpoint, Job, Project, Version
from ppt_service.server.repository import Repository
from ppt_service.server.acceptance.resolver import COMPLETED, PENDING, SCHEMA, fingerprint


def seed_review(app, owner=Identity('https://issuer', 'alice')):
    root = app.state.storage.root
    pid, vid, jid, cid, nonce = (str(uuid.uuid4()) for _ in range(5))

    def put(key, data):
        target = root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(data, bytes):
            data = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        target.write_bytes(data)
        return {'path': key, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def rows(prefix):
        result = []
        for path in sorted((root / prefix).rglob('*')):
            if path.is_file():
                data = path.read_bytes()
                result.append({'path': path.relative_to(root / prefix).as_posix(),
                               'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        return result

    cp_key = f'projects/{pid}/checkpoints/{cid}'
    evidence_key = f'projects/{pid}/snapshots/validation-evidence/{nonce}'
    artifact_key = f'projects/{pid}/versions/{vid}/review'
    image = 'arm-cluster-master:5000/ppt-hermes-worker@sha256:' + 'a' * 64
    raw_manifest_hash = 'b' * 64
    master_commit = 'c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7'
    session_id = 'synthetic-review-session'
    with app.state.sessions.begin() as session:
        project = Project(id=pid, issuer=owner.issuer, subject=owner.sub, title='Synthetic review',
                          external_model_ack=True, settings={'page_count': 3}, revision=8,
                          stage='awaiting_review')
        session.add(project)
        session.flush()
        snapshot = Repository(session).snapshot(project)
        source_rows = []
        for key, data in [('deck.pptx', b'PK\x03\x04synthetic-pptx'),
                          ('design_spec.md', b'Synthetic design, not evidence'),
                          ('spec_lock.md', b'Synthetic lock')]:
            row = put(cp_key + '/project/deck/' + key, data)
            source_rows.append(dict(row, path=key))
        svgs = []
        for ordinal in range(1, 4):
            key = f'svg_output/{ordinal:02d}.svg'
            row = put(cp_key + '/project/deck/' + key, f'<svg>Synthetic {ordinal}</svg>'.encode())
            source_rows.append(dict(row, path=key))
            svgs.append(dict(row, path=key, ordinal=ordinal, png_width=1280, png_height=720))
        source_rows.sort(key=lambda row: row['path'])
        source_fp = fingerprint(source_rows)
        pptx = next(row for row in source_rows if row['path'] == 'deck.pptx')
        source = {'project_root': 'deck', 'source_manifest_sha256': raw_manifest_hash,
                  'input_snapshot_sha256': snapshot['hash'], 'copied_source_fingerprint': source_fp,
                  'pptx': pptx, 'svg_sources': svgs}
        put(cp_key + '/home/state.db', b'Synthetic session database bytes')
        indexes = {name: {'files': rows(cp_key + '/' + name),
                          'bytes': sum(row['bytes'] for row in rows(cp_key + '/' + name))}
                   for name in ('project', 'home')}
        meta = {'schema_version': 1, 'status': 'review', 'eligible_as_revision_base': False,
                'session_id': session_id, 'input_snapshot_sha256': snapshot['hash'],
                'project_root': 'deck', **indexes}
        put(cp_key + '/checkpoint.json', meta)
        artifacts, pages, slides = [], [], []
        pptx_row = put(evidence_key + '/delivery/review-artifacts/presentation.pptx', b'PK\x03\x04synthetic-pptx')
        artifacts.append(dict(pptx_row, path='review-artifacts/presentation.pptx', role='pptx'))
        put(artifact_key + '/presentation.pptx', b'PK\x03\x04synthetic-pptx')
        for ordinal, svg in enumerate(svgs, 1):
            data = b'\x89PNG\r\n\x1a\n' + f'Synthetic PNG {ordinal}'.encode()
            row = put(evidence_key + f'/delivery/review-artifacts/slides/{ordinal}.png', data)
            artifacts.append(dict(row, path=f'review-artifacts/slides/{ordinal}.png',
                                  ordinal=ordinal, role='preview'))
            put(artifact_key + f'/{ordinal}.png', data)
            pages.append({'number': ordinal, 'preview_sha256': row['sha256'], 'source_sha256': svg['sha256']})
            slides.append({'preview_key': artifact_key + f'/{ordinal}.png',
                           'preview_sha256': row['sha256'], 'source_sha256': svg['sha256'],
                           'title': f'Synthetic page {ordinal}'})
        report = {'source': source, 'controller_nonce': nonce}
        for name in ('programmatic', 'postflight'):
            put(evidence_key + f'/delivery/{name}/{name}-evidence.json', report)
        retained = rows(evidence_key + '/delivery')
        receipt = {'schema': SCHEMA, 'status': 'review', 'engine': 'hermes', 'delivery_kind': 'review_candidate',
                   'original_candidate_status': 'candidate', 'review_artifacts_ready': True,
                   'accepted': False, 'published': False, 'publication_authorization': False,
                   'revision_base_eligible': False, 'full_exporter_postflight_verified': False,
                   'independent_visual_review': False, 'required_pending_gates': sorted(PENDING),
                   'completed_programmatic_gates': sorted(COMPLETED), 'execution_image': image,
                   'ppt_master_commit': master_commit, 'controller_nonce': nonce,
                   'source_manifest_sha256': raw_manifest_hash, 'source_fingerprint_sha256': source_fp,
                   'input_snapshot_sha256': snapshot['hash'], 'job_id': jid, 'attempt': 1,
                   'session_id': session_id, 'phase': 'generate', 'source': source,
                   'retained_files': retained, 'retained_bytes': sum(row['bytes'] for row in retained),
                   'artifacts': artifacts, 'pages': pages, 'native_chart_count': 0,
                   'programmatic_report_sha256': fingerprint(report), 'postflight_report_sha256': fingerprint(report)}
        receipt_row = put(evidence_key + '/delivery/candidate-delivery.json', receipt)
        transport_rows = rows(evidence_key)
        transport = {'schema_version': 1, 'controller_nonce': nonce, 'source_manifest_sha256': raw_manifest_hash,
                     'execution_image': image, 'transport_verified': True, 'writers_stopped': True,
                     'sandbox_exit_code': 0, 'controller_error_code': None, 'pod_uid': str(uuid.uuid4()),
                     'files': transport_rows, 'bytes': sum(row['bytes'] for row in transport_rows)}
        put(evidence_key + '/transport-manifest.json', transport)
        public = {'status': 'review', 'publication_authorization': False, 'eligible_as_revision_base': False,
                  'pptx_key': artifact_key + '/presentation.pptx', 'pptx_sha256': pptx['sha256'],
                  'slides': slides, 'required_pending_gates': sorted(PENDING),
                  'review_evidence': {'storage_key': evidence_key, 'receipt_sha256': receipt_row['sha256'],
                                      'controller_nonce': nonce, 'execution_image': image,
                                      'source_manifest_sha256': raw_manifest_hash,
                                      'source_fingerprint_sha256': source_fp}}
        checkpoint = Checkpoint(id=cid, project_id=pid, issuer=owner.issuer, subject=owner.sub,
                                storage_key=cp_key, manifest=meta)
        job = Job(id=jid, project_id=pid, issuer=owner.issuer, subject=owner.sub, kind='generate',
                  stage='review', status='succeeded', stopped=True, input_snapshot=snapshot,
                  session_id=session_id, attempt=1)
        session.add_all([checkpoint, job])
        session.flush()
        session.add(Version(id=vid, project_id=pid, issuer=owner.issuer, subject=owner.sub,
                            job_id=jid, checkpoint_id=cid, status='review', artifact_root=artifact_key,
                            manifest=public))
    return {'pid': pid, 'vid': vid, 'jid': jid, 'cid': cid, 'nonce': nonce,
            'cp_key': cp_key, 'artifact_key': artifact_key, 'evidence_key': evidence_key}
