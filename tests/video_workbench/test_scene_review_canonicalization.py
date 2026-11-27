"""Same-clock capture selection preserves independent clocks and original bytes."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from agent_service.contracts import ServiceError, digest
from video_workbench.scene_review_canonicalization import canonicalize_clock_observations
from video_workbench.scene_space_plan import automatic_plan, default_budget, freeze_plan


@pytest.fixture
def captures():
    world=json.loads((Path(__file__).resolve().parents[1]/'fixtures/scene_space/world.json').read_text())
    plan=automatic_plan([world],id='same-clock-test',viewport=dict(width=1280,height=720),
        duration_frames=72,budget=default_budget(),
        intents=[dict(world_id=world['id'],weight=1,view='push',focus=dict(x=700,y=400),zoom=1,end_zoom=1.1)])
    out=io.BytesIO();Image.new('RGB',(1280,720),(240,235,220)).save(out,format='PNG')
    raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest();source='a'*64
    frames=[];files={};provenance=[]
    for identity,absolute,kind,relative in [('preview-0',0,'static_preview',0),
            ('preview-71',71,'static_preview',0),('clip-0-0',0,'clip_keyframe',0),
            ('clip-0-47',47,'clip_keyframe',47)]:
        name=identity+'.png';frames.append(dict(frame_id=identity,file_name=name,sha256=sha,purpose='output'))
        files[name]=raw
        capture=dict(schema='video.scene-space-frame-capture.v1',absolute_frame=absolute,
            relative_frame=relative,format='png',quality=None,sha256=sha,bytes=len(raw))
        provenance.append(dict(frame_id=identity,absolute_frame=absolute,kind=kind,render_id='render-'+kind,
            source_sha256=source,plan_sha256=digest(plan),capture=capture,retained_sha256=sha,
            model_input_sha256=sha,model_input_bytes=len(raw),conversion='identity_original_png'))
    caps=dict(admitted=True,engines=['canvas'],runtime_sha256='b'*64,host_sha256='c'*64)
    return [freeze_plan(plan),source,caps,frames,files,provenance]


def test_one_actual_observation_per_identical_clock_retains_all_capture_provenance(captures):
    before=deepcopy(captures)
    frames,files,evidence=canonicalize_clock_observations(*captures)
    assert [item['frame_id'] for item in frames]==['preview-0','preview-71','clip-0-47']
    assert evidence['capture_to_supplied_frame']=={'preview-0':'preview-0','preview-71':'preview-71',
        'clip-0-0':'preview-0','clip-0-47':'clip-0-47'}
    assert evidence['original_frames']==captures[3] and evidence['original_provenance']==captures[5]
    assert evidence['supplied_frame_ids']==[item['frame_id'] for item in frames]
    assert len(evidence['identities'])==4
    assert captures==before
    assert len(files)==3 and all(raw is captures[4][name] for name,raw in files.items())
    assert evidence['sha256']==digest({k:v for k,v in evidence.items() if k!='sha256'})


def test_identical_bytes_at_47_and_71_remain_independent_observations(captures):
    frames,_,evidence=canonicalize_clock_observations(*captures)
    assert len({frame['sha256'] for frame in frames})==1
    assert evidence['identities']['preview-71']['sha256']!=evidence['identities']['clip-0-47']['sha256']
    assert {item['absolute_frame'] for item in evidence['identities'].values()}=={0,47,71}


def test_static_representative_is_selected_even_if_clip_is_first(captures):
    captures[3].reverse();captures[5].reverse()
    frames,_,evidence=canonicalize_clock_observations(*captures)
    assert len(frames)==3 and evidence['capture_to_supplied_frame']['clip-0-0']=='preview-0'


def test_distinct_same_clock_png_remains_independently_supplied(captures):
    out=io.BytesIO();Image.new('RGB',(1280,720),(241,235,220)).save(out,format='PNG')
    raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest()
    frame=captures[3][2];item=captures[5][2]
    captures[4][frame['file_name']]=raw;frame['sha256']=sha
    item.update(retained_sha256=sha,model_input_sha256=sha,model_input_bytes=len(raw))
    item['capture'].update(sha256=sha,bytes=len(raw))
    frames,files,evidence=canonicalize_clock_observations(*captures)
    assert len(frames)==len(files)==4
    assert evidence['capture_to_supplied_frame']['clip-0-0']=='clip-0-0'


@pytest.mark.parametrize('change',[
    lambda x:x[5][2].update(source_sha256='f'*64),
    lambda x:x[5][2].update(plan_sha256='f'*64),
    lambda x:x[5][2].update(absolute_frame=1),
    lambda x:x[5][2]['capture'].update(absolute_frame=1),
    lambda x:x[5][2].update(model_input_sha256='f'*64),
    lambda x:x[5][2].update(conversion='reencoded_png'),
    lambda x:x[5][2].update(world_id='different-world'),
    lambda x:x[5][2].update(camera_sha256='f'*64),
    lambda x:x[5][2].update(viewport_sha256='f'*64),
    lambda x:x[5][2].update(runtime_identity_sha256='f'*64),
    lambda x:x[2].pop('host_sha256'),
    lambda x:x[2].update(runtime_sha256=None),
    lambda x:x[2].update(admitted=False),
    lambda x:x[4].update({'clip-0-0.png':b'\x89PNG\r\n\x1a\nchanged'}),
    lambda x:x[0]['plan']['viewport'].update(width=1279),
    lambda x:x[0].update(sha256='f'*64),
    lambda x:x[5].pop(),
])
def test_missing_changed_or_ambiguous_identity_fails_closed(captures,change):
    change(captures)
    with pytest.raises(ServiceError):canonicalize_clock_observations(*captures)


def test_complete_capability_is_part_of_explicit_logical_identity(captures):
    _,_,before=canonicalize_clock_observations(*captures)
    captures[2]['runtime_sha256']='d'*64
    _,_,after=canonicalize_clock_observations(*captures)
    assert before['identities']['preview-0']['sha256']!=after['identities']['preview-0']['sha256']
    assert before['identities']['preview-0']['runtime_identity_sha256']!=after['identities']['preview-0']['runtime_identity_sha256']
