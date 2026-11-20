"""A bounded formal-module library probe, never a user film or Agent artwork."""
from copy import deepcopy
import hashlib
from pathlib import Path

from agent_service.contracts import digest
from video_workbench.hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle
from video_workbench.hyperframes_film_project import (
    FILM_CLOCK_SCHEMA, LIFECYCLE, MODULE_SCHEMA, BOUNDARY_SCHEMA, compile_film_project, module_identity,
)
from video_workbench.ink_landscape_library import read_assets


def build_probe_bundle(runtime_fingerprint, *, source_root, duration_seconds=1):
    source_root = Path(source_root)
    if (type(duration_seconds) not in {int, float} or not 0 < duration_seconds <= 300
            or round(duration_seconds * 24) != duration_seconds * 24):
        raise ValueError('Explicit probe duration must be 1..7200 whole frames')
    total, cursor, rows = round(duration_seconds * 24), 0, []
    while cursor < total:
        end = min(total, cursor + 1440)
        rows.append(dict(scene_id='ink-probe-' + str(len(rows)), title='Pinned drawing library technical probe', narration='',
        visual_intent='Verify local library import, actual drawing and exact replay in the formal module realm.',
        audio_version_id=None, audio_sha256=None, audio_seconds=0,
        start_seconds=cursor / 24, end_seconds=end / 24, leading_silence_seconds=0,
        trailing_silence_seconds=(end - cursor) / 24, clock_source='silent'))
        cursor = end
    film = dict(schema=FILM_CLOCK_SCHEMA, project_id='formal-ink-technical-probe', joint_plan_version_id='technical',
        content_binding_sha256='a' * 64, title='Formal ink dependency technical probe', script='',
        understanding_goal='Dependency custody only; no artistic acceptance.', style='', scenes=rows, total_seconds=duration_seconds)
    checksum = digest(film)
    source = '''import * as ink from '/project/assets/ink-landscape.mjs';
let root,canvas,ctx,doc;
export async function mount(value,context){
 root=value;canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;
 root.append(canvas);ctx=canvas.getContext('2d');
 doc=ink.createStrokeGroupDocument([{id:'probe',strokes:[{id:'contact',
  points:[{x:30,y:90,pressure:.4},{x:150,y:60,pressure:1},{x:290,y:100,pressure:.3}],
  options:{width:12,ink:'#242622',opacity:1,dryness:.4}}]}],
  {width:320,height:180,seed:1,background:'#f5f2e9'});
}
export async function renderAt(context){ink.renderCanvas(ctx,doc.result,{paperTexture:0,time:context.time.numerator/context.time.denominator});}
export async function dispose(){root.replaceChildren();doc=null;ctx=null;canvas=null;root=null;}
'''
    assets = {'assets/' + name: raw for name, raw in read_assets().items()}
    modules, boundaries = [], []
    for index, row in enumerate(rows):
        namespace = 'ink_probe_' + str(index);entry = 'modules/' + namespace + '/entry.mjs'
        module = dict(schema=MODULE_SCHEMA, module_id='ink-probe-' + str(index), namespace=namespace, role='scene',
            scene_id=row['scene_id'], film_sha256=checksum, engine='canvas', entry=entry,
            files={entry: source}, files_sha256={entry: hashlib.sha256(source.encode()).hexdigest()},
            lifecycle=deepcopy(LIFECYCLE), assets=sorted(assets),
            states=dict(entry={'contact': {'x': 30}}, exit={'contact': {'x': 30}}))
        if index:
            previous = modules[-1]
            boundaries.append(dict(schema=BOUNDARY_SCHEMA, from_scene_id=previous['scene_id'], to_scene_id=row['scene_id'],
                from_module_sha256=module_identity(previous), to_module_sha256=module_identity(module),
                at_frame=round(row['start_seconds'] * 24), objects=[dict(object_id='contact',
                    outgoing_state_sha256=digest(previous['states']['exit']['contact']),
                    incoming_state_sha256=digest(module['states']['entry']['contact']), mode='retain')], transition=None))
        modules.append(module)
    project = compile_film_project(film=film, film_sha256=checksum, modules=modules, recordings=[],
        boundaries=boundaries, assets=assets, runtime_fingerprint=runtime_fingerprint)
    hosts = {name: (source_root / 'video_workbench/motion_renderer' / name).read_bytes() for name in HOST_NAMES}
    bundle = compile_film_browser_bundle(project,
        expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=320, height=180, host_files=hosts)
    return bundle, hashlib.sha256(bundle['browser-manifest.json']).hexdigest()
