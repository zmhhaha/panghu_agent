"""Real >60s single encoding, synthetic frame/probe transport (no browser proof)."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import hyperframes_film_export as E
from video_workbench import hyperframes_film_snapshot as S
from video_workbench.hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle
from video_workbench import hyperframes_film_project as H
from test_hyperframes_film_project import inputs, module
from test_hyperframes_film_export import png

ROOT = Path(__file__).resolve().parents[2]


def long_bundle(durations=(30, 31), *, visual=False):
    value = inputs()
    template = value['film']['scenes'][0]
    rows, cursor = [], 0
    for index, duration in enumerate(durations):
        row = deepcopy(template)
        row.update(scene_id='long-scene-' + str(index), title='Long scene ' + str(index),
            narration='', audio_version_id=None, audio_sha256=None, audio_seconds=0,
            clock_source='silent', start_seconds=cursor, end_seconds=cursor + duration,
            leading_silence_seconds=0, trailing_silence_seconds=duration)
        cursor += duration
        rows.append(row)
    value['film'].update(schema=H.FILM_CLOCK_SCHEMA, scenes=rows, total_seconds=cursor)
    value['film_sha256'] = digest(value['film'])
    value['recordings'] = []
    value['modules'] = [module('long_' + str(i), row['scene_id'], value['film_sha256'],
        engine='canvas', entry_state={'x': i}, exit_state={'x': i + 1}) for i, row in enumerate(rows)]
    if visual:
        for item in value['modules']:
            entry=item['entry']
            text='''let canvas,ctx,cache;
export async function mount(root,context){
 canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;root.append(canvas);ctx=canvas.getContext('2d');
 cache=document.createElement('canvas');cache.width=640;cache.height=360;const p=cache.getContext('2d');
 p.fillStyle='#e9e4d5';p.fillRect(0,0,640,360);p.fillStyle='#45594a';p.fillRect(24,230,580,130);
}
export async function renderAt(context){ctx.clearRect(0,0,640,360);ctx.drawImage(cache,0,0);
 ctx.fillStyle='#a54b32';ctx.fillRect(20+(context.global_frame%420),80,40,40);}
export async function dispose(){canvas?.remove();canvas=ctx=cache=null;}
'''
            item['files']={entry:text}
            item['files_sha256']={entry:hashlib.sha256(text.encode()).hexdigest()}
    value['boundaries'] = [dict(schema=H.BOUNDARY_SCHEMA,
        from_scene_id=rows[i]['scene_id'], to_scene_id=rows[i + 1]['scene_id'],
        from_module_sha256=H.module_identity(value['modules'][i]),
        to_module_sha256=H.module_identity(value['modules'][i + 1]), at_frame=round(rows[i]['end_seconds']*24),
        objects=[dict(object_id='subject', mode='retain',
            outgoing_state_sha256=digest(value['modules'][i]['states']['exit']['subject']),
            incoming_state_sha256=digest(value['modules'][i + 1]['states']['entry']['subject']))], transition=None)
        for i in range(len(rows) - 1)]
    project=H.compile_film_project(**value)
    hosts={name:(ROOT/'video_workbench/motion_renderer'/name).read_bytes() for name in HOST_NAMES}
    files=compile_film_browser_bundle(project,
        expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=640,height=360,host_files=hosts)
    release=dict(runtime_fingerprint=value['runtime_fingerprint'],trusted_host_sha256={name:S.sha(raw) for name,raw in hosts.items()},
        image='registry.internal/video-motion-renderer:latest@'+value['runtime_fingerprint']['image_digest'])
    return files,S.sha(files['browser-manifest.json']),release


def synthetic_stream_capture(monkeypatch, *, mutate=None):
    real_run=E._run
    def run(command, *, cwd, deadline):
        if len(command)<2 or Path(command[1]).name!='hyperframes_film_capture.mjs':
            return real_run(command,cwd=cwd,deadline=deadline)
        config=json.loads(Path(command[2]).read_bytes())
        assert 'streamEncoding' in config
        bundle=json.loads((Path(config['bundleRoot'])/'browser-manifest.json').read_bytes())
        project=json.loads((Path(config['bundleRoot'])/'project/manifest.json').read_bytes())
        count=project['clock']['frame_count']
        Path(config['framesRoot']).mkdir()
        raw=png();(cwd/'one-transport-frame.png').write_bytes(raw)
        helper=ROOT/'video_workbench/motion_renderer/hyperframes_film_stream.mjs'
        script=cwd/'synthetic-stream.mjs'
        script.write_text("import fs from 'node:fs';\nimport {createFilmPngEncoder} from "+json.dumps(helper.as_uri())+";\n"
            "const config=JSON.parse(fs.readFileSync(process.argv[2]));const raw=fs.readFileSync('one-transport-frame.png');\n"
            "const writer=await createFilmPngEncoder({...config.streamEncoding,width:640,height:360,frameCount:"+str(count)+"});\n"
            "for(let i=0;i<"+str(count)+";i++)await writer.write(raw);console.log(JSON.stringify(await writer.finish()));\n",encoding='utf-8')
        storage=json.loads(real_run([command[0],str(script),command[2]],cwd=cwd,deadline=deadline))
        checksum=hashlib.sha256(raw).hexdigest()
        frames=[dict(frame=i,path='frame-'+str(i).zfill(6)+'.png',bytes=len(raw),sha256=checksum) for i in range(count)]
        modules={m['module_id']:m for m in project['modules']}
        observations=[]
        previous={};serial=0
        for ordinal,frame in enumerate([*range(count),count-1,0,count//2]):
            fresh=ordinal>=count
            if fresh:previous={}
            realms=[];current={}
            for scene in project['scenes']:
                if not scene['active_start_frame']<=frame<scene['active_end_frame']:continue
                mid=scene['module_id'];m=modules[mid]
                if mid not in previous:serial+=1
                instance=previous.get(mid,serial);current[mid]=instance
                realms.append(dict(module_id=mid,module_sha256=m['module_sha256'],instance_id=instance,
                    sandbox='allow-scripts',realm_origin='null',
                    context=dict(global_frame=frame,local_frame=frame-scene['start_frame'],phase='active'),
                    observation=dict(engine=m['engine'],revision='film-opaque-realm-guard-1',
                        pending_animation_frames=0,service_worker_available=False,
                        contexts=[dict(kind='2d',width=640,height=360,connected=True,visible=True)])))
            previous=current
            observations.append(dict(requested_frame=frame,completed_frame=frame,actual_seconds=frame/24,
                manifest_sha256=bundle['project_manifest_sha256'],film_sha256=project['film_sha256'],
                core_seek_count=ordinal+1,dispatch_count=ordinal+1,frame_sha256=checksum,
                fresh_reconstruction=fresh,realms=realms))
        result=dict(schema='video.hyperframes-film-frame-capture.v2',bundle_sha256=config['expectedBundleSha256'],
            project_manifest_sha256=bundle['project_manifest_sha256'],film_sha256=project['film_sha256'],
            fps=24,width=640,height=360,frame_count=count,frame_bytes=count*len(raw),frames=frames,storage=storage,
            probe=dict(schema='video.hyperframes-film-technical-probe.v1',bundle_sha256=config['expectedBundleSha256'],
                core_sha256=bundle['root_config']['hyperframes_core_sha256'],observations=observations,
                lifecycle_mode='persistent-active-modules-v1',errors=[],blocked=[],service_worker_accessor_disabled=True,
                cleanup=dict(remaining_iframes=0,pending_cleanup_modules=0)))
        if mutate:mutate(result)
        return canonical_bytes(result)
    monkeypatch.setattr(E,'_run',run)


def test_actual_61_second_pipe_is_one_fully_decoded_movie_without_frame_inventory(tmp_path,monkeypatch):
    if not all(shutil.which(name) for name in ['node','ffmpeg','ffprobe']):pytest.skip('Existing runtime tools required')
    files,checksum,_=long_bundle();synthetic_stream_capture(monkeypatch)
    result=E.export_film_browser_bundle(files,expected_bundle_sha256=checksum,scratch_dir=tmp_path,timeout_seconds=3600)
    assert result.receipt['clock']['frame_count']==1464
    assert result.receipt['media']['full_decode_passed'] and result.receipt['concatenated_scene_videos'] is False
    assert result.capture['storage']['frame_files']==0 and result.capture['storage']['peak_pending_frames']==1
    assert result.capture['storage']['encoder_invocations']==1
    assert result.audio_mix['contains_narration'] is False
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('mutation',['storage','fresh','instances','hash'])
def test_long_capture_cannot_claim_streaming_without_bound_storage_and_fresh_reconstruction(tmp_path,monkeypatch,mutation):
    if not all(shutil.which(name) for name in ['node','ffmpeg','ffprobe']):pytest.skip('Existing runtime tools required')
    files,checksum,_=long_bundle()
    def mutate(r):
        if mutation=='storage':r['storage']['frame_files']=1464
        elif mutation=='fresh':r['probe']['observations'][-1]['fresh_reconstruction']=False
        elif mutation=='instances':r['probe']['observations'][1]['realms'][0]['instance_id']=99
        else:r['storage']['frame_inventory_sha256']='f'*64
    synthetic_stream_capture(monkeypatch,mutate=mutate)
    with pytest.raises(ServiceError):E.export_film_browser_bundle(files,expected_bundle_sha256=checksum,scratch_dir=tmp_path)
    assert list(tmp_path.iterdir())==[]


def test_300_second_snapshot_freezes_stream_lifecycle_and_long_execution_budget():
    files,checksum,release=long_bundle((60,60,60,60,60))
    snapshot=S.freeze_film_execution_snapshot(files,expected_bundle_sha256=checksum,
        project_revision=1,production_run_id='long-film-test',timeout_seconds=3600,**release)
    assert snapshot['clock']['frame_count']==7200
    assert S.decode_film_execution_snapshot(snapshot,expected_snapshot_sha256=snapshot['sha256'],**release)==files


def test_long_duration_is_bounded_before_execution():
    files,checksum,release=long_bundle((60,60,60,60,60,1))
    with pytest.raises(ServiceError,match='duration_budget'):
        S.freeze_film_execution_snapshot(files,expected_bundle_sha256=checksum,
            project_revision=1,production_run_id='long-film-test',timeout_seconds=3600,**release)
