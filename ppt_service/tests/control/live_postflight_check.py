"""Check fresh official source audits on frozen V1 without re-rendering it.

Run only in the pinned, network-none validation image. This intentionally does
not deserialize old ProgrammaticEvidence or manufacture publication authority.
"""
import json
from pathlib import Path
import uuid

from ppt_service.control.postflight_validation import (
    OfficialPostflightAudit, check_quality_report, check_resource_report, run_postflight,
)
from ppt_service.control.publisher import complete_index
from ppt_service.control.render_validation import fresh_project, hash_file, source_binding, unchanged
from ppt_service.control.staging import atomic_json, read_json


def verify_snapshot(snapshot, indexes):
    count = 0
    for name, part in indexes.items():
        for row in part['files']:
            actual = hash_file(snapshot / name, row['path'])
            assert actual['sha256'] == row['sha256'] and actual['bytes'] == row['bytes']
            count += 1
    return count


def main():
    snapshot, output = Path('/snapshot'), Path('/output')
    indexes = read_json(snapshot, 'snapshot.json')['indexes']
    before = verify_snapshot(snapshot, indexes)
    raw = read_json(snapshot / 'result', 'manifest.json')
    complete, _ = complete_index(snapshot, raw)
    request = read_json(Path('/request'), 'request.json')
    request.update(validation_nonce=str(uuid.uuid4()),
                   source_manifest_sha256=hash_file(snapshot / 'result', 'manifest.json')['sha256'])
    binding, source, index = source_binding(snapshot, complete, request)
    project = output / 'fresh-source' / binding['project_root']
    rows = fresh_project(source, project, index)
    official = OfficialPostflightAudit().run(project, binding['svg_sources'], output / 'official')
    quality = check_quality_report(read_json(project / 'validation', 'svg_quality_report.json'),
                                   project, binding['svg_sources'])
    resources = check_resource_report(read_json(output / 'official', 'resources.json'))
    unchanged(source, rows)
    unchanged(project, rows)
    assert verify_snapshot(snapshot, indexes) == before
    # No live prior instance exists in this independent process. The full
    # entrypoint must stay pending; persisted JSON is not re-issued as proof.
    pending = run_postflight(snapshot, complete, request, None, set(), output / 'pending').report()
    assert pending['publication_authorization'] is False
    assert not pending['completed_programmatic_gates']
    report = {
        'test': 'real-v1-source-audit-only', 'official': official,
        'source_manifest_sha256': request['source_manifest_sha256'],
        'controller_nonce': request['validation_nonce'],
        'source_files_verified': before, 'source_unchanged': True,
        'slides': len(binding['svg_sources']), 'quality': quality, 'resources': resources,
        'prior_evidence_available': False, 'full_entrypoint': pending,
        'rerendered': False, 'models_called': 0, 'publication_authorization': False,
        'powerpoint_acceptance': 'pending',
    }
    atomic_json(output / 'verification.json', report)
    print(json.dumps({k: report[k] for k in (
        'test', 'source_files_verified', 'source_unchanged', 'slides',
        'prior_evidence_available', 'rerendered', 'models_called', 'publication_authorization',
    )}))


if __name__ == '__main__':
    main()
