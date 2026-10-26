"""Independent validator entrypoint and fenced publication from PRIVATE snapshots.

Only fixed image-owned scripts execute. Candidate reports remain untrusted.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,os,subprocess,tempfile,uuid
from contextlib import closing
from pathlib import Path
from .execution import ControlError,Attempt,RuntimeConfig
from .staging import relative,read_json,open_regular,copy_file,copy_tree,atomic_json,make_readonly

def complete_index(snapshot:Path,manifest:dict):
    original=copy.deepcopy(manifest);project=relative(manifest.get('project_root'))
    root=snapshot/'project'/project
    declarations=manifest.get('artifacts',[])
    if not isinstance(declarations,list) or len(declarations)>2048:raise ControlError('artifact_limit')
    indexed={};folded=set()
    for row in declarations:
        name=relative(row.get('path'))
        if name.casefold() in folded:raise ControlError('duplicate_artifact')
        folded.add(name.casefold());indexed[name]=row
    files=[];total=0
    preview_refs={s.get('preview_path') for s in manifest.get('slides',[]) if isinstance(s,dict)}
    for folder,dirs,names in os.walk(root,followlinks=False):
        for name in dirs:
            if (Path(folder)/name).is_symlink():raise ControlError('linked_artifact')
        for name in names:
            path=Path(folder)/name;key=path.relative_to(root).as_posix();relative(key)
            with open_regular(root,key,64*1024**2) as stream:
                h=hashlib.sha256();size=0
                while chunk:=stream.read(65536):h.update(chunk);size+=len(chunk)
            total+=size
            if len(files)>=2048 or total>256*1024**2:raise ControlError('artifact_limit')
            existing=indexed.get(key)
            if existing and (existing.get('bytes')!=size or existing.get('sha256')!=h.hexdigest()):raise ControlError('declared_hash_mismatch')
            suffix=path.suffix.lower()
            role='preview' if key in preview_refs else 'pptx' if suffix=='.pptx' else 'svg' if suffix=='.svg' else 'source'
            mime={'.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation','.png':'image/png','.svg':'image/svg+xml','.json':'application/json','.md':'text/markdown'}.get(suffix,'application/octet-stream')
            files.append({'path':key,'bytes':size,'sha256':h.hexdigest(),'role':existing.get('role',role) if existing else role,'media_type':existing.get('media_type',mime) if existing else mime})
    actual={r['path'] for r in files}
    if set(indexed)-actual:raise ControlError('missing_declared_artifact')
    result=copy.deepcopy(manifest);result['artifacts']=sorted(files,key=lambda x:x['path'])
    return result,{'original_status':original.get('status'),'completed_artifact_count':len(files),'total_bytes':total,'worker_declarations_changed':False}

class OfficialGates:
    def __init__(self,output:Path):self.output=output;self.output.mkdir(parents=True,exist_ok=True)
    def _run(self,name,args):
        with open(self.output/(name+'.private.log'),'wb') as log:
            completed=subprocess.run(args,cwd='/',stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=90,check=False,env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONPATH':'/opt/ppt-service','HOME':'/tmp','PYTHONDONTWRITEBYTECODE':'1'})
        if completed.returncode:raise ControlError('independent_quality_gate_failed')
    def run(self,project:Path,phase:str,pages:int):
        scripts='/opt/ppt-master/skills/ppt-master/scripts/'
        python='/opt/hermes/.venv/bin/python'
        self._run('attribution',[python,scripts+'attribution_guard.py'])
        self._run('project_validate',[python,scripts+'project_manager.py','validate',str(project)])
        if phase in ('generate','revise'):
            report=self.output/'svg-quality.json'
            self._run('svg_final',[python,scripts+'svg_quality_checker.py',str(project),'--stage','final','--json','--json-output',str(report)])
            data=read_json(self.output,'svg-quality.json')
            # Check actual report shape and page roster, never empty exit-0 output.
            if isinstance(data,list):rows=data
            elif isinstance(data,dict):rows=data.get('files',data.get('results',data.get('reports')))
            else:rows=None
            if isinstance(rows,dict):rows=list(rows.values())
            if not isinstance(rows,list) or len(rows)!=pages:raise ControlError('quality_report_roster_unverified')
            for row in rows:
                if not isinstance(row,dict) or row.get('errors') or row.get('passed') is False or row.get('status') in ('failed','error'):raise ControlError('independent_quality_gate_failed')
        return {'official_gates_verified':True}

def check_session_database(snapshot:Path,session_id:str):
    import sqlite3
    home=snapshot/'home';databases=[]
    for folder,dirs,files in os.walk(home,followlinks=False):
        for name in files:
            if name.endswith('.db'):
                key=(Path(folder)/name).relative_to(home).as_posix()
                with open_regular(home,key,64*1024**2):pass
                databases.append(key)
    if not databases or len(databases)>16:raise ControlError('session_database_missing')
    found=False
    with tempfile.TemporaryDirectory(prefix='ppt-session-check-') as temp:
        scratch=Path(temp)/'home';copy_tree(home,scratch)
        from .staging import make_writable
        make_writable(scratch)
        for key in databases:
            try:
                # sqlite3.Connection.__exit__ only commits/rolls back; it does
                # not close the handle. Close before the scratch tree is removed
                # so Windows can delete the copied session database as well.
                with closing(sqlite3.connect('file:'+str(scratch/key)+'?mode=ro',uri=True,timeout=1)) as db:
                    if db.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ControlError('session_database_inconsistent')
                    table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
                    if table and db.execute('SELECT 1 FROM sessions WHERE id=?',(session_id,)).fetchone():found=True
            except sqlite3.DatabaseError:raise ControlError('session_database_inconsistent') from None
    if not found:raise ControlError('checkpoint_session_not_found')

REQUIRED_PENDING_GATES=(
    'independent_raster_regeneration_and_binding',
    'independent_visual_review',
    'independent_postflight_and_delivery',
)

def publication_readiness(phase:str,result:dict,*,programmatic_evidence=None,validation_id=None,source_manifest_sha256=None):
    """Runtime programmatic evidence is not visual or full delivery acceptance."""
    if phase not in ('generate','revise'):return result
    completed=[];pending=list(REQUIRED_PENDING_GATES);payload=None
    if programmatic_evidence is not None:
        from .render_validation import ProgrammaticEvidence,RASTER_GATE
        if type(programmatic_evidence) is not ProgrammaticEvidence or not programmatic_evidence.verify(validation_id,source_manifest_sha256):raise ControlError('invalid_programmatic_evidence')
        payload=programmatic_evidence.report();completed=list(payload['completed_programmatic_gates'])
        if RASTER_GATE in completed:pending.remove(RASTER_GATE)
    clean={k:v for k,v in result.items() if k!='independent_programmatic_evidence'}
    answer={**clean,'accepted':False,'programmatic_checks_passed':True,
            'validation_status':'gate_pending','error_code':'independent_acceptance_required',
            'required_pending_gates':pending,'completed_programmatic_gates':completed}
    if payload is not None:answer['independent_programmatic_evidence']=payload
    return answer

def validate_private(snapshot:Path,request:dict,gate_runner=None,*,evidence_root=None):
    from ppt_service.worker.candidate_validation import validate_manifest
    execution=read_json(snapshot/'result','execution.json')
    for key in ('job_id','attempt','phase','session_id'):
        if execution.get(key)!=request.get(key):raise ControlError('execution_identity_mismatch')
    if execution.get('status')!='completed' or execution.get('gateway_stopped') is not True or execution.get('preview_stopped') is not True:raise ControlError('worker_not_completed')
    manifest=read_json(snapshot/'result','manifest.json')
    if manifest.get('phase')!=request['phase'] or manifest.get('input_snapshot_sha256')!=request['input_snapshot_sha256']:raise ControlError('manifest_identity_mismatch')
    complete,collection=complete_index(snapshot,manifest)
    if manifest.get('status')=='needs_input':
        plan=manifest.get('plan',{})
        approval=plan.get('approval_status') if isinstance(plan,dict) else None
        return {'accepted':False,'status':'needs_input','blocked_kind':'operator_permission' if approval in ('pending','denied','timed_out') else 'business_input','manifest':complete,'collection':collection,'actor_record_preserved':True}
    check_session_database(snapshot,request['session_id'])
    phase=request['phase'];pages=request['pages']
    if phase in ('generate','revise') and not isinstance(request.get('expected_charts'),list):raise ControlError('trusted_chart_oracle_required')
    integrity=validate_manifest(snapshot/'project',complete,expected_charts=request.get('expected_charts'))
    root=snapshot/'project'/relative(complete['project_root'])
    if phase in ('plan_initial','plan_options'):
        if complete['status']!='awaiting_confirmation' or not isinstance(complete.get('plan'),dict):raise ControlError('planning_contract_invalid')
        for name in ('design_spec.md','spec_lock.md'):
            if (root/name).exists():raise ControlError('premature_formal_spec')
        from .planning import project_planning
        with open_regular(snapshot/'result','manifest.json',4*1024**2) as stream:source_hash=hashlib.sha256(stream.read()).hexdigest()
        projection=project_planning(manifest,source_manifest_sha256=source_hash,expected_page_count=pages)
        return {'accepted':True,'status':'awaiting_confirmation',**projection,'manifest':complete,'integrity':integrity,'collection':collection}
    for name in ('design_spec.md','spec_lock.md'):
        with open_regular(root,name,4*1024**2) as f:h=hashlib.sha256(f.read()).hexdigest()
        approved=request.get('approved_'+name.replace('.md','')+'_sha256')
        if phase=='generate' and (not approved or h!=approved):raise ControlError('approved_spec_lock_changed')
    with tempfile.TemporaryDirectory(prefix='ppt-independent-') as temp:
        scratch=Path(temp);copy_tree(root,scratch/'project');
        from .staging import make_writable
        make_writable(scratch/'project')
        gates=gate_runner or OfficialGates(scratch/'evidence')
        evidence=gates.run(scratch/'project',phase,pages)
    if not evidence.get('official_gates_verified'):raise ControlError('independent_quality_gate_failed')
    if phase in ('generate','revise'):
        from PIL import Image
        slides=complete.get('slides',[])
        if len(slides)!=pages or len(integrity['pptx'])!=1:raise ControlError('page_count_mismatch')
        for slide in slides:
            key=relative(slide['preview_path'])
            if not key.endswith('.png'):raise ControlError('unsafe_preview_type')
            with Image.open(root/key) as image:
                if image.format!='PNG' or image.width*image.height>20_000_000:raise ControlError('preview_invalid')
                image.verify()
        pptx=integrity['pptx'][0]
        if pptx['chart_count'] and not pptx.get('chart_data_verified'):raise ControlError('native_chart_not_verified')
    result={'accepted':True,'status':complete['status'],'manifest':complete,'integrity':integrity,'collection':collection,'evidence':evidence,'manual_powerpoint_acceptance':'pending'}
    if phase in ('generate','revise'):
        from .render_validation import verify_programmatic,hash_file
        validation_id=str(uuid.uuid4());source_hash=hash_file(snapshot/'result','manifest.json',4*1024**2)['sha256']
        if evidence_root is None:
            with tempfile.TemporaryDirectory(prefix='ppt-programmatic-evidence-') as temp:
                issued=verify_programmatic(snapshot,complete,request,Path(temp)/validation_id,validation_id)
        else:issued=verify_programmatic(snapshot,complete,request,Path(evidence_root)/validation_id,validation_id)
        return publication_readiness(phase,result,programmatic_evidence=issued,validation_id=validation_id,source_manifest_sha256=source_hash)
    return result

class Publisher:
    def __init__(self,config:RuntimeConfig,sandbox):self.config=config;self.sandbox=sandbox
    def validate(self,snapshot,request):return self.sandbox.validate(snapshot,request)
    def promote(self,repo,a:Attempt,snapshot:Path,validated:dict):
        # No concrete independent acceptance certificate issuer exists yet.
        # Do not let even an in-process caller's accepted=True dict bypass this.
        if a.phase in ('generate','revise'):raise ControlError('independent_acceptance_required')
        if not validated.get('accepted'):raise ControlError('unaccepted_candidate')
        # Lock/fence BEFORE creating baselines, then again in repository publication.
        job,project,slot=repo.fenced(a.job_id,a.attempt,a.token)
        if not job.stopped or job.status!='validating':raise ControlError('stop_proof_required')
        from .staging import require_storage
        require_storage(self.config,sum(row['bytes'] for name in ('home','project') for row in read_json(snapshot,'snapshot.json')['indexes'][name]['files'])+validated.get('collection',{}).get('total_bytes',0))
        checkpoint_key=f'projects/{a.project_id}/checkpoints/{uuid.uuid4()}'
        cp=self.config.data_root/checkpoint_key;cp.mkdir(parents=True,exist_ok=False)
        indexes={name:copy_tree(snapshot/name,cp/name,skip_home_bootstrap=name=='home') for name in ('home','project')}
        manifest=validated['manifest'];project_key=relative(manifest['project_root'])
        checkpoint_manifest={'schema_version':1,'session_id':a.session_id,'input_snapshot_sha256':a.snapshot['hash'],'project_root':project_key,'control_context':a.snapshot.get('control_context',{}),**indexes}
        for name in ('design_spec.md','spec_lock.md'):
            path=snapshot/'project'/project_key/name
            if path.is_file():
                with open_regular(snapshot/'project'/project_key,name,4*1024**2) as f:checkpoint_manifest[name+'_sha256']=hashlib.sha256(f.read()).hexdigest()
        atomic_json(cp/'checkpoint.json',checkpoint_manifest);make_readonly(cp)
        if a.phase in ('plan_initial','plan_options'):
            return repo.accept_plan(a.job_id,a.attempt,a.token,outline=validated['outline'],design=validated['design'],checkpoint_key=checkpoint_key,checkpoint_manifest=checkpoint_manifest,independently_validated=True)
        if a.phase=='prepare':return repo.accept_prepared(a.job_id,a.attempt,a.token,checkpoint_key=checkpoint_key,checkpoint_manifest=checkpoint_manifest,independently_validated=True)
        root_key=f'projects/{a.project_id}/versions/{uuid.uuid4()}/published';root=self.config.data_root/root_key;root.mkdir(parents=True,exist_ok=False)
        source=snapshot/'project'/project_key
        pptxs=[row for row in manifest['artifacts'] if row['role']=='pptx']
        if len(pptxs)!=1:raise ControlError('single_pptx_required')
        copy_file(source,pptxs[0]['path'],root/'presentation.pptx',expected_hash=pptxs[0]['sha256'])
        slides=[]
        index={row['path']:row for row in manifest['artifacts']}
        for n,slide in enumerate(manifest['slides'],1):
            row=index[slide['preview_path']];name=f'{n}.png';copy_file(source,row['path'],root/name,expected_hash=row['sha256'])
            slides.append({'preview_key':root_key+'/'+name,'title':slide.get('title',''),'summary':slide.get('summary','')})
        make_readonly(root)
        return repo.publish_version(a.job_id,a.attempt,a.token,manifest={'pptx_key':root_key+'/presentation.pptx','slides':slides},artifact_root=root_key,checkpoint_key=checkpoint_key,checkpoint_manifest=checkpoint_manifest,independently_validated=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--snapshot',type=Path,default=Path('/snapshot'));parser.add_argument('--request',type=Path,default=Path('/request/request.json'));parser.add_argument('--output',type=Path,default=Path('/output/validated.json'));args=parser.parse_args()
    request={}
    try:
        request=read_json(args.request.parent,args.request.name)
        result=validate_private(args.snapshot,request,evidence_root=args.output.parent/'independent')
        result['validation_nonce']=request.get('validation_nonce');result['source_manifest_sha256']=request.get('source_manifest_sha256')
        atomic_json(args.output,result);return 0
    except Exception as exc:
        atomic_json(args.output,{'accepted':False,'error_code':exc.code if isinstance(exc,ControlError) else 'quality_gate_failed','validation_nonce':request.get('validation_nonce'),'source_manifest_sha256':request.get('source_manifest_sha256')});return 2
if __name__=='__main__':raise SystemExit(main())
