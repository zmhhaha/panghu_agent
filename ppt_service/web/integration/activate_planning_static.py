"""Scoped static-only deployment; never restarts or reconfigures a service."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path('/root/ppt-service-webdev-20261021/source/ppt_service/web')
LIVE = ROOT / 'dist'
STAGE = ROOT / 'incoming-C66NMsHg'
BACKUP = ROOT / 'versions' / 'before-planning-csrf-C66NMsHg-20261022'
EXPECTED_IMAGE = 'sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def inspect_index(directory):
    index = directory / 'index.html'
    assert directory.resolve() == directory and not directory.is_symlink()
    html = index.read_text(encoding='utf-8')
    assets = re.findall(r'(?:src|href)="(/assets/[^"<>]+)"', html)
    assert assets and all('..' not in item for item in assets)
    for item in assets:
        assert (directory / item.lstrip('/')).is_file()
    return {'directory':str(directory),'index_sha256':digest(index),'assets':[{'url':item,'sha256':digest(directory/item.lstrip('/'))} for item in assets]}

metadata = subprocess.run(['docker','inspect','--format','{{.State.Status}}|{{index .Config.Labels "app"}}|{{.Image}}|{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}','ppt-service-webdev-api-20261021'],check=True,capture_output=True,text=True).stdout.strip()
state,label,image,ip = metadata.split('|')
assert state == 'running' and label == 'ppt-service-webdev' and image == EXPECTED_IMAGE
print(json.dumps({'container_verified':True,'state':state,'app_label':label,'image':image,'ip':ip,'before':inspect_index(LIVE)},ensure_ascii=False),flush=True)

if len(sys.argv)>1 and sys.argv[1]=='activate':
    staged = inspect_index(STAGE)
    assert {a['url'] for a in staged['assets']} == {'/assets/index-C66NMsHg.js','/assets/index-C0RgSHL_.css'}
    assert ROOT.resolve() == ROOT and BACKUP.parent.resolve() == ROOT/'versions'
    assert not BACKUP.exists(), 'Do not overwrite an earlier backup.'
    shutil.copytree(LIVE,BACKUP)
    assert inspect_index(BACKUP)['index_sha256'] == inspect_index(LIVE)['index_sha256']
    # Retain old hashed assets for existing tabs. New hashes are installed before index.
    for source in (STAGE/'assets').iterdir():
        assert source.is_file() and not source.is_symlink()
        target = LIVE/'assets'/source.name
        if target.exists():
            assert digest(target)==digest(source), 'A hashed asset must not change in place.'
        else:
            temporary = LIVE/'assets'/(source.name+'.incoming')
            shutil.copy2(source,temporary)
            temporary.replace(target)
    temporary_index = LIVE/'index.html.incoming-C66NMsHg'
    shutil.copy2(STAGE/'index.html',temporary_index)
    temporary_index.replace(LIVE/'index.html')
    print(json.dumps({'backup':inspect_index(BACKUP),'after':inspect_index(LIVE),'old_hashed_assets_retained':True,'service_restarted':False},ensure_ascii=False),flush=True)
