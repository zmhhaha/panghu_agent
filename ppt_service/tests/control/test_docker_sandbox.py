"""Opt-in no-model tests of the real pinned-image parsing sandbox on Docker host."""
import hashlib,json,os,struct,tempfile,uuid,zipfile,zlib
from pathlib import Path
import pytest
from ppt_service.control.execution import RuntimeConfig,ControlError
from ppt_service.control.pods import Docker,DockerSandbox
from ppt_service.control.staging import atomic_json
pytestmark=pytest.mark.skipif(os.getenv('PPT_RUN_DOCKER_SANDBOX_TESTS')!='true',reason='Explicit Docker sandbox test flag required')

def pdf_bytes(text=True,pages=1):
    objects=[]
    objects.append(b'<< /Type /Catalog /Pages 2 0 R >>')
    kids=' '.join(f'{3+n*2} 0 R' for n in range(pages))
    objects.append(f'<< /Type /Pages /Kids [{kids}] /Count {pages} >>'.encode())
    font=3+pages*2
    for n in range(pages):
        stream=b'BT /F1 12 Tf 10 100 Td (Public deterministic source) Tj ET' if text else b''
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {4+n*2} 0 R >>'.encode())
        objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
    objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    output=bytearray(b'%PDF-1.4\n');offsets=[0]
    for n,obj in enumerate(objects,1):offsets.append(len(output));output.extend(f'{n} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref=len(output);output.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(output)
def png_bytes():
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xff\x00\x00\xff'))+chunk(b'IEND',b'')
@pytest.fixture
def sandbox_root():
    root=Path(os.environ['PPT_CONTROL_HOST_DATA_ROOT']);root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='test-',dir=root) as temp:
        p=Path(temp);p.chmod(0o755)
        yield p

def execute(root,files):
    inputs=root/'inputs';inputs.mkdir();sources=[]
    for name,mime,data in files:
        (inputs/name).write_bytes(data);sources.append({'id':str(uuid.uuid4()),'path':name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'media_type':mime})
    inventory={'schema_version':1,'input_snapshot_sha256':'a'*64,'sources':sources};atomic_json(inputs/'inventory.json',inventory)
    config=RuntimeConfig(root,code_root=Path(os.environ['PPT_CONTROL_HOST_CODE_ROOT']))
    sandbox=DockerSandbox(config);scope=str(uuid.uuid4());sandbox.scope_uid=scope
    try:return sandbox.parse(inputs,inventory)
    finally:assert Docker().ids(scope)==(), 'Sandbox namespace must be removed even after rejection'

def test_real_md_docx_pdf_png_container(sandbox_root):
    import io
    doc=io.BytesIO()
    with zipfile.ZipFile(doc,'w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Document public source</w:t></w:r></w:p></w:document>')
    files=[('source.md','text/markdown','公开测试材料'.encode()),('source.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document',doc.getvalue()),('source.pdf','application/pdf',pdf_bytes()),('image.png','image/png',png_bytes())]
    result=execute(sandbox_root,files)
    assert len(result['sources'])==4;assert result['sources'][2]['pages']==1;assert 'Public deterministic source' in result['sources'][2]['text'];assert result['sources'][3]['image']=={'width':1,'height':1}
@pytest.mark.parametrize('data',[pdf_bytes(False),pdf_bytes(True,151)])
def test_real_scan_and_pdf_page_limit_reject(sandbox_root,data):
    with pytest.raises(ControlError,match='parse_failed'):execute(sandbox_root,[('source.pdf','application/pdf',data)])
