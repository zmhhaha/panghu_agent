"""Real V1 programmatic validation through the production retaining transport."""
import json,uuid
from pathlib import Path
from ppt_service.control.execution import RuntimeConfig,ControlError
from ppt_service.control.pods import Docker,DockerSandbox
from ppt_service.control.staging import read_json,atomic_json,relative
from ppt_service.control.render_validation import hash_file

CASE=Path('/root/ppt-service-webdev-20261021/publisher-live-v1-20261022')
SOURCE=Path('/root/ppt-service-sample/acceptance-v3/attempt-ed00fb6abfe2489b8f7834ce43f66d9b')
def main():
    config=RuntimeConfig(CASE/'runtime',code_root=CASE/'code/control');sandbox=DockerSandbox(config);scope=str(uuid.uuid4());sandbox.scope_uid=scope
    report={'scope_uid':scope,'database_publish_called':False,'model_calls':0,'visual_review':'pending','powerpoint_acceptance':'pending'}
    try:
        result=sandbox.validate(CASE/'snapshot',read_json(CASE,'request.json'));report['result']={k:result.get(k) for k in ('accepted','programmatic_checks_passed','required_pending_gates','completed_programmatic_gates','persistent_evidence','validation_status')}
        retained=Path(result['persistent_evidence']['path']);bundle=read_json(retained,'transport-manifest.json');owned=result['independent_programmatic_evidence'];report['programmatic']={k:owned.get(k) for k in ('validation_id','controller_nonce','worker_image','ppt_master_commit','render_exit_code','delivery_exit_code','delivery','native','preview_group_stopped','command_groups_stopped','excluded_cache_roots','preview_directory_existed_before_render','source','pngs')}
        assert result['accepted'] is False;assert result['programmatic_checks_passed'] is True
        assert 'independent_visual_review' in result['required_pending_gates'];assert 'independent_postflight_and_delivery' in result['required_pending_gates']
        assert len(owned['pngs'])==6;assert bundle['writers_stopped'] is True;assert bundle['transport_verified'] is True;assert bundle['controller_error_code'] is None
        for png in owned['pngs']:
            key=relative('independent/'+owned['validation_id']+'/'+png['relative_path']);actual=hash_file(retained,key)
            assert actual['sha256']==png['sha256'];assert actual['bytes']==png['bytes']
        report['persistent_pngs_verified']=6;report['retained_files']=len(bundle['files']);report['retained_bytes']=bundle['bytes']
    except Exception as exc:
        report['error_code']=exc.code if isinstance(exc,ControlError) else type(exc).__name__;report['evidence_path']=getattr(exc,'evidence_path',None)
    report['remaining_containers']=list(Docker().ids(scope))
    index=read_json(CASE/'snapshot','snapshot.json')['indexes'];count=0
    for name,part in index.items():
        for row in part['files']:
            assert hash_file(SOURCE/name,row['path'])['sha256']==row['sha256'];count+=1
    report['original_source_unchanged']=True;report['original_source_files_verified']=count
    from ppt_service.control.staging import copy_file
    previous=CASE/'render-check/production-transport-result.json'
    if previous.exists() and not (CASE/'render-check/production-transport-result.initial.json').exists():copy_file(previous.parent,previous.name,CASE/'render-check/production-transport-result.initial.json',8*1024**2)
    atomic_json(CASE/'render-check/production-transport-result.json',report)
    print(json.dumps(report,ensure_ascii=False))
    if report.get('error_code') or report['remaining_containers']:raise SystemExit(2)
if __name__=='__main__':main()
