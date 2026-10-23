"""Independent programmatic raster/delivery verification in a network-none sandbox.

No visual judgment or full exporter postflight is claimed. Only pinned image
scripts execute; copied project programs, reports and cached PNGs are not used.
"""
from __future__ import annotations
import copy,hashlib,json,os,posixpath,re,signal,subprocess,time,uuid,zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
from .execution import ControlError,PINNED_WORKER,PPT_MASTER_COMMIT
from .staging import relative,open_regular,copy_file,atomic_json,read_json,make_writable

SCRIPTS=Path('/opt/ppt-master/skills/ppt-master/scripts')
PYTHON='/opt/hermes/.venv/bin/python'
RASTER_GATE='independent_raster_regeneration_and_binding'
DELIVERY_GATE='independent_delivery_audit'
NATIVE_GATE='independent_native_ooxml_and_chart_data'
_ISSUER=object()
CACHE_ROOTS=frozenset(('.preview','.review','live_preview','.live_preview.lock','validation'))

def hash_file(root,key,limit=64*1024**2):
    h=hashlib.sha256();size=0
    with open_regular(root,relative(key),limit) as stream:
        while chunk:=stream.read(65536):h.update(chunk);size+=len(chunk)
    return {'path':key,'sha256':h.hexdigest(),'bytes':size}
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def embedded_raster(value):
    import base64,io
    if len(value)>4*1024**2 or not re.fullmatch(r'data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=\r\n]+',value):raise ControlError('unsafe_svg_data_resource')
    try:data=base64.b64decode(''.join(value.split(',',1)[1].split()),validate=True)
    except ValueError:raise ControlError('unsafe_svg_data_resource') from None
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ('PNG','JPEG') or image.width*image.height>20_000_000:raise ControlError('unsafe_svg_data_resource')
        image.verify()
    digest=hashlib.sha256(data).hexdigest()
    return {'path':'embedded-data/'+digest,'sha256':digest,'bytes':len(data),'embedded':True}

class ProgrammaticEvidence:
    """An in-process issuer-bound result, never deserialized from request JSON."""
    def __init__(self,*args,**kwargs):raise TypeError('Evidence is issued only by the independent runtime')
    @classmethod
    def _issue(cls,payload,validation_id,manifest_sha256):
        result=object.__new__(cls);result._issuer=_ISSUER;result._payload=copy.deepcopy(payload);result._validation_id=validation_id;result._manifest_sha256=manifest_sha256;result._seal=fingerprint(result._payload);return result
    def verify(self,validation_id,manifest_sha256):
        return self._issuer is _ISSUER and self._validation_id==validation_id and self._manifest_sha256==manifest_sha256 and self._seal==fingerprint(self._payload)
    def report(self):return copy.deepcopy(self._payload)


def source_binding(snapshot,manifest,request):
    raw=read_json(snapshot/'result','manifest.json')
    if raw.get('phase')!=request['phase'] or raw.get('input_snapshot_sha256')!=request['input_snapshot_sha256']:raise ControlError('render_source_identity_mismatch')
    project=relative(raw['project_root'])
    if manifest.get('project_root')!=project or manifest.get('slides')!=raw.get('slides'):raise ControlError('render_source_identity_mismatch')
    root=snapshot/'project'/project;rows=manifest.get('artifacts',[]);index={r['path']:r for r in rows}
    declared={r['path']:r for r in raw.get('artifacts',[])}
    slides=raw.get('slides')
    if not isinstance(slides,list) or len(slides)!=request['pages'] or not 3<=len(slides)<=20:raise ControlError('render_roster_mismatch')
    svg_rows=[];names=[];resources={}
    for i,slide in enumerate(slides,1):
        key=relative(slide.get('svg_path'))
        if slide.get('ordinal')!=i or not key.startswith('svg_output/') or len(key.split('/'))!=2 or not key.endswith('.svg'):raise ControlError('render_roster_mismatch')
        name=key.split('/')[-1]
        if name in names:raise ControlError('render_roster_mismatch')
        names.append(name);actual=hash_file(root,key,8*1024**2);expected=declared.get(key,index.get(key))
        if not expected or actual['sha256']!=expected.get('sha256') or actual['bytes']!=expected.get('bytes'):raise ControlError('render_source_hash_mismatch')
        with open_regular(root,key,8*1024**2) as f:data=f.read()
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ControlError('unsafe_svg_source')
        svg=ET.fromstring(data);count=0
        for node in svg.iter():
            count+=1
            if count>100000 or node.tag.rsplit('}',1)[-1] in ('script','foreignObject','iframe','object','embed'):raise ControlError('unsafe_svg_source')
            for attr,value in node.attrib.items():
                if attr.rsplit('}',1)[-1].lower().startswith('on'):raise ControlError('unsafe_svg_source')
                if attr.rsplit('}',1)[-1] in ('href','src') and value.startswith('data:'):
                    resource=embedded_raster(value);resources[resource['path']]=resource
                if attr.rsplit('}',1)[-1] in ('href','src') and value and not value.startswith(('#','data:')):
                    if value.startswith('/') or any(x in value for x in (':','\\','?','#','%')):raise ControlError('external_svg_resource')
                    reference=relative(posixpath.normpath(posixpath.join('svg_output',value)))
                    resource=hash_file(root,reference)
                    recorded=declared.get(reference,index.get(reference))
                    if not recorded or resource['sha256']!=recorded.get('sha256') or resource['bytes']!=recorded.get('bytes'):raise ControlError('render_resource_hash_mismatch')
                    resources[reference]=resource
                for token in re.findall(r'url\(([^)]*)\)',value,re.I):
                    reference=token.strip().strip('\"\x27')
                    if reference.startswith('data:'):
                        resource=embedded_raster(reference);resources[resource['path']]=resource
                    elif not reference.startswith('#'):raise ControlError('external_svg_resource')
            if node.tag.rsplit('}',1)[-1]=='style' and node.text:
                if '@import' in node.text.lower():raise ControlError('external_svg_resource')
                for token in re.findall(r'url\(([^)]*)\)',node.text,re.I):
                    reference=token.strip().strip('\"\x27')
                    if reference.startswith('data:'):
                        resource=embedded_raster(reference);resources[resource['path']]=resource
                    elif not reference.startswith('#'):raise ControlError('external_svg_resource')
        view=svg.attrib.get('viewBox','').replace(',',' ').split()
        try:canvas=[float(v) for v in view]
        except ValueError:raise ControlError('invalid_svg_canvas') from None
        import math
        if len(canvas)!=4 or any(not math.isfinite(v) for v in canvas) or canvas[2]<=0 or canvas[3]<=0 or canvas[2]*canvas[3]>20_000_000:raise ControlError('invalid_svg_canvas')
        svg_rows.append({**actual,'ordinal':i,'png_width':math.ceil(canvas[2]),'png_height':math.ceil(canvas[3])})
    actual_names=sorted(p.name for p in (root/'svg_output').iterdir() if p.suffix.lower()=='.svg')
    if set(actual_names)!=set(names) or len(actual_names)!=len(names):raise ControlError('render_roster_mismatch')
    pptxs=[r for r in rows if r.get('role')=='pptx']
    if len(pptxs)!=1:raise ControlError('single_pptx_required')
    pptx=hash_file(root,relative(pptxs[0]['path']));expected=declared.get(pptx['path'],pptxs[0])
    if pptx['sha256']!=expected.get('sha256') or pptx['bytes']!=expected.get('bytes'):raise ControlError('delivery_source_hash_mismatch')
    return {'source_manifest_sha256':hash_file(snapshot/'result','manifest.json',4*1024**2)['sha256'],'input_snapshot_sha256':request['input_snapshot_sha256'],'project_root':project,'svg_sources':svg_rows,'local_resources':[resources[k] for k in sorted(resources)],'pptx':pptx,'expected_charts_canonical_sha256':fingerprint(request.get('expected_charts'))},root,index


def fresh_project(root,target,index):
    """Copy actual source into a new tree, excluding every old preview/review cache."""
    if target.exists():raise ControlError('render_scratch_not_fresh')
    target.mkdir(parents=True)
    copied=[];total=0
    for folder,dirs,files in os.walk(root,followlinks=False):
        base=Path(folder).relative_to(root)
        if base==Path('.'):
            dirs[:]=[name for name in dirs if name not in CACHE_ROOTS]
            files=[name for name in files if name not in CACHE_ROOTS]
        for name in dirs:
            path=Path(folder)/name
            if path.is_symlink():raise ControlError('render_copy_link_rejected')
            (target/base/name).mkdir()
        for name in files:
            key=(base/name).as_posix();row=index.get(key)
            if not row:raise ControlError('render_copy_source_unindexed')
            copied_row=copy_file(root,key,target/key,expected_hash=row['sha256']);copied.append(copied_row);total+=copied_row['bytes']
            if len(copied)>2048 or total>256*1024**2:raise ControlError('render_copy_limit')
    make_writable(target)
    for cache in CACHE_ROOTS:
        if (target/cache).exists():raise ControlError('render_scratch_not_fresh')
    return sorted(copied,key=lambda r:r['path'])

def unchanged(root,rows):
    for row in rows:
        current=hash_file(root,row['path'])
        if current['sha256']!=row['sha256'] or current['bytes']!=row['bytes']:raise ControlError('render_source_changed')

def stop_group(process,*,grace=3):
    """Stop/reap only our newly-created process group, including adopted children."""
    for sig in (signal.SIGTERM,signal.SIGKILL):
        try:os.killpg(process.pid,sig)
        except ProcessLookupError:process.poll();return True
        end=time.monotonic()+grace
        while time.monotonic()<end:
            process.poll()
            try:
                while True:
                    pid,_=os.waitpid(-process.pid,os.WNOHANG)
                    if pid<=0:break
            except ChildProcessError:pass
            try:os.killpg(process.pid,0)
            except ProcessLookupError:process.poll();return True
            time.sleep(0.05)
    return False

def finish_preview(preview):
    try:preview.stop()
    except Exception:raise ControlError('preview_group_cleanup_failed') from None
    if preview.stopped is not True:raise ControlError('preview_group_cleanup_failed')
    return True

class OfficialCommands:
    def run(self,args,stdout,stderr,*,timeout=120):
        from ppt_service.worker.preview import renderer_environment
        with open(stdout,'wb') as out,open(stderr,'wb') as err:
            p=subprocess.Popen(args,cwd='/',env=renderer_environment(),stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=True)
            code=None;failure=None
            try:code=p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:failure='independent_command_timeout'
            finally:
                stopped=stop_group(p)
            if not stopped:raise ControlError('renderer_group_cleanup_failed')
            if failure:raise ControlError(failure)
            return code


def check_raster_report(project,summary,binding,start_ns):
    expected=binding['svg_sources'];records=summary.get('pages')
    if summary.get('project')!=str(project) or summary.get('server_url')!='http://127.0.0.1:8765' or summary.get('rendered')!=len(expected) or summary.get('failed')!=0 or summary.get('all_background')!=0 or not isinstance(records,list) or len(records)!=len(expected):raise ControlError('render_roster_mismatch')
    previews=project/'.preview';actual=[]
    pngs={p.name for p in previews.iterdir() if p.suffix.lower()=='.png'}
    required={Path(row['path']).stem+'.png' for row in expected}
    if pngs!=required:raise ControlError('render_roster_mismatch')
    for source,record in zip(expected,records):
        name=Path(source['path']).name;png=Path(name).stem+'.png';target=previews/png
        if not isinstance(record,dict) or record.get('page')!=name or record.get('ok') is not True or record.get('all_background') is not False or record.get('path')!=str(target):raise ControlError('render_roster_mismatch')
        if target.stat().st_mtime_ns<start_ns:raise ControlError('stale_preview_output')
        data=hash_file(previews,png,4*1024**2)
        canvas=record.get('canvas',{})
        from PIL import Image
        with Image.open(target) as image:
            if image.format!='PNG' or image.size!=(source['png_width'],source['png_height']):raise ControlError('render_canvas_mismatch')
            dimensions=image.size;image.verify()
        if data['bytes']!=record.get('bytes') or canvas.get('png_width')!=dimensions[0] or canvas.get('png_height')!=dimensions[1]:raise ControlError('render_canvas_mismatch')
        actual.append({**data,'width':dimensions[0],'height':dimensions[1],'svg_path':source['path'],'svg_sha256':source['sha256'],'ordinal':source['ordinal']})
    return actual


def check_delivery_report(report,exit_code,pptx,binding):
    if report.get('schema')!='ppt-master.pptx-delivery-check.v1' or exit_code!=0 or report.get('status') not in ('passed','passed-with-advisories') or report.get('errors')!=[]:raise ControlError('independent_delivery_failed')
    if report.get('file',{}).get('path')!=str(pptx.resolve()) or report.get('file',{}).get('bytes')!=binding['pptx']['bytes']:raise ControlError('delivery_source_hash_mismatch')
    if report.get('slides',{}).get('count')!=len(binding['svg_sources']) or report.get('slides',{}).get('hidden_count')!=0 or report.get('package',{}).get('zip_integrity')!='passed':raise ControlError('independent_delivery_failed')
    motion=report.get('motion',{});transitions=motion.get('transitions',{});animations=motion.get('object_animations',{})
    for key in ('carrier_slide_count','visual_effect_slide_count','timed_advance_slide_count'):
        if transitions.get(key)!=0:raise ControlError('unexpected_motion')
    for key in ('timing_slide_count','object_animation_slide_count','audio_timing_slide_count'):
        if animations.get(key)!=0:raise ControlError('unexpected_motion')
    if report.get('package',{}).get('parts',{}).get('notes')!=0:raise ControlError('unexpected_notes')
    advisories=report.get('advisories')
    if not isinstance(advisories,list) or bool(advisories)!=(report['status']=='passed-with-advisories'):raise ControlError('invalid_delivery_report')
    # Advisories are preserved and explicitly not equivalent to zero warnings.
    return {'status':report['status'],'advisory_count':len(advisories),'advisories':copy.deepcopy(advisories),'full_exporter_postflight_verified':False,'policy':'errors/motion/notes/hidden-slides reject; advisories retained, not waived as full acceptance'}


def verify_programmatic(snapshot,manifest,request,output,validation_id,*,commands=None,preview_factory=None):
    from ppt_service.worker.preview import OfficialPreview,ORIGIN,VISUAL_REVIEW
    from ppt_service.worker.candidate_validation import inspect_pptx
    if output.exists():raise ControlError('render_output_not_fresh')
    output.mkdir(parents=True)
    commands=commands or OfficialCommands();preview_factory=preview_factory or OfficialPreview
    binding,source,index=source_binding(snapshot,manifest,request)
    atomic_json(output/'source-binding.json',binding)
    parent=output/'scratch-projects';parent.mkdir();project=parent/binding['project_root']
    rows=fresh_project(source,project,index);binding['copied_source_fingerprint']=fingerprint(rows)
    atomic_json(output/'copied-source-index.json',rows);atomic_json(output/'source-binding.json',binding)
    script_hashes={name:hash_file(SCRIPTS,name)['sha256'] for name in ('visual_review.py','pptx_delivery_check.py','svg_editor/server.py')}
    commit=Path('/opt/ppt-service/build-info/ppt-master.commit').read_text().strip()
    if commit!=PPT_MASTER_COMMIT:raise ControlError('unapproved_official_scripts')
    pptx=project/binding['pptx']['path'];delivery_out=output/'delivery.json';delivery_err=output/'delivery.stderr.log'
    code=commands.run([PYTHON,str(SCRIPTS/'pptx_delivery_check.py'),str(pptx)],delivery_out,delivery_err,timeout=60)
    delivery=read_json(output,'delivery.json',4*1024**2);delivery_checked=check_delivery_report(delivery,code,pptx,binding)
    native=inspect_pptx(pptx,request.get('expected_charts'))
    if native['slide_count']!=len(binding['svg_sources']) or (native['chart_count'] and not native['chart_data_verified']):raise ControlError('native_chart_not_verified')
    atomic_json(output/'native-inspection.json',native)
    preview=None;render_code=None;cleanup_ok=False
    start_ns=time.time_ns()
    try:
        if (project/'.preview').exists():raise ControlError('stale_preview_output')
        preview=preview_factory(parent,lifetime=180);preview.start()
        render_code=commands.run([PYTHON,VISUAL_REVIEW,str(project),'--server-url',ORIGIN],output/'raster.json',output/'raster.stderr.log',timeout=120)
    except Exception as exc:
        atomic_json(output/'render-failure.json',{'error_type':type(exc).__name__,'message':str(exc)[:300]})
        if isinstance(exc,ControlError):raise
        raise ControlError('independent_preview_or_render_failed') from None
    finally:
        if preview is not None:cleanup_ok=finish_preview(preview)
    if not cleanup_ok:raise ControlError('preview_group_cleanup_failed')
    if render_code!=0:raise ControlError('independent_render_failed')
    summary=read_json(output,'raster.json',4*1024**2);pngs=check_raster_report(project,summary,binding,start_ns)
    for png in pngs:png['relative_path']='scratch-projects/'+binding['project_root']+'/.preview/'+png['path']
    unchanged(project,rows);unchanged(source,[index[row['path']] for row in rows])
    after=hash_file(project,binding['pptx']['path'])
    if after['sha256']!=binding['pptx']['sha256']:raise ControlError('delivery_source_changed')
    # Durable transport is the caller's responsibility. Files are retained under
    # output in the standalone test; the generic API sandbox currently deletes
    # its temporary output after returning the JSON report.
    report={'schema_version':1,'issuer':'ppt-service-independent-programmatic-runtime-v1','validation_id':validation_id,'controller_nonce':request.get('validation_nonce'),'worker_image':PINNED_WORKER,'ppt_master_commit':commit,'official_script_sha256':script_hashes,'source':binding,'pngs':pngs,'render_exit_code':render_code,'render_started_ns':start_ns,'preview_directory_existed_before_render':False,'excluded_cache_roots':sorted(CACHE_ROOTS),'delivery_exit_code':code,'delivery':delivery_checked,'native':native,'preview_group_stopped':cleanup_ok,'command_groups_stopped':True,'completed_programmatic_gates':[RASTER_GATE,DELIVERY_GATE,NATIVE_GATE],'independent_visual_review':False,'full_exporter_postflight_verified':False,'powerpoint_acceptance':'pending','artifact_directory':str(output),'artifact_retention_requires_caller':True}
    atomic_json(output/'programmatic-evidence.json',report)
    return ProgrammaticEvidence._issue(report,validation_id,binding['source_manifest_sha256'])
