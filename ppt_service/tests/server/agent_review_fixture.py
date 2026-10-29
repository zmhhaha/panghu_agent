"""Synthetic portable review provenance, never a real acceptance certificate."""
import hashlib
import json
from uuid import uuid4

from ppt_service.control.agent_connector import _confirmation
from ppt_service.server.models import AgentOutbox, Asset, Checkpoint, Job, Plan, Project, Version, now
from ppt_service.server.repository import Repository
from ppt_service.server.acceptance.resolver import AGENT_COMPLETED, AGENT_PENDING, fingerprint


def seed_agent_review(app, owner, engine='pi'):
    root = app.state.storage.root
    pid, vid, jid, cid, remote_id, attempt_id, nonce, validation_id = (str(uuid4()) for _ in range(8))
    fence = 19

    def put(key, value):
        raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return {'path': key, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}

    def tree(key):
        return [{'path': path.relative_to(root / key).as_posix(), 'bytes': path.stat().st_size,
                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted((root / key).rglob('*')) if path.is_file()]

    cp_key = f'projects/{pid}/checkpoints/{cid}'
    source_key = f'projects/{pid}/agent-results/{jid}/{fence}'
    artifact_key = f'projects/{pid}/versions/{vid}/review'
    pins = {'engine': engine, 'profile': 'ppt.generate.v1', 'profile_revision': 'v1',
            'runtime_revision': 'synthetic-runtime', 'image_digest': 'sha256:' + 'a' * 64}
    validator_image = 'synthetic/ppt-validator@sha256:' + 'b' * 64
    with app.state.sessions.begin() as session:
        project = Project(id=pid, issuer=owner.issuer, subject=owner.sub, title='Portable review fixture',
                          settings={'page_count': 3}, external_model_ack=True, revision=8, stage='awaiting_review')
        session.add(project)
        session.flush()
        aid = str(uuid4())
        asset_key = f'projects/{pid}/assets/{aid}/original'
        asset = put(asset_key, b'# Synthetic public input')
        session.add(Asset(id=aid, project_id=pid, storage_key=asset_key, display_name='source.md',
                          media_type='text/markdown', bytes=asset['bytes'], sha256=asset['sha256'], parse_status='ready'))
        session.flush()
        snapshot = Repository(session).snapshot(project)
        source_rows = []
        for name, raw in [('presentation.pptx', b'PK\x03\x04synthetic deck'),
                          ('design_spec.md', b'Synthetic approved design'), ('spec_lock.md', b'Synthetic approved lock')]:
            row = put(source_key + '/project/deck/' + name, raw)
            source_rows.append(dict(row, path=name))
            put(cp_key + '/project/deck/' + name, raw)
        svgs, manifest_slides, pngs = [], [], []
        for n in range(1, 4):
            name = f'svg_output/{n:02d}.svg'
            raw = f'<svg>Synthetic page {n}</svg>'.encode()
            row = put(source_key + '/project/deck/' + name, raw)
            source_rows.append(dict(row, path=name))
            put(cp_key + '/project/deck/' + name, raw)
            svgs.append(dict(row, path=name, ordinal=n, png_width=1280, png_height=720))
            manifest_slides.append({'id': f'slide-{n}', 'ordinal': n, 'title': f'Page {n}', 'svg_path': name,
                                    'preview_path': f'previews/{n}.png', 'source_refs': [], 'native_chart_keys': []})
        source_rows.sort(key=lambda row: row['path'])
        cp_rows = [dict(row, path='deck/' + row['path']) for row in source_rows]
        cp = {'schema_version': 'ppt-artifact-checkpoint.v1',
              'ppt_master_revision': 'c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7',
              'project_id': pid, 'source_business_job_id': jid, 'source_job_id': remote_id,
              'source_attempt_id': attempt_id, 'phase': 'generate', 'input_snapshot_sha256': snapshot['hash'],
              'project_root': 'deck', 'files': cp_rows, 'total_bytes': sum(row['bytes'] for row in cp_rows),
              'publication_authorized': False}
        cp['sha256'] = fingerprint(cp)
        for prefix in (source_key, cp_key):
            put(prefix + '/result/checkpoint.json', cp)
        manifest = {'schema_version': 1, 'phase': 'generate', 'status': 'candidate',
                    'input_snapshot_sha256': snapshot['hash'], 'project_root': 'deck',
                    'slides': manifest_slides, 'plan': {}, 'questions': [], 'quality': {}, 'provenance': {},
                    'artifacts': [dict(row, role='pptx' if row['path'].endswith('.pptx') else
                                       'svg' if row['path'].endswith('.svg') else 'specification',
                                       media_type='application/octet-stream') for row in source_rows]}
        source_hash = put(source_key + '/result/manifest.json', manifest)['sha256']
        evidence_key = f'projects/{pid}/agent-results/{jid}/agent-validation-{fence}-{source_hash[:16]}'
        programmatic_key = evidence_key + '/independent/' + validation_id
        public_slides = []
        for n, svg in enumerate(svgs, 1):
            relative = f'scratch-projects/deck/.preview/{n}.png'
            raw = b'\x89PNG\r\n\x1a\n' + f'Synthetic preview {n}'.encode()
            row = put(programmatic_key + '/' + relative, raw)
            pngs.append(dict(row, path=f'{n}.png', relative_path=relative, ordinal=n,
                             svg_path=svg['path'], svg_sha256=svg['sha256'], width=1280, height=720))
            put(artifact_key + f'/{n}.png', raw)
            public_slides.append({'preview_key': artifact_key + f'/{n}.png', 'title': f'Page {n}',
                                  'preview_sha256': row['sha256'], 'source_sha256': svg['sha256']})
        pptx = next(row for row in source_rows if row['path'] == 'presentation.pptx')
        put(artifact_key + '/presentation.pptx', b'PK\x03\x04synthetic deck')
        source = {'project_root': 'deck', 'source_manifest_sha256': source_hash,
                  'input_snapshot_sha256': snapshot['hash'], 'copied_source_fingerprint': fingerprint(source_rows),
                  'pptx': pptx, 'svg_sources': svgs, 'expected_charts_canonical_sha256': fingerprint([])}
        programmatic = {'schema_version': 1, 'issuer': 'ppt-service-independent-programmatic-runtime-v1',
            'validation_id': validation_id, 'controller_nonce': nonce, 'source': source, 'pngs': pngs,
            'preview_group_stopped': True, 'command_groups_stopped': True, 'render_exit_code': 0,
            'delivery_exit_code': 0, 'independent_visual_review': False, 'full_exporter_postflight_verified': False,
            'completed_programmatic_gates': sorted(AGENT_COMPLETED),
            'native': {'chart_count': 0, 'slide_count': 3, 'chart_data_verified': True}}
        put(programmatic_key + '/programmatic-evidence.json', programmatic)
        put(programmatic_key + '/source-binding.json', source)
        put(programmatic_key + '/native-inspection.json', programmatic['native'])
        report = {'phase': 'generate', 'input_snapshot_sha256': snapshot['hash'], 'source_manifest_sha256': source_hash,
            'validation_nonce': nonce, 'publication_authorized': False, 'manifest': manifest, 'status': 'candidate',
            'accepted': False, 'programmatic_checks_passed': True, 'evidence': {'official_gates_verified': True},
            'required_pending_gates': sorted(AGENT_PENDING), 'independent_programmatic_evidence': programmatic}
        report_hash = put(evidence_key + '/validated.json', report)['sha256']
        retained = tree(evidence_key)
        put(evidence_key + '/transport-manifest.json', {'schema_version': 'ppt-agent-validation-transport.v1',
            'source_manifest_sha256': source_hash, 'validation_nonce': nonce, 'sandbox_pod_uid': str(uuid4()),
            'sandbox_exit_code': 0, 'writers_stopped': True, 'transport_verified': True,
            'execution_image': validator_image, 'validation_request_sha256': 'c' * 64,
            'files': retained, 'bytes': sum(row['bytes'] for row in retained)})
        business_ref = f'ppt:{jid}:1:{fence}'
        remote = {'job_id': remote_id, 'business_ref': business_ref, 'pins': pins, 'state': 'completed',
            'attempt': {'attempt_id': attempt_id, 'fence': 2},
            'stop_proof': {'attempt_id': attempt_id, 'fence': 2, 'stop_proven': True, 'resource_ids': ['synthetic-pod']}}
        remote_hash = put(source_key + '/remote-job.json', {'remote': remote, 'artifact_manifest_sha256': 'd' * 64})['sha256']
        service_ref = {'checkpoint_id': str(uuid4()), 'sha256': 'e' * 64, 'format': 'workspace-tree.v1'}
        meta = {'schema_version': 1, 'checkpoint_format': 'ppt-agent-artifacts-v1', 'agent_checkpoint': cp,
            'service_checkpoint_ref': service_ref, 'project_root': 'deck', 'input_snapshot_sha256': snapshot['hash'],
            'status': 'review', 'eligible_as_revision_base': False,
            'agent_source': {'storage_key': source_key, 'remote_job_sha256': remote_hash, 'artifact_manifest_sha256': 'd' * 64}}
        checkpoint = Checkpoint(id=cid, project_id=pid, issuer=owner.issuer, subject=owner.sub,
                                storage_key=cp_key, manifest=meta)
        session.add(checkpoint)
        session.flush()
        plans = []
        for n, phase in enumerate(('plan_initial', 'plan_options'), 1):
            outline = {'pages': [{'ordinal': i, 'title': f'Page {i}', 'purpose': 'Explain the source'} for i in range(1, 4)]}
            design = {'directions': [{'id': 'A', 'name': 'Source guide', 'page_plan': outline,
                       'production_parameters': {'language': 'zh-CN', 'aspect_ratio': '16:9'}}]}
            plan = Plan(id=str(uuid4()), project_id=pid, revision=n, phase=phase, checkpoint_id=cid,
                        input_snapshot_hash=snapshot['hash'], outline=outline, design=design,
                        confirmed_at=now().replace(tzinfo=None), confirmation_selection=
                        {'direction_id': 'A'} if n == 2 else {'design_mode': 'free_design', 'template_id': None})
            session.add(plan)
            plans.append(plan)
        session.flush()
        confirmations = {'stage' + str(n): _confirmation(plan) for n, plan in enumerate(plans, 1)}
        meta = dict(meta, confirmation_plan_ids={key: value['plan_id'] for key, value in confirmations.items()})
        checkpoint.manifest = meta
        put(cp_key + '/checkpoint.json', meta)
        public = {'status': 'review', 'publication_authorization': False, 'eligible_as_revision_base': False,
            'pptx_key': artifact_key + '/presentation.pptx', 'pptx_sha256': pptx['sha256'], 'slides': public_slides,
            'required_pending_gates': sorted(AGENT_PENDING), 'review_evidence': {'kind': 'agent-validation.v1',
                'storage_key': evidence_key, 'receipt_sha256': report_hash, 'controller_nonce': nonce,
                'execution_image': validator_image, 'source_manifest_sha256': source_hash,
                'source_fingerprint_sha256': source['copied_source_fingerprint']}}
        session.add(Job(id=jid, project_id=pid, issuer=owner.issuer, subject=owner.sub, kind='generate',
                        stage='review', status='succeeded', input_snapshot=snapshot, attempt=1,
                        fencing_token=fence, stopped=True, run_id=remote_id))
        session.flush()
        session.add(Version(id=vid, project_id=pid, issuer=owner.issuer, subject=owner.sub, job_id=jid,
                            checkpoint_id=cid, status='review', manifest=public, artifact_root=artifact_key))
        task = {'schema_version': 'ppt-generate.v1', 'phase': 'generate', 'business_job_id': jid, 'project_id': pid,
                'input_snapshot_sha256': snapshot['hash'], 'input_snapshot': {key: snapshot[key]
                    for key in ('project_id', 'title', 'settings', 'assets')}, 'confirmations': confirmations}
        session.add(AgentOutbox(job_id=jid, attempt=1, fencing_token=fence, business_ref=business_ref,
            idempotency_key=business_ref, request={'task': task, 'business_ref': business_ref,
                'profile': 'ppt.generate.v1', 'profile_revision': 'v1'}, remote_job_id=remote_id,
            remote_pins=pins, checkpoint_ref=service_ref, state='mapped',
            result={'status': 'review', 'version_id': vid, 'artifact_manifest_sha256': 'd' * 64}))
    return {'pid': pid, 'vid': vid, 'jid': jid, 'cid': cid, 'cp_key': cp_key, 'source_key': source_key,
            'evidence_key': evidence_key, 'programmatic_key': programmatic_key, 'artifact_key': artifact_key,
            'plan_ids': {key: value['plan_id'] for key, value in confirmations.items()}, 'pins': pins}
