"""Publication must stay closed until independent acceptance has a real issuer."""
import copy,json,uuid
import pytest
from ppt_service.control.execution import Attempt,RuntimeConfig,ControlError
from ppt_service.control.publisher import Publisher,publication_readiness,validate_private,REQUIRED_PENDING_GATES
from ppt_service.control.staging import atomic_json

@pytest.mark.parametrize('phase',['generate','revise'])
def test_static_success_and_forged_flags_do_not_authorize_delivery(phase):
    original={'accepted':True,'status':'candidate','manifest':{'status':'candidate','quality':{'visual':'passed'},'independent_visual_review':True},'integrity':{'slide_count':6,'chart_data_verified':True},'evidence':{'official_gates_verified':True,'independent_visual_review':True,'independent_raster_regeneration_and_binding':True,'independent_postflight_and_delivery':True},'required_pending_gates':[]}
    before=copy.deepcopy(original);result=publication_readiness(phase,original)
    assert result['accepted'] is False;assert result['programmatic_checks_passed'] is True;assert result['validation_status']=='gate_pending';assert result['error_code']=='independent_acceptance_required'
    assert result['required_pending_gates']==list(REQUIRED_PENDING_GATES)
    assert result['manifest']['status']=='candidate';assert original==before

@pytest.mark.parametrize('phase',['plan_initial','plan_options','prepare'])
def test_planning_and_prepare_are_not_reclassified(phase):
    result={'accepted':True,'status':'awaiting_confirmation' if phase.startswith('plan') else 'prepared'}
    assert publication_readiness(phase,result) is result

@pytest.mark.parametrize('phase',['generate','revise'])
@pytest.mark.parametrize('forged',[{'accepted':True},{'accepted':True,'programmatic_checks_passed':True,'required_pending_gates':[]},{'accepted':True,'trusted_certificate':{'issuer':'trusted-controller','rendered':True,'visual_passed':True,'delivery_passed':True}}])
def test_direct_promote_cannot_bypass_missing_acceptance_issuer(tmp_path,phase,forged):
    class NoDatabaseAccess:
        def __getattr__(self,key):raise AssertionError('publication must reject before DB or filesystem changes')
    attempt=Attempt(str(uuid.uuid4()),str(uuid.uuid4()),1,1,phase,{'hash':'a'*64},'session')
    publisher=Publisher(RuntimeConfig(tmp_path),None)
    with pytest.raises(ControlError,match='independent_acceptance_required'):publisher.promote(NoDatabaseAccess(),attempt,tmp_path,forged)
    assert list(tmp_path.iterdir())==[]

@pytest.mark.parametrize('approval,kind',[('denied','operator_permission'),(None,'business_input')])
def test_needs_input_original_classification_and_manifest_survive(tmp_path,approval,kind):
    root=tmp_path/'project'/'deck';root.mkdir(parents=True);(root/'source.md').write_text('Public source')
    result=tmp_path/'result';result.mkdir()
    request={'job_id':'fixture','attempt':1,'phase':'generate','session_id':'session','input_snapshot_sha256':'a'*64,'pages':3,'independent_visual_review':True,'independent_delivery_check':True,'accepted':True}
    atomic_json(result/'execution.json',{**{k:request[k] for k in ('job_id','attempt','phase','session_id')},'status':'completed','gateway_stopped':True,'preview_stopped':True})
    manifest={'schema_version':1,'phase':'generate','status':'needs_input','input_snapshot_sha256':'a'*64,'project_root':'deck','plan':{'approval_status':approval},'questions':['Actual missing input'],'artifacts':[],'slides':[],'quality':{}}
    atomic_json(result/'manifest.json',manifest);before=(result/'manifest.json').read_bytes()
    checked=validate_private(tmp_path,request)
    assert checked['accepted'] is False;assert checked['status']=='needs_input';assert checked['blocked_kind']==kind;assert checked['manifest']['status']=='needs_input';assert checked['actor_record_preserved'] is True
    assert (result/'manifest.json').read_bytes()==before
