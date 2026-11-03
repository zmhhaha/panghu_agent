"""UUID-addressed blobs; no Office parsing in the public API."""
import os,stat,hashlib,uuid,codecs,errno,shutil,logging
from pathlib import Path
from fastapi import HTTPException
MAX_FILE=25*1024**2
MAX_PROJECT=100*1024**2
DEFAULT_UPLOAD_MIN_FREE_BYTES=128*1024**2
logger=logging.getLogger(__name__)
TYPES={'.md':'text/markdown','.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}
def safe_key(key):
    parts=key.split('/')
    if not parts or any(x in ('','.','..') or '\\' in x or ':' in x for x in parts):raise ValueError('unsafe key')
    return parts

def validate_upload_reserve(value):
    if type(value) is not int or value<0:
        raise ValueError('upload_min_free_bytes must be a nonnegative integer')
    return value

class Storage:
    def __init__(self,root,*,min_free_bytes=DEFAULT_UPLOAD_MIN_FREE_BYTES):
        self.min_free_bytes=validate_upload_reserve(min_free_bytes)
        self.root=Path(root).absolute();self.root.mkdir(parents=True,exist_ok=True)
        if self.root.is_symlink():raise ValueError('symlink storage root')

    def _require_upload_capacity(self,incoming=0,*,fd=None):
        try:
            if os.name=='posix':
                measured=os.statvfs(self.root) if fd is None else os.fstatvfs(fd)
                blocks,block_size=measured.f_bavail,measured.f_frsize
                if type(blocks) is not int or blocks<0 or type(block_size) is not int or block_size<=0:
                    raise ValueError('invalid filesystem capacity measurement')
                available=blocks*block_size
            else:
                # Windows development uses an actual measurement as well;
                # absence of statvfs must never silently disable the guard.
                available=shutil.disk_usage(self.root).free
                if type(available) is not int or available<0:
                    raise ValueError('invalid filesystem capacity measurement')
        except OSError as exc:
            if exc.errno==errno.ENOSPC:
                raise HTTPException(507,detail={'code':'storage_capacity_low'}) from None
            raise HTTPException(503,detail={'code':'storage_capacity_unavailable'}) from None
        except (AttributeError,ValueError,OverflowError):
            raise HTTPException(503,detail={'code':'storage_capacity_unavailable'}) from None
        if available<self.min_free_bytes+incoming:
            raise HTTPException(507,detail={'code':'storage_capacity_low'})
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
        target=self.root.joinpath(*safe_key(key));directory_created=False
        h=hashlib.sha256();size=0;head=b''
        decoder=codecs.getincrementaldecoder('utf-8')() if suffix=='.md' else None
        try:
            self._require_upload_capacity()
            target.parent.mkdir(parents=True,exist_ok=False);directory_created=True
            # Unbuffered writes let every following capacity measurement see
            # earlier chunks, including streams that return tiny chunks.
            with open(target,'xb',buffering=0) as out:
                while chunk:=await file.read(65536):
                    size+=len(chunk)
                    if size>MAX_FILE:raise HTTPException(413,detail={'code':'file_too_large'})
                    if decoder:
                        if b'\x00' in chunk:raise HTTPException(415,detail={'code':'invalid_text_encoding'})
                        try:decoder.decode(chunk,final=False)
                        except UnicodeDecodeError:raise HTTPException(415,detail={'code':'invalid_text_encoding'}) from None
                    if len(head)<8192:head=(head+chunk)[:8192]
                    remaining=memoryview(chunk)
                    while remaining:
                        self._require_upload_capacity(len(remaining),fd=out.fileno())
                        written=out.write(remaining)
                        if not isinstance(written,int) or written<=0:
                            raise OSError(errno.EIO,'incomplete upload write')
                        remaining=remaining[written:]
                    h.update(chunk)
            valid= size>0 and ((suffix=='.md' and b'\x00' not in head) or (suffix=='.pdf' and head.startswith(b'%PDF-')) or (suffix=='.docx' and head.startswith(b'PK\x03\x04')) or (suffix=='.png' and head.startswith(b'\x89PNG\r\n\x1a\n')) or (suffix in ('.jpg','.jpeg') and head.startswith(b'\xff\xd8\xff')))
            if decoder:
                try:decoder.decode(b'',final=True)
                except UnicodeDecodeError:valid=False
            if not valid:raise HTTPException(415,detail={'code':'invalid_blob_signature'})
            return aid,key,size,h.hexdigest(),TYPES[suffix]
        except BaseException as exc:
            # Only the new UUID directory belongs to this attempt. A mkdir
            # collision or preflight rejection must never remove prior data.
            if directory_created:
                try:self.remove_upload(key)
                except OSError:logger.error('partial upload cleanup failed')
            if isinstance(exc,OSError) and exc.errno==errno.ENOSPC:
                raise HTTPException(507,detail={'code':'storage_capacity_low'}) from None
            raise
    def remove_upload(self,key):
        path=self.root.joinpath(*safe_key(key));path.unlink(missing_ok=True)
        try:path.parent.rmdir()
        except OSError:pass
