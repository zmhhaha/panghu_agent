"""Resource-only recovery preserves stopped failures and never resubmits the planner."""
from copy import deepcopy
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError, digest
from video_workbench.app import create_app
from video_workbench.joint_plan_store import JointPlanStore
from video_workbench.joint_plan_workflow import JointPlanWorkflow
from video_workbench.resource_planning_coordinator import ResourcePlanningCoordinator
from video_workbench.store import Store
from test_resource_planning import JointGateway, planning_result, producers


OWNER='owner'


class ResourcePeer:
    def __init__(self):
        self.uploads=0;self.submissions=0;self.polls=[];self.lookups=[]
        self.by_id={};self.by_key={};self.lose_ack=False;self.new_running=False

    def upload(self, document):
        self.uploads+=1
        return dict(input_id=str(uuid4()),sha256=digest(document),path='resource-plan-input.json',role='resource_plan_input')

    def submit(self, operation_id, document, deadline_at, input_ref=None):
        self.submissions+=1;self.document=deepcopy(document);self.deadline_at=deadline_at
        assert input_ref['sha256']==digest(document)
        attempt_id=str(uuid4());job_id=str(uuid4());state='failed' if self.submissions==1 else 'running' if self.new_running else 'completed'
        job=dict(job_id=job_id,state=state,pins=dict(profile='video.resource-plan.v1',profile_revision='1'),
            attempt=dict(attempt_id=attempt_id,fence=self.submissions),stop_proven=state!='running',
            stop_proof=dict(stop_proven=state!='running',attempt_id=attempt_id,fence=self.submissions,
                request_sha256='a'*64,result_sha256='b'*64,resource_ids=['synthetic-resource-'+job_id],observed_at=time.time()))
        self.by_id[job_id]=job;self.by_key[operation_id]=job
        if self.lose_ack:
            self.lose_ack=False
            raise ServiceError('service_timeout',504)
        return deepcopy(job)

    def poll(self, job_id):
        self.polls.append(job_id);return deepcopy(self.by_id[job_id])

    def lookup(self, key):
        self.lookups.append(key);return deepcopy(self.by_key[key])

    def collect(self, job_id, checksum, document):
        assert self.by_id[job_id]['state']=='completed' and checksum==digest(document)
        return planning_result(document,with_node=False)

    def cancel(self, job_id):
        raise AssertionError('No cancellation is needed in these stopped fixtures')


def saved_rows(store, operation_id, version_id):
    with store.store.transaction() as db:
        return tuple(db.execute('SELECT document FROM '+table+' WHERE id=?',(identity,)).fetchone()[0]
            for table,identity in [('joint_plan_operations',operation_id),('joint_plan_versions',version_id)])


def request_for(store, project, original):
    source=store.operation(original['id']);stage=source['resource_planning']
    version=store.version(OWNER,source['version_id'])
    return dict(expected_revision=store.store.project(OWNER,project['id'])['revision'],source_version_id=version['id'],
        expected_head_version_id=None,expected_joint_input_sha256=source['input_sha256'],
        expected_joint_result_sha256=digest(version['result']),expected_resource_input_sha256=stage['input_sha256'],
        expected_producer_catalog_sha256=stage['producer_catalog_sha256'],timeout_seconds=120,
        execution_deadline_at=time.time()+180)


@pytest.fixture
def env(tmp_path):
    business=Store(tmp_path/'business.sqlite3')
    project=business.create_project(OWNER,dict(title='Requests',script='A request waits in a queue before execution.',style='Clear'))
    store=JointPlanStore(business);joint=JointGateway();resource=ResourcePeer()
    coordinator=ResourcePlanningCoordinator(store,resource,producer_catalog=lambda owner:producers())
    workflow=JointPlanWorkflow(store,joint,enabled=True,resource_planner=coordinator)
    original=store.start(OWNER,project['id'],dict(expected_revision=project['revision'],brief=dict(audience='',goal='',tone='conversational')),'original-plan')
    workflow.tick();workflow.tick();source=store.operation(original['id'])
    assert source['status']=='failed' and source['resource_planning']['stop_proven'] is True
    return business,project,store,joint,resource,coordinator,workflow,source


def test_recovery_reuses_planner_and_frozen_input_keeps_failed_rows_and_waits_for_result(env):
    business,project,store,joint,resource,coordinator,workflow,source=env
    request=request_for(store,project,source);before=saved_rows(store,source['id'],source['version_id'])
    recovered=coordinator.recover(OWNER,project['id'],source['id'],request,'recover-once')
    child=store.operation(recovered['id']);candidate=store.version(OWNER,recovered['version_id'])
    assert child['job_id']==source['job_id'] and child['resource_planning']['job_id'] is None
    assert child['resource_planning']['operation_id']!=source['resource_planning']['operation_id']
    for field in ('document','input_sha256','input_ref','joint_input_sha256','joint_result_sha256','producer_catalog_sha256'):
        assert child['resource_planning'][field]==source['resource_planning'][field]
    assert candidate['result']==store.version(OWNER,source['version_id'])['result'] and not candidate['adoptable']
    assert candidate['resource_recovery']['source_resource_job_id']==source['resource_planning']['job_id']
    assert candidate['resource_recovery']['extra_resource_job_budget']==1
    with pytest.raises(ServiceError,match='resources_not_ready'):
        store.adopt(OWNER,project['id'],dict(expected_revision=business.project(OWNER,project['id'])['revision'],
            version_id=candidate['id'],expected_head_version_id=None))
    assert recovered==coordinator.recover(OWNER,project['id'],source['id'],request,'recover-once')
    workflow.tick()
    complete=store.version(OWNER,candidate['id']);assert complete['adoptable'] and complete['resource_plan']['nodes']==[]
    assert resource.uploads==1 and resource.submissions==2 and joint.submissions==1
    assert saved_rows(store,source['id'],source['version_id'])==before
    adopted=store.adopt(OWNER,project['id'],dict(expected_revision=business.project(OWNER,project['id'])['revision'],
        version_id=candidate['id'],expected_head_version_id=None))
    assert adopted['joint_plan']['version_id']==candidate['id']
    assert store.version(OWNER,source['version_id'])['resource_planning']['status']=='failed'
    assert recovered['id']==coordinator.recover(OWNER,project['id'],source['id'],request,'recover-once')['id']
    assert child['deadline_at']<=request['execution_deadline_at'] and child['deadline_at']<=child['created_at']+120


def test_unknown_recovery_child_submission_reads_same_key_without_repaying(env):
    _,project,store,joint,resource,coordinator,workflow,source=env
    recovered=coordinator.recover(OWNER,project['id'],source['id'],request_for(store,project,source),'recover-unknown')
    resource.lose_ack=True;workflow.tick()
    assert store.operation(recovered['id'])['resource_planning']['status']=='submission_unknown'
    restarted=JointPlanWorkflow(store,joint,enabled=True,resource_planner=ResourcePlanningCoordinator(store,resource,
        producer_catalog=lambda owner:(_ for _ in ()).throw(AssertionError('frozen catalogue must be reused'))))
    restarted.tick()
    assert store.version(OWNER,recovered['version_id'])['adoptable']
    assert len(resource.lookups)==1 and resource.submissions==2 and joint.submissions==1


@pytest.mark.parametrize('change,code',[
    (lambda r:r.update(expected_revision=r['expected_revision']+1),'revision_conflict'),
    (lambda r:r.update(expected_head_version_id=str(uuid4())),'joint_plan_head_conflict'),
    (lambda r:r.update(expected_joint_result_sha256='f'*64),'resource_recovery_hash_conflict'),
    (lambda r:r.update(execution_deadline_at=time.time()-1),'resource_recovery_deadline_expired'),
])
def test_request_cas_hash_and_deadline_rejections_have_no_new_task(env,change,code):
    _,project,store,_,resource,coordinator,_,source=env
    request=request_for(store,project,source);change(request)
    with pytest.raises(ServiceError,match=code):coordinator.recover(OWNER,project['id'],source['id'],request,'recover-cas')
    assert resource.submissions==1


def test_wrong_owner_and_actual_prior_stop_or_pin_mismatch_are_rejected(env):
    _,project,store,_,resource,coordinator,_,source=env
    request=request_for(store,project,source);polls=len(resource.polls)
    with pytest.raises(ServiceError,match='not_found'):coordinator.recover('other',project['id'],source['id'],request,'recover-owner')
    assert len(resource.polls)==polls
    original=deepcopy(resource.by_id[source['resource_planning']['job_id']])
    for mutation in (lambda j:j.update(stop_proven=False),lambda j:j['stop_proof'].update(fence=99),
                     lambda j:j['pins'].update(profile='video.joint-plan.v1')):
        resource.by_id[original['job_id']]=deepcopy(original);mutation(resource.by_id[original['job_id']])
        with pytest.raises(ServiceError,match='stop_unproven'):
            coordinator.recover(OWNER,project['id'],source['id'],request,'recover-proof')
    assert resource.submissions==1


def test_second_key_and_recursive_recovery_are_rejected(env):
    _,project,store,_,_,coordinator,workflow,source=env
    request=request_for(store,project,source)
    recovered=coordinator.recover(OWNER,project['id'],source['id'],request,'recover-first')
    with pytest.raises(ServiceError,match='already_exists'):
        coordinator.recover(OWNER,project['id'],source['id'],request,'recover-second')
    with pytest.raises(ServiceError,match='recursive'):
        coordinator.recover(OWNER,project['id'],recovered['id'],dict(request,source_version_id=recovered['version_id']),'recover-recursive')
    with pytest.raises(ServiceError,match='idempotency_conflict'):
        coordinator.recover(OWNER,project['id'],source['id'],dict(request,timeout_seconds=121),'recover-first')


def test_project_edit_during_actual_stop_poll_cannot_commit_stale_recovery(env):
    business,project,store,_,resource,coordinator,_,source=env
    request=request_for(store,project,source);poll=resource.poll
    def changed(job_id):
        with business.transaction() as db:
            current=business._project(db,OWNER,project['id']);current['style']='new content';business._save(db,current)
        return poll(job_id)
    resource.poll=changed
    with pytest.raises(ServiceError,match='revision_conflict'):
        coordinator.recover(OWNER,project['id'],source['id'],request,'recover-race')
    assert resource.submissions==1


def test_fresh_revision_does_not_make_changed_content_a_valid_source(env):
    business,project,store,_,resource,coordinator,_,source=env
    with business.transaction() as db:
        current=business._project(db,OWNER,project['id']);current['style']='a different composition';business._save(db,current)
    request=request_for(store,project,source)
    with pytest.raises(ServiceError,match='joint_plan_stale'):
        coordinator.recover(OWNER,project['id'],source['id'],request,'recover-stale')
    assert resource.submissions==1


def test_new_recovery_result_waits_for_authentic_stopped_child(env):
    _,project,store,joint,resource,coordinator,workflow,source=env
    recovered=coordinator.recover(OWNER,project['id'],source['id'],request_for(store,project,source),'recover-new-stop')
    resource.new_running=True;workflow.tick()
    stage=store.operation(recovered['id'])['resource_planning'];job=resource.by_id[stage['job_id']]
    job['state']='completed';workflow.tick()
    assert store.operation(recovered['id'])['resource_planning']['error']=='resource_planning_stop_unproven'
    assert not store.version(OWNER,recovered['version_id'])['adoptable']
    job['stop_proven']=job['stop_proof']['stop_proven']=True;workflow.tick()
    assert store.version(OWNER,recovered['version_id'])['adoptable']
    assert joint.submissions==1 and resource.submissions==2


def test_http_recovery_has_shared_auth_csrf_owner_and_strict_body(tmp_path):
    joint=JointGateway();resource=ResourcePeer()
    app=create_app(db_path=tmp_path/'http.sqlite3',local_mode=True,background=False,trusted_hosts={'testserver'},
        gateway=object(),joint_plan_gateway=joint,joint_plan_enabled=True,resource_planning_gateway=resource,
        resource_producer_catalog=lambda owner:producers())
    with TestClient(app) as client:
        client.headers['Origin']='https://testserver'
        csrf=client.get('/api/session').json()['csrf_token'];client.headers['X-CSRF-Token']=csrf
        created=client.post('/api/projects',json=dict(title='Requests',script='A request waits in a queue before execution.',style='Clear'))
        assert created.status_code==200,created.json()
        project=created.json()
        original=client.post('/api/projects/'+project['id']+'/joint-plan',json=dict(expected_revision=project['revision']),
            headers={'Idempotency-Key':'http-original-plan'}).json()
        app.state.joint_plan_workflow.tick();app.state.joint_plan_workflow.tick()
        source=app.state.joint_plan_store.operation(original['id']);stage=source['resource_planning']
        current=client.get('/api/projects/'+project['id']).json()
        request=dict(expected_revision=current['revision'],source_version_id=source['version_id'],expected_head_version_id=None,
            expected_joint_input_sha256=source['input_sha256'],expected_joint_result_sha256=stage['joint_result_sha256'],
            expected_resource_input_sha256=stage['input_sha256'],expected_producer_catalog_sha256=stage['producer_catalog_sha256'],
            timeout_seconds=120,execution_deadline_at=time.time()+180)
        url='/api/projects/'+project['id']+'/joint-plan-operations/'+source['id']+'/resource-recovery'
        invalid=client.post(url,json=dict(request,unexpected=True),headers={'Idempotency-Key':'http-recovery'})
        assert invalid.status_code==422
        del client.headers['X-CSRF-Token']
        assert client.post(url,json=request,headers={'Idempotency-Key':'http-recovery'}).status_code==403
        client.headers['X-CSRF-Token']=csrf
        response=client.post(url,json=request,headers={'Idempotency-Key':'http-recovery'})
        assert response.status_code==202 and response.json()['kind']=='joint-plan-resource-recovery'
        assert response.json()['job_id']==source['job_id'] and joint.submissions==resource.submissions==1
        assert client.post(url,json=request,headers={'Idempotency-Key':'http-recovery'}).json()==response.json()
