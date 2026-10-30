"""Independent validator boundaries; synthetic transport never proves live acceptance."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import canonical_bytes, digest
from agent_service.deploy.acceptance_probe import fixture
from agent_service.profiles.ppt import (
    CHECKPOINT_SCHEMA, PPT_MASTER_REVISION, _manifest_example, checkpoint_descriptor,
)
from ppt_service.control.agent_validation import (
    AgentKubernetesSandbox, AgentValidationFailure, TRANSPORT_SCHEMA, validate_private_candidate,
)
from ppt_service.control.execution import ControlError
from ppt_service.control.render_validation import DELIVERY_GATE, NATIVE_GATE, RASTER_GATE


IMAGE = 'registry.fixture/ppt-validator@sha256:' + 'a' * 64


def put(root, key, raw):
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {'path': key, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def json_file(root, key, value):
    return put(root, key, canonical_bytes(value))


@pytest.fixture
def candidate(tmp_path):
    task, _ = fixture('ppt.plan_initial.v1')
    snapshot = tmp_path / 'snapshot'
    manifest = _manifest_example(task)
    row = put(snapshot / 'project/deck', 'planning-notes.md', b'# Synthetic proposal\n')
    manifest['artifacts'] = [dict(row, role='planning', media_type='text/markdown')]
    raw = canonical_bytes(manifest)
    put(snapshot, 'result/manifest.json', raw)
    checkpoint = dict(schema_version=CHECKPOINT_SCHEMA, ppt_master_revision=PPT_MASTER_REVISION,
        project_id=task['project_id'], source_business_job_id=task['business_job_id'],
        source_job_id=str(uuid4()), source_attempt_id=str(uuid4()), phase=task['phase'],
        input_snapshot_sha256=task['input_snapshot_sha256'], project_root='deck',
        files=[dict(row, path='deck/' + row['path'])], total_bytes=row['bytes'], publication_authorized=False)
    checkpoint['sha256'] = digest(checkpoint)
    json_file(snapshot, 'result/checkpoint.json', checkpoint)
    request = dict(execution_contract='ppt-agent.v1', task=task, phase=task['phase'],
        job_id=task['business_job_id'], input_snapshot_sha256=task['input_snapshot_sha256'], pages=3)
    source = hashlib.sha256(raw).hexdigest()
    report = validate_private_candidate(snapshot, dict(request,
        source_manifest_sha256=source, validation_nonce=str(uuid4())))
    return SimpleNamespace(snapshot=snapshot, request=request, source=source, report=report,
                           manifest=manifest, checkpoint=checkpoint, root=tmp_path)


def transport(case, report=None):
    """Frozen transport double; no Kubernetes Pod or model is launched."""
    report = copy.deepcopy(case.report if report is None else report)
    final = case.root / 'independent-validator'
    final.mkdir(exist_ok=True)
    rows = [json_file(final, 'validated.json', report)]
    body = dict(schema_version=TRANSPORT_SCHEMA, source_manifest_sha256=case.source,
        validation_nonce=report['validation_nonce'], sandbox_pod_uid=str(uuid4()),
        execution_image=IMAGE, validation_request_sha256=digest(case.request),
        sandbox_exit_code=0, writers_stopped=True, transport_verified=True,
        files=rows, bytes=sum(row['bytes'] for row in rows))
    json_file(final, 'transport-manifest.json', body)
    sandbox = object.__new__(AgentKubernetesSandbox)
    sandbox.transport = SimpleNamespace(settings=SimpleNamespace(worker_image=IMAGE))
    return sandbox, final, body


def read(case, sandbox, final, request=None):
    return sandbox._read_verified(final, case.source, case.snapshot,
                                  case.request if request is None else request)


def test_initial_plan_preserves_full_business_projection_without_native_home(candidate):
    assert not (candidate.snapshot / 'home').exists()
    assert candidate.report['accepted'] is True
    assert candidate.report['design']['source_plan'] == candidate.manifest['plan']
    assert candidate.report['publication_authorized'] is False
    sandbox, final, _ = transport(candidate)
    receipt = read(candidate, sandbox, final)
    assert receipt.verify(candidate.source)
    assert receipt.report() == candidate.report
    assert not receipt.verify('f' * 64)
    returned = receipt.report()
    returned['accepted'] = False
    assert receipt.report()['accepted'] is True


@pytest.mark.parametrize('changed', ['pages', 'oracle', 'feedback'])
def test_cached_receipt_cannot_be_reused_with_a_different_request(candidate, changed):
    sandbox, final, _ = transport(candidate)
    request = copy.deepcopy(candidate.request)
    if changed == 'pages': request['pages'] = 4
    elif changed == 'oracle': request['expected_charts'] = [{'chart_key': 'other'}]
    else: request['task']['feedback'] = 'Different user input'
    with pytest.raises(ControlError, match='agent_validator_transport_unverified'):
        read(candidate, sandbox, final, request)


@pytest.mark.parametrize('changed', ['stopped', 'exit', 'pod', 'image', 'bytes', 'duplicate', 'hash'])
def test_cached_receipt_requires_exact_stopped_validator_transport(candidate, changed):
    sandbox, final, body = transport(candidate)
    if changed == 'stopped': body['writers_stopped'] = False
    elif changed == 'exit': body['sandbox_exit_code'] = False  # bool is not an observed exit code.
    elif changed == 'pod': body['sandbox_pod_uid'] = ''
    elif changed == 'image': body['execution_image'] = 'registry.fixture/other@sha256:' + 'b' * 64
    elif changed == 'bytes': body['bytes'] += 1
    elif changed == 'duplicate': body['files'].append(copy.deepcopy(body['files'][0]))
    else: body['files'][0]['sha256'] = 'c' * 64
    json_file(final, 'transport-manifest.json', body)
    with pytest.raises(ControlError):
        read(candidate, sandbox, final)


def test_unknown_file_in_cached_evidence_is_rejected(candidate):
    sandbox, final, _ = transport(candidate)
    put(final, 'undeclared.json', b'{}')
    with pytest.raises(ControlError, match='agent_validation_roster_mismatch'):
        read(candidate, sandbox, final)


@pytest.mark.parametrize('changed', ['extra', 'source', 'checkpoint'])
def test_receipt_rechecks_candidate_bytes_and_complete_checkpoint_roster(candidate, changed):
    sandbox, final, _ = transport(candidate)
    if changed == 'extra': put(candidate.snapshot, 'project/deck/extra.md', b'extra')
    elif changed == 'source': put(candidate.snapshot, 'project/deck/planning-notes.md', b'changed')
    else:
        cp = candidate.checkpoint
        cp['files'][0]['sha256'] = 'c' * 64
        cp['sha256'] = digest({key: value for key, value in cp.items() if key != 'sha256'})
        json_file(candidate.snapshot, 'result/checkpoint.json', cp)
    with pytest.raises(ControlError):
        read(candidate, sandbox, final)


def test_receipt_cannot_authorize_publication_even_from_a_reindexed_report(candidate):
    report = dict(candidate.report, publication_authorized=True)
    sandbox, final, _ = transport(candidate, report)
    with pytest.raises(ControlError, match='agent_validator_binding_changed'):
        read(candidate, sandbox, final)


def test_private_validation_rejects_a_different_page_count(candidate):
    with pytest.raises(ControlError, match='agent_validation_identity_mismatch'):
        validate_private_candidate(candidate.snapshot, dict(candidate.request, pages=4,
            source_manifest_sha256=candidate.source, validation_nonce=str(uuid4())))


@pytest.fixture
def prepared_candidate(candidate):
    # Explicit synthetic file-only transport, not a production confirmation or
    # an official quality report. The real gate process is checked separately.
    task = candidate.request['task']
    task.update(phase='prepare', schema_version='ppt-prepare.v1')
    parent = copy.deepcopy(candidate.checkpoint)
    parent['phase'] = 'plan_options'
    parent['sha256'] = digest({key: value for key, value in parent.items() if key != 'sha256'})
    task['checkpoint'] = checkpoint_descriptor(parent)
    shared = dict(channel='website_database', input_snapshot_sha256=task['input_snapshot_sha256'],
        confirmed_at='2026-10-29T13:00:00+00:00', plan_sha256='c' * 64)
    chosen = dict(id='A', name='Synthetic fixture direction')
    pages = {'pages': [dict(ordinal=n, title=f'Page {n}', purpose='Explain the synthetic source')
                       for n in range(1, 4)]}
    task['confirmations'] = {
        'stage1': dict(shared, plan_id=str(uuid4()), plan_revision=1,
            selection={'design_mode': 'free_design'}, outline={}, design={}),
        'stage2': dict(shared, plan_id=str(uuid4()), plan_revision=2,
            selection={'direction_id': 'A'}, outline=pages,
            design={'directions': [chosen], 'production_parameters': {'language': 'en'}},
            selected_direction=chosen),
    }
    root_name = 'synthetic_workflow_20261029'
    root = candidate.snapshot / 'project' / root_name
    (candidate.snapshot / 'project/deck').rename(root)
    manifest = _manifest_example(task)
    manifest['project_root'] = root_name
    rows = [put(root, name, raw) for name, raw in (
        ('design_spec.md', b'# Synthetic design specification\n'),
        ('spec_lock.md', b'# Synthetic specification lock\n'),
        ('reports/gates.json', b'{"fixture_only": true}\n'),
    )]
    manifest['artifacts'] = [dict(row, role='quality_report' if row['path'].endswith('.json')
        else 'specification', media_type='application/json' if row['path'].endswith('.json')
        else 'text/markdown') for row in rows] + [candidate.manifest['artifacts'][0]]
    candidate.manifest = manifest
    candidate.source = json_file(candidate.snapshot, 'result/manifest.json', manifest)['sha256']
    cp = candidate.checkpoint
    cp.update(phase='prepare', project_root=root_name,
        files=[{key: row[key] for key in ('path', 'bytes', 'sha256')} for row in manifest['artifacts']])
    cp['files'] = [dict(row, path=root_name + '/' + row['path']) for row in cp['files']]
    cp['total_bytes'] = sum(row['bytes'] for row in cp['files'])
    cp['sha256'] = digest({key: value for key, value in cp.items() if key != 'sha256'})
    json_file(candidate.snapshot, 'result/checkpoint.json', cp)
    candidate.request.update(phase='prepare', source_manifest_sha256=candidate.source,
        validation_nonce=str(uuid4()))
    candidate.project = root
    return candidate


@pytest.mark.parametrize('gate_rejects', [False, True])
def test_prepare_file_only_checkpoint_restores_empty_svg_directory_only_in_gate_scratch(
        prepared_candidate, monkeypatch, gate_rejects):
    from ppt_service.control.publisher import OfficialGates
    case = prepared_candidate
    before = {path.relative_to(case.snapshot).as_posix(): path.read_bytes()
              for path in case.snapshot.rglob('*') if path.is_file()}
    assert not (case.project / 'svg_output').exists()
    invoked = []

    def observe_command(gates, name, args):
        invoked.append(name)
        if name == 'project_validate':
            assert args[-3].endswith('/project_manager.py') and args[-2] == 'validate'
            project = Path(args[-1])
            assert project.name == case.project.name and project != case.project
            assert (project / 'svg_output').is_dir() and not list((project / 'svg_output').iterdir())
            measured = {path.relative_to(project).as_posix(): path.read_bytes()
                        for path in project.rglob('*') if path.is_file()}
            assert measured == {row['path']: (case.project / row['path']).read_bytes()
                                for row in case.manifest['artifacts']}
            if gate_rejects:
                raise ControlError('independent_quality_gate_failed')

    monkeypatch.setattr(OfficialGates, '_run', observe_command)
    if gate_rejects:
        with pytest.raises(ControlError, match='independent_quality_gate_failed'):
            validate_private_candidate(case.snapshot, case.request)
    else:
        report = validate_private_candidate(case.snapshot, case.request)
        assert report['accepted'] is True and report['status'] == 'prepared'
        assert report['evidence']['official_gates_verified'] is True
        assert report['publication_authorized'] is False and report['manifest'] == case.manifest
    assert invoked == ['attribution', 'project_validate']
    assert not (case.project / 'svg_output').exists()
    assert before == {path.relative_to(case.snapshot).as_posix(): path.read_bytes()
                      for path in case.snapshot.rglob('*') if path.is_file()}


def test_prepare_scratch_reconstruction_does_not_repair_tampered_formal_specs(prepared_candidate, monkeypatch):
    from ppt_service.control.publisher import OfficialGates
    case = prepared_candidate
    (case.project / 'design_spec.md').write_bytes(b'Altered after the checkpoint was frozen')

    def never_run(*args):
        raise AssertionError('File integrity must be checked before constructing a gate project')

    monkeypatch.setattr(OfficialGates, '_run', never_run)
    with pytest.raises(ControlError, match='agent_validator_evidence_changed'):
        validate_private_candidate(case.snapshot, case.request)
    assert not (case.project / 'svg_output').exists()


def generated_report(candidate):
    # Controller-read fixture only: these bytes do not claim to be a real PPTX
    # or an independently validated deck. No Office parser is invoked here.
    candidate.request['phase'] = candidate.request['task']['phase'] = 'generate'
    candidate.request['expected_charts'] = []
    candidate.manifest.update(phase='generate', status='candidate', slides=[
        {'ordinal': n, 'svg_path': f'svg_output/{n}.svg'} for n in range(1, 4)])
    rows = [put(candidate.snapshot / 'project/deck', f'svg_output/{n}.svg', b'<svg/>') for n in range(1, 4)]
    pptx = put(candidate.snapshot / 'project/deck', 'presentation.pptx', b'Synthetic PPTX transport bytes')
    original = candidate.manifest['artifacts'][0]
    candidate.manifest['artifacts'] += [dict(row, role='svg', media_type='image/svg+xml') for row in rows]
    candidate.manifest['artifacts'].append(dict(pptx, role='pptx', media_type='application/octet-stream'))
    candidate.source = json_file(candidate.snapshot, 'result/manifest.json', candidate.manifest)['sha256']
    cp = candidate.checkpoint
    cp.update(phase='generate', files=[{key: original[key] for key in ('path', 'bytes', 'sha256')}] + rows + [pptx])
    cp['files'] = [dict(row, path='deck/' + row['path']) for row in cp['files']]
    cp['total_bytes'] = sum(row['bytes'] for row in cp['files'])
    cp['sha256'] = digest({key: value for key, value in cp.items() if key != 'sha256'})
    json_file(candidate.snapshot, 'result/checkpoint.json', cp)
    return dict(candidate.report, phase='generate', status='candidate', accepted=False,
        programmatic_checks_passed=True, source_manifest_sha256=candidate.source,
        manifest=candidate.manifest, required_pending_gates=[
            'independent_visual_review', 'independent_postflight_and_delivery'])


def test_generated_candidate_needs_persistent_independent_previews(candidate):
    sandbox, final, _ = transport(candidate, generated_report(candidate))
    with pytest.raises(ControlError, match='agent_validator_evidence_changed'):
        read(candidate, sandbox, final)


def independent_preview_transport(candidate):
    report = generated_report(candidate)
    validation_id = str(uuid4())
    final = candidate.root / 'independent-validator'
    base = 'independent/' + validation_id
    pngs, retained = [], []
    for n in range(1, 4):
        relative = f'scratch-projects/deck/.preview/{n}.png'
        png = put(final, base + '/' + relative, f'Synthetic bound PNG {n}'.encode())
        retained.append(png)
        pngs.append(dict(png, path=f'{n}.png', relative_path=relative, ordinal=n,
            svg_path=f'svg_output/{n}.svg', svg_sha256=hashlib.sha256(b'<svg/>').hexdigest()))
    pptx = next(row for row in candidate.manifest['artifacts'] if row['role'] == 'pptx')
    from ppt_service.control.render_validation import fingerprint
    evidence = dict(schema_version=1, issuer='ppt-service-independent-programmatic-runtime-v1',
        validation_id=validation_id, controller_nonce=report['validation_nonce'],
        source=dict(source_manifest_sha256=candidate.source,
            input_snapshot_sha256=candidate.request['input_snapshot_sha256'], project_root='deck',
            expected_charts_canonical_sha256=fingerprint([]),
            pptx={key: pptx[key] for key in ('path', 'bytes', 'sha256')}),
        pngs=pngs, preview_group_stopped=True, command_groups_stopped=True,
        render_exit_code=0, delivery_exit_code=0, independent_visual_review=False,
        full_exporter_postflight_verified=False,
        completed_programmatic_gates=[RASTER_GATE, DELIVERY_GATE, NATIVE_GATE])
    report['independent_programmatic_evidence'] = evidence
    retained.append(json_file(final, base + '/programmatic-evidence.json', evidence))
    sandbox, final, body = transport(candidate, report)
    body['files'] += retained
    body['bytes'] = sum(row['bytes'] for row in body['files'])
    json_file(final, 'transport-manifest.json', body)
    return sandbox, final, body


def test_independent_previews_are_bound_to_real_source_bytes_without_publication(candidate):
    sandbox, final, _ = independent_preview_transport(candidate)
    receipt = read(candidate, sandbox, final)
    assert receipt.verify(candidate.source)
    assert receipt.report()['accepted'] is False
    assert receipt.report()['publication_authorized'] is False
    assert len(receipt.report()['independent_programmatic_evidence']['pngs']) == 3


@pytest.mark.parametrize('changed', ['png', 'source', 'persisted_report', 'oracle'])
def test_independent_preview_evidence_cannot_be_rebound(candidate, changed):
    sandbox, final, body = independent_preview_transport(candidate)
    report_path = final / 'validated.json'
    import json
    report = json.loads(report_path.read_text())
    evidence = report['independent_programmatic_evidence']
    if changed == 'png': evidence['pngs'][0]['sha256'] = 'e' * 64
    elif changed == 'source': evidence['pngs'][0]['svg_sha256'] = 'e' * 64
    elif changed == 'oracle': evidence['source']['expected_charts_canonical_sha256'] = 'e' * 64
    else: evidence['artifact_retention_requires_caller'] = False
    new = json_file(final, 'validated.json', report)
    body['files'] = [new if row['path'] == 'validated.json' else row for row in body['files']]
    body['bytes'] = sum(row['bytes'] for row in body['files'])
    json_file(final, 'transport-manifest.json', body)
    with pytest.raises(ControlError, match='agent_validator_evidence_changed'):
        read(candidate, sandbox, final)


def test_unobserved_validator_failure_cannot_mint_stop_authority():
    with pytest.raises(TypeError):
        AgentValidationFailure('validation_failed')
