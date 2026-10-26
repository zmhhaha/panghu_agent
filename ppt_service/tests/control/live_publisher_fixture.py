"""Read-only frozen attempt7 verification; no DB, model, publication or evidence rewriting."""
from __future__ import annotations
import argparse,hashlib,json,os,uuid
from pathlib import Path
from ppt_service.control.execution import RuntimeConfig,ControlError
from ppt_service.control.staging import copy_file,copy_tree,read_json,open_regular,relative,atomic_json,make_readonly

SOURCE=Path('/root/ppt-service-sample/acceptance-v3/attempt-ed00fb6abfe2489b8f7834ce43f66d9b')
ORACLE=Path('/root/ppt-service-sample/acceptance-v3/trusted-chart-oracle.retry7.json')
MANIFEST_SHA='cef634bae4e6b9892c03b72f71c6a2f29b8c0c06defd3a147cff82b066d57471'
PPTX_SHA='dcff0a90aa089ef4e46746d516f95fa148da0223cf67ff2daecec28108006068'
INPUT_SHA='f66d2e18eb73ea458f4e9bd6761be760e1c6d6741e4f9c4fa928b73274ef587b'
SPEC_SHA='ebe5f57d179ad1978ec9cab6be0637518a11b41d5aac381aecdfaf81fd7e2cf3'
LOCK_SHA='e5d9bd1f82fa5d780942aa6333af28c0ceb29aa645d15035b8ec5fb99631a344'

def sha(root,key,limit=64*1024**2):
    h=hashlib.sha256()
    with open_regular(root,key,limit) as f:
        while chunk:=f.read(65536):h.update(chunk)
    return h.hexdigest()
def collect(case):
    if (case/'snapshot').exists():raise ControlError('fixture_copy_already_exists')
    actual=sha(SOURCE/'result','manifest.json',4*1024**2)
    if actual!=MANIFEST_SHA:raise ControlError('frozen_manifest_hash_mismatch')
    manifest=read_json(SOURCE/'result','manifest.json');project=relative(manifest['project_root']);execution=read_json(SOURCE/'result','execution.json')
    if manifest['phase']!='generate' or manifest['input_snapshot_sha256']!=INPUT_SHA:raise ControlError('frozen_input_identity_mismatch')
    if sha(SOURCE/'project'/project,'design_spec.md')!=SPEC_SHA or sha(SOURCE/'project'/project,'spec_lock.md')!=LOCK_SHA:raise ControlError('frozen_approved_spec_lock_mismatch')
    pptxs=[a for a in manifest.get('artifacts',[]) if a.get('role')=='pptx']
    if len(pptxs)!=1 or sha(SOURCE/'project'/project,relative(pptxs[0]['path']))!=PPTX_SHA:raise ControlError('frozen_pptx_hash_mismatch')
    oracle=read_json(ORACLE.parent,ORACLE.name)
    charts=oracle if isinstance(oracle,list) else oracle.get('expected_charts',oracle.get('charts'))
    if not isinstance(charts,list) or len(charts)!=1:raise ControlError('trusted_oracle_shape_mismatch')
    expected=charts[0]
    if expected.get('slide')!=4 or expected.get('categories')!=['Q1','Q2','Q3'] or [(x.get('name'),x.get('values')) for x in expected.get('series',[])]!=[('Cloud',[12,15,19]),('Services',[8,9,11])]:raise ControlError('trusted_oracle_values_mismatch')
    case.mkdir(parents=True,exist_ok=True);snapshot=case/'snapshot';snapshot.mkdir()
    index={}
    for name in ('project','result','home'):index[name]=copy_tree(SOURCE/name,snapshot/name,skip_home_bootstrap=name=='home')
    atomic_json(snapshot/'snapshot.json',{'source_attempt':'ed00fb6abfe2489b8f7834ce43f66d9b','indexes':index})
    make_readonly(snapshot)
    copy_file(ORACLE.parent,ORACLE.name,case/'trusted-chart-oracle.retry7.json',4*1024**2)
    request={k:execution.get(k) for k in ('job_id','attempt','phase','session_id')}
    request.update({'input_snapshot_sha256':INPUT_SHA,'pages':6,'expected_charts':charts,'approved_design_spec_sha256':SPEC_SHA,'approved_spec_lock_sha256':LOCK_SHA})
    if not all(request[k] for k in ('job_id','attempt','phase','session_id')):raise ControlError('execution_identity_missing')
    atomic_json(case/'request.json',request)
    selected={k:execution.get(k) for k in ('job_id','attempt','phase','session_id','status','hermes_status','gateway_stopped','preview_stopped','manifest_status','candidate_output_status')}
    files=[{'name':e.name,'kind':'directory' if e.is_dir(follow_symlinks=False) else 'file' if e.is_file(follow_symlinks=False) else 'link_or_special'} for e in os.scandir(SOURCE)]
    evidence={'manifest_sha256':actual,'pptx_sha256':PPTX_SHA,'spec_sha256':SPEC_SHA,'lock_sha256':LOCK_SHA,'oracle_sha256':sha(ORACLE.parent,ORACLE.name),'input_snapshot_sha256':INPUT_SHA,'manifest_status':manifest.get('status'),'execution':selected,'source_top_level':files,'copy_index':{k:{'files':len(v['files']),'bytes':v['bytes']} for k,v in index.items()},'original_manifest_unchanged':sha(SOURCE/'result','manifest.json',4*1024**2)==MANIFEST_SHA,'source_readonly_mount':True}
    atomic_json(case/'collection-evidence.json',evidence);print(json.dumps(evidence,ensure_ascii=False))

def run(case,forged=False):
    if (case/'sandbox-result.json').exists() and not (case/'sandbox-result.initial.json').exists():
        copy_file(case,'sandbox-result.json',case/'sandbox-result.initial.json',8*1024**2)
    from ppt_service.control.pods import Docker,DockerSandbox
    runtime=case/'runtime';runtime.mkdir(exist_ok=True)
    config=RuntimeConfig(runtime,code_root=case/'code/control')
    sandbox=DockerSandbox(config);uid=str(uuid.uuid4());sandbox.scope_uid=uid
    request=read_json(case,'request.json')
    if forged:request.update({'accepted':True,'independent_raster_regeneration_and_binding':True,'independent_visual_review':True,'independent_postflight_and_delivery':True,'required_pending_gates':[],'trusted_certificate':{'issuer':'trusted-controller','all_gates_passed':True}})
    try:
        result=sandbox.validate(case/'snapshot',request)
    except Exception as exc:
        result={'accepted':False,'error_code':exc.code if isinstance(exc,ControlError) else type(exc).__name__}
    remaining=Docker().ids(uid)
    report={'scope_uid':uid,'remaining_containers':list(remaining),'result':result,'database_publish_called':False,'model_calls':0,'powerpoint_acceptance':'pending'}
    atomic_json(case/('sandbox-result.forged.json' if forged else 'sandbox-result.json'),report)
    summary={key:result.get(key) for key in ('accepted','status','programmatic_checks_passed','validation_status','error_code','required_pending_gates','collection','evidence','integrity','manual_powerpoint_acceptance')}
    print(json.dumps({**report,'result':summary},ensure_ascii=False))
    if remaining:raise ControlError('sandbox_cleanup_failed')

def probe(case):
    """Run current pure checks on the untouched private copy; not acceptance."""
    from ppt_service.control.publisher import complete_index,check_session_database
    from ppt_service.worker.candidate_validation import validate_manifest
    snapshot=case/'snapshot';request=read_json(case,'request.json');manifest=read_json(snapshot/'result','manifest.json');report={}
    for name,func in (
        ('complete_index',lambda:complete_index(snapshot,manifest)),
        ('session_database',lambda:check_session_database(snapshot,request['session_id'])),
        ('manifest_integrity',lambda:validate_manifest(snapshot/'project',complete_index(snapshot,manifest)[0],expected_charts=request['expected_charts']))):
        try:
            result=func()
            if name=='complete_index':report[name]={'ok':True,**result[1]}
            else:report[name]={'ok':True,'result':result}
        except Exception as exc:report[name]={'ok':False,'error_code':exc.code if isinstance(exc,ControlError) else type(exc).__name__,'message':str(exc)[:300] if type(exc).__name__=='ValidationError' else None}
    atomic_json(case/'probe-result.json',report);print(json.dumps(report,ensure_ascii=False))

def verify_source(case):
    index=read_json(case/'snapshot','snapshot.json')['indexes'];count=0
    for name,part in index.items():
        for row in part['files']:
            if sha(SOURCE/name,row['path'])!=row['sha256']:raise ControlError('original_source_changed')
            count+=1
    report={'original_source_unchanged':True,'verified_files':count,'manifest_sha256':sha(SOURCE/'result','manifest.json'),'database_publish_called':False,'model_calls':0}
    atomic_json(case/'source-after-validation.json',report);print(json.dumps(report))

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('collect','run','forged','probe','verify-source'));p.add_argument('--case',type=Path,required=True);args=p.parse_args()
    {'collect':collect,'run':run,'forged':lambda case:run(case,True),'probe':probe,'verify-source':verify_source}[args.action](args.case)
if __name__=='__main__':main()
