"""Add new declared caller credentials without copying the existing secret bundle."""
from copy import deepcopy
import os
from pathlib import Path
import re
import stat

POSIX=os.name=='posix'

def secure_posix_metadata(observed,uid,gid):
    mode=stat.S_IMODE(observed.st_mode)
    return (observed.st_uid in {0,uid} and not mode&0o7137
        and (not mode&0o040 or observed.st_gid in {0,gid}))

def _token(path):
    filename=Path(path)
    if not filename.is_absolute() or '..' in filename.parts:raise ValueError()
    for component in (*reversed(filename.parents),filename):
        observed=component.lstat()
        if stat.S_ISLNK(observed.st_mode) or getattr(observed,'st_file_attributes',0)&0x400:raise ValueError()
        if component!=filename and not stat.S_ISDIR(observed.st_mode):raise ValueError()
    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink!=1 or not 32<=observed.st_size<=513:raise ValueError()
    if POSIX and not secure_posix_metadata(observed,os.geteuid(),os.getegid()):raise ValueError()
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0)
    with os.fdopen(os.open(filename,flags),'rb') as stream:
        opened=os.fstat(stream.fileno())
        if not os.path.samestat(observed,opened):raise ValueError()
        raw=stream.read(514);after=os.fstat(stream.fileno())
        if len(raw)!=opened.st_size or len(raw)>513 or (opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError()
    if raw.endswith(b'\n'):raw=raw[:-1]
    token=raw.decode('ascii')
    if not 32<=len(token)<=512 or any(not 33<=ord(char)<=126 for char in token):raise ValueError()
    return token,(opened.st_dev,opened.st_ino)

def overlay_caller_tokens(secret_wire,callers,arguments):
    """Only additive, explicit CLI TOKEN_REF=/absolute/file values are accepted."""
    if arguments is None:return secret_wire,()
    if type(arguments) not in (list,tuple) or len(arguments)>32:raise ValueError()
    result=deepcopy(secret_wire);tokens=result['tokens']
    if type(tokens) is not dict:raise ValueError()
    allowed={row.token_ref for row in callers};references=set();paths=set();files=set()
    for argument in arguments:
        if type(argument) is not str or '=' not in argument:raise ValueError()
        reference,path=argument.split('=',1)
        if (re.fullmatch('[a-z][a-z0-9_.-]{0,63}',reference) is None or reference not in allowed
                or reference in tokens or reference in references or not path or len(path)>4096):raise ValueError()
        canonical=str(Path(path).absolute())
        if canonical in paths:raise ValueError()
        value,identity=_token(path)
        if identity in files or value in tokens.values():raise ValueError()
        references.add(reference);paths.add(canonical);files.add(identity);tokens[reference]=value
    return result,tuple(sorted(references))
