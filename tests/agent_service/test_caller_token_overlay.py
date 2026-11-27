"""Offline additive caller credentials; all values are disposable test fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from agent_service import __main__ as cli
from agent_service.config import ConfigurationError,load_configuration
from agent_service.caller_token_overlay import secure_posix_metadata
from test_config import public,secrets,files,TOKEN_SENTINEL,WORKER_TOKEN

NEW_TOKEN='new-synthetic-scoped-caller-value-for-offline-tests-only'

def environment(tmp_path):
    cfg=public();cfg['callers'].append(dict(tenant='internal',caller='new-reviewer',
        profiles=['workspace-task.v1'],token_ref='new-reviewer'))
    paths=files(tmp_path,cfg);token=tmp_path/'new-caller-token';token.write_bytes(NEW_TOKEN.encode());token.chmod(0o600)
    return paths,token

def test_adds_new_declared_principal_preserves_all_legacy_bytes_and_credentials(tmp_path):
    paths,token=environment(tmp_path);before=[p.read_bytes() for p in paths]
    loaded=load_configuration(*paths,caller_token_files=['new-reviewer='+str(token)])
    assert [p.read_bytes() for p in paths]==before
    assert set(loaded.api_settings.principals)=={TOKEN_SENTINEL,NEW_TOKEN}
    principal=loaded.api_settings.principals[NEW_TOKEN]
    assert (principal.tenant,principal.caller,principal.profiles)==('internal','new-reviewer',frozenset({'workspace-task.v1'}))
    assert loaded.worker_endpoints[0].token==WORKER_TOKEN
    assert loaded.summary()['caller_token_overlay_refs']==['new-reviewer']
    assert NEW_TOKEN not in repr(loaded) and NEW_TOKEN not in json.dumps(loaded.summary())

@pytest.mark.parametrize('bad',['unknown','existing','duplicate_ref','duplicate_path','relative','absent',
    'directory','empty','short','long','space','double_lf','nonascii','reuse_old','reuse_worker','symlink'])
def test_rejects_unknown_override_alias_bad_file_or_reused_secret_without_exposure(tmp_path,bad):
    paths,token=environment(tmp_path);argument='new-reviewer='+str(token);args=[argument]
    if bad=='unknown':args=['unknown='+str(token)]
    elif bad=='existing':args=['ppt-service='+str(token)]
    elif bad=='duplicate_ref':args=[argument,argument]
    elif bad=='duplicate_path':
        cfg=json.loads(paths[0].read_bytes());cfg['callers'].append(dict(tenant='internal',caller='another',profiles=['workspace-task.v1'],token_ref='another'))
        paths[0].write_text(json.dumps(cfg));args+=["another="+str(token)]
    elif bad=='relative':args=['new-reviewer=relative-token']
    elif bad=='absent':args=['new-reviewer='+str(tmp_path/'missing')]
    elif bad=='directory':args=['new-reviewer='+str(tmp_path)]
    elif bad=='symlink':
        linked=tmp_path/'linked-token'
        try:linked.symlink_to(token)
        except OSError:pytest.skip('Host cannot create symbolic links')
        args=['new-reviewer='+str(linked)]
    else:
        value={'empty':b'','short':b'too-short','long':b'x'*514,'space':b'x'*32+b' ',
            'double_lf':NEW_TOKEN.encode()+b'\n\n','nonascii':b'\xff'*40,
            'reuse_old':TOKEN_SENTINEL.encode(),'reuse_worker':WORKER_TOKEN.encode()}[bad]
        token.write_bytes(value)
    with pytest.raises(ConfigurationError,match='caller_token_overlay_invalid') as captured:
        load_configuration(*paths,caller_token_files=args)
    assert NEW_TOKEN not in str(captured.value) and TOKEN_SENTINEL not in str(captured.value)

@pytest.mark.parametrize('mode,uid,gid,allowed',[
    (0o600,10000,10000,True),(0o440,0,10000,True),(0o400,10000,10000,True),
    (0o640,0,10000,True),(0o644,0,10000,False),(0o660,0,10000,False),
    (0o500,0,10000,False),(0o444,0,10000,False),(0o440,1234,10000,False),
    (0o440,0,2222,False),(0o4600,0,10000,False)])
def test_posix_token_metadata_rules_without_platform_dependent_chmod(mode,uid,gid,allowed):
    observed=SimpleNamespace(st_mode=stat.S_IFREG|mode,st_uid=uid,st_gid=gid)
    assert secure_posix_metadata(observed,10000,10000) is allowed

def test_one_terminal_lf_is_accepted_but_no_environment_can_choose_overlay(tmp_path,monkeypatch):
    paths,token=environment(tmp_path);token.write_bytes(NEW_TOKEN.encode()+b'\n')
    assert NEW_TOKEN in load_configuration(*paths,caller_token_files=['new-reviewer='+str(token)]).api_settings.principals
    monkeypatch.setenv('AGENT_CALLER_TOKEN_FILES','new-reviewer='+str(token))
    with pytest.raises(ConfigurationError,match='required_secret_missing'):load_configuration(*paths)

def test_cli_explicit_overlay_is_passed_to_loader_and_check_does_not_open_database(tmp_path,capsys):
    paths,token=environment(tmp_path)
    assert cli.main(['--config',str(paths[0]),'--secrets',str(paths[1]),
        '--caller-token-file','new-reviewer='+str(token),'check'])==0
    printed=capsys.readouterr();summary=json.loads(printed.out)
    assert summary['caller_count']==2 and summary['caller_token_overlay_refs']==['new-reviewer']
    assert NEW_TOKEN not in printed.out+printed.err
