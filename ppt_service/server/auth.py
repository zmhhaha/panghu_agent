from dataclasses import dataclass
import os,ipaddress,re,secrets,stat
from fastapi import Request,HTTPException
import jwt
@dataclass(frozen=True)
class Identity:
    issuer:str
    sub:str
@dataclass
class Settings:
    database_url:str
    data_root:str
    oidc_issuer:str=''
    oidc_audience:str=''
    jwks_url:str=''
    trusted_proxy_cidrs:tuple[str,...]=()
    allowed_origins:tuple[str,...]=()
    invitation_emails_file:str=''
    max_storage_bytes:int=20*1024**3
    event_retention_days:int=30
    raw_log_retention_days:int=7
    staging_retention_days:int=7
    @classmethod
    def env(cls):
        def items(k):return tuple(x.strip() for x in os.getenv(k,'').split(',') if x.strip())
        return cls(database_url=os.environ['DATABASE_URL'],data_root=os.environ['DATA_ROOT'],oidc_issuer=os.getenv('OIDC_ISSUER',''),oidc_audience=os.getenv('OIDC_AUDIENCE',''),jwks_url=os.getenv('OIDC_JWKS_URL',''),trusted_proxy_cidrs=items('TRUSTED_PROXY_CIDRS'),allowed_origins=items('ALLOWED_ORIGINS'),invitation_emails_file=os.getenv('INVITATION_EMAILS_FILE',''),max_storage_bytes=int(os.getenv('MAX_STORAGE_BYTES',str(20*1024**3))),event_retention_days=int(os.getenv('EVENT_RETENTION_DAYS','30')),raw_log_retention_days=int(os.getenv('RAW_LOG_RETENTION_DAYS','7')),staging_retention_days=int(os.getenv('STAGING_RETENTION_DAYS','7')))

MAX_INVITATION_BYTES=64*1024
EMAIL_LOCAL=re.compile(r"[A-Za-z0-9.!#$%&'+/=^_`{|}~-]+\Z")
EMAIL_LABEL=re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z')

def _exact_email(value):
    if not isinstance(value,str) or len(value)>254 or value.count('@')!=1:return False
    local,domain=value.split('@')
    if not local or len(local)>64 or not EMAIL_LOCAL.fullmatch(local):return False
    if local.startswith('.') or local.endswith('.') or '..' in local:return False
    labels=domain.split('.')
    return len(labels)>1 and all(EMAIL_LABEL.fullmatch(label) for label in labels)

def invitation_emails(path):
    """Reload a bounded owner file; follow Kubernetes ConfigMap projection links."""
    if not path:return frozenset()
    try:
        flags=os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0)
        with os.fdopen(os.open(path,flags),'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_INVITATION_BYTES:raise ValueError()
            content=stream.read(MAX_INVITATION_BYTES+1)
        if len(content)>MAX_INVITATION_BYTES:raise ValueError()
        emails=set()
        for line in content.decode('utf-8').splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            if not _exact_email(line):raise ValueError()
            emails.add(line.casefold())
        return frozenset(emails)
    except (OSError,UnicodeError,ValueError):
        raise HTTPException(503,detail={'code':'invitation_configuration_unavailable'}) from None

async def authenticate(request:Request)->Identity:
    config=request.app.state.settings
    try:
        peer=ipaddress.ip_address(request.client.host)
        if not any(peer in ipaddress.ip_network(c) for c in config.trusted_proxy_cidrs):raise ValueError()
        auth=request.headers.get('authorization','')
        if not auth.startswith('Bearer '):raise ValueError()
        token=auth[7:]
        client=request.app.state.jwks
        if client is None:raise ValueError()
        key=client.get_signing_key_from_jwt(token).key
        claims=jwt.decode(token,key,algorithms=['RS256'],audience=config.oidc_audience,issuer=config.oidc_issuer,options={'require':['exp','iat','iss','sub','aud']})
        sub=claims['sub']
        if not isinstance(sub,str) or not sub or len(sub)>256:raise ValueError()
        email=claims.get('email')
        if claims.get('email_verified') is not True or not _exact_email(email):raise ValueError()
    except Exception:
        raise HTTPException(401,detail={'code':'authentication_required'}) from None
    if email.casefold() not in invitation_emails(config.invitation_emails_file):
        raise HTTPException(401,detail={'code':'authentication_required'})
    if request.method not in ('GET','HEAD','OPTIONS'):
        if request.headers.get('origin') not in config.allowed_origins:raise HTTPException(403,detail={'code':'origin_rejected'})
        cookie=request.cookies.get('ppt_csrf','');header=request.headers.get('x-csrf-token','')
        if not cookie or not secrets.compare_digest(cookie,header):raise HTTPException(403,detail={'code':'csrf_rejected'})
    return Identity(config.oidc_issuer,sub)
