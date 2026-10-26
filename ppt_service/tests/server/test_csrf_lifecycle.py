"""Independent browser documents retain matching CSRF headers after /me reads."""
import re
import pytest
from test_auth import auth_client


def test_profile_reads_keep_other_documents_mutations_valid(auth_client):
    _,client,token=auth_client
    auth={'Authorization':'Bearer '+token()}
    first=client.get('/api/v1/me',headers=auth)
    assert first.status_code==200
    saved=first.json()['csrf_token']
    assert re.fullmatch(r'[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]',saved)
    second=client.get('/api/v1/me',headers=auth)
    assert second.status_code==200
    assert second.json()['csrf_token']==saved
    headers=auth|{'Origin':'https://ppt.test','X-CSRF-Token':saved}
    response=client.post('/api/v1/projects',headers=headers,json={'title':'First document'})
    assert response.status_code==201
    assert client.post('/api/v1/projects',headers=headers|{'X-CSRF-Token':'incorrect'},json={'title':'Wrong token'}).status_code==403
    assert client.post('/api/v1/projects',headers=headers|{'Origin':'https://elsewhere.test'},json={'title':'Wrong origin'}).status_code==403


@pytest.mark.parametrize('invalid',['short','A'*42,'A'*44,'A'*42+'B','A'*42+'=','A'*42+'/'])
def test_profile_replaces_only_invalid_csrf_cookie(auth_client,invalid):
    _,client,token=auth_client
    client.cookies.set('ppt_csrf',invalid,domain='ppt.test',path='/')
    response=client.get('/api/v1/me',headers={'Authorization':'Bearer '+token()})
    assert response.status_code==200
    replacement=response.json()['csrf_token']
    assert replacement!=invalid
    assert re.fullmatch(r'[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]',replacement)
    assert 'Secure' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']


def test_profile_does_not_reuse_cookies_without_authenticated_identity(auth_client):
    _,client,_=auth_client
    client.cookies.set('ppt_csrf','A'*43,domain='ppt.test',path='/')
    assert client.get('/api/v1/me').status_code==401
