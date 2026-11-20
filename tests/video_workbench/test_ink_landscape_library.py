import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_service.contracts import ServiceError
from video_workbench import ink_landscape_library as ink


def test_pinned_bundle_executes_real_editable_api():
    module = (ink.ROOT / 'ink-landscape.mjs').as_uri()
    code = '''
const ink = await import(process.argv[1]);
const rock = ink.createRockMacro({id:'proof-rock',seed:17,size:120,textureCount:9});
const tree = ink.createTreeMacro({id:'proof-tree',seed:18,size:80,depth:2});
const doc = ink.createStrokeGroupDocument([...rock.groups,...tree.groups],
 {width:640,height:360,seed:19});
const recipe = JSON.parse(JSON.stringify(doc.recipe));
const replay = ink.compileStrokeGroupRecipe(recipe);
if (JSON.stringify(replay)!==JSON.stringify(doc.result)) throw Error('rebuild');
console.log(JSON.stringify({marks:doc.result.marks.length,groups:recipe.groups.length,
 renderCanvas:typeof ink.renderCanvas,inkProcess:typeof ink.createInkProcessDocument}));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', code, module],
                            capture_output=True, text=True, check=True, timeout=30)
    value = json.loads(result.stdout)
    assert value['marks'] > 10 and value['groups'] > 1
    assert value['renderCanvas'] == value['inkProcess'] == 'function'


@pytest.mark.parametrize('name', list(ink.ASSETS) + ['manifest.json', 'skill/SKILL.md'])
def test_modified_vendored_bytes_are_rejected(tmp_path, name):
    root = tmp_path / 'vendor'
    shutil.copytree(ink.ROOT, root)
    path = root / name
    path.write_bytes(path.read_bytes() + b'\nchanged')
    with pytest.raises(ServiceError, match='ink_landscape_library_bytes_invalid'):
        ink.read_skill_files(root) if name.startswith('skill/') else ink.read_assets(root)


def test_skill_files_and_dependency_descriptor_bind_actual_assets():
    from agent_service.animation_skills_contract import build_skill_package
    assets, files = ink.read_assets(), ink.read_skill_files()
    descriptor = ink.descriptor()
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in assets.items()} == descriptor['assets']
    package = build_skill_package(skill_id='ink-landscape', version=1, kind='foundation',
        files=files, runtime_requirements=dict(engines=['canvas'],
            drawing_libraries={'ink-landscape': descriptor}))
    assert package['files']['SKILL.md'] == files['SKILL.md']
    assert len(files) == 5
    ink.validate_descriptor(descriptor, descriptor['assets'])
    changed = dict(descriptor, source_revision='0' * 40)
    with pytest.raises(ServiceError, match='ink_landscape_library_not_admitted'):
        ink.validate_descriptor(changed, descriptor['assets'])
