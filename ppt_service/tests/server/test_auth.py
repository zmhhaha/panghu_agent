"""Signed JWT, authenticated access, issuer/sub and Origin/CSRF boundaries."""
from types import SimpleNamespace
from datetime import datetime,timedelta,timezone
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
    config=Settings('sqlite://',str(tmp_path),oidc_issuer='https://casdoor.test',oidc_audience='ppt',trusted_proxy_cidrs=('127.0.0.1/32',),allowed_origins=('https://ppt.test',))
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

@pytest.mark.parametrize('claims',[{'iss':'https://other.test'},
    {'sub':''},{'sub':123},{'sub':'x'*257},{'aud':'other'},{'exp':datetime.now(timezone.utc)-timedelta(seconds=60)},
    {'omit':('email',)},{'omit':('exp',)},{'omit':('iat',)},{'omit':('iss',)},
    {'omit':('sub',)},{'omit':('aud',)},
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


def test_forwarded_email_cannot_replace_invalid_signed_email(auth_client):
    _,c,token=auth_client
    response=c.get('/api/v1/me',headers={'Authorization':'Bearer '+token(email='not-an-email'),
        'X-Forwarded-Email':'alice@example.test','X-Auth-Request-Email':'alice@example.test'})
    assert response.status_code==401


@pytest.mark.parametrize('email',['new-user@community.example','Second.User@ANOTHER.TEST'])
def test_signed_accounts_from_other_email_domains_can_use_the_service(auth_client,email):
    _,c,token=auth_client
    headers={'Authorization':'Bearer '+token(sub='new-community-user',email=email)}
    response=c.get('/api/v1/me',headers=headers)
    assert response.status_code==200
    assert response.json()['sub']=='new-community-user'
    created=c.post('/api/v1/projects',json={'title':'Community feedback'},headers=headers|{
        'Origin':'https://ppt.test','X-CSRF-Token':response.json()['csrf_token']})
    assert created.status_code==201
    assert c.get('/api/v1/projects',headers=headers).json()[0]['id']==created.json()['id']


@pytest.mark.parametrize('authorization',[None,'','Bearer ','Bearer not-a-jwt','Basic ignored'])
def test_missing_or_malformed_token_still_rejects(auth_client,authorization):
    _,c,_=auth_client
    headers={'X-Auth-Request-Email':'anyone@example.test','X-Auth-Request-User':'anyone'}
    if authorization is not None:headers['Authorization']=authorization
    assert c.get('/api/v1/me',headers=headers).status_code==401


def test_wrong_signature_is_rejected_for_an_otherwise_valid_account(auth_client):
    _,c,token=auth_client
    claims=jwt.decode(token(email='new-user@community.example'),options={'verify_signature':False})
    other_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    forged=jwt.encode(claims,other_key,algorithm='RS256')
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+forged}).status_code==401


def test_new_email_account_can_create_only_its_own_projects(auth_client):
    _,c,token=auth_client
    alice={'Authorization':'Bearer '+token()}
    me=c.get('/api/v1/me',headers=alice).json()
    response=c.post('/api/v1/projects',json={'title':'Alice private project'},headers=alice|{
        'Origin':'https://ppt.test','X-CSRF-Token':me['csrf_token']})
    assert response.status_code==201
    project=response.json()['id']
    newcomer={'Authorization':'Bearer '+token(sub='stable-newcomer',email='newcomer@another.test')}
    newcomer_me=c.get('/api/v1/me',headers=newcomer)
    assert newcomer_me.status_code==200
    assert c.get('/api/v1/projects',headers=newcomer).json()==[]
    assert c.get('/api/v1/projects/'+project,headers=newcomer).status_code==404
    created=c.post('/api/v1/projects',json={'title':'Newcomer private project'},headers=newcomer|{
        'Origin':'https://ppt.test','X-CSRF-Token':newcomer_me.json()['csrf_token']})
    assert created.status_code==201
    assert c.get('/api/v1/projects/'+created.json()['id'],headers=alice).status_code==404
    assert c.get('/api/v1/projects/'+project,headers=alice).status_code==200


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


def test_obsolete_invitation_environment_cannot_block_valid_login(auth_client,monkeypatch,tmp_path):
    app,c,token=auth_client
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://unused@localhost/unused')
    monkeypatch.setenv('DATA_ROOT',str(tmp_path))
    monkeypatch.setenv('OIDC_ISSUER','https://casdoor.test')
    monkeypatch.setenv('OIDC_AUDIENCE','ppt')
    monkeypatch.setenv('TRUSTED_PROXY_CIDRS','127.0.0.1/32')
    monkeypatch.setenv('ALLOWED_ORIGINS','https://ppt.test')
    monkeypatch.setenv('INVITATION_EMAILS_FILE',str(tmp_path/'does-not-exist'))
    app.state.settings=Settings.env()
    response=c.get('/api/v1/me',headers={'Authorization':'Bearer '+token(email='new-user@community.example')})
    assert response.status_code==200
    assert response.json()['sub']=='stable-alice'
