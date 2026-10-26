"""Signed JWT, proxy-peer, issuer/sub and Origin/CSRF boundary checks."""
from types import SimpleNamespace
from datetime import datetime,timedelta,timezone
import os
from pathlib import Path
import jwt,pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from ppt_service.server.app import create_app
from ppt_service.server.auth import Settings
from ppt_service.server.migrate import migrate

@pytest.fixture
def auth_client(tmp_path):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);migrate(engine)
    emails=tmp_path/'allowed-emails'
    emails.write_text('alice@example.test\n',encoding='utf-8')
    config=Settings('sqlite://',str(tmp_path),oidc_issuer='https://casdoor.test',oidc_audience='ppt',trusted_proxy_cidrs=('127.0.0.1/32',),allowed_origins=('https://ppt.test',),invitation_emails_file=str(emails))
    app=create_app(config,engine)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    app.state.jwks=SimpleNamespace(get_signing_key_from_jwt=lambda token:SimpleNamespace(key=key.public_key()))
    def token(*,omit=(),**overrides):
        claims={'iss':'https://casdoor.test','sub':'stable-alice','aud':'ppt','iat':datetime.now(timezone.utc),'exp':datetime.now(timezone.utc)+timedelta(minutes=5),'email':'alice@example.test'}|overrides
        for claim in omit:claims.pop(claim,None)
        return jwt.encode(claims,key,algorithm='RS256')
    c=TestClient(app,base_url='https://ppt.test',client=('127.0.0.1',12345))
    return app,c,token

def test_signed_identity_and_csrf(auth_client):
    app,c,token=auth_client;headers={'Authorization':'Bearer '+token(),'X-Auth-Request-User':'mallory'}
    me=c.get('/api/v1/me',headers=headers);assert me.status_code==200;assert me.json()['sub']=='stable-alice'
    assert c.post('/api/v1/projects',json={'title':'ok'},headers=headers).status_code==403
    mutation=headers|{'Origin':'https://ppt.test','X-CSRF-Token':me.json()['csrf_token']}
    assert c.post('/api/v1/projects',json={'title':'ok'},headers=mutation).status_code==201
    assert c.post('/api/v1/projects',json={'title':'bad'},headers=mutation|{'Origin':'https://evil.test'}).status_code==403
    assert c.post('/api/v1/projects',json={'title':'bad'},headers=mutation|{'X-CSRF-Token':'wrong'}).status_code==403

@pytest.mark.parametrize('claims',[{'iss':'https://other.test'},{'email':'unlisted@example.test'},
    {'sub':''},{'sub':123},{'sub':'x'*257},{'aud':'other'},{'exp':datetime.now(timezone.utc)-timedelta(seconds=60)},
    {'omit':('email',)},
    {'email':None},{'email':123},{'email':'not-an-email'},{'email':' alice@example.test'}])
def test_invalid_claims_reject(auth_client,claims):
    app,c,token=auth_client
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+token(**claims)}).status_code==401

@pytest.mark.parametrize('claims',[{'omit':('email_verified',)},
    {'email_verified':False},{'email_verified':'true'}], ids=['missing','false','string'])
def test_casdoor_email_verification_claim_does_not_control_admission(auth_client,claims):
    _,c,token=auth_client
    response=c.get('/api/v1/me',headers={'Authorization':'Bearer '+token(**claims)})
    assert response.status_code==200
    assert response.json()['sub']=='stable-alice'

def test_untrusted_peer_rejects_even_valid_jwt(auth_client):
    app,c,token=auth_client
    other=TestClient(app,base_url='https://ppt.test',client=('192.0.2.3',12345))
    assert other.get('/api/v1/me',headers={'Authorization':'Bearer '+token(),'X-Forwarded-For':'127.0.0.1'}).status_code==401

def test_forwarded_email_cannot_authorize_unlisted_token(auth_client):
    _,c,token=auth_client
    response=c.get('/api/v1/me',headers={'Authorization':'Bearer '+token(email='unlisted@example.test'),
        'X-Forwarded-Email':'alice@example.test','X-Auth-Request-Email':'alice@example.test'})
    assert response.status_code==401

def test_casefold_comments_and_dynamic_allowlist_updates(auth_client):
    app,c,token=auth_client
    path=Path(app.state.settings.invitation_emails_file)
    headers={'Authorization':'Bearer '+token(email='ALICE@EXAMPLE.TEST')}
    path.write_text('# owner-approved users\n\n  Alice@Example.Test  \n',encoding='utf-8')
    assert c.get('/api/v1/me',headers=headers).status_code==200
    path.write_text('bob@example.test\n',encoding='utf-8')
    assert c.get('/api/v1/me',headers=headers).status_code==401
    path.write_text('alice@example.test\n',encoding='utf-8')
    assert c.get('/api/v1/me',headers=headers).status_code==200
    path.unlink()
    response=c.get('/api/v1/me',headers=headers)
    assert response.status_code==503
    assert response.json()['detail']['code']=='invitation_configuration_unavailable'

@pytest.mark.parametrize('contents',[b'*@example.test\n',b'alice@*.test\n',b'alice?@example.test\n',
    b'Alice <alice@example.test>\n',b'alice@example.test # inline comment\n',b'\xff\n',b'a'*(64*1024+1)],
    ids=['local-wildcard','domain-wildcard','question-wildcard','display-name','inline-comment','invalid-utf8','oversize'])
def test_invalid_allowlist_fails_closed_with_503(auth_client,contents):
    app,c,token=auth_client
    Path(app.state.settings.invitation_emails_file).write_bytes(contents)
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+token()}).status_code==503

@pytest.mark.parametrize('contents',['','# comments only\n\n'])
def test_empty_allowlist_denies_everyone(auth_client,contents):
    app,c,token=auth_client
    Path(app.state.settings.invitation_emails_file).write_text(contents,encoding='utf-8')
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+token()}).status_code==401

def test_unconfigured_allowlist_denies_everyone(auth_client):
    app,c,token=auth_client
    app.state.settings.invitation_emails_file=''
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+token()}).status_code==401

def test_unreadable_allowlist_is_503(auth_client,monkeypatch):
    import ppt_service.server.auth as auth
    app,c,token=auth_client
    original=auth.os.open
    def unreadable(path,*args,**kwargs):
        if str(path)==app.state.settings.invitation_emails_file:raise PermissionError()
        return original(path,*args,**kwargs)
    monkeypatch.setattr(auth.os,'open',unreadable)
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+token()}).status_code==503

def test_configmap_projection_symlink_reloads(auth_client,tmp_path):
    app,c,token=auth_client
    v1=tmp_path/'version-1';v1.mkdir()
    v2=tmp_path/'version-2';v2.mkdir()
    (v1/'emails').write_text('alice@example.test\n',encoding='utf-8')
    (v2/'emails').write_text('bob@example.test\n',encoding='utf-8')
    data=tmp_path/'..data'
    try:
        data.symlink_to(v1,target_is_directory=True)
        projected=tmp_path/'projected-emails';projected.symlink_to(data/'emails')
    except OSError as exc:
        if os.name=='nt' and getattr(exc,'winerror',None)==1314:
            pytest.skip('Windows account cannot create symlinks; real ConfigMap projection requires Linux validation')
        raise
    app.state.settings.invitation_emails_file=str(projected)
    headers={'Authorization':'Bearer '+token()}
    assert c.get('/api/v1/me',headers=headers).status_code==200
    replacement=tmp_path/'..next';replacement.symlink_to(v2,target_is_directory=True)
    replacement.replace(data)
    assert c.get('/api/v1/me',headers=headers).status_code==401

def test_same_email_does_not_merge_subject_ownership(auth_client):
    _,c,token=auth_client
    alice={'Authorization':'Bearer '+token()}
    me=c.get('/api/v1/me',headers=alice).json()
    mutation=alice|{'Origin':'https://ppt.test','X-CSRF-Token':me['csrf_token']}
    response=c.post('/api/v1/projects',json={'title':'Alice private project'},headers=mutation)
    assert response.status_code==201
    project=response.json()['id']
    other={'Authorization':'Bearer '+token(sub='stable-other-same-email')}
    assert c.get('/api/v1/me',headers=other).status_code==200
    assert c.get('/api/v1/projects',headers=other).json()==[]
    assert c.get('/api/v1/projects/'+project,headers=other).status_code==404
    assert c.get('/api/v1/projects/'+project,headers=alice).status_code==200

def test_same_email_and_sub_does_not_merge_issuer_ownership(auth_client):
    app,c,token=auth_client
    alice={'Authorization':'Bearer '+token()}
    me=c.get('/api/v1/me',headers=alice).json()
    response=c.post('/api/v1/projects',json={'title':'Original issuer private project'},
        headers=alice|{'Origin':'https://ppt.test','X-CSRF-Token':me['csrf_token']})
    assert response.status_code==201
    project=response.json()['id']
    app.state.settings.oidc_issuer='https://other-casdoor.test'
    other={'Authorization':'Bearer '+token(iss='https://other-casdoor.test')}
    assert c.get('/api/v1/me',headers=other).status_code==200
    assert c.get('/api/v1/projects',headers=other).json()==[]
    assert c.get('/api/v1/projects/'+project,headers=other).status_code==404

def test_nonregular_allowlist_is_503(auth_client,tmp_path):
    app,c,token=auth_client
    app.state.settings.invitation_emails_file=str(tmp_path)
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+token()}).status_code==503

def test_environment_uses_owner_email_file(monkeypatch):
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://unused@localhost/unused')
    monkeypatch.setenv('DATA_ROOT','/unused')
    monkeypatch.setenv('INVITATION_EMAILS_FILE','/owner/emails')
    assert Settings.env().invitation_emails_file=='/owner/emails'
