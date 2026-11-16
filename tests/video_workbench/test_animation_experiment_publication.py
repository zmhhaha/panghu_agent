"""Publication-boundary tests with synthetic bytes, never real media proof."""
from argparse import Namespace
from copy import deepcopy
import io
import json
from pathlib import Path
import tarfile

import pytest

from video_workbench.animation_experiments import canonical, digest, definition, pilot_input, signed
from video_workbench.deploy import publish_animation_experiments as publication


@pytest.fixture
def run(tmp_path):
    directory = tmp_path / 'identity-development-v1'
    directory.mkdir()
    (directory / 'frames').mkdir()
    image = b'\xff\xd8SYNTHETIC-BOUNDARY-ONLY\xff\xd9'
    media = b'0000ftypSYNTHETIC-NOT-DECODED'
    frames, artifacts = [], {}
    for index in range(192):
        name = 'frames/%06d.jpg' % index
        (directory / name).write_bytes(image)
        artifacts[name] = dict(size=len(image), sha256=digest(image), mime='image/jpeg')
        frames.append(dict(name='frame-%06d.jpg' % index, sha256=digest(image), bytes=len(image)))
    frames.append(dict(name='reverse-initial.jpg', sha256=digest(image), bytes=len(image)))
    document = pilot_input()
    report = dict(schema='video.animation-experiment-report.v1', input_sha256=digest(document),
        agent_job_id='synthetic-job',
        renderer=dict(status='rendered', cleanup=dict(status='removed_and_confirmed_absent'),
            technical=dict(status='failed', issues=['synthetic technical observation']),
            receipt=dict(status='rendered', decode_verified=True, frame_count=192, frames=frames,
                         output_sha256=digest(media))),
        published_frames=[dict(index=i, seconds=i/24, artifact='frames/%06d.jpg' % i) for i in range(192)])
    report_raw = canonical(report)
    (directory / 'report.json').write_bytes(report_raw)
    (directory / 'output.mp4').write_bytes(media)
    artifacts.update({'report.json': dict(size=len(report_raw), sha256=digest(report_raw), mime='application/json'),
                      'output.mp4': dict(size=len(media), sha256=digest(media), mime='video/mp4')})
    row = dict(schema='video.animation-experiment-run.v1', id=directory.name,
        experiment_id=definition()['id'], experiment_identity=definition()['identity'],
        source='agent', input_role='development', input_document=document, input_sha256=digest(document),
        label='synthetic boundary fixture', prompt_text='synthetic only', status='rendered',
        provenance=dict(job_id='synthetic-job', stop_proven=True), artifacts=artifacts,
        code_files={'scene.html': '<p>inspection text only</p>'}, strategy={})
    rewrite(directory, row)
    return directory


def rewrite(directory, row):
    frozen = signed({key: value for key, value in row.items() if key != 'identity'})
    (directory / 'experiment-run.json').write_bytes(canonical(frozen))
    return frozen


def archive_for(run, tmp_path, capsys):
    archive = tmp_path / 'publication.tar'
    publication.bundle(Namespace(output=archive, run=[run]))
    receipt = json.loads(capsys.readouterr().out)
    raw = archive.read_bytes()
    manifest, files = publication.unpack_publication(raw, receipt['manifest_sha256'])
    return raw, receipt, manifest, files


def make_tar(manifest, files, extra=None):
    memory = io.BytesIO()
    with tarfile.open(fileobj=memory, mode='w') as archive:
        for name, raw in [(publication.MANIFEST, canonical(manifest)), *sorted(files.items())]:
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
        if extra:
            archive.addfile(*extra)
    return memory.getvalue()


def test_bundle_verifies_every_frame_and_excludes_executable_source(run, tmp_path, capsys):
    (run / 'scene.html').write_text('<script>throw Error("never execute")</script>')
    raw, receipt, manifest, files = archive_for(run, tmp_path, capsys)
    assert receipt['archive_sha256'] == digest(raw)
    assert len(files) == 195 and len(manifest['records']) == 1
    assert all(not name.endswith(('.html', '.js', '.css')) for name in files)
    assert receipt['copied_generated_executable_files'] == receipt['model_calls'] == receipt['database_writes'] == 0
    # A real rendered work with technical issues must remain publishable.
    assert manifest['records'][0]['status'] == 'rendered'
    (run / 'frames/000096.jpg').write_bytes(b'changed-frame')
    with pytest.raises(RuntimeError, match='publication_artifact_mismatch'):
        publication.verified_files(run)


@pytest.mark.parametrize('change', ['manifest', 'file_hash', 'traversal', 'symlink', 'source', 'duplicate'])
def test_archive_boundary_rejects_tampering_and_executable_files(run, tmp_path, capsys, change):
    raw, receipt, manifest, files = archive_for(run, tmp_path, capsys)
    expected = receipt['manifest_sha256']
    code = None
    if change == 'manifest':
        expected, code = '0' * 64, 'publication_manifest_changed'
    elif change == 'file_hash':
        files[run.name + '/frames/000096.jpg'] = b'changed'
        raw, code = make_tar(manifest, files), 'publication_file_mismatch'
    elif change == 'traversal':
        member = tarfile.TarInfo('../outside'); member.size = 1
        raw, code = make_tar(manifest, files, (member, io.BytesIO(b'x'))), 'publication_archive_member_invalid'
    elif change == 'symlink':
        member = tarfile.TarInfo(run.name + '/link'); member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'
        raw, code = make_tar(manifest, files, (member, None)), 'publication_archive_member_invalid'
    elif change == 'source':
        name, source = run.name + '/scene.html', b'<script>never</script>'
        files[name] = source
        manifest['files'][name] = dict(bytes=len(source), sha256=digest(source))
        expected = digest(canonical(manifest))
        raw, code = make_tar(manifest, files), 'publication_file_type_forbidden'
    elif change == 'duplicate':
        member = tarfile.TarInfo(publication.MANIFEST); member.size = 1
        raw, code = make_tar(manifest, files, (member, io.BytesIO(b'x'))), 'publication_archive_member_invalid'
    with pytest.raises(RuntimeError, match=code):
        publication.unpack_publication(raw, expected)


def test_install_preserves_identical_frozen_runs_and_refuses_changed_identity(run, tmp_path, capsys):
    _, _, manifest, files = archive_for(run, tmp_path, capsys)
    destination = tmp_path / 'published'
    result = publication.install_validated(destination, manifest, files)
    assert result['status'] == 'published' and result['existing_runs_preserved']
    record_path = destination / run.name / 'experiment-run.json'
    original = record_path.read_bytes()
    publication.install_validated(destination, manifest, files)
    assert record_path.read_bytes() == original
    row = json.loads(files[run.name + '/experiment-run.json'])
    changed = signed({**{k: v for k, v in row.items() if k != 'identity'}, 'label': 'changed identity'})
    files[run.name + '/experiment-run.json'] = canonical(changed)
    manifest['records'][0]['identity'] = changed['identity']
    with pytest.raises(RuntimeError, match='publication_existing_run_immutable'):
        publication.install_validated(destination, manifest, files)
    assert record_path.read_bytes() == original


def test_all_records_are_validated_before_first_directory_is_exposed(run, tmp_path, capsys):
    _, _, manifest, files = archive_for(run, tmp_path, capsys)
    second_id = 'identity-development-v2'
    row = json.loads(files[run.name + '/experiment-run.json'])
    invalid = signed({**{k: v for k, v in row.items() if k != 'identity'},
                      'id': second_id, 'experiment_identity': 'incorrect-definition-binding'})
    files.update({second_id + name[len(run.name):]: content for name, content in list(files.items())})
    files[second_id + '/experiment-run.json'] = canonical(invalid)
    manifest['records'].append({**manifest['records'][0], 'id': second_id, 'identity': invalid['identity']})
    destination = tmp_path / 'atomic'
    with pytest.raises(ValueError, match='animation_experiment_run_invalid'):
        publication.install_validated(destination, manifest, files)
    assert not (destination / run.name).exists() and not (destination / second_id).exists()


def test_frame_budget_and_agent_stop_proof_are_checked_before_bundle(run):
    row = json.loads((run / 'experiment-run.json').read_bytes())
    row['provenance']['stop_proven'] = False
    rewrite(run, row)
    with pytest.raises(RuntimeError, match='publication_stop_render_proof_required'):
        publication.verified_files(run)
    row['provenance']['stop_proven'] = True
    image = (run / 'frames/000000.jpg').read_bytes()
    for index in range(192, 241):
        name = 'frames/%06d.jpg' % index
        (run / name).write_bytes(image)
        row['artifacts'][name] = dict(size=len(image), sha256=digest(image), mime='image/jpeg')
    rewrite(run, row)
    with pytest.raises(RuntimeError, match='publication_frame_limit'):
        publication.verified_files(run)


def test_failed_record_publishes_without_media_or_false_stop_render_success(tmp_path):
    directory = tmp_path / 'original-failed'
    directory.mkdir()
    document = pilot_input()
    report = dict(schema='video.animation-experiment-report.v1', input_sha256=digest(document),
                  agent_job_id='original-failed-job', error='package_resources_incomplete', renderer={})
    raw = canonical(report)
    (directory / 'report.json').write_bytes(raw)
    rewrite(directory, dict(schema='video.animation-experiment-run.v1', id=directory.name,
        experiment_id=definition()['id'], experiment_identity=definition()['identity'], source='agent',
        input_role='development', input_document=document, input_sha256=digest(document),
        label='original precreative failure', prompt_text='frozen prompt', status='failed',
        provenance=dict(job_id='original-failed-job', agent_generation_started=False,
                        model_request_count=1, stop_proven=False), code_files={}, strategy={},
        error='package_resources_incomplete',
        artifacts={'report.json': dict(size=len(raw), sha256=digest(raw), mime='application/json')}))
    row, files = publication.verified_files(directory)
    assert row['status'] == 'failed' and set(files) == {'experiment-run.json', 'report.json'}


def test_install_cli_rejects_any_other_destination_before_reading_input():
    with pytest.raises(RuntimeError, match='publication_exact_destination_required'):
        publication.install(Namespace(destination_root='/tmp/other', manifest_sha256='0' * 64))
