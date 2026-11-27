"""Retain the existing mountain while probing four assets in the real film realm.

This compiles a local technical bundle; it does not create an Agent task,
grant production admission, or change the preserved standalone study.
"""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path

from agent_service.contracts import canonical_bytes, digest
from video_workbench.hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle
from video_workbench.hyperframes_film_project import FILM_CLOCK_SCHEMA, LIFECYCLE, MODULE_SCHEMA, compile_film_project
from video_workbench.ink_landscape_library import read_assets as ink_assets
from video_workbench.spatial_three_library import read_assets as spatial_assets


def build_probe_bundle(runtime_fingerprint, *, source_root):
    root = Path(source_root)
    examples = root / 'video_workbench/examples'
    original = json.loads((examples / 'spatial-foundation-study/baseline-source.json').read_text(encoding='utf-8'))
    if original['source_sha256'] != '1b6693b620f576dacf2b328367999a00458b8440f3f86ff825e996b153734be0':
        raise ValueError('Exact retained mountain source is required')
    baseline = original['files']['scene.js']
    marker = "import * as ink from './assets/ink-landscape.mjs';"
    if baseline.count(marker) != 1:
        raise ValueError('Retained baseline import marker changed')
    baseline = baseline.replace(marker, "import * as ink from '/project/assets/ink-landscape.mjs';", 1)
    baseline += '\nexport {MASS_CONFIG,authoredHeight};\n'
    plan = json.loads((examples / 'spatial-foundation-study/plan.json').read_text(encoding='utf-8'))
    study = (examples / 'three-spatial-foundation-study/study.mjs').read_text(encoding='utf-8')
    begin = "const plan=await(await fetch('/plan.json')).json(),world="
    body = study[study.index(begin):study.index('function pixel(')]
    body = body.replace(begin, 'const plan=PLAN,world=', 1)
    if body.count('document.body.appendChild(renderer.domElement);') != 1:
        raise ValueError('Retained Three placement marker changed')
    body = body.replace('document.body.appendChild(renderer.domElement);', 'root.appendChild(renderer.domElement);', 1)
    source = ("import * as THREE from '/project/assets/three.module.min.js';\n"
        "import {createThreeAppearanceAdapter,threeAppearanceObservation} from '/project/assets/spatial_three_adapter.mjs';\n"
        "import * as surface from '/project/assets/scene_space_surface.mjs';\n"
        "import {MASS_CONFIG,authoredHeight} from './baseline.mjs';\n"
        'const PLAN=' + canonical_bytes(plan).decode() + ';\n'
        'let state=null;\nexport async function mount(root,context){\n' + body
        + '''state={renderFrame,dispose(){for(const adapter of adapters)adapter.dispose();
 scene.traverse(object=>{if(object.isMesh){object.geometry.dispose();object.material.dispose();}});
 for(const texture of textures)texture.dispose();renderer.dispose();renderer.forceContextLoss();root.replaceChildren();}};
}
export async function renderAt(context){state.renderFrame(context.global_frame);}
export async function dispose(){if(state){state.dispose();state=null;}}
''')
    namespace = 'three_spatial_probe';entry = f'modules/{namespace}/entry.mjs'
    files = {entry: source, f'modules/{namespace}/baseline.mjs': baseline}
    assets = {**spatial_assets(), **ink_assets()}
    vendor = root / 'video_workbench/motion_renderer/vendor'
    assets.update({name: (vendor / name).read_bytes() for name in ('three.module.min.js', 'three.LICENSE.txt')})
    assets = {'assets/' + name: raw for name, raw in assets.items()}
    row = dict(scene_id='same-mountain', title='Same mountain four-asset host probe', narration='',
        visual_intent='Replay the already generated shared mountain with the existing camera path.',
        audio_version_id=None, audio_sha256=None, audio_seconds=0, start_seconds=0, end_seconds=10,
        leading_silence_seconds=0, trailing_silence_seconds=10, clock_source='silent')
    film = dict(schema=FILM_CLOCK_SCHEMA, project_id='same-mountain-spatial-three-technical',
        joint_plan_version_id='technical-host-probe', content_binding_sha256=digest(plan),
        title='Same mountain four-asset film realm', script='',
        understanding_goal='Asset and host execution only; not a new artistic iteration.',
        style='', scenes=[row], total_seconds=10)
    checksum = digest(film)
    module = dict(schema=MODULE_SCHEMA, module_id='three-spatial-probe', namespace=namespace, role='scene',
        scene_id=row['scene_id'], film_sha256=checksum, engine='three', entry=entry, files=files,
        files_sha256={name: hashlib.sha256(raw.encode()).hexdigest() for name, raw in files.items()},
        lifecycle=deepcopy(LIFECYCLE), assets=sorted(assets),
        states=dict(entry={'shared-mountain': {'seed': 631907}}, exit={'shared-mountain': {'seed': 631907}}))
    project = compile_film_project(film=film, film_sha256=checksum, modules=[module], recordings=[],
        boundaries=[], assets=assets, runtime_fingerprint=runtime_fingerprint)
    hosts = {name: (root / 'video_workbench/motion_renderer' / name).read_bytes() for name in HOST_NAMES}
    bundle = compile_film_browser_bundle(project,
        expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=1280, height=720, host_files=hosts)
    provenance = dict(scope='local_same_content_formal_realm_probe_not_production_admission',
        baseline_source_sha256=original['source_sha256'], original_scene_js_sha256=hashlib.sha256(original['files']['scene.js'].encode()).hexdigest(),
        study_mjs_sha256=hashlib.sha256(study.encode()).hexdigest(), model_requests=0, production_posts=0,
        controlled_changes=['Original study drawing moved into mount/renderAt/dispose without its standalone policy installer.',
            'Only baseline drawing-library URL changes to the declared formal assets path.',
            'The trusted realm freezes the four-asset metadata policy before author import.'],
        runtime_fingerprint_scope='retained_packaging_input_not_a_current_local_container_or_browser_identity')
    return bundle, hashlib.sha256(bundle['browser-manifest.json']).hexdigest(), provenance


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--runtime-json', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    document = json.loads(args.runtime_json.read_text(encoding='utf-8'))
    runtime = deepcopy(document.get('runtime', document))
    if 'image_digest' not in runtime and type(document.get('image')) is str:
        runtime['image_digest'] = document['image'].rsplit('@', 1)[-1]
    files, checksum, provenance = build_probe_bundle(runtime, source_root=args.source_root)
    args.out.mkdir(parents=True, exist_ok=False)
    target = args.out / 'bundle';target.mkdir()
    for name, raw in files.items():
        path = target / name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(raw)
    provenance.update(bundle_sha256=checksum, runtime_input_file_sha256=hashlib.sha256(args.runtime_json.read_bytes()).hexdigest())
    (args.out / 'provenance.json').write_bytes(canonical_bytes(provenance))
    print(json.dumps({'bundle_sha256': checksum, 'bundle': str(target), 'model_requests': 0}))
