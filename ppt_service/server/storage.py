"""UUID-addressed blobs; no Office parsing in the public API."""
import os,stat,hashlib,uuid,codecs
from pathlib import Path
from fastapi import HTTPException
MAX_FILE=25*1024**2
MAX_PROJECT=100*1024**2
TYPES={'.md':'text/markdown','.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}
def safe_key(key):
    parts=key.split('/')
    if not parts or any(x in ('','.','..') or '\\' in x or ':' in x for x in parts):raise ValueError('unsafe key')
    return parts
class Storage:
    def __init__(self,root):
        self.root=Path(root).absolute();self.root.mkdir(parents=True,exist_ok=True)
        if self.root.is_symlink():raise ValueError('symlink storage root')
    def open(self,key,max_bytes):
        parts=safe_key(key)
        # POSIX dir_fd and O_NOFOLLOW prevent both ancestor and final symlink races.
        if os.name=='posix':
            fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                for part in parts[:-1]:
                    n=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=n
                f=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            finally:os.close(fd)
            stream=os.fdopen(f,'rb')
        else:
            # Local Windows development only; production is Linux dir_fd/no-follow.
            target=self.root
            for part in parts:
                target=target/part
                if target.is_symlink() or target.is_junction():raise OSError('link rejected')
            stream=open(target,'rb')
        st=os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size>max_bytes:stream.close();raise OSError('invalid blob')
        return stream
    async def upload(self,project_id,file):
        suffix=Path(file.filename or '').suffix.lower()
        if suffix not in TYPES:raise HTTPException(415,detail={'code':'unsupported_media_type'})
        aid=str(uuid.uuid4());key=f'projects/{project_id}/assets/{aid}/original'
        target=self.root.joinpath(*safe_key(key));target.parent.mkdir(parents=True,exist_ok=False)
        h=hashlib.sha256();size=0;head=b''
        decoder=codecs.getincrementaldecoder('utf-8')() if suffix=='.md' else None
        try:
            with open(target,'xb') as out:
                while chunk:=await file.read(65536):
                    size+=len(chunk)
                    if size>MAX_FILE:raise HTTPException(413,detail={'code':'file_too_large'})
                    if decoder:
                        if b'\x00' in chunk:raise HTTPException(415,detail={'code':'invalid_text_encoding'})
                        try:decoder.decode(chunk,final=False)
                        except UnicodeDecodeError:raise HTTPException(415,detail={'code':'invalid_text_encoding'}) from None
                    if len(head)<8192:head=(head+chunk)[:8192]
                    h.update(chunk);out.write(chunk)
            valid= size>0 and ((suffix=='.md' and b'\x00' not in head) or (suffix=='.pdf' and head.startswith(b'%PDF-')) or (suffix=='.docx' and head.startswith(b'PK\x03\x04')) or (suffix=='.png' and head.startswith(b'\x89PNG\r\n\x1a\n')) or (suffix in ('.jpg','.jpeg') and head.startswith(b'\xff\xd8\xff')))
            if decoder:
                try:decoder.decode(b'',final=True)
                except UnicodeDecodeError:valid=False
            if not valid:raise HTTPException(415,detail={'code':'invalid_blob_signature'})
            return aid,key,size,h.hexdigest(),TYPES[suffix]
        except BaseException:
            target.unlink(missing_ok=True);target.parent.rmdir();raise
    def remove_upload(self,key):
        path=self.root.joinpath(*safe_key(key));path.unlink(missing_ok=True)
        try:path.parent.rmdir()
        except OSError:pass
