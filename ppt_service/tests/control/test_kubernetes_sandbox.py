import hashlib
from dataclasses import replace
import json
from pathlib import Path
import uuid

import pytest

from ppt_service.control.execution import ControlError
from ppt_service.control.kubernetes import FINALIZER, KubernetesParseScopes, ROLE
from ppt_service.control.kubernetes_sandbox import KubernetesSandbox
from ppt_service.control.parsing import parse_inputs
from ppt_service.control.staging import atomic_json
from ppt_service.control import hermes_delivery
from ppt_service.control.render_validation import fingerprint, hash_file, RASTER_GATE, DELIVERY_GATE, NATIVE_GATE
from ppt_service.control.postflight_validation import PENDING_GATES, SOURCE_GATE
from ppt_service.control.execution import PINNED_WORKER, PPT_MASTER_COMMIT
from ppt_service.control.publisher import complete_index

from test_kubernetes import runtime, staged


def source_mount(runtime, obj, target):
    config, settings, api, backend, attempt = runtime
    row = next(m for m in obj['spec']['containers'][0]['volumeMounts'] if m['mountPath'] == target)
    prefix = settings.pvc_data_prefix + '/'
    assert row['subPath'].startswith(prefix)
    return config.data_root / row['subPath'][len(prefix):]


def install_parser(runtime):
    config, settings, api, backend, attempt = runtime
    def on_pod(obj):
        if obj['metadata']['labels'][ROLE] != 'sandbox':
            return
        inputs = source_mount(runtime, obj, '/inputs')
        output = source_mount(runtime, obj, '/output')
        atomic_json(output / 'parsed.json', parse_inputs(inputs, json.loads((inputs / 'inventory.json').read_text())))
        api.terminal(obj)
    api.on_pod = on_pod


def material(runtime):
    config, settings, api, backend, attempt = runtime
    root = config.data_root / 'material'
    root.mkdir()
    content = b'# Public material\nA source for the presentation.'
    (root / 'public.md').write_bytes(content)
    inventory = {'schema_version': 1, 'input_snapshot_sha256': 'b' * 64,
                 'sources': [{'id': str(uuid.uuid4()), 'path': 'public.md',
                              'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                              'media_type': 'text/markdown'}]}
    atomic_json(root / 'inventory.json', inventory)
    return root, inventory


def candidate(runtime):
    config, settings, api, backend, attempt = runtime
    snapshot = config.data_root / 'snapshots' / 'sample'
    (snapshot / 'result').mkdir(parents=True)
    (snapshot / 'project' / 'deck' / 'svg_output').mkdir(parents=True)
    for number in range(1, 4):
        (snapshot / 'project' / 'deck' / 'svg_output' / f'slide_{number:02}.svg').write_text('<svg/>')
    atomic_json(snapshot / 'result' / 'manifest.json', {
        'phase': 'generate', 'status': 'candidate', 'input_snapshot_sha256': 'b' * 64,
        'project_root': 'deck', 'slides': [{'ordinal': n, 'svg_path': f'svg_output/slide_{n:02}.svg'} for n in range(1, 4)]})
    return snapshot, {'job_id': attempt.job_id, 'attempt': 1, 'phase': 'generate', 'pages': 3,
                      'session_id': 'ppt-session', 'input_snapshot_sha256': 'b' * 64, 'expected_charts': [],
                      'trusted_oracle': {'expected_charts': [], 'source_sha256': []}, 'authorized_source_sha256': []}


def stopped_job(runtime):
    config, settings, api, backend, attempt = runtime
    h = backend.create(replace(attempt, phase='generate'), staged(runtime))
    backend.stop(h)
    return h


def transport_delivery(output, request):
    """API transport fixture only: real render/OOXML checks have a separate suite."""
    root = output / 'delivery'
    root.mkdir()
    deck = root / 'review-artifacts' / 'presentation.pptx'
    deck.parent.mkdir()
    deck.write_bytes(b'PK\x03\x04offline transport sample')
    artifacts = [{**hash_file(root, 'review-artifacts/presentation.pptx'), 'role': 'pptx',
                  'media_type': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}]
    pages, svgs = [], []
    from PIL import Image
    for number in range(1, request['pages'] + 1):
        name = f'review-artifacts/slides/{number}.png'
        path = root / name
        path.parent.mkdir(exist_ok=True)
        Image.new('RGB', (10, 10), 'white').save(path)
        row = hash_file(root, name)
        artifacts.append({**row, 'role': 'preview', 'media_type': 'image/png', 'ordinal': number})
        svgs.append({'ordinal': number, 'sha256': 'c' * 64, 'png_width': 10, 'png_height': 10})
        pages.append({'number': number, 'preview_path': name, 'preview_sha256': row['sha256'],
                      'source_sha256': 'c' * 64, 'width': 10, 'height': 10})
    binding = {'pptx': artifacts[0], 'source_manifest_sha256': request['source_manifest_sha256'],
               'input_snapshot_sha256': request['input_snapshot_sha256'], 'svg_sources': svgs,
               'copied_source_fingerprint': 'd' * 64}
    report = {'source': binding, 'controller_nonce': request['validation_nonce']}
    atomic_json(root / 'programmatic' / 'programmatic-evidence.json', report)
    atomic_json(root / 'postflight' / 'postflight-evidence.json', report)
    inventory, total = hermes_delivery._inventory(root)
    return {'schema': hermes_delivery.SCHEMA, 'engine': 'hermes', 'status': 'review',
            'delivery_kind': 'review_candidate', 'validation_status': 'gate_pending',
            'original_candidate_status': 'candidate', 'review_artifacts_ready': True,
            'stop_proof_requires_controller': True, 'powerpoint_acceptance': 'pending',
            **dict.fromkeys(('accepted', 'published', 'publication_authorization', 'transport_verified',
                             'independent_visual_review', 'full_exporter_postflight_verified', 'revision_base_eligible'), False),
            **{key: request[key] for key in ('job_id', 'attempt', 'phase', 'session_id',
                                            'input_snapshot_sha256', 'source_manifest_sha256')},
            'controller_nonce': request['validation_nonce'], 'worker_image': PINNED_WORKER,
            'execution_image': request['execution_image'],
            'ppt_master_commit': PPT_MASTER_COMMIT, 'required_pending_gates': list(PENDING_GATES),
            'completed_programmatic_gates': [RASTER_GATE, DELIVERY_GATE, NATIVE_GATE, SOURCE_GATE],
            'native_chart_count': 0, 'retained_files': inventory, 'retained_bytes': total,
            'artifacts': artifacts, 'pages': pages, 'source': binding, 'source_fingerprint_sha256': 'd' * 64,
            'artifact_bytes': sum(row['bytes'] for row in artifacts),
            'programmatic_report_sha256': fingerprint(report), 'postflight_report_sha256': fingerprint(report)}


def install_validator(runtime, *, mutate=None):
    config, settings, api, backend, attempt = runtime
    def on_pod(obj):
        if obj['metadata']['labels'][ROLE] != 'sandbox':
            return
        request = json.loads((source_mount(runtime, obj, '/request') / 'request.json').read_text())
        output = source_mount(runtime, obj, '/output')
        review = obj['spec']['containers'][0]['args'] == ['-m', 'ppt_service.control.hermes_delivery']
        if review:
            result = transport_delivery(output, request)
            if mutate:
                mutate(result, output, request)
            atomic_json(output / 'delivery' / 'candidate-delivery.json', result)
            api.terminal(obj)
            return
        result = {'status': 'candidate', 'accepted': False, 'programmatic_checks_passed': True,
                  'validation_status': 'gate_pending', 'error_code': 'independent_acceptance_required',
                  'required_pending_gates': ['independent_visual_review', 'independent_postflight_and_delivery'],
                  'validation_nonce': request['validation_nonce'],
                  'source_manifest_sha256': request['source_manifest_sha256']}
        if mutate:
            mutate(result, output, request)
        atomic_json(output / 'validated.json', result)
        api.terminal(obj)
    api.on_pod = on_pod


def test_material_parse_uses_offline_pod_and_exact_scope_proof(runtime):
    config, settings, api, backend, attempt = runtime
    install_parser(runtime)
    inputs, inventory = material(runtime)
    scopes = KubernetesParseScopes(backend)
    uid = str(uuid.uuid4())
    assert not scopes.prove_stopped(uid)
    scopes.ensure_scope(uid)
    sandbox = KubernetesSandbox(config, settings, backend)
    sandbox.scope_uid = uid
    result = sandbox.parse(inputs, inventory)
    assert result['sources'][0]['parse_status'] == 'succeeded'
    assert 'Public material' in result['sources'][0]['text']
    assert scopes.prove_stopped(uid)
    pod = next(call[3] for call in api.calls if call[:2] == ('POST', 'pods'))
    assert pod['metadata']['labels']['ppt.panghuer.top/attempt'] == uid
    assert pod['spec']['containers'][0]['args'] == ['-m', 'ppt_service.control.parsing']
    assert pod['spec']['containers'][0]['env'] == [
        {'name': 'PPT_RUNTIME_WORKER_IMAGE', 'value': settings.worker_image}]
    mounts = {m['mountPath']: m for m in pod['spec']['containers'][0]['volumeMounts']}
    assert mounts['/inputs']['readOnly'] and mounts['/request']['readOnly']
    assert not mounts['/output']['readOnly']
    assert not any(call[1] == 'secrets' for call in api.calls)
    assert not api.objects
    assert scopes.stop_and_prove(uid)


def test_validation_persists_stopped_outputs_without_granting_publication(runtime):
    config, settings, api, backend, attempt = runtime
    install_validator(runtime)
    h = stopped_job(runtime)
    sandbox = KubernetesSandbox(config, settings, backend)
    sandbox.scope_uid = h.uid
    snapshot, request = candidate(runtime)
    result = sandbox.validate(snapshot, request)
    assert result['accepted'] is False
    assert result['review_candidate'] is True
    assert result['manifest'] == complete_index(snapshot, json.loads((snapshot / 'result' / 'manifest.json').read_text()))[0]
    descriptor = result['persistent_evidence']
    assert descriptor['transport_verified'] is True and descriptor['publication_authorization'] is False
    assert descriptor['execution_image'] == settings.worker_image
    retained = Path(descriptor['path'])
    assert retained.is_relative_to(snapshot.parent / 'validation-evidence')
    transport = json.loads((retained / 'transport-manifest.json').read_text())
    assert transport['writers_stopped'] and transport['pod_uid'].startswith('api-uid-')
    assert transport['sandbox_exit_code'] == 0
    assert transport['execution_image'] == settings.worker_image
    raw_result = json.loads((retained / 'delivery' / 'candidate-delivery.json').read_text())
    assert 'persistent_evidence' not in raw_result  # Controller adds transport only after stop.
    assert not any(kind == 'pods' and obj['metadata']['labels'][ROLE] == 'sandbox'
                   for (kind, _), obj in api.objects.items())
    assert backend.prove_stopped(h).all_writers_stopped
    backend.cleanup(h)
    assert not api.objects


def test_review_transport_completes_undeclared_source_index(runtime):
    config, settings, api, backend, attempt = runtime
    install_validator(runtime)
    h = stopped_job(runtime)
    sandbox = KubernetesSandbox(config, settings, backend)
    sandbox.scope_uid = h.uid
    snapshot, request = candidate(runtime)
    (snapshot / 'project' / 'deck' / 'extra-source.md').write_text('Undeclared source retained for review audit.')
    result = sandbox.validate(snapshot, request)
    original = json.loads((snapshot / 'result' / 'manifest.json').read_text())
    assert 'artifacts' not in original
    assert result['manifest'] == complete_index(snapshot, original)[0]
    assert any(row['path'] == 'extra-source.md' and row['role'] == 'source' for row in result['manifest']['artifacts'])
    assert result['accepted'] is False


@pytest.mark.parametrize('reported_code, expected_code', [
    ('stage1_contract_invalid', 'stage1_contract_invalid'),
    ('stage2_contract_invalid', 'stage2_contract_invalid'),
    ('quality_gate_failed', 'quality_gate_failed'),
    ('/private/path?token=SECRET', 'validation_execution_failed'),
    ('UPSTREAM_SECRET', 'validation_execution_failed'),
    ('invalid\ncode', 'validation_execution_failed'),
    ('x' * 81, 'validation_execution_failed'),
    (1, 'validation_execution_failed'),
    (None, 'validation_execution_failed'),
])
def test_planning_failure_transport_preserves_bounded_error_codes_with_stage_numbers(
        runtime, reported_code, expected_code):
    config, settings, api, backend, attempt = runtime
    h = backend.create(attempt, staged(runtime))
    backend.stop(h)
    sandbox = KubernetesSandbox(config, settings, backend)
    sandbox.scope_uid = h.uid
    snapshot, request = candidate(runtime)
    request['phase'] = 'plan_initial'
    manifest_path = snapshot / 'result' / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['phase'] = 'plan_initial'
    manifest['status'] = 'awaiting_confirmation'
    atomic_json(manifest_path, manifest)

    def on_pod(obj):
        if obj['metadata']['labels'][ROLE] != 'sandbox':
            return
        envelope = json.loads((source_mount(runtime, obj, '/request') / 'request.json').read_text())
        output = source_mount(runtime, obj, '/output')
        atomic_json(output / 'validated.json', {
            'accepted': False, 'error_code': reported_code,
            'validation_nonce': envelope['validation_nonce'],
            'source_manifest_sha256': envelope['source_manifest_sha256'],
        })
        api.terminal(obj, exit_code=2)

    api.on_pod = on_pod
    with pytest.raises(ControlError) as raised:
        sandbox.validate(snapshot, request)
    assert raised.value.code == expected_code
    evidence = Path(raised.value.evidence_path)
    transport = json.loads((evidence / 'transport-manifest.json').read_text())
    assert transport['controller_error_code'] == expected_code
    assert transport['sandbox_exit_code'] == 2
    assert transport['writers_stopped'] is True
    assert transport['transport_verified'] is False
    assert [row['path'] for row in transport['files']] == ['validated.json']
    # Preserve the raw rejected result privately, without using it to publish.
    assert json.loads((evidence / 'validated.json').read_text())['error_code'] == reported_code
    assert backend.prove_stopped(h).all_writers_stopped
    backend.cleanup(h)
    assert not api.objects


@pytest.mark.parametrize('fault', ['nonce', 'source', 'accepted', 'gates', 'png', 'image'])
def test_validation_rejects_unbound_or_forged_output(runtime, fault):
    config, settings, api, backend, attempt = runtime
    def mutate(result, output, request):
        if fault == 'nonce':
            result['controller_nonce'] = str(uuid.uuid4())
        elif fault == 'source':
            result['source_manifest_sha256'] = '0' * 64
        elif fault == 'accepted':
            result['accepted'] = True
        elif fault == 'gates':
            result['required_pending_gates'] = []
        elif fault == 'png':
            result['pages'][0]['preview_sha256'] = '0' * 64
        elif fault == 'image':
            result['execution_image'] = 'registry.internal:5000/ppt-worker@sha256:' + 'f' * 64
    install_validator(runtime, mutate=mutate)
    h = stopped_job(runtime)
    sandbox = KubernetesSandbox(config, settings, backend)
    sandbox.scope_uid = h.uid
    snapshot, request = candidate(runtime)
    with pytest.raises(ControlError) as raised:
        sandbox.validate(snapshot, request)
    evidence = Path(raised.value.evidence_path)
    assert evidence.is_dir()
    receipt = json.loads((evidence / 'transport-manifest.json').read_text())
    assert receipt['transport_verified'] is False and receipt['writers_stopped']
    assert not any(kind == 'pods' and obj['metadata']['labels'][ROLE] == 'sandbox'
                   for (kind, _), obj in api.objects.items())


def test_unstopped_sandbox_is_retained_for_recovery(runtime):
    config, settings, api, backend, attempt = runtime
    h = stopped_job(runtime)
    sandbox = KubernetesSandbox(config, settings, backend)
    sandbox.scope_uid = h.uid
    api.terminate_on_delete = False
    snapshot, request = candidate(runtime)
    with pytest.raises(ControlError, match='validation_writers_not_stopped') as raised:
        sandbox.validate(snapshot, request)
    root = Path(raised.value.evidence_path)
    assert (root / 'controller-quarantine.json').exists()
    assert (root / 'output').exists()
    assert not backend.prove_stopped(h).all_writers_stopped
    pod = next(obj for (kind, _), obj in api.objects.items() if kind == 'pods')
    assert FINALIZER in pod['metadata']['finalizers']
    assert not any(call[0] == 'PATCH' and call[2].startswith('ppt-sandbox-') for call in api.calls)


def test_scope_recovery_never_creates_authority_for_missing_scope(runtime):
    config, settings, api, backend, attempt = runtime
    scopes = KubernetesParseScopes(backend)
    uid = str(uuid.uuid4())
    assert not scopes.stop_and_prove(uid)
    assert not backend._path(uid).exists()


def test_rejected_hardlink_output_cleanup_preserves_original_mode(runtime):
    import os
    import stat
    config, settings, api, backend, attempt = runtime
    original = config.data_root / 'private-original'
    original.write_bytes(b'unchanged private source')
    original.chmod(0o600)
    mode = stat.S_IMODE(original.stat().st_mode)
    def mutate(result, output, request):
        os.link(original, output / 'linked-output')
    install_validator(runtime, mutate=mutate)
    h = stopped_job(runtime)
    sandbox = KubernetesSandbox(config, settings, backend)
    sandbox.scope_uid = h.uid
    snapshot, request = candidate(runtime)
    with pytest.raises(ControlError):
        sandbox.validate(snapshot, request)
    assert original.read_bytes() == b'unchanged private source'
    assert stat.S_IMODE(original.stat().st_mode) == mode
