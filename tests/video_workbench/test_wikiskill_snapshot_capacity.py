"""A growing library must reach the compiler without dropping frozen evidence."""
from copy import deepcopy
import hashlib
import json
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service import animation_wikiskill_contract as C
from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.contracts import InlineFile
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service import compilation_input_tools as tool
from agent_service.animation_wikiskill_compact import COMPACT_RESULT_SCHEMA
from test_animation_wikiskill import result_for
from video_workbench.animation_wikiskill_store import AnimationWikiSkillStore
from video_workbench.animation_wikiskill_coordinator import WikiSkillCoordinator


@pytest.fixture(scope='module')
def complete_library():
    # This crosses both original limits while each real record remains ordinary.
    source = 'x' * (2 * 1024 * 1024)
    return [dict(record_id=f'original-{i:03}', kind='operation_input',
        content=dict(source=source, ordinal=i, evidence_scope='synthetic_capacity_only'))
        for i in range(18)]


@pytest.fixture(scope='module')
def prepared(complete_library):
    snapshot = C.make_snapshot('publisher', complete_library, captured_at=1000., compilation_revision='2')
    document = C.build_input(snapshot, compilation_revision='2')
    files = C.input_files(snapshot, document, compilation_revision='2')
    return snapshot, document, files


def test_large_library_preserves_every_original_and_roundtrips_above_both_legacy_limits(complete_library, prepared):
    snapshot, document, files = prepared
    assert len(canonical_bytes(snapshot)) > 30 * 1024 * 1024
    assert 32 * 1024 * 1024 < sum(map(len, files.values())) < 64 * 1024 * 1024
    assert len(snapshot['records']) == len(complete_library) == len(document['records'])
    originals = {row['record_id']: row for row in complete_library}
    for row, entry in zip(snapshot['records'], document['records']):
        assert row['content'] == originals[row['record_id']]['content']
        assert row['sha256'] == digest(originals[row['record_id']]['content'])
        assert files[entry['file_name']] == canonical_bytes(row)
        assert entry['file_sha256'] == hashlib.sha256(files[entry['file_name']]).hexdigest()
    assert C.validate_input_files(document, files, compilation_revision='2') == snapshot
    with pytest.raises(ServiceError, match='snapshot_too_large'):
        C.make_snapshot('publisher', complete_library, captured_at=1000.)
    with pytest.raises(ServiceError, match='inputs_too_large'):
        C.validate_input(document)


def test_new_capacity_still_rejects_manifest_overflow_and_changed_originals(prepared):
    snapshot, document, files = prepared
    over = deepcopy(document)
    for row in over['records']:
        row['bytes'] = 4 * 1024 * 1024
    with pytest.raises(ServiceError, match='inputs_too_large'):
        C.validate_input(over, compilation_revision='2')
    changed = dict(files)
    name = document['records'][-1]['file_name']
    changed[name] += b' '
    with pytest.raises(ServiceError, match='binding_mismatch'):
        C.validate_input_files(document, changed, compilation_revision='2')
    with pytest.raises(ServiceError, match='snapshot_changed'):
        C.validate_snapshot(dict(snapshot, owner='another-owner'), compilation_revision='2')


def test_small_historical_snapshot_and_manifest_are_identical_across_capacity_versions():
    records = [dict(record_id='historical', kind='review', content={'observed': 'fixed original text'})]
    old = C.make_snapshot('publisher', records, captured_at=1000.)
    newer = C.make_snapshot('publisher', records, captured_at=1000., snapshot_id=old['id'], compilation_revision='2')
    assert newer == old
    old_document = C.build_input(old, note='unchanged frozen note')
    assert C.build_input(newer, note='unchanged frozen note', compilation_revision='2') == old_document
    assert C.input_files(old, old_document) == C.input_files(newer, old_document, compilation_revision='2')


def test_native_compiler_and_fixed_tools_accept_large_inputs_only_in_capacity_revision(tmp_path, prepared):
    _, document, files = prepared
    for name, raw in files.items():
        target = tmp_path / 'inputs' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    task = dict(input_sha256=digest(document))
    current = implementation('video.animation-wikiskill-compile.v4', '2')
    current.validate_inputs(tmp_path, task)
    with pytest.raises(ServiceError, match='inputs_too_large'):
        implementation('video.animation-wikiskill-compile.v4').validate_inputs(tmp_path, task)
    output = result_for(document)
    wire_output = deepcopy(output)
    wire_output['schema'] = COMPACT_RESULT_SCHEMA
    wire_output['index'][0].pop('source_record_ids')
    wire_output['index'][0]['source_record_ranges'] = [[0, len(document['records'])]]
    (tmp_path / 'output.json').write_bytes(canonical_bytes(wire_output))
    envelope = SimpleNamespace(attempt_id=str(uuid4()), profile=current.name, profile_revision='2',
        task=task, checkpoint_inputs=[], checkpoint_ref=None,
        inputs=[InlineFile.from_bytes(name, raw) for name, raw in files.items()])
    proof = StopEvidence(execution_id=envelope.attempt_id, all_writers_stopped=True,
        resource_ids=('synthetic-capacity-check',))
    collected = current.collect_output(tmp_path, envelope, proof)
    assert json.loads(collected[0].content()) == output
    with pytest.raises(ServiceError, match='profile_binding_mismatch'):
        current.collect_output(tmp_path, SimpleNamespace(**{**vars(envelope), 'profile_revision': '1'}), proof)
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable for fixed tool integration')
    program = '''
const module = await import(process.argv[1]); const tools = new Map();
module.default({registerTool(t){tools.set(t.name,t);}});
try {
 const response=await tools.get('read_compilation_input').execute('offline',
   {mode:'survey',input_sha256:process.argv[2],offset:0,limit:128},new AbortController().signal);
 process.stdout.write(JSON.stringify({ok:true,registered:[...tools.keys()],
   replyBytes:Buffer.byteLength(response.content[0].text),body:JSON.parse(response.content[0].text)}));
} catch (error) {process.stdout.write(JSON.stringify({ok:false,error:error.message}));}
'''
    def read(extension):
        p = subprocess.run([node, '--input-type=module', '-e', program,
            extension.resolve().as_uri(), digest(document)], cwd=tmp_path,
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)
    capacity_extension = tool.verified_compilation_input_extension(tool.COMPILATION_CAPACITY_ENVIRONMENT_NAME)
    current_reply = read(capacity_extension)
    assert current_reply['ok'] and current_reply['replyBytes'] <= 32768
    assert current_reply['registered'] == ['read_compilation_input', 'check_compilation_output']
    assert current_reply['body']['next_offset'] is None
    assert not read(tool.COMPILATION_CHECKED_EXTENSION)['ok']
    assert all((tmp_path / 'inputs' / name).read_bytes() == raw for name, raw in files.items())


class UnpaidGateway:
    def __init__(self):
        self.selected, self.uploaded = [], []

    def for_revision(self, revision):
        self.selected.append(revision)
        parent = self
        class Selected:
            def preflight(self, deadline):
                return {}
            def upload(self, document, files):
                C.validate_input_files(document, files, compilation_revision=revision)
                parent.uploaded.append((revision, digest(document), len(files)))
                return []
        return Selected()


def test_capacity_job_freezes_revision_and_restart_replays_without_recapture_or_another_upload(tmp_path, complete_library):
    store = AnimationWikiSkillStore(tmp_path / 'library.sqlite3', clock=lambda: 1000.)
    gateway = UnpaidGateway()
    coordinator = WikiSkillCoordinator(store, gateway, lambda owner: complete_library, clock=lambda: 1000.)
    request = dict(request_id=str(uuid4()), note='Explicit isolated capacity fixture')
    # Keep the existing capacity-only contract frozen while new manual
    # publications select the separately admitted hour-budget revision.
    snapshot = C.make_snapshot('publisher', complete_library, captured_at=1000., compilation_revision='2')
    created = store.create('publisher', request, snapshot,
        C.build_input(snapshot, note=request['note'], compilation_revision='2'), compilation_revision='2')
    assert created['compiler_revision'] == '2' and created['state'] == 'queued'
    assert not gateway.selected and not gateway.uploaded
    frozen = store.snapshot('publisher', created['snapshot_id'], compilation_revision='2')
    assert len(frozen['records']) == len(complete_library)
    with pytest.raises(ServiceError, match='snapshot_not_found'):
        store.snapshot('other-owner', created['snapshot_id'], compilation_revision='2')
    resumed = WikiSkillCoordinator(AnimationWikiSkillStore(store.path, clock=lambda: 1100.), gateway,
        lambda _: pytest.fail('accepted requests must not recapture live records'), clock=lambda: 1100.)
    assert resumed.start('publisher', request) == created
    assert resumed.advance('publisher', created['id'])['state'] == 'prepared'
    assert gateway.selected == ['2']
    assert gateway.uploaded == [('2', created['input_sha256'], len(complete_library) + 1)]
    assert resumed.start('publisher', request)['snapshot_sha256'] == created['snapshot_sha256']
    assert len(gateway.uploaded) == 1
    token = store.acquire('publisher', created['id'])
    with pytest.raises(ServiceError):
        store.change('publisher', created['id'], token, compiler_revision='1')
    store.release_lease('publisher', created['id'], token)


def test_unmarked_historical_job_keeps_revision_one_after_upgrade(tmp_path):
    store = AnimationWikiSkillStore(tmp_path / 'historical.sqlite3', clock=lambda: 1000.)
    snapshot = C.make_snapshot('publisher', [dict(record_id='old', kind='review', content={'fixed': True})], captured_at=1000.)
    request = dict(request_id=str(uuid4()))
    old = store.create('publisher', request, snapshot, C.build_input(snapshot))
    assert 'compiler_revision' not in old
    original = store.operation('publisher', old['id'])
    gateway = UnpaidGateway()
    coordinator = WikiSkillCoordinator(store, gateway, lambda _: pytest.fail('old input is frozen'), clock=lambda: 1100.)
    assert coordinator.start('publisher', request) == old
    assert store.operation('publisher', old['id']) == original
    assert coordinator.advance('publisher', old['id'])['state'] == 'prepared'
    assert gateway.selected == ['1'] and gateway.uploaded[0][0] == '1'
    assert 'compiler_revision' not in store.operation('publisher', old['id'])


def test_over_capacity_rejection_creates_no_job_snapshot_or_dispatch(tmp_path, complete_library):
    store = AnimationWikiSkillStore(tmp_path / 'rejected.sqlite3', clock=lambda: 1000.)
    gateway = UnpaidGateway()
    records = [dict(complete_library[0], record_id=f'oversized-{i}') for i in range(34)]
    coordinator = WikiSkillCoordinator(store, gateway, lambda _: records, clock=lambda: 1000.)
    request = dict(request_id=str(uuid4()))
    with pytest.raises(ServiceError, match='snapshot_too_large'):
        coordinator.start('publisher', request)
    assert store.catalogue('publisher')['jobs'] == []
    with store._transaction() as db:
        assert db.execute('SELECT count(*) FROM wikiskill_snapshots').fetchone()[0] == 0
    assert not gateway.selected and not gateway.uploaded
