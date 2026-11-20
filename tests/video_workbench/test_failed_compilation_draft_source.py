"""Synthetic private recovery data; never a published or repaired Skill."""
from copy import deepcopy
import hashlib
import json
import os
from uuid import uuid4

import pytest

from agent_service.animation_wikiskill_contract import build_input, input_files, make_snapshot, validate_input_files
from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.failed_compilation_draft_source import SCHEMA, with_failed_compilation_draft
from video_workbench.animation_wikiskill_coordinator import WikiSkillCoordinator
from video_workbench.animation_wikiskill_store import AnimationWikiSkillStore
from test_animation_wikiskill import Gateway, finish, records as ordinary_records

OWNER = '27714443'


@pytest.fixture
def recovery(tmp_path):
    rows = ordinary_records() + [dict(record_id='drawing-library:protected', kind='documentation', content={'text': 'Fixed API bytes.'})]
    frozen = make_snapshot(OWNER, rows, captured_at=1000.)
    original = build_input(frozen)
    publication_id, request_id, job_id, attempt_id, pod_uid = [str(uuid4()) for _ in range(5)]
    draft = dict(schema='video.animation-wikiskill-result.v2', input_sha256=digest(original),
        snapshot_sha256=frozen['sha256'], packages=[dict(version='1.0.0', files={'SKILL.md': 'Original invalid prose.'})])
    raw = json.dumps(draft, indent=2).encode() + b'\n'
    stop = dict(stop_proven=True, attempt_id=attempt_id, fence=7, request_sha256='a'*64,
        result_sha256='b'*64, resource_ids=['k8s:private:'+pod_uid], observed_at=1910.)
    remote = dict(job_id=job_id, state='timed_out', publication_authorized=False,
        business_ref='animation-wikiskill:'+publication_id, deadline_at=1900.,
        pins=dict(profile='video.animation-wikiskill-compile.v4', profile_revision='1'),
        attempt=dict(attempt_id=attempt_id, fence=7), stop_proven=True, stop_proof=stop)
    publication = dict(id=publication_id, request_id=request_id, owner=OWNER, state='failed',
        release_id=None, job_id=job_id, remote_state='timed_out', remote_stop_proven=True,
        remote_receipt=remote, input_sha256=digest(original), snapshot_id=frozen['id'], snapshot_sha256=frozen['sha256'])
    provenance = dict(schema='video.wikiskill-private-failed-draft-provenance.v1', owner=OWNER,
        publication_id=publication_id, request_id=request_id, agent_job_id=job_id,
        attempt_id=attempt_id, fence=7, input_sha256=digest(original),
        snapshot_id=frozen['id'], snapshot_sha256=frozen['sha256'],
        original_draft=dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), modified=False),
        original_input_manifest=dict(record_count=len(rows)),
        failed_job=dict(state='timed_out', stop_proven=True, deadline_at=1900., outer_pod_uid=pod_uid, stop_observed_at=1910.),
        model_write=dict(http_status=200, model_request_sha256='c'*64, raw_model_response_sha256='d'*64,
            function_call_id='synthetic-original-call', model_response_id='synthetic-original-response'),
        scope=dict(unpublished=True, structurally_invalid=True, first_and_only_completed_model_write_proposal=True,
            no_new_visual_audio_or_artistic_acceptance=True, original_check_returns_retained=False,
            partial_rewrite_available=False))
    diagnostics = dict(original_output_sha256=hashlib.sha256(raw).hexdigest(), original_output_modified=False,
        projection_saved_or_supplied_as_source=False, findings=[dict(path='packages[0].version', actual='1.0.0', expected='integer 1')])
    call = dict(schema='video.preserved-model-write-call.v1',name='write',
        arguments=dict(path='output.json',content=raw.decode()),
        **{key:provenance['model_write'][key] for key in ('model_request_sha256','raw_model_response_sha256',
            'function_call_id','model_response_id')})
    directory = tmp_path/'knowledge-recovery'/'synthetic-data'
    directory.mkdir(parents=True)
    artifacts = {'draft.json': raw, 'provenance.json': canonical_bytes(provenance),
        'original-input.json': canonical_bytes(original), 'failed-publication.json': canonical_bytes(publication),
        'diagnostics.json': canonical_bytes(diagnostics),'model-write-call.json': canonical_bytes(call)}
    for name, data in artifacts.items():
        (directory/name).write_bytes(data)
    manifest = dict(schema=SCHEMA, owner=OWNER, scope='unpublished_invalid_model_draft_only',
        protected_record_ids=['drawing-library:protected'], files={name:dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)) for name,data in artifacts.items()})
    def configure(capture=None):
        manifest_raw = canonical_bytes(manifest)
        (directory/'manifest.json').write_bytes(manifest_raw)
        return with_failed_compilation_draft(capture or (lambda _: deepcopy(rows)), trusted_root=tmp_path,
            manifest_file=directory/'manifest.json', manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(), owner=OWNER)
    def rewrite(name, value):
        data = canonical_bytes(value);(directory/name).write_bytes(data)
        manifest['files'][name] = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    return dict(rows=rows, original=original, raw=raw, directory=directory, manifest=manifest,
        publication=publication, provenance=provenance, call=call, configure=configure, rewrite=rewrite)


def test_private_draft_is_verbatim_invalid_and_has_exact_current_mapping(recovery):
    r=recovery;rows=r['configure']()(OWNER);source=rows[-1]['content']
    assert rows[-1]['record_id']>max(row['record_id'] for row in rows[:-1])
    assert source['original_draft_text'].encode()==r['raw']==(r['directory']/'draft.json').read_bytes()
    assert json.loads(source['original_draft_text'])['packages'][0]['version']=='1.0.0'
    assert source['all_original_addresses_unchanged'] is True and source['draft_index']==3
    assert all(row['original_index']==row['current_index'] and row['unchanged'] for row in source['original_index_to_current'])
    for key in ('published','structurally_valid','native_write_execution_proven','check_passed','human_approval','artistic_acceptance'):
        assert source[key] is False
    assert source['operator_schema_diagnostics']['findings'][0]['actual']=='1.0.0'
    frozen=make_snapshot(OWNER,rows,captured_at=2000.)
    document=build_input(frozen)
    assert digest(document)!=source['original_input_sha256']
    assert validate_input_files(document,input_files(frozen,document))==frozen


def test_other_owner_does_not_read_or_expose_private_data(recovery,monkeypatch):
    from video_workbench import failed_compilation_draft_source as source
    wrapper=recovery['configure']()
    monkeypatch.setattr(source,'_read',lambda *_:pytest.fail('unrelated owner read private source'))
    assert wrapper('different-owner')==recovery['rows']


def test_changed_missing_and_shifted_original_addresses_are_explicit(recovery):
    rows=deepcopy(recovery['rows']);rows[0]['content']['goal']='Changed current evidence.';rows.pop(1)
    rows.append(dict(record_id='0-new',kind='brief',content={'goal':'New source.'}))
    content=recovery['configure'](lambda _:rows)(OWNER)[-1]['content']
    mapped={row['record_id']:row for row in content['original_index_to_current']}
    assert content['all_original_addresses_unchanged'] is False
    assert mapped['goal']['unchanged'] is False
    assert mapped['observation']['current_index'] is None and mapped['observation']['unchanged'] is False
    assert mapped['drawing-library:protected']['unchanged'] is True
    assert mapped['drawing-library:protected']['original_index']!=mapped['drawing-library:protected']['current_index']


def test_protected_original_source_bytes_cannot_change(recovery):
    rows=deepcopy(recovery['rows']);rows[-1]['content']['text']='Changed API bytes.'
    with pytest.raises(ServiceError,match='recovery_source_invalid'):
        recovery['configure'](lambda _:rows)(OWNER)


@pytest.mark.parametrize('filename',['draft.json','provenance.json','original-input.json','failed-publication.json','diagnostics.json','model-write-call.json'])
def test_any_original_byte_change_fails_closed(recovery,filename):
    wrapper=recovery['configure']();path=recovery['directory']/filename;path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ServiceError,match='recovery_source_invalid'):wrapper(OWNER)


@pytest.mark.parametrize('change',['owner','stop','publication','attempt','input'])
def test_rehashed_owner_failure_and_original_input_mismatches_are_rejected(recovery,change):
    publication=deepcopy(recovery['publication'])
    if change=='owner':publication['owner']='different-owner'
    if change=='stop':publication['remote_receipt']['stop_proven']=False
    if change=='publication':publication['state']='published'
    if change=='attempt':publication['remote_receipt']['attempt']['fence']+=1
    if change=='input':publication['input_sha256']='0'*64
    recovery['rewrite']('failed-publication.json',publication)
    with pytest.raises(ServiceError,match='recovery_source_invalid'):recovery['configure']()(OWNER)


@pytest.mark.parametrize('change',['content','path','request','response','call'])
def test_rehashed_write_call_must_still_match_original_draft_and_provenance(recovery,change):
    call=deepcopy(recovery['call'])
    if change=='content':call['arguments']['content']+=' '
    if change=='path':call['arguments']['path']='repaired.json'
    if change=='request':call['model_request_sha256']='e'*64
    if change=='response':call['model_response_id']='different-response'
    if change=='call':call['function_call_id']='different-call'
    recovery['rewrite']('model-write-call.json',call)
    with pytest.raises(ServiceError,match='recovery_source_invalid'):recovery['configure']()(OWNER)


@pytest.mark.parametrize('filename',['provenance.json','failed-publication.json','model-write-call.json','diagnostics.json'])
def test_rehashed_nonobject_documents_fail_with_fixed_error(recovery,filename):
    recovery['rewrite'](filename,[])
    with pytest.raises(ServiceError,match='recovery_source_invalid'):recovery['configure']()(OWNER)


def test_outside_traversal_and_linked_private_data_are_rejected(recovery,tmp_path):
    with pytest.raises(ServiceError,match='recovery_source_invalid'):
        with_failed_compilation_draft(lambda _:[],trusted_root=tmp_path,manifest_file=tmp_path/'elsewhere.json',
            manifest_sha256='a'*64,owner=OWNER)
    with pytest.raises(ServiceError,match='recovery_source_invalid'):
        with_failed_compilation_draft(lambda _:[],trusted_root=tmp_path,
            manifest_file=tmp_path/'knowledge-recovery'/'..'/'elsewhere.json',manifest_sha256='a'*64,owner=OWNER)
    wrapper=recovery['configure']();path=recovery['directory']/'draft.json'
    os.link(path,recovery['directory']/'alias.json')
    with pytest.raises(ServiceError,match='recovery_source_invalid'):wrapper(OWNER)


def test_only_new_manual_capture_reads_data_and_old_release_stays_unchanged(recovery,tmp_path,monkeypatch):
    from video_workbench import failed_compilation_draft_source as source
    store=AnimationWikiSkillStore(tmp_path/'library.sqlite3',clock=lambda:1000.)
    gateway=Gateway();coordinator=WikiSkillCoordinator(store,gateway,lambda _:ordinary_records(),clock=lambda:1000.)
    prior=coordinator.start(OWNER,{'request_id':str(uuid4())});finish((store,gateway,coordinator,[]),prior)
    old=store.active(OWNER);snapshot=store.snapshot(OWNER,prior['snapshot_id'])
    reads=[];original=source._read
    def observe(*args):reads.append(args[0]);return original(*args)
    monkeypatch.setattr(source,'_read',observe)
    coordinator.capture=recovery['configure']()
    coordinator.catalogue(OWNER);coordinator.get(OWNER,prior['id']);assert not reads
    body={'request_id':str(uuid4())};job=coordinator.start(OWNER,body);count=len(reads)
    assert count==7 and coordinator.start(OWNER,body)==job and len(reads)==count
    assert store.active(OWNER)==old and store.snapshot(OWNER,prior['snapshot_id'])==snapshot
    assert store.snapshot(OWNER,job['snapshot_id'])['records'][-1]['kind']=='unpublished_failed_compilation_model_draft'
    assert len(gateway.submissions)==1


@pytest.mark.parametrize('flags',[
    dict(wikiskill_recovery_owner=OWNER),dict(wikiskill_recovery_manifest_file='unused'),
    dict(wikiskill_recovery_manifest_sha256='a'*64),
    dict(wikiskill_recovery_manifest_file='unused',wikiskill_recovery_owner=OWNER)])
def test_executor_recovery_configuration_is_all_or_none(tmp_path,flags):
    from video_workbench.animation_creation_executor import create_server
    with pytest.raises(ValueError,match='wikiskill_recovery_config_incomplete'):
        create_server(root=tmp_path,agent_url='http://unused',agent_token_file='unused',token_file='unused',
            renderer_image='unused',enable_isolated_rendering=True,method_agent_token_file='unused',**flags)
