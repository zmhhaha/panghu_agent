import hashlib
import json
from pathlib import Path
import re
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


def test_runtime_contract_example_and_rejected_transfer_fields_match_pinned_api():
    # Execute the supplied schema example against the actual fixed module. This
    # catches documentation drift without asserting that it is an artwork.
    example, = re.findall(r'```javascript\n(.*?)\n```', ink.runtime_guidance(), re.S)
    code = '''
const ink = await import(process.argv[1]);
''' + example + '''
const recipe = JSON.parse(JSON.stringify(sampleDocument.recipe));
if (JSON.stringify(ink.compileStrokeGroupRecipe(recipe)) !== JSON.stringify(sampleDocument.result))
  throw Error('Example recipe did not rebuild');
const points = [{x:0,y:0},{x:1,y:1}];
const options = {width:2,height:2};
function rejects(action, message) {
  try { action(); } catch (error) {
    if (error.message.includes(message)) return;
    throw error;
  }
  throw Error('Invalid API call unexpectedly accepted: ' + message);
}
rejects(() => ink.createStrokeGroupDocument([{id:'g',ink:'#242622',
  strokes:[{id:'s',points}]}],options), 'Unsupported stroke group field: ink');
rejects(() => ink.createStrokeGroupDocument([{id:'g',
  strokes:[{id:'s',points,path:points}]}],options), 'Unsupported group stroke field: path');
rejects(() => ink.createStrokeGroupDocument([{id:'g',strokes:[{id:'s',points}]}],
  {...options,background:'transparent'}), 'Group colours must be #RRGGBB');
rejects(() => ink.renderCanvas(null,sampleDocument.result,{paperTexture:false}),
  'paperTexture must be finite');
const strokes = Array.from({length:512},(_,i)=>({id:String(i),points}));
const bounded = ink.createStrokeGroupDocument([{id:'g',strokes}],options);
rejects(() => ink.createStrokeGroupDocument([{id:'g',strokes:[...strokes,{id:'overflow',points}]}],
  options), 'at most 512 strokes');
console.log(JSON.stringify({exampleMarks:sampleDocument.result.marks.length,
  boundedMarks:bounded.result.marks.length}));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', code,
        (ink.ROOT / 'ink-landscape.mjs').as_uri()],
        capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(result.stdout) == {'exampleMarks': 1, 'boundedMarks': 512}


@pytest.mark.parametrize('name', list(ink.ASSETS) + ['manifest.json', 'skill/SKILL.md'])
def test_modified_vendored_bytes_are_rejected(tmp_path, name):
    root = tmp_path / 'vendor'
    shutil.copytree(ink.ROOT, root)
    path = root / name
    path.write_bytes(path.read_bytes() + b'\nchanged')
    with pytest.raises(ServiceError, match='ink_landscape_library_bytes_invalid'):
        ink.read_skill_files(root) if name.startswith('skill/') else ink.read_assets(root)


@pytest.mark.parametrize('name', ['docs/learning/brush-calligraphy.md',
    'docs/learning/jieziyuan.md', 'docs/bark-document.md'])
def test_modified_publication_text_is_rejected(tmp_path, name):
    root = tmp_path / 'vendor'
    shutil.copytree(ink.ROOT, root)
    path = root / 'publication' / name
    path.write_bytes(path.read_bytes() + b'\nchanged')
    with pytest.raises(ServiceError, match='ink_landscape_library_bytes_invalid'):
        ink.read_publication_files(root)


def test_publication_has_all_actual_readings_and_new_revision_api_documents():
    from video_workbench.vendor_ink_landscape import PUBLICATION_DOCUMENTS
    files = ink.read_publication_files()
    assert files == {**ink.read_skill_files(), **{
        path: (ink.ROOT / 'publication' / path).read_text(encoding='utf-8')
        for path in PUBLICATION_DOCUMENTS}}
    books = ['brush-calligraphy', 'ink-water', 'texture-vegetation', 'linework']
    texts = [files['docs/learning/' + name + '.md'] for name in books]
    reading_ids = [identity for text in texts for identity in re.findall(r'<a id="([^"]+)"></a>', text)]
    assert len(reading_ids) == len(set(reading_ids)) == 100
    assert sum(text.count('阅读状态：') for text in texts) == 100
    code = '''
const ink = await import(process.argv[1]);
console.log(JSON.stringify({ids:ink.TECHNIQUE_READINGS.map(row=>row.id),
 sources:ink.TECHNIQUE_READING_SOURCES.length,
 newApis:['createBarkDocument','createSurfaceCunDocument','createPointTextureDocument']
   .map(name=>typeof ink[name])}));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', code,
        (ink.ROOT / 'ink-landscape.mjs').as_uri()],
        capture_output=True, text=True, check=True, timeout=30)
    actual = json.loads(result.stdout)
    assert set(reading_ids) == set(actual['ids']) and actual['sources'] == 80
    assert actual['newApis'] == ['function'] * 3
    assert {'docs/bark-document.md', 'docs/line-texture-revision.md',
        'docs/point-texture-revision.md', 'docs/existing-technique-revision.md'} <= set(files)
    assert '不是全书通读' in files['docs/learning/jieziyuan.md']
    assert ink.manifest()['publication_scope']['source_revision'] == ink.SOURCE_REVISION


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
