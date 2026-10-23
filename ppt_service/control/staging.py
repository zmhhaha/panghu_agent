"""No-follow immutable copies and server-only attempt/task construction."""
from __future__ import annotations
import hashlib,json,os,stat,uuid
from pathlib import Path
from .execution import Attempt,Layout,RuntimeConfig,ControlError

EXT={'text/markdown':'.md','application/pdf':'.pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document':'.docx','image/png':'.png','image/jpeg':'.jpg'}
def relative(value):
    if not isinstance(value,str) or not value or len(value)>1024 or any(c in value for c in '\\:%?#') or any(ord(c)<32 for c in value) or any(p in ('','.','..') for p in value.split('/')):raise ControlError('unsafe_path')
    return value

def open_regular(root:Path,key:str,max_bytes:int):
    parts=relative(key).split('/')
    if os.name!='posix':
        p=root
        for part in parts:
            p=p/part
            if p.is_symlink() or p.is_junction():raise ControlError('linked_file')
        f=open(p,'rb')
    else:
        fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                nextfd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nextfd
            result=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        finally:os.close(fd)
        f=os.fdopen(result,'rb')
    st=os.fstat(f.fileno())
    if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>max_bytes:f.close();raise ControlError('invalid_file')
    return f

def atomic_json(path:Path,value):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex)
    with open(tmp,'xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
def read_json(root:Path,key:str,max_bytes=4*1024**2):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise ControlError('duplicate_json_key')
            d[k]=v
        return d
    def invalid(value):raise ControlError('invalid_json_constant')
    with open_regular(root,key,max_bytes) as f:return json.loads(f.read(max_bytes+1),object_pairs_hook=pairs,parse_constant=invalid)

def copy_file(root:Path,key:str,target:Path,max_bytes=64*1024**2,expected_hash=None):
    target.parent.mkdir(parents=True,exist_ok=True)
    h=hashlib.sha256();size=0
    with open_regular(root,key,max_bytes) as src,open(target,'xb') as out:
        initial=os.fstat(src.fileno())
        while chunk:=src.read(65536):
            size+=len(chunk)
            if size>max_bytes:raise ControlError('snapshot_limit')
            h.update(chunk);out.write(chunk)
        after=os.fstat(src.fileno())
        if initial.st_size!=size or initial.st_mtime_ns!=after.st_mtime_ns:raise ControlError('writers_not_stopped')
        out.flush();os.fsync(out.fileno())
    if expected_hash and h.hexdigest()!=expected_hash:raise ControlError('source_hash_mismatch')
    target.chmod(0o444)
    return {'path':key,'bytes':size,'sha256':h.hexdigest()}

def copy_tree(source:Path,target:Path,*,max_total=512*1024**2,max_files=8192,skip_home_bootstrap=False):
    if target.exists():raise ControlError('snapshot_exists')
    target.mkdir(parents=True)
    total=0;index=[]
    def walk(folder:Path,prefix=''):
        nonlocal total
        if folder.is_symlink():raise ControlError('linked_directory')
        for entry in sorted(os.scandir(folder),key=lambda e:e.name):
            key=relative(prefix+entry.name);info=entry.stat(follow_symlinks=False)
            # Service config/plugins/skill links are rebuilt from fixed image code.
            if skip_home_bootstrap and not prefix and entry.name in ('plugins','skills','config.yaml','.env','lazy-packages'):continue
            if stat.S_ISDIR(info.st_mode):
                (target/key).mkdir();walk(Path(entry.path),key+'/')
            elif stat.S_ISREG(info.st_mode):
                if len(index)>=max_files:raise ControlError('snapshot_file_limit')
                row=copy_file(source,key,target/key);total+=row['bytes'];index.append(row)
                if total>max_total:raise ControlError('snapshot_limit')
            else:raise ControlError('linked_or_special_file')
    walk(source)
    return {'files':index,'bytes':total}

def make_readonly(root:Path):
    for folder,dirs,files in os.walk(root,followlinks=False):
        for name in files:(Path(folder)/name).chmod(0o444)
        for name in dirs:(Path(folder)/name).chmod(0o555)
    root.chmod(0o555)
def make_writable(root:Path):
    for folder,dirs,files in os.walk(root,followlinks=False):
        Path(folder).chmod(0o700)
        for name in files:(Path(folder)/name).chmod(0o600)
    if os.name=='posix':
        for folder,dirs,files in os.walk(root):
            os.chown(folder,10000,10000)
            for name in files:os.chown(Path(folder)/name,10000,10000)

def storage_bytes(root:Path):
    total=0;count=0
    for folder,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(folder)/d).is_symlink()]
        for name in files:
            info=(Path(folder)/name).lstat();count+=1
            if count>200000:raise ControlError('storage_inventory_limit')
            if stat.S_ISREG(info.st_mode):total+=info.st_size
    return total

def require_storage(config:RuntimeConfig,additional:int):
    if additional<0 or storage_bytes(config.data_root)+additional>config.max_storage_bytes:raise ControlError('storage_full')

class Stager:
    def __init__(self,config:RuntimeConfig):self.config=config;self.root=config.data_root.absolute();self.root.mkdir(parents=True,exist_ok=True)
    def layout(self,a:Attempt):
        root=self.root/f'projects/{a.project_id}/attempts/{a.job_id}/{a.attempt}-{a.token}'
        return Layout(root,root/'inputs',root/'task',root/'home',root/'project',root/'result',root/'audit',root/'base')
    def stage(self,a:Attempt,checkpoint_manifest:dict|None=None):
        baseline=sum(checkpoint_manifest.get(k,{}).get('bytes',0) for k in ('home','project')) if checkpoint_manifest else 0
        require_storage(self.config,baseline*2+sum(x['bytes'] for x in a.snapshot.get('assets',[]))+self.config.max_snapshot_bytes)
        layout=self.layout(a)
        if layout.root.exists():raise ControlError('attempt_already_staged')
        for p in (layout.inputs,layout.task,layout.home,layout.project,layout.result,layout.audit,layout.base):p.mkdir(parents=True,exist_ok=False)
        sources=[]
        for asset in a.snapshot.get('assets',[]):
            aid=str(uuid.UUID(asset['id']));expected=f'projects/{a.project_id}/assets/{aid}/original'
            if asset['storage_key']!=expected or asset['media_type'] not in EXT:raise ControlError('invalid_source_mapping')
            name=aid+EXT[asset['media_type']]
            row=copy_file(self.root,expected,layout.inputs/name,25*1024**2,asset['sha256'])
            if row['bytes']!=asset['bytes']:raise ControlError('source_size_mismatch')
            sources.append({'id':aid,'path':name,'sha256':row['sha256'],'bytes':row['bytes'],'media_type':asset['media_type']})
        inventory={'schema_version':1,'input_snapshot_sha256':a.snapshot['hash'],'sources':sources}
        atomic_json(layout.inputs/'inventory.json',inventory)
        if a.checkpoint_key:
            key=relative(a.checkpoint_key)
            if not key.startswith(f'projects/{a.project_id}/checkpoints/'):raise ControlError('checkpoint_owner_mismatch')
            source=self.root/key
            meta=read_json(source,'checkpoint.json')
            if checkpoint_manifest is None or meta!=checkpoint_manifest or meta.get('session_id')!=a.session_id:raise ControlError('checkpoint_identity_mismatch')
            for name in ('home','project'):
                # Empty destination from layout construction; copy into new temporary dir.
                target=layout.root/(name+'-restore');index=copy_tree(source/name,target,skip_home_bootstrap=name=='home')
                expected={v['path']:v['sha256'] for v in meta.get(name,{}).get('files',[])}
                if {v['path']:v['sha256'] for v in index['files']}!=expected:raise ControlError('checkpoint_hash_mismatch')
                getattr(layout,name).rmdir();os.replace(target,getattr(layout,name))
            copy_tree(source/'project',layout.base/'project')
        elif a.phase!='plan_initial':raise ControlError('explicit_checkpoint_required')
        make_writable(layout.home);make_writable(layout.project);make_writable(layout.result)
        make_readonly(layout.base)
        return layout,inventory
    def finish_inputs(self,a:Attempt,layout:Layout,parsed:dict):
        if parsed.get('input_snapshot_sha256')!=a.snapshot['hash']:raise ControlError('parse_snapshot_mismatch')
        atomic_json(layout.inputs/'parsed.json',parsed)
        settings=a.snapshot.get('settings',{})
        context=a.snapshot.get('control_context',{})
        settings=settings|{'title':a.snapshot.get('title',''),'source_inventory':'/work/inputs/inventory.json','parsed_materials':'/work/inputs/parsed.json','revision_pages':a.snapshot.get('pages',[])}
        inventory=read_json(layout.inputs,'inventory.json')
        assets=[{**source,'path':'/work/inputs/'+source['path'],'display_name':source['id']+EXT[source['media_type']]} for source in inventory['sources']]
        task={'schema_version':1,'job_id':a.job_id,'attempt':a.attempt,'phase':a.phase,'session_id':a.session_id,'input_snapshot_sha256':a.snapshot['hash'],'requirements':settings|{'pages':settings.get('page_count',8),'external_model_acknowledged':True},'assets':assets,'feedback':a.snapshot.get('feedback',''),'base_version_id':a.base_version_id}
        if context.get('stage1_confirmation'):task['stage1_confirmation']=context['stage1_confirmation']
        if context.get('stage2_confirmation'):task['stage2_confirmation']=context['stage2_confirmation']
        if a.phase=='plan_options' and not task.get('stage1_confirmation'):raise ControlError('stage1_confirmation_required')
        if a.phase in ('prepare','generate','revise') and not task.get('stage2_confirmation'):raise ControlError('stage2_confirmation_required')
        atomic_json(layout.task/'task.json',task);make_readonly(layout.inputs);make_readonly(layout.task)
        return task
    def collect(self,a:Attempt,layout:Layout):
        key=f'projects/{a.project_id}/snapshots/{uuid.uuid4()}'
        snapshot=self.root/key;snapshot.mkdir(parents=True,exist_ok=False)
        indexes={}
        for name in ('project','result','home'):
            indexes[name]=copy_tree(getattr(layout,name),snapshot/name,max_total=self.config.max_snapshot_bytes,skip_home_bootstrap=name=='home')
            if sum(i['bytes'] for i in indexes.values())>self.config.max_snapshot_bytes:raise ControlError('snapshot_limit')
        atomic_json(snapshot/'snapshot.json',{'job_id':a.job_id,'attempt':a.attempt,'token':a.token,'indexes':indexes})
        make_readonly(snapshot)
        return snapshot,indexes
