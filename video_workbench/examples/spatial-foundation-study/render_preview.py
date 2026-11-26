"""Replay the retained mountain through the real SceneSpace capture host.

No production task, model, deployment, or old source is modified. Run from the
repository root: python video_workbench/examples/spatial-foundation-study/render_preview.py
--out <new isolated directory> [--only stills|film|all].
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

EXAMPLE = Path(__file__).resolve().parent
REPO = EXAMPLE.parents[2]
sys.path.insert(0, str(REPO))

from agent_service.contracts import canonical_bytes
from agent_service.profiles.video_animation_create import source_sha256
from video_workbench.ink_landscape_library import descriptor
from video_workbench.scene_space_plan import compile_evaluator
from video_workbench.scene_space_renderer import (
    HOST, RUNTIME, SceneSpaceRenderer, render_capability,
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_bytes(canonical_bytes(value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--only', choices=['stills', 'film', 'all'], default='all')
    parser.add_argument('--chrome', default='C:/Program Files/Google/Chrome/Application/chrome.exe')
    parser.add_argument('--playwright-package', default='C:/Users/4770_2070super/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright-core/package.json')
    parser.add_argument('--hyperframes-dist', default='D:/codex-work/operations/scene-space-adaptation-20261122/render-npm/node_modules/hyperframes/dist')
    parser.add_argument('--publish-artifacts', action='store_true', help='Copy checked previews into this new example only.')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    plan = json.loads((EXAMPLE/'plan.json').read_text(encoding='utf-8'))
    base = json.loads((EXAMPLE/'baseline-source.json').read_text(encoding='utf-8'))
    assert source_sha256(base) == base['source_sha256'] == '1b6693b620f576dacf2b328367999a00458b8440f3f86ff825e996b153734be0'
    source = dict(engine='canvas', strategy=dict(
        description='The retained version-35 shared mountain, stone bank, pines, mist, sun and birds; deterministic shared lighting, material and atmosphere guides control the existing brush contacts. A bounded ten-second camera push reuses each object cache.',
        tradeoffs='Fixed authored orthographic meshes, painterly light assumptions and existing ink strokes; no calibrated lighting, physically transported shadows, ring camera, DOF or motion blur. User art acceptance and model visual review are pending.'),
        files={name:(EXAMPLE/name).read_text(encoding='utf-8') for name in ('scene.html','scene.js')})
    source['source_sha256'] = source_sha256(source)
    baseline_plan = json.loads((EXAMPLE/'baseline-plan.json').read_text(encoding='utf-8'))
    assert plan['worlds'] == baseline_plan['worlds'], 'retained world layout changed'
    def section(text, start, end):
        return text[text.index(start):text.index(end)]
    invariants = {}
    for key, start, end in [
        ('geometry_and_field','const MASS_CONFIG=','// The mesh keeps visibility;'),
        ('coverage','function meshCoverage','function nearBank')]:
        old = section(base['files']['scene.js'], start, end)
        new = section(source['files']['scene.js'], start, end)
        assert old == new, 'retained '+key+' changed'
        invariants[key+'_sha256'] = hashlib.sha256(new.encode('utf-8')).hexdigest()
    invariants['worlds_sha256'] = hashlib.sha256(canonical_bytes(plan['worlds'])).hexdigest()
    invariants['geometry_field_coverage_and_world_unchanged'] = True
    save(out/'source.json', source)
    save(out/'plan.json', plan)
    # This is a local native host replay. The digest is the historical renderer
    # admission identity, not a claim that a container of new code was deployed.
    runtime = SimpleNamespace(
        image='arm-cluster-master:5000/video-motion-renderer:latest@sha256:45504cc19d0ce1a0dd2234a47ac953afcb957bb1b92bbeaf63356610f641f13b',
        assets_dir=None, enabled=True, timeout=3600,
        capabilities=lambda:dict(allowed_engines=['canvas'], assets={}))
    renderer = SceneSpaceRenderer(out/'local-preparation', runtime_renderer=runtime)
    library = descriptor()
    constraints = dict(width=1280, height=720, drawing_libraries={'ink-landscape':library},
                       scene_space_capability=render_capability(admitted_engines=['canvas']))
    doc = dict(constraints=constraints, assets=library['assets'])
    save(out/'document.json', doc)
    cases = []
    switches = {
        'combined':dict(lighting=True, material=True, atmosphere=True, diagnostic='ink'),
        'without-lighting':dict(lighting=False, material=True, atmosphere=True, diagnostic='ink'),
        'without-material':dict(lighting=True, material=False, atmosphere=True, diagnostic='ink'),
        'without-atmosphere':dict(lighting=True, material=True, atmosphere=False, diagnostic='ink'),
        'coverage':dict(lighting=True, material=True, atmosphere=True, diagnostic='coverage'),
        'depth':dict(lighting=True, material=True, atmosphere=True, diagnostic='depth'),
        'normal':dict(lighting=True, material=True, atmosphere=True, diagnostic='normal'),
        'light-guide':dict(lighting=True, material=True, atmosphere=True, diagnostic='light'),
    }
    def add(name, src, movie=False):
        d = deepcopy(doc)
        if movie:
            d['constraints']['duration_seconds'] = 10
        binding = renderer.validate_request('spatial-foundation-'+name, src, d, scene_space=plan,
            frame_range=None if movie else dict(start_frame=120, end_frame=121))
        path = out/(name+'-binding.json')
        save(path, binding)
        cases.append(dict(name=name, binding=str(path), output=str(out/name),
            assets={name:str(REPO/'video_workbench/vendor/ink-landscape'/name) for name in library['assets']}))
        return binding
    if args.only in {'stills','all'}:
        add('baseline-35', base)
        marker = 'const STUDY_OPTIONS = Object.freeze({"lighting":true,"material":true,"atmosphere":true,"diagnostic":"ink"});'
        assert marker in source['files']['scene.js'], 'study switch marker missing'
        for name, options in switches.items():
            variant = deepcopy(source)
            variant['files']['scene.js'] = variant['files']['scene.js'].replace(
                marker, 'const STUDY_OPTIONS = Object.freeze('+json.dumps(options,separators=(',',':'))+');', 1)
            variant['strategy']['description'] += ' Same camera/time, switch condition '+name+'.'
            variant['source_sha256'] = source_sha256(variant)
            save(out/(name+'-source.json'), variant)
            add(name, variant)
    film_binding = None
    if args.only in {'film','all'}:
        film_binding = add('film', source, movie=True)
    evaluator = compile_evaluator(plan)
    # All layers retain parallax=1. The viewport stays inside the frozen paper;
    # no invented border pixels, outpainting, or unseen mesh backs are needed.
    view_bounds = []
    for frame in range(241):
        camera = evaluator.frame(frame)['camera']
        x,y,z = camera['x'],camera['y'],camera['zoom']
        box = [x-640/z,y-360/z,x+640/z,y+360/z]
        assert box[0]>=-1e-8 and box[1]>=-1e-8 and box[2]<=1280+1e-8 and box[3]<=720+1e-8
        view_bounds.append(box)
    save(out/'camera-coverage.json', dict(
        schema='video.spatial-study-camera-coverage.v1',frames_checked=241,
        full_plan_including_endpoint=True,source_world_bounds=[0,0,1280,720],
        opening=view_bounds[0],closing=view_bounds[-1],viewports_stay_inside_existing_paper=True,
        projection='fixed_orthographic_mesh_plus_2d_camera_crop',all_layer_parallax=1,
        additional_content_generated=False))
    config = dict(playwright_package=args.playwright_package, browser=args.chrome,
        hyperframes_dist=args.hyperframes_dist,host=str(HOST),scene_runtime=str(RUNTIME),
        engine_runtime=str(REPO/'video_workbench/motion_renderer/engine_runtime.mjs'),
        project_runtime=str(REPO/'video_workbench/motion_renderer/hyperframes_project.mjs'),
        ffmpeg=shutil.which('ffmpeg'),ffprobe=shutil.which('ffprobe'),cases=cases,
        output=str(out/'native-summary.json'))
    save(out/'native-config.json', config)
    started = time.monotonic()
    subprocess.run(['node', str(REPO/'tests/video_workbench/scene_space_browser_probe.mjs'),str(out/'native-config.json')],
                   check=True,timeout=3600)
    elapsed = time.monotonic()-started
    summary = json.loads((out/'native-summary.json').read_bytes())
    for row in summary['results']:
        assert all(row['checks'][k] for k in ('repeat_passed','reverse_passed','reload_passed'))
        metrics = row['scene_space_observation']
        assert metrics['objects_painted'] == 10
        assert metrics['geometry_helper_calls']['createTerrainMesh'] == 2
        assert metrics['geometry_helper_calls']['projectMesh'] == 2
        assert metrics['coverage_objects'] == 2
    evidence = dict(schema='video.spatial-foundation-study-evidence.v1',
        source_sha256=source['source_sha256'],baseline_source_sha256=base['source_sha256'],
        native_summary='native-summary.json',actual_native_host=True,container_run=False,
        production_tasks=0,production_model_calls=0,model_review='not_run',
        artistic_acceptance=False,effect_adopted=False,wikiskill_published=False,
        learning_iterations=1,render_limit_seconds=3600,measured_native_probe_seconds=elapsed,
        retained_invariants=invariants,
        per_case_native_observation=summary['results'],camera_coverage='camera-coverage.json',
        reused_scene_content=True,new_content_generated=False,continuous_human_review=False,
        audio_mode='silent',audio_observed=False)
    if film_binding:
        movie = out/'film/output.mp4'
        subprocess.run([config['ffmpeg'],'-v','error','-i',str(movie),'-f','null','-'],check=True,timeout=180)
        probe = json.loads(subprocess.check_output([config['ffprobe'],'-v','error','-count_frames',
            '-show_streams','-show_format','-of','json',str(movie)],timeout=120))
        video = next(row for row in probe['streams'] if row['codec_type']=='video')
        assert int(video['nb_read_frames']) == 240 and video['width']==1280 and video['height']==720
        assert video['r_frame_rate']=='24/1' and abs(float(probe['format']['duration'])-10)<.001
        evidence.update(full_video_decoded=True,frames=240,duration_seconds=10,fps=24,
                        video_sha256=sha(movie),video_bytes=movie.stat().st_size,media_probe=probe)
        if args.publish_artifacts:
            shutil.copyfile(movie,EXAMPLE/'preview.mp4')
        from PIL import Image, ImageDraw, ImageFont
        timeline = Image.new('RGB',(1280,768),'#eee9dc')
        text = ImageDraw.Draw(timeline)
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',19)
        for i, frame in enumerate([0,79,159,239]):
            x=i%2*640; y=i//2*384
            text.text((x+12,y+3),f'frame {frame} / {frame/24:.2f} s',font=font,fill='#253327')
            timeline.paste(Image.open(out/'film/frames'/f'{frame:06d}.jpg').convert('RGB').resize((640,360),Image.Resampling.LANCZOS),(x,y+24))
        timeline.save(out/'timeline.png')
        if args.publish_artifacts:
            shutil.copyfile(out/'timeline.png',EXAMPLE/'timeline.png')
    if args.only in {'stills','all'}:
        from PIL import Image, ImageDraw, ImageFont
        names=['baseline-35','combined','without-lighting','without-material','without-atmosphere']
        sheet=Image.new('RGB',(1280,1152),'#eee9dc')
        draw=ImageDraw.Draw(sheet)
        font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',19)
        labels={'baseline-35':'35版 / 同一镜头时刻','combined':'新通用引导 / 全开',
                'without-lighting':'关闭统一光向引导','without-material':'关闭表面材质引导',
                'without-atmosphere':'关闭距离空气引导'}
        for i,name in enumerate(names):
            x=i%2*640;y=i//2*384
            draw.text((x+12,y+4),labels[name],font=font,fill='#253327')
            picture=Image.open(out/name/'output.png').convert('RGB')
            sheet.paste(picture.resize((640,360),Image.Resampling.LANCZOS),(x,y+12+12))
        draw.text((652,796),'相同内容 / seed / 几何 / 相机 / 第120帧',font=font,fill='#253327')
        draw.text((652,832),'仅切换指定引导，均为独立冻结来源。',font=font,fill='#253327')
        draw.text((652,868),'技术验证与艺术认可分别记录。',font=font,fill='#253327')
        sheet.save(out/'comparison.png')
        diagnostic=Image.new('RGB',(1280,768),'#eee9dc')
        d=ImageDraw.Draw(diagnostic)
        for i,name in enumerate(['coverage','depth','normal','light-guide']):
            x=i%2*640;y=i//2*384
            d.text((x+12,y+3),name,font=font,fill='#253327')
            diagnostic.paste(Image.open(out/name/'output.png').convert('RGB').resize((640,360),Image.Resampling.LANCZOS),(x,y+24))
        diagnostic.save(out/'diagnostics.png')
        evidence['static_output_hashes']={name:sha(out/name/'output.png') for name in ['baseline-35',*switches]}
        if args.publish_artifacts:
            for name in ['comparison.png','diagnostics.png']:
                shutil.copyfile(out/name,EXAMPLE/name)
            shutil.copyfile(out/'combined/output.png',EXAMPLE/'preview.png')
    save(out/'evidence.json',evidence)
    if args.publish_artifacts:
        save(EXAMPLE/'evidence.json',evidence)
    print(json.dumps(dict(output=str(out),cases=len(cases),seconds=elapsed,model_review='not_run')))


if __name__=='__main__':
    main()
