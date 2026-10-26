"""Freeze public accepted stage-2 artifact as a test-only projection fixture.
No API/model requests. Writes only inside web/src/test/fixtures.
"""
import hashlib
import json
from pathlib import Path
web = Path(__file__).resolve().parents[1]
source = web.parent / '.runtime/recovered-options/result/manifest.json'
manifest = json.loads(source.read_text(encoding='utf-8'))
plan = manifest['plan']
options = plan['options']
choice = options['design_directions']
assert [c['id'] for c in choice['candidates']] == ['evidence-handbook','acceptance-console','guided-walkthrough']
production = {key:value for key,value in options.items() if key != 'design_directions'}
fixture = {'outline':plan['page_plan'], 'design':{
 'directions':[{**candidate,'page_plan':plan['page_plan'],'production_parameters':production} for candidate in choice['candidates']],
 'page_plan':plan['page_plan'], 'production_parameters':production,
 'recommendation':{'direction_id':choice['candidates'][choice['selected']]['id'],'source_index':choice['selected'],'selection_is':choice['selection_is'],'reason':choice['reason']},
 'source_projection':{'schema_version':1,'shape':'official_nested_stage2','source_manifest_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_input_snapshot_hash':manifest['input_snapshot_sha256']}
}}
target = web / 'src/test/fixtures/official-stage2.json'
target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Frozen actual public stage-2 projection: 3 original IDs / 6 shared full pages; test fixture only.')
