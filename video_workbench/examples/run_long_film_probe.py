"""Actual >60s browser/core/PNG-pipe export; this fixture is not the user film."""
from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import time

from agent_service.contracts import digest
from video_workbench.hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle
from video_workbench.hyperframes_film_export import export_film_browser_bundle
from video_workbench.hyperframes_film_project import (
    FILM_CLOCK_SCHEMA, LIFECYCLE, MODULE_SCHEMA, BOUNDARY_SCHEMA, compile_film_project, module_identity,
)
from video_workbench.examples.build_ink_film_probe import build_probe_bundle


def build_long_probe_bundle(runtime_fingerprint, *, source_root, duration_seconds=61):
    if type(duration_seconds) is not int or not 61 <= duration_seconds <= 300:
        raise ValueError('Long fixture must be between 61 and 300 seconds')
    source_root = Path(source_root)
    base, _ = build_probe_bundle(runtime_fingerprint, source_root=source_root)
    original = json.loads(base['project/manifest.json'])
    first = original['modules'][0]
    source = base['project/' + first['entry']].decode()
    source = source.replace(
        'ink.renderCanvas(ctx,doc.result,{paperTexture:0,time:context.time.numerator/context.time.denominator});',
        "ink.renderCanvas(ctx,doc.result,{paperTexture:0,time:context.time.numerator/context.time.denominator});"
        "ctx.fillStyle='#b04732';ctx.fillRect(12+(context.global_frame%280),140,5,5);")
    if "context.global_frame%280" not in source:
        raise ValueError('Expected exact bounded ink technical module source')
    count = (duration_seconds + 29) // 30
    base_duration, remainder = divmod(duration_seconds, count)
    durations = [base_duration + (1 if i < remainder else 0) for i in range(count)]
    rows, cursor = [], 0
    for index, duration in enumerate(durations):
        row = deepcopy(original['film']['scenes'][0])
        row.update(scene_id='long-ink-' + str(index), title='Long ink clock probe ' + str(index),
            start_seconds=cursor, end_seconds=cursor + duration, trailing_silence_seconds=duration)
        cursor += duration
        rows.append(row)
    film = deepcopy(original['film'])
    film.update(schema=FILM_CLOCK_SCHEMA, project_id='long-ink-technical-probe', scenes=rows,
        title='Bounded long-film core and library probe', total_seconds=duration_seconds)
    checksum = digest(film)
    modules=[]
    for index, row in enumerate(rows):
        namespace='long_ink_' + str(index)
        entry='modules/' + namespace + '/entry.mjs'
        modules.append(dict(schema=MODULE_SCHEMA,module_id=namespace,namespace=namespace,role='scene',
            scene_id=row['scene_id'],film_sha256=checksum,engine='canvas',entry=entry,
            files={entry:source},files_sha256={entry:hashlib.sha256(source.encode()).hexdigest()},
            lifecycle=deepcopy(LIFECYCLE),assets=list(first['assets']),
            states=dict(entry={'contact':{'x':30}},exit={'contact':{'x':30}})))
    boundaries=[dict(schema=BOUNDARY_SCHEMA,from_scene_id=rows[i]['scene_id'],to_scene_id=rows[i+1]['scene_id'],
        from_module_sha256=module_identity(modules[i]),to_module_sha256=module_identity(modules[i+1]),
        at_frame=round(rows[i]['end_seconds']*24),transition=None,
        objects=[dict(object_id='contact',mode='retain',outgoing_state_sha256=digest({'x':30}),
            incoming_state_sha256=digest({'x':30}))]) for i in range(count-1)]
    assets={name[len('project/'):]:raw for name,raw in base.items() if name.startswith('project/assets/')}
    project=compile_film_project(film=film,film_sha256=checksum,modules=modules,recordings=[],
        boundaries=boundaries,assets=assets,runtime_fingerprint=runtime_fingerprint)
    hosts={name:(source_root/'video_workbench/motion_renderer'/name).read_bytes() for name in HOST_NAMES}
    bundle=compile_film_browser_bundle(project,expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=320,height=180,host_files=hosts)
    return bundle,hashlib.sha256(bundle['browser-manifest.json']).hexdigest()


def _size(root):
    total=0
    for directory,_,names in os.walk(root,followlinks=False):
        for name in names:
            try:
                info=(Path(directory)/name).lstat()
                if stat.S_ISREG(info.st_mode):total+=info.st_size
            except FileNotFoundError:
                pass
    return total


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=61)
    parser.add_argument('--runtime-json',type=Path)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--playwright-module',default='/opt/renderer/node_modules/playwright-core/index.mjs')
    parser.add_argument('--chromium',default='/opt/browser/headless_shell')
    parser.add_argument('--hyperframes-dist',default='/opt/renderer/node_modules/hyperframes/dist')
    args=parser.parse_args()
    if not args.image.rsplit('@',1)[-1].startswith('sha256:') or ':latest@' not in args.image:
        raise ValueError('Exact latest image digest is required')
    if args.output.exists() or not args.output.is_absolute() or not args.output.parent.is_dir():
        raise ValueError('New output directory beneath an existing parent is required')
    args.output.mkdir(mode=0o700)
    scratch=args.output/'scratch';scratch.mkdir(mode=0o700)
    runtime=json.loads(args.runtime_json.read_bytes()) if args.runtime_json else json.loads(subprocess.check_output(
        ['node','/opt/renderer/runtime.mjs'],timeout=30))
    runtime['image_digest']=args.image.rsplit('@',1)[-1]
    bundle,checksum=build_long_probe_bundle(runtime,source_root=args.source_root,duration_seconds=args.seconds)
    done=threading.Event();peak=0
    def sample():
        nonlocal peak
        while not done.wait(.1):peak=max(peak,_size(scratch))
    sampler=threading.Thread(target=sample,daemon=True);sampler.start();started=time.monotonic()
    try:
        result=export_film_browser_bundle(bundle,expected_bundle_sha256=checksum,scratch_dir=scratch,
            playwright_module=args.playwright_module,chromium_path=args.chromium,
            hyperframes_dist=args.hyperframes_dist,timeout_seconds=3600)
    finally:
        done.set();sampler.join(timeout=5)
    elapsed=time.monotonic()-started
    (args.output/'probe.mp4').write_bytes(result.data)
    for name,value in [('export',result.receipt),('capture',result.capture),('audio',result.audio_mix)]:
        (args.output/(name+'-receipt.json')).write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    memory_peak=None
    for memory in [Path('/sys/fs/cgroup/memory.peak'),Path('/sys/fs/cgroup/memory/memory.max_usage_in_bytes')]:
        if memory.is_file():
            memory_peak=int(memory.read_text().strip());break
    summary=dict(status='technical_long_export_verified',image=args.image,duration_seconds=args.seconds,
        frames=result.receipt['clock']['frame_count'],elapsed_seconds=round(elapsed,3),
        observed_fps=round(args.seconds*24/elapsed,3),sampled_scratch_peak_bytes=peak,scratch_sample_seconds=.1,
        cgroup_memory_peak_bytes=memory_peak,frame_files=result.capture['storage']['frame_files'],
        encoder_invocations=result.capture['storage']['encoder_invocations'],
        continuous_main_realm_instances=len({realm['instance_id'] for row in result.capture['probe']['observations'][:-3]
            for realm in row['realms']}),fresh_probe_instances=len({realm['instance_id'] for row in result.capture['probe']['observations'][-3:]
            for realm in row['realms']}),png_frame_bytes=result.capture['frame_bytes'],
        full_decode_passed=result.receipt['media']['full_decode_passed'],media_sha256=hashlib.sha256(result.data).hexdigest(),
        contains_narration=result.audio_mix['contains_narration'],scratch_clean=not any(scratch.iterdir()),
        scope='Real core/native Canvas/library/time/streaming only; not the user film, visual approval or production admission')
    (args.output/'summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,sort_keys=True))


if __name__=='__main__':main()
