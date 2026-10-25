"""Programmatic gate failures and evidence transport; no fake full acceptance."""
import copy,hashlib,json,os,sys,time,uuid
from pathlib import Path
import pytest
from ppt_service.control.execution import RuntimeConfig,ControlError
from ppt_service.control.staging import atomic_json,read_json
from ppt_service.control.render_validation import (source_binding,fresh_project,hash_file,check_raster_report,check_delivery_report,finish_preview,OfficialCommands,ProgrammaticEvidence,RASTER_GATE,embedded_raster)
from ppt_service.control.publisher import publication_readiness,REQUIRED_PENDING_GATES
from ppt_service.control.pods import DockerSandbox

@pytest.fixture
def inputs(tmp_path):
    snapshot=tmp_path/'snapshot';root=snapshot/'project'/'deck';(root/'svg_output').mkdir(parents=True);(snapshot/'result').mkdir()
    rows=[];slides=[]
    for i in range(1,4):
        key=f'svg_output/{i:02}.svg';(root/key).write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="20" height="20" fill="red"/></svg>')
        rows.append({**hash_file(root,key),'role':'svg'});slides.append({'ordinal':i,'svg_path':key,'preview_path':f'.preview/{i:02}.png'})
    (root/'deck.pptx').write_bytes(b'PK public binding fixture, not a valid delivery candidate')
    rows.append({**hash_file(root,'deck.pptx'),'role':'pptx'})
    (root/'.preview').mkdir();(root/'.preview/01.png').write_bytes(b'OLD PNG MUST NOT BE COPIED')
    (root/'.review').mkdir();(root/'.review/old.json').write_text('{"visual":"passed"}')
    (root/'validation').mkdir();(root/'validation/old.json').write_text('{"passed":true}')
    (root/'live_preview').mkdir();(root/'live_preview/lock.json').write_text('{"pid":1}')
    for key in ('.preview/01.png','.review/old.json','validation/old.json','live_preview/lock.json'):rows.append({**hash_file(root,key),'role':'untrusted-old-output'})
    manifest={'phase':'generate','status':'candidate','project_root':'deck','input_snapshot_sha256':'a'*64,'slides':slides,'artifacts':rows}
    atomic_json(snapshot/'result/manifest.json',manifest)
    return snapshot,root,manifest,{'phase':'generate','pages':3,'input_snapshot_sha256':'a'*64,'expected_charts':[]}

def test_hash_bound_copy_excludes_old_previews_reviews_and_locks(inputs,tmp_path):
    snapshot,root,manifest,request=inputs;binding,source,index=source_binding(snapshot,manifest,request)
    target=tmp_path/'fresh';rows=fresh_project(source,target,index)
    assert len(binding['svg_sources'])==3;assert not (target/'.preview').exists();assert not (target/'.review').exists();assert not (target/'validation').exists();assert not (target/'live_preview').exists()
    assert all(not r['path'].startswith(('.preview/','.review/','validation/','live_preview/')) for r in rows)
    with pytest.raises(ControlError,match='render_scratch_not_fresh'):fresh_project(source,target,index)

@pytest.mark.parametrize('problem',['tamper','extra_page','wrong_roster'])
def test_source_tamper_or_roster_mismatch_rejects(inputs,problem):
    snapshot,root,manifest,request=inputs
    if problem=='tamper':(root/'svg_output/01.svg').write_text('<svg viewBox="0 0 1 1"/>')
    elif problem=='extra_page':(root/'svg_output/04.svg').write_text('<svg/>')
    else:request['pages']=4
    with pytest.raises(ControlError):source_binding(snapshot,manifest,request)

def test_partial_copy_missing_or_linked_source_rejects(inputs,tmp_path):
    snapshot,root,manifest,request=inputs;_,source,index=source_binding(snapshot,manifest,request)
    (root/'svg_output/01.svg').unlink();(root/'svg_output/01.svg').symlink_to(root/'deck.pptx')
    with pytest.raises((ControlError,OSError)):fresh_project(source,tmp_path/'copy',index)

@pytest.mark.parametrize('uri',['data:text/html;base64,PHNjcmlwdD4=','data:image/svg+xml;base64,PHN2Zz4=','data:application/javascript;base64,YQ=='])
def test_active_data_uri_rejects_before_browser(uri):
    with pytest.raises(ControlError,match='unsafe_svg_data_resource'):embedded_raster(uri)

@pytest.mark.parametrize('summary',[{'rendered':0,'failed':0,'all_background':0,'pages':[]},{'rendered':3,'failed':1,'all_background':0,'pages':[{}, {}, {}]},{'rendered':3,'failed':0,'all_background':1,'pages':[{}, {}, {}]}])
def test_exit_zero_empty_failed_or_blank_report_is_not_render_success(inputs,summary):
    snapshot,root,manifest,request=inputs;binding,_,_=source_binding(snapshot,manifest,request)
    summary={'project':str(root),'server_url':'http://127.0.0.1:8765',**summary}
    with pytest.raises(ControlError):check_raster_report(root,summary,binding,time.time_ns())

def test_stale_existing_png_never_counts_as_fresh(inputs):
    snapshot,root,manifest,request=inputs;binding,_,_=source_binding(snapshot,manifest,request)
    records=[]
    for i in range(1,4):
        target=root/f'.preview/{i:02}.png';target.write_bytes(b'Old stale cache');os.utime(target,(1,1));records.append({'page':f'{i:02}.svg','ok':True,'all_background':False,'path':str(target)})
    summary={'project':str(root),'server_url':'http://127.0.0.1:8765','rendered':3,'failed':0,'all_background':0,'pages':records}
    with pytest.raises(ControlError,match='stale_preview_output'):check_raster_report(root,summary,binding,time.time_ns())

def delivery_fixture(path):
    return {'schema':'ppt-master.pptx-delivery-check.v1','status':'passed','errors':[],'advisories':[],'file':{'path':str(path.resolve()),'bytes':10},'slides':{'count':3,'hidden_count':0},'package':{'zip_integrity':'passed','parts':{'notes':0}},'motion':{'transitions':dict.fromkeys(('carrier_slide_count','visual_effect_slide_count','timed_advance_slide_count'),0),'object_animations':dict.fromkeys(('timing_slide_count','object_animation_slide_count','audio_timing_slide_count'),0)}}

def test_delivery_advisories_are_preserved_not_full_postflight(tmp_path):
    path=tmp_path/'deck.pptx';report=delivery_fixture(path);report['status']='passed-with-advisories';report['advisories']=[{'code':'font_portability','message':'Requires target-font review'}]
    result=check_delivery_report(report,0,path,{'pptx':{'bytes':10},'svg_sources':[{}, {}, {}]})
    assert result['advisory_count']==1;assert result['full_exporter_postflight_verified'] is False

def test_complete_notes_count_is_supported_but_not_a_package_certificate(tmp_path):
    path=tmp_path/'deck.pptx';report=delivery_fixture(path);report['package']['parts']['notes']=3
    result=check_delivery_report(report,0,path,{'pptx':{'bytes':10},'svg_sources':[{}, {}, {}]})
    assert result['notes_count']==3;assert result['full_exporter_postflight_verified'] is False

@pytest.mark.parametrize('notes',[1,2,4,-1,True,None,'3'])
def test_partial_or_invalid_note_counts_reject(tmp_path,notes):
    path=tmp_path/'deck.pptx';report=delivery_fixture(path);report['package']['parts']['notes']=notes
    with pytest.raises(ControlError,match='invalid_notes_roster'):
        check_delivery_report(report,0,path,{'pptx':{'bytes':10},'svg_sources':[{}, {}, {}]})

@pytest.mark.parametrize('problem',['exit','notes','motion','errors','contradictory'])
def test_delivery_failures_reject(tmp_path,problem):
    path=tmp_path/'deck.pptx';report=delivery_fixture(path);code=0
    if problem=='exit':code=1
    elif problem=='notes':report['package']['parts']['notes']=1
    elif problem=='motion':report['motion']['transitions']['carrier_slide_count']=1
    elif problem=='errors':report['errors']=[{'code':'broken_relationship'}]
    else:report['advisories']=[{'code':'warning_not_accounted_for'}]
    with pytest.raises(ControlError):check_delivery_report(report,code,path,{'pptx':{'bytes':10},'svg_sources':[{}, {}, {}]})

@pytest.mark.parametrize('raises,stopped',[(True,True),(False,False)])
def test_preview_shutdown_exception_or_incomplete_group_is_not_passed(raises,stopped):
    class Preview:
        def __init__(self):self.stopped=stopped
        def stop(self):
            if raises:raise RuntimeError('Test shutdown failure')
    with pytest.raises(ControlError,match='preview_group_cleanup_failed'):finish_preview(Preview())

def test_command_timeout_stops_real_test_process_group(tmp_path):
    with pytest.raises(ControlError,match='independent_command_timeout'):OfficialCommands().run([sys.executable,'-c','import time; time.sleep(20)'],tmp_path/'out',tmp_path/'err',timeout=0.05)

def test_command_group_cleanup_failure_is_not_hidden(tmp_path,monkeypatch):
    monkeypatch.setattr('ppt_service.control.render_validation.stop_group',lambda process:False)
    with pytest.raises(ControlError,match='renderer_group_cleanup_failed'):OfficialCommands().run([sys.executable,'-c','pass'],tmp_path/'out',tmp_path/'err')

@pytest.mark.parametrize('problem',['json','replayed_nonce','wrong_source','mutated_payload'])
def test_fake_or_reused_programmatic_evidence_rejects(problem):
    data={'completed_programmatic_gates':[RASTER_GATE]};evidence=ProgrammaticEvidence._issue(data,'original-run','a'*64);nonce='original-run';sha='a'*64
    if problem=='json':evidence={'issuer':'trusted','validation_id':nonce,'completed_programmatic_gates':[RASTER_GATE]}
    elif problem=='replayed_nonce':nonce='new-run'
    elif problem=='wrong_source':sha='b'*64
    else:evidence._payload['completed_programmatic_gates'].append('independent_visual_review')
    with pytest.raises(ControlError,match='invalid_programmatic_evidence'):publication_readiness('generate',{'accepted':True},programmatic_evidence=evidence,validation_id=nonce,source_manifest_sha256=sha)
    with pytest.raises(TypeError):ProgrammaticEvidence(data)

class FakeDocker:
    """Only tests transport; never pretends that a renderer/visual gate passed."""
    def __init__(self,fault=None):self.fault=fault;self.removed=False;self.calls=[]
    def call(self,*args,**kwargs):
        self.calls.append(args)
        if args[0]=='create':
            for value in args:
                if isinstance(value,str) and value.startswith('type=bind,'):
                    fields=dict(v.split('=',1) for v in value.split(',') if '=' in v)
                    if fields.get('dst')=='/request':self.inputs=Path(fields['src'])
                    elif fields.get('dst')=='/output':self.output=Path(fields['src'])
            return 0,b'c'*64
        if args[0]=='start':
            request=read_json(self.inputs,'request.json');self.request=request
            result={'accepted':False,'status':'candidate','programmatic_checks_passed':False,'required_pending_gates':list(REQUIRED_PENDING_GATES),'validation_nonce':request['validation_nonce'],'source_manifest_sha256':request['source_manifest_sha256']}
            if self.fault=='nonce':result['validation_nonce']='old-run'
            elif self.fault=='source':result['source_manifest_sha256']='0'*64
            elif self.fault=='publication':result['accepted']=True
            elif self.fault=='empty_png':result['independent_programmatic_evidence']={'validation_id':str(uuid.uuid4()),'controller_nonce':request['validation_nonce'],'source':{'source_manifest_sha256':request['source_manifest_sha256']},'pngs':[]}
            atomic_json(self.output/'validated.json',result);(self.output/'proof.txt').write_text('Retained transport fixture; no acceptance certificate')
            if self.fault=='symlink':(self.output/'linked').symlink_to(self.inputs/'request.json')
            elif self.fault=='size':
                with open(self.output/'huge','wb') as f:f.truncate(65*1024**2)
            return 0,b''
        if args[0]=='wait':
            if self.fault=='wait':raise ControlError('control_command_unavailable')
            return 0,b'0'
        if args[0]=='rm':
            if self.fault=='running':return 1,b''
            self.removed=True;return 0,b''
        raise AssertionError(args)
    def inspect(self,name):return {'Running':True,'Pid':123} if not self.removed else None


def test_validation_transport_is_persistent_and_not_a_certificate(inputs,tmp_path):
    snapshot,root,manifest,request=inputs;docker=FakeDocker();runtime=tmp_path/'runtime';runtime.mkdir();sandbox=DockerSandbox(RuntimeConfig(runtime),docker)
    result=sandbox.validate(snapshot,dict(request,validation_nonce='caller-choice',source_manifest_sha256='caller-choice'))
    retained=Path(result['persistent_evidence']['path']);assert retained.is_dir();assert (retained/'proof.txt').read_text().startswith('Retained transport fixture')
    transport=read_json(retained,'transport-manifest.json');assert transport['transport_verified'] is True;assert transport['controller_error_code'] is None;assert transport['controller_nonce']!='caller-choice'
    assert result['persistent_evidence']['publication_authorization'] is False;assert result['accepted'] is False;assert docker.removed
    for row in transport['files']:assert hash_file(retained,row['path'])['sha256']==row['sha256']
    assert not list(runtime.glob('ppt-validation-*'))

@pytest.mark.parametrize('fault,code',[('nonce','validation_envelope_mismatch'),('source','validation_envelope_mismatch'),('empty_png','validation_artifact_mismatch'),('publication','publication_guard_violation'),('symlink','validation_output_copy_failed'),('size','validation_output_copy_failed'),('wait','control_command_unavailable'),('running','validation_writers_not_stopped')])
def test_rejected_transport_has_no_success_descriptor_and_keeps_rejection_audit(inputs,tmp_path,fault,code):
    snapshot,root,manifest,request=inputs;runtime=tmp_path/'runtime';runtime.mkdir();docker=FakeDocker(fault);sandbox=DockerSandbox(RuntimeConfig(runtime),docker)
    with pytest.raises(ControlError,match=code) as raised:sandbox.validate(snapshot,request)
    retained=Path(raised.value.evidence_path);assert retained.exists()
    if retained.name.startswith('.pending-'):audit=read_json(retained,'.controller-quarantine.json')
    elif retained.name.startswith('.running-'):audit=read_json(retained,'controller-quarantine.json')
    else:
        audit=read_json(retained,'transport-manifest.json');raw=read_json(retained,'validated.json')
        if fault in ('nonce','source'):assert raw['programmatic_checks_passed'] is False
    assert audit['transport_verified'] is False;assert audit['controller_error_code']==code


def test_parse_path_still_uses_original_nonretaining_protocol(tmp_path,monkeypatch):
    sandbox=DockerSandbox(RuntimeConfig(tmp_path),FakeDocker());calls=[]
    monkeypatch.setattr(sandbox,'_run',lambda *args:(calls.append(args) or {'sources':[]}))
    assert sandbox.parse(tmp_path,{})=={'sources':[]};assert calls[0][0]=='ppt_service.control.parsing';assert not (tmp_path/'validation-evidence').exists()
