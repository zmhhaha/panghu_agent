from copy import deepcopy

from agent_service.contracts import digest
from agent_service.spatial_rendering_contract import foundation_guidance
from agent_service.profiles.video_joint_plan_contract import validate_joint_plan_result
from test_joint_plan_api import env, start, adopt, proposal
from test_spatial_rendering_route import intent


def test_planning_offer_is_frozen_and_required_intent_survives_adoption(env):
    client,app,gateway,project=env
    app.state.joint_plan_store.rendering_foundation=True
    original=gateway.collect
    def collect(job_id,input_sha256,document):
        result=original(job_id,input_sha256,document)
        assert document['rendering_foundation']==foundation_guidance()
        result['rendering_intent']=intent(engine='html-svg',space_mode='planar',
            required_features=['scale','coverage'],optional_features=[])
        return result
    gateway.collect=collect
    assert start(client,project,preserve_script=True).status_code==202
    app.state.joint_plan_workflow.tick()
    state=app.state.joint_plan_store.project('local-developer',project['id'])
    candidate=state['versions'][0]
    assert candidate['result']['rendering_intent']['space_mode']=='planar'
    adopted=adopt(client,app,project,candidate)
    assert adopted.status_code==200
    assert adopted.json()['joint_plan']['rendering_intent']==candidate['result']['rendering_intent']


def test_new_offer_rejects_missing_intent_while_legacy_result_remains_valid(env):
    client,app,gateway,project=env
    assert start(client,project,preserve_script=True).status_code==202
    op=app.state.joint_plan_store.project('local-developer',project['id'])['operation']
    with app.state.store.transaction() as db:
        saved=app.state.joint_plan_store._op(db,op['id'])
    document=saved['document'];result=proposal(document,digest(document))
    assert validate_joint_plan_result(result,digest(document),document)
    fresh=deepcopy(document);fresh['rendering_foundation']=foundation_guidance()
    result['input_sha256']=digest(fresh)
    import pytest
    from agent_service.contracts import ServiceError
    with pytest.raises(ServiceError):
        validate_joint_plan_result(result,digest(fresh),fresh)
