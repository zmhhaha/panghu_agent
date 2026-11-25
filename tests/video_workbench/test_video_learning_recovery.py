"""Source-only recovery preserves stopped failures and pays for no reauthor."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from test_video_learning_service import system, real_mp4, start, finish


def failed_render(system,raw):
    service,author,reviewer,renderer=system
    renderer.fail=True
    project,_=start(service,raw)
    stopped=finish(service,project)
    run=service.store.read('experiments','developer',stopped['latest_experiment_id'])
    op=service.store.read('operations','developer',run['operation_ids'][-1])
    candidate=run['candidates'][-1]
    body=dict(request_id=uuid4().hex,failed_experiment_id=run['id'],operation_id=op['id'],
        operation_input_sha256=op['input_sha256'],origin=dict(kind='candidate',sha256=candidate['source_sha256']),
        source=deepcopy(candidate['source']),method=deepcopy(candidate['method']),
        reason='Explicit same-content recovery with current runtime admission.')
    return service,stopped,run,op,body


def test_recovery_bypasses_author_and_preserves_failed_run_and_goal(system,real_mp4):
    service,project,prior,operation,body=failed_render(system,real_mp4)
    author,reviewer,renderer=system[1:]
    author_calls=len(author.submissions)
    old_project=service.store.read('projects','developer',project['id'])
    renderer.fail=False
    restored=service.recover_source('developer',project['id'],body)
    run=service.store.read('experiments','developer',restored['latest_experiment_id'])
    assert run['stage']=='render' and run['mode']=='source_recovery'
    assert run['budget']==dict(max_rounds=1,max_seconds=3600)
    assert run['frozen']['goal']==prior['frozen']['goal']
    assert run['frozen']['reference_observation']==prior['frozen']['reference_observation']
    assert run['frozen']['knowledge_snapshot']==prior['frozen']['knowledge_snapshot']
    assert run['source_recovery']['model_authorship_claimed'] is False
    assert service._document(run,'create')['constraints']['output_contract']['schema'].endswith('author-output-guide.v1')
    assert 'author_operation_id' not in run['candidates'][0]
    assert run['candidates'][0]['source']==prior['candidates'][0]['source']
    final=finish(service,restored)
    assert final['state']=='completed' and len(author.submissions)==author_calls
    assert len(reviewer.submissions)==1
    assert service.store.read('experiments','developer',prior['id'])==prior
    assert service.store.read('operations','developer',operation['id'])==operation
    current=service.store.read('projects','developer',project['id'])
    assert current['adopted_candidate_id']==old_project['adopted_candidate_id']
    assert service.recover_source('developer',project['id'],body)['latest_experiment_id']==run['id']
    assert len(service.store.list('experiments','developer',project_id=project['id']))==2


def test_recovery_rechecks_idempotency_body_and_original_source_identity(system,real_mp4):
    service,project,prior,operation,body=failed_render(system,real_mp4)
    bad=deepcopy(body);bad['origin']['sha256']='f'*64
    with pytest.raises(ServiceError,match='source_changed'):
        service.recover_source('developer',project['id'],bad)
    bad=deepcopy(body);bad['operation_input_sha256']='f'*64
    with pytest.raises(ServiceError,match='operation_changed'):
        service.recover_source('developer',project['id'],bad)
    service.recover_source('developer',project['id'],body)
    changed=deepcopy(body);changed['reason']='Changed request body'
    with pytest.raises(ServiceError,match='idempotency_conflict'):
        service.recover_source('developer',project['id'],changed)


def test_recovery_owner_and_stop_evidence_fail_closed(system,real_mp4):
    service,project,prior,operation,body=failed_render(system,real_mp4)
    with pytest.raises(ServiceError):service.recover_source('another-owner',project['id'],body)
    prior['stop_proven']=False
    service.store.put('experiments',prior)
    with pytest.raises(ServiceError,match='stop_required'):
        service.recover_source('developer',project['id'],body)
    assert len(service.store.list('experiments','developer',project_id=project['id']))==1


def test_recovery_validates_repaired_source_before_creating_new_attempt(system,real_mp4):
    service,project,prior,operation,body=failed_render(system,real_mp4)
    body['source']['files']['scene.js']+='\n// change without new source identity'
    with pytest.raises(ServiceError):service.recover_source('developer',project['id'],body)
    assert len(service.store.list('experiments','developer',project_id=project['id']))==1
    assert len(system[1].submissions)==1 and len(system[3].calls)==1


def test_recovered_author_output_is_explicitly_operator_attributed(system,real_mp4):
    service,author,reviewer,renderer=system
    original=author.prepare_submission
    def fail_author(*args,**kwargs):
        submit=original(*args,**kwargs)
        def call():
            job=submit();author._job(job['job_id']).update(state='failed',stop_proven=True)
            return job
        return call
    author.prepare_submission=fail_author
    project,_=start(service,real_mp4);project=finish(service,project)
    run=service.store.read('experiments','developer',project['latest_experiment_id'])
    operation=service.store.read('operations','developer',run['operation_ids'][-1])
    # This is new operator-supplied source, never a claim of recovered worker bytes.
    from test_picture_learning_service import source
    body=dict(request_id=uuid4().hex,failed_experiment_id=run['id'],operation_id=operation['id'],
        operation_input_sha256=operation['input_sha256'],origin=dict(kind='operator_reconstruction',sha256='a'*64),
        source=source(),method=None,reason='Repair a malformed retained output proposal.')
    recovered=service.recover_source('developer',project['id'],body)
    new=service.store.read('experiments','developer',recovered['latest_experiment_id'])
    assert new['source_recovery']['origin_verified_by_host'] is False
    assert new['source_recovery']['operator_supplied'] is True
    assert service.store.read('experiments','developer',run['id'])==run
    assert finish(service,recovered)['state']=='completed'
    assert len(author.submissions)==1


def test_private_http_recovery_preserves_owner_boundary_and_original_request(system,real_mp4):
    import json
    from types import SimpleNamespace
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from video_workbench.animation_creation_http import CreationHTTPServer
    from test_animation_method_http import serving,TOKEN
    service,project,prior,operation,body=failed_render(system,real_mp4)
    with serving(CreationHTTPServer(SimpleNamespace(),TOKEN,pump=False,video_learning_service=service)) as server:
        url='http://127.0.0.1:'+str(server.server_port)+'/api/video-lab/projects/'+project['id']+'/recover-source'
        def request(owner):
            return Request(url,data=json.dumps(body).encode(),method='POST',headers={
                'Authorization':'Bearer '+TOKEN,'X-Animation-Creation-Owner':owner,'Content-Type':'application/json'})
        with pytest.raises(HTTPError) as denied:urlopen(request('other-owner'),timeout=10)
        assert denied.value.code==404
        with urlopen(request('developer'),timeout=10) as response:
            assert response.status==202
            accepted=json.load(response)
        with urlopen(request('developer'),timeout=10) as response:
            assert json.load(response)['latest_experiment_id']==accepted['latest_experiment_id']
        assert len(service.store.list('experiments','developer',project_id=project['id']))==2
