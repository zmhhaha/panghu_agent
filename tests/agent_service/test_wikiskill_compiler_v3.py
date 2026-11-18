"""V3 reduces repetitive transport, never evidence identity or coverage checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service import animation_wikiskill_contract as C
from agent_service import compilation_input_tools as tool
from agent_service.animation_wikiskill_compact import COMPACT_RESULT_SCHEMA, expand_compact_result
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_wikiskill_v3 as P


def stage(tmp_path, count=40):
    snapshot = C.make_snapshot('fixture', [dict(record_id=f'evidence-{i:03}', kind='method',
        content=dict(goal='Keep object identity', guidance='g' * 1700,
            source={'files': {'scene.js': 'original-program;' * 10000}},
            review={'scope': 'sampled_frames_only', 'audio_verified': False},
            feedback=[{'comment': 'Preserve this actual comment'}])) for i in range(count)], captured_at=1000.)
    document = C.build_input(snapshot)
    files = C.input_files(snapshot, document)
    for name, raw in files.items():
        path = tmp_path / 'inputs' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return snapshot, document, files


def run_reader(root, document, params):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable')
    source = '''
const m=await import(process.argv[1]);let t;m.default({registerTool(v){t=v;}});
const p=JSON.parse(process.argv[2]);p.input_sha256=process.argv[3];
try { const r=await t.execute('offline',p,new AbortController().signal); process.stdout.write(JSON.stringify(r)); }
catch(e){process.stdout.write(JSON.stringify({error:e.message}));}
'''
    result = subprocess.run([node, '--input-type=module', '-e', source,
        tool.COMPILATION_EVIDENCE_EXTENSION.resolve().as_uri(), json.dumps(params), digest(document)],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    reply = json.loads(result.stdout)
    if 'error' in reply:
        return reply
    assert len(reply['content'][0]['text'].encode()) <= 32768
    return json.loads(reply['content'][0]['text'])


def compact(document):
    return dict(schema=COMPACT_RESULT_SCHEMA,input_sha256=digest(document),
        snapshot_sha256=document['snapshot']['sha256'],evidence_scope=C.EVIDENCE_SCOPE,
        summary='Synthetic compiler fixture, not an actual learned effect.',
        packages=[dict(skill_id='causal-guide',version=1,kind='foundation',
            files={'SKILL.md':'---\nname: causal-guide\ndescription: Understand bounded causal methods.\n---\n'
                'Preserve object identity. These recorded sampled stills do not verify motion.'},
            runtime_requirements={'engines':['canvas']})],
        index=[dict(skill_id='causal-guide',parent_skill_id=None,source_record_ranges=[[0,1]])],
        exclusions=[dict(record_ranges=[[1,len(document['records'])]],
            reason='Surveyed structural history; original programs were not independently inspected.')],
        limitations=[C.COMPILATION_LIMITATION,'Structural surveys are partial source views.'])


def test_complete_survey_preserves_originals_and_exposes_exact_omissions(tmp_path):
    snapshot, document, files = stage(tmp_path)
    offset, records = 0, []
    while offset is not None:
        result = run_reader(tmp_path, document, dict(mode='survey',offset=offset,limit=128))
        assert result['snapshot'] == document['snapshot']
        records.extend(result['records'])
        offset = result['next_offset']
    assert [r['record_index'] for r in records] == list(range(40))
    assert [r['file_sha256'] for r in records] == [r['file_sha256'] for r in document['records']]
    assert all(set(r)=={'record_index','kind','file_sha256'} for r in records)
    part = run_reader(tmp_path,document,dict(mode='file',file_name='records/000000.json',offset_bytes=0,max_bytes=16384))
    assert files[part['file_name']].startswith(part['text'].encode())
    assert C.validate_input_files(document,files) == snapshot
    assert all((tmp_path/'inputs'/name).read_bytes()==raw for name,raw in files.items())


def test_survey_is_navigation_not_an_implicit_full_read_or_validation(tmp_path):
    _,document,files=stage(tmp_path,count=2)
    result=run_reader(tmp_path,document,dict(mode='survey',offset=0,limit=128))
    assert result['next_offset'] is None
    assert 'were not read by the model' in result['view_scope']
    row=result['records'][0]
    assert row['file_sha256']==hashlib.sha256(files['records/000000.json']).hexdigest()
    assert 'review' not in row and 'guidance' not in row and 'source' not in row
    assert len(json.dumps(row,ensure_ascii=False).encode())<180


@pytest.mark.parametrize('params',[
    dict(mode='survey',offset=-1,limit=1),dict(mode='survey',offset=0,limit=129),
    dict(mode='survey',offset=True,limit=2),dict(mode='survey',offset=0,limit=1,path='/etc/passwd')])
def test_survey_rejects_unbounded_or_arbitrary_paths(tmp_path,params):
    _,document,_=stage(tmp_path,count=2)
    assert run_reader(tmp_path,document,params)['error']=='compilation_input_invalid'


def test_compact_addresses_expand_to_exact_complete_existing_contract(tmp_path):
    _,document,_=stage(tmp_path,count=40)
    authored=compact(document);before=deepcopy(authored)
    expanded=expand_compact_result(authored,document)
    valid=C.validate_result(C.normalize_agent_result(expanded),digest(document),document)
    assert authored==before
    assert valid['index'][0]['source_record_ids']==[document['records'][0]['record_id']]
    assert [r['record_id'] for r in valid['exclusions']]==[r['record_id'] for r in document['records'][1:]]


@pytest.mark.parametrize('ranges',[[[0,0]],[[0,99]],[[True,2]],[[1,2],[0,1]],[[0,2],[1,3]]])
def test_invalid_compact_ranges_cannot_rewrite_or_skip_identity(tmp_path,ranges):
    _,document,_=stage(tmp_path,count=4)
    output=compact(document);output['index'][0]['source_record_ranges']=ranges
    with pytest.raises(ServiceError,match='ranges_invalid'):
        expand_compact_result(output,document)


def test_collector_normalizes_compact_output_only_after_exact_stopped_input_binding(tmp_path):
    _,document,files=stage(tmp_path,count=4)
    handler=implementation(P.PROFILE)
    assert handler.required_tools==('read_compilation_input','write')
    envelope=SimpleNamespace(attempt_id=str(uuid4()),profile=P.PROFILE,profile_revision='1',
        task={'input_sha256':digest(document)},checkpoint_inputs=[],checkpoint_ref=None,
        inputs=[InlineFile.from_bytes(name,raw) for name,raw in files.items()])
    proof=StopEvidence(execution_id=envelope.attempt_id,all_writers_stopped=True,resource_ids=('fixture-agent',))
    (tmp_path/'output.json').write_bytes(canonical_bytes(compact(document)))
    artifacts=handler.collect_output(tmp_path,envelope,proof)
    value=json.loads(artifacts[0].content())
    assert value['schema']==C.RESULT_SCHEMA and len(value['exclusions'])==3
    assert C.validate_result(value,digest(document),document)==value
    (tmp_path/'inputs'/'records/000001.json').write_bytes(b'{}')
    with pytest.raises(ServiceError,match='binding_mismatch'):
        handler.collect_output(tmp_path,envelope,proof)


def test_v3_reader_and_v2_reader_have_separate_pinned_execution_identities():
    assert tool.verified_compilation_input_extension(tool.COMPILATION_EVIDENCE_ENVIRONMENT_NAME)==tool.COMPILATION_EVIDENCE_EXTENSION
    assert tool.verified_compilation_input_extension()==tool.COMPILATION_INPUT_EXTENSION
    assert tool.COMPILATION_EVIDENCE_EXTENSION_SHA256 != tool.COMPILATION_INPUT_EXTENSION_SHA256
    assert P.verified_bundle() and implementation('video.animation-wikiskill-compile.v2').name != P.PROFILE
