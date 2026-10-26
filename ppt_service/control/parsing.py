"""Bounded source parser entrypoint; production runs ONLY in a network-none sandbox."""
from __future__ import annotations
import argparse,hashlib,json,stat,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
from .staging import open_regular,relative,read_json,atomic_json
from .execution import ControlError
MAX_BYTES=25*1024**2
MAX_TEXT=500_000
MAX_TOTAL_TEXT=1_500_000
MAX_PAGES=150
MAX_ZIP=64*1024**2

def xml(data):
    if len(data)>8*1024**2 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ControlError('unsafe_xml')
    root=ET.fromstring(data);count=0
    def walk(node,depth=0):
        nonlocal count
        count+=1
        if count>100000 or depth>64:raise ControlError('xml_limit')
        for child in node:walk(child,depth+1)
    walk(root);return root

def docx(path:Path):
    total=0;seen=set()
    with zipfile.ZipFile(path) as package:
        if len(package.infolist())>2048:raise ControlError('zip_entry_limit')
        document=None
        for info in package.infolist():
            name=info.orig_filename.rstrip('/') if info.is_dir() else info.orig_filename
            relative(name)
            if name.casefold() in seen:raise ControlError('duplicate_zip_part')
            seen.add(name.casefold());mode=stat.S_IFMT(info.external_attr>>16)
            if info.flag_bits&1 or mode not in (0,stat.S_IFREG,stat.S_IFDIR) or info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):raise ControlError('unsupported_zip_part')
            total+=info.file_size
            if info.file_size>16*1024**2 or total>MAX_ZIP or info.file_size>max(1,info.compress_size)*100:raise ControlError('zip_expansion_limit')
            lower=name.lower()
            if any(v in lower for v in ('vbaproject','activex','oleobject','encryptedpackage','encryptioninfo')) or lower.endswith(('.bin','.vba')):raise ControlError('active_office_content')
            if info.is_dir():continue
            with package.open(info) as part:data=part.read(16*1024**2+1)
            if len(data)!=info.file_size:raise ControlError('zip_size_mismatch')
            if name.endswith(('.xml','.rels')):
                parsed=xml(data)
                for node in parsed.iter():
                    if node.attrib.get('TargetMode','').lower()=='external':raise ControlError('external_office_reference')
                    if any(v in value.lower() for value in node.attrib.values() for v in ('macroenabled','vbaproject','activex','oleobject')):raise ControlError('active_office_content')
                if name=='word/document.xml':document=parsed
        if document is None or '[content_types].xml' not in seen:raise ControlError('invalid_docx')
        paragraphs=[]
        for node in document.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
            paragraphs.append(''.join(n.text or '' for n in node.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t')))
        result='\n'.join(paragraphs)
        if len(result)>MAX_TEXT:raise ControlError('text_limit')
        return result

def parse_one(path:Path,mime:str):
    if mime=='text/markdown':
        with open_regular(path.parent,path.name,MAX_BYTES) as f:raw=f.read(MAX_BYTES+1)
        if b'\x00' in raw:raise ControlError('invalid_text')
        text=raw.decode('utf-8')
        if len(text)>MAX_TEXT:raise ControlError('text_limit')
        return {'text':text,'pages':None}
    if mime=='application/vnd.openxmlformats-officedocument.wordprocessingml.document':return {'text':docx(path),'pages':None}
    if mime=='application/pdf':
        import fitz
        with fitz.open(path) as document:
            if document.is_encrypted or document.needs_pass or document.embfile_count():raise ControlError('unsupported_pdf')
            if document.page_count>MAX_PAGES:raise ControlError('pdf_page_limit')
            texts=[];size=0
            for page in document:
                if next(iter(page.widgets() or ()),None) is not None:raise ControlError('pdf_active_form')
                data=page.get_text('text');size+=len(data)
                if size>MAX_TEXT:raise ControlError('text_limit')
                texts.append(data)
            text='\n'.join(texts)
            if not text.strip():raise ControlError('scanned_pdf_unsupported')
            return {'text':text,'pages':document.page_count}
    if mime in ('image/png','image/jpeg'):
        from PIL import Image
        Image.MAX_IMAGE_PIXELS=20_000_000
        with Image.open(path) as image:
            if image.format not in ('PNG','JPEG') or image.width*image.height>20_000_000 or image.width>20000 or image.height>20000:raise ControlError('image_limit')
            width,height=image.size;image.verify()
        return {'text':'','image':{'width':width,'height':height},'pages':None}
    raise ControlError('unsupported_media')

def parse_inputs(root:Path,inventory:dict):
    sources=inventory.get('sources')
    if not isinstance(sources,list) or len(sources)>10:raise ControlError('source_limit')
    results=[];total=0
    for source in sources:
        key=relative(source['path']);path=root/key
        with open_regular(root,key,MAX_BYTES) as stream:
            h=hashlib.sha256();size=0
            while chunk:=stream.read(65536):h.update(chunk);size+=len(chunk)
        if h.hexdigest()!=source['sha256'] or size!=source['bytes']:raise ControlError('source_hash_mismatch')
        parsed=parse_one(path,source['media_type']);total+=len(parsed['text'])
        if total>MAX_TOTAL_TEXT:raise ControlError('project_text_limit')
        results.append({'id':source['id'],'path':key,'sha256':source['sha256'],'parse_status':'succeeded',**parsed})
    return {'schema_version':1,'input_snapshot_sha256':inventory['input_snapshot_sha256'],'sources':results}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--inputs',type=Path,default=Path('/inputs'));parser.add_argument('--output',type=Path,default=Path('/output/parsed.json'));args=parser.parse_args()
    try:
        inventory=read_json(args.inputs,'inventory.json',1024**2)
        result=parse_inputs(args.inputs,inventory);atomic_json(args.output,result);return 0
    except Exception:
        # Parser diagnostics never disclose source text, filesystem paths or stack.
        atomic_json(args.output,{'error_code':'parse_failed'});return 2
if __name__=='__main__':raise SystemExit(main())
