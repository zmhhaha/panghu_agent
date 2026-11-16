"""Technical fixtures only; not reusable animation templates or Agent evidence.

Builds a silent three-engine project with an authored transition for a real-core
loader probe. Does not download dependencies, build an image or render media.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path

from agent_service.contracts import digest
from video_workbench.hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle, verify_film_browser_bundle
from video_workbench.hyperframes_film_project import (LIFECYCLE, MODULE_SCHEMA, FILM_CLOCK_SCHEMA,
    BOUNDARY_SCHEMA, compile_film_project)


def build_probe_bundle(runtime_fingerprint, *, source_root, case='positive'):
    if case not in {'positive','import','mount','render','dispose','prototype_without_context'}:
        raise ValueError('Unknown bounded technical probe case')
    source_root = Path(source_root)
    rows = [dict(scene_id='probe-' + str(index), title='Technical ' + engine, narration='',
        visual_intent='Validate new opaque realm and exact global frame, not expressive quality.',
        audio_version_id=None,audio_sha256=None,audio_seconds=0,start_seconds=index,end_seconds=index+1,
        leading_silence_seconds=0,trailing_silence_seconds=1,clock_source='silent')
        for index, engine in enumerate(('html-svg','canvas','three'))]
    film = dict(schema=FILM_CLOCK_SCHEMA,project_id='opaque-realm-technical-probe',joint_plan_version_id='probe-1',
        content_binding_sha256='a'*64,title='Three-engine opaque realm technical fixture',script='',
        understanding_goal='Prove fresh module realms, native contexts and actual HF synchronization.',
        style='',scenes=rows,total_seconds=3)
    film_sha=digest(film)
    prefix = '''
globalThis.__filmProbeImports=(globalThis.__filmProbeImports||0)+1;
if(globalThis.__filmProbeImports!==1||window.origin!=='null')throw Error('probe_realm_not_fresh_opaque');
let confined=false;try{void window.parent.document;}catch{confined=true;}
if(!confined)throw Error('probe_parent_not_confined');
let root;
'''
    svg=prefix+'''
export async function mount(value){root=value;root.innerHTML='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><circle id="subject" cy="160" r="42" fill="#2276dd"/></svg>';await Promise.resolve();}
export async function renderAt(context){root.querySelector('#subject').setAttribute('cx',80+context.global_frame*8);await Promise.resolve();}
export async function dispose(){root.replaceChildren();}
'''
    canvas=prefix+'''
let canvas,context;
export async function mount(value){root=value;canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;root.append(canvas);context=canvas.getContext('2d');await Promise.resolve();}
export async function renderAt(frame){context.clearRect(0,0,640,360);context.fillStyle='#21a071';context.fillRect(40+frame.global_frame*4,120,100,100);await Promise.resolve();}
export async function dispose(){root.replaceChildren();}
'''
    three="import * as THREE from '../../assets/three.module.min.js';\n"+prefix+'''
let renderer,scene,camera,mesh,geometry,material;
export async function mount(value){root=value;renderer=new THREE.WebGLRenderer({alpha:true,antialias:false});renderer.setSize(640,360);renderer.setPixelRatio(1);root.append(renderer.domElement);
scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(45,640/360,.1,100);camera.position.z=5;geometry=new THREE.BoxGeometry(1.6,1.6,1.6);material=new THREE.MeshNormalMaterial();mesh=new THREE.Mesh(geometry,material);scene.add(mesh);await Promise.resolve();}
export async function renderAt(frame){mesh.rotation.x=frame.global_frame*.025;mesh.rotation.y=frame.global_frame*.04;renderer.render(scene,camera);await Promise.resolve();}
export async function dispose(){geometry.dispose();material.dispose();renderer.dispose();renderer.forceContextLoss();root.replaceChildren();}
'''
    transition=prefix+'''
export async function mount(value){root=value;root.innerHTML='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><path id="relation" d="M80 270 L560 270" stroke="#e67324" stroke-width="8"/></svg>';}
export async function renderAt(frame){root.querySelector('#relation').setAttribute('stroke-dasharray',String(20+frame.local_frame*50)+' 600');await Promise.resolve();}
export async function dispose(){root.replaceChildren();}
'''
    if case=='import': svg="throw Error('fixture_import_failed');\n"+svg
    elif case in {'mount','render','dispose'}:
        function={'mount':'mount','render':'renderAt','dispose':'dispose'}[case]
        svg=re.sub(r'(export async function '+function+r'\([^)]*\)\{)',
            r"\1throw Error('fixture_"+case+r"_failed');",svg,count=1)
    elif case=='prototype_without_context':
        svg=svg.replace('export async function renderAt(context){', '''export async function renderAt(context){
Array.prototype.map=()=>[{kind:'2d',visible:true,width:640,height:360}];Array.prototype.some=()=>true;
Function.prototype.call=()=>{throw Error('poisoned_call');};
''')
    modules=[]
    for index,(engine,source) in enumerate(zip(('html-svg','canvas','three','html-svg'),(svg,canvas,three,transition))):
        if index==0 and case=='prototype_without_context': engine='canvas'
        namespace='fixture_'+str(index)
        entry='modules/'+namespace+'/entry.mjs'
        states=dict(entry={'subject':{'x':index if index<2 else 3 if index==2 else 2}},
            exit={'subject':{'x':index+1 if index<2 else 4 if index==2 else 3}})
        modules.append(dict(schema=MODULE_SCHEMA,module_id=namespace,namespace=namespace,
            role='scene' if index<3 else 'transition',scene_id=rows[index]['scene_id'] if index<3 else None,
            film_sha256=film_sha,engine=engine,entry=entry,files={entry:source},files_sha256={entry:hashlib.sha256(source.encode()).hexdigest()},
            lifecycle=deepcopy(LIFECYCLE),assets=['assets/three.module.min.js','assets/three.LICENSE.txt'] if engine=='three' else [],states=states))
    from video_workbench.hyperframes_film_project import module_identity
    boundaries=[]
    for index in range(2):
        outgoing,incoming=modules[index:index+2]
        boundaries.append(dict(schema=BOUNDARY_SCHEMA,from_scene_id=rows[index]['scene_id'],to_scene_id=rows[index+1]['scene_id'],
            from_module_sha256=module_identity(outgoing),to_module_sha256=module_identity(incoming),at_frame=(index+1)*24,
            objects=[dict(object_id='subject',outgoing_state_sha256=digest(outgoing['states']['exit']['subject']),
                incoming_state_sha256=digest(incoming['states']['entry']['subject']),mode='retain' if index==0 else 'transform')],
            transition=None if index==0 else dict(module_id=modules[3]['module_id'],module_sha256=module_identity(modules[3]),start_frame=46,end_frame=50)))
    assets={'assets/'+name:(source_root/'video_workbench/motion_renderer/vendor'/name).read_bytes()
        for name in ('three.module.min.js','three.LICENSE.txt')}
    project=compile_film_project(film=film,film_sha256=film_sha,modules=modules,recordings=[],boundaries=boundaries,
        assets=assets,runtime_fingerprint=runtime_fingerprint)
    bundle=compile_film_browser_bundle(project,expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=640,height=360,host_files={name:(source_root/'video_workbench/motion_renderer'/name).read_bytes() for name in HOST_NAMES})
    checksum=hashlib.sha256(bundle['browser-manifest.json']).hexdigest()
    verify_film_browser_bundle(bundle,expected_bundle_sha256=checksum)
    return bundle,checksum


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-json',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--source-root',default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument('--case',default='positive',choices=['positive','import','mount','render','dispose','prototype_without_context'])
    args=parser.parse_args()
    output=Path(args.output)
    if output.exists(): raise SystemExit('Output directory must be new')
    files,checksum=build_probe_bundle(json.loads(Path(args.runtime_json).read_text()),source_root=args.source_root,case=args.case)
    output.mkdir(parents=True)
    for path,raw in files.items():
        target=output/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    print(json.dumps(dict(bundle_root=str(output),bundle_sha256=checksum,scope='technical_fixture_not_agent_creative_evidence')))
