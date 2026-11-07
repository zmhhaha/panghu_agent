"""Finite Canvas 2D projects for the local animation developer workbench.

The canvas is painted from absolute timeline time. There is no simulation loop,
wall clock or random browser state, so a backward seek reconstructs the scene.
This developer compiler does not grant production renderer admission.
"""
from __future__ import annotations

import html
import json
import re

from agent_service.contracts import ServiceError
from .motion import (FPS, GSAP_VERSION, HYPERFRAMES_VERSION, canonical, semantic_digest,
                     sha256, validate_alignment, validate_beats)


COMPILER_VERSION = 'animation-lab-canvas-compiler-1'
PLAN_SCHEMA = 'video.canvas-plan.v1'
_PLAN_KEYS = {'schema', 'template', 'title', 'takeaway', 'seed', 'packet_count', 'labels'}


def _require(value, code='animation_lab_canvas_plan_invalid'):
    if not value:
        raise ValueError(code)


def _text(value, limit):
    _require(type(value) is str and 0 < len(value) <= limit and value.strip()
             and not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value))


def validate_canvas_plan(plan):
    _require(type(plan) is dict and set(plan) == _PLAN_KEYS)
    _require(plan['schema'] == PLAN_SCHEMA and plan['template'] == 'queue-backpressure')
    _text(plan['title'], 32)
    _text(plan['takeaway'], 68)
    _require(type(plan['seed']) is int and 0 <= plan['seed'] <= 65535)
    _require(type(plan['packet_count']) is int and 3 <= plan['packet_count'] <= 12)
    _require(type(plan['labels']) is dict and set(plan['labels']) == {'source', 'queue', 'worker'})
    for value in plan['labels'].values():
        _text(value, 8)
    return json.loads(canonical(plan))


def example_canvas_plan():
    return dict(schema=PLAN_SCHEMA, template='queue-backpressure', title='流量突增时，请求去了哪里？',
                takeaway='队列先接住突增流量，再按处理能力逐个交给执行节点。',
                seed=101, packet_count=8,
                labels=dict(source='请求入口', queue='缓冲队列', worker='执行节点'))


def _script_json(value):
    return canonical(value).decode('utf-8').replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')


def compile_canvas_project(plan, beats, alignment, *, variant='candidate'):
    """Compile a real canvas scene with a bounded, exact input/output contract."""
    plan = validate_canvas_plan(plan)
    _require(type(variant) is str and variant in {'baseline', 'candidate'},
             'animation_lab_canvas_variant_invalid')
    try:
        beats = validate_beats(beats)
        alignment = validate_alignment(alignment, beats)
    except (ServiceError, ValueError, TypeError, KeyError):
        raise ValueError('animation_lab_canvas_alignment_invalid') from None
    seconds = alignment['audio_seconds']
    _require(3 <= seconds <= 60, 'animation_lab_canvas_duration_invalid')
    scale = seconds / 18
    actions = []
    for index in range(plan['packet_count']):
        target = f'packet-{index+1}'
        for kind, start, duration, label in (
            ('reveal', .45 + index * .14, .38, '请求出现'),
            ('move', 1.5 + index * .2, 1.25, '进入队列'),
            ('move', 6.4 + index * .75, 1.0, '交给执行节点'),
        ):
            actions.append(dict(kind=kind, target=target, start=round(start * scale, 6),
                                duration=round(duration * scale, 6), label=label))
    actions.sort(key=lambda row: (row['start'], row['target']))
    for ordinal, action in enumerate(actions):
        action['ordinal'] = ordinal
    timeline = dict(schema='video.motion-timeline.v1', compiler=COMPILER_VERSION,
        engine='canvas', template='canvas-queue', variant=variant, fps=FPS,
        width=1280, height=720, duration_seconds=seconds, plan_sha256=sha256(plan),
        audio_sha256=alignment['audio_sha256'], semantic_sha256=semantic_digest(beats),
        actions=actions, deterministic=True, seed=plan['seed'])
    payload = dict(plan=plan, timeline=timeline)
    substitutions = dict(__TITLE__=html.escape(plan['title']),
        __TAKEAWAY__=html.escape(plan['takeaway']), __DURATION__=str(seconds),
        __PAYLOAD__=_script_json(payload), __ENGINE_SCRIPT__=_ENGINE)
    page = re.sub('|'.join(map(re.escape, substitutions)),
                  lambda match: substitutions[match.group()], _PAGE)
    files = {'index.html': page.encode('utf-8'), 'plan.json': canonical(plan),
             'beats.json': canonical(beats), 'alignment.json': canonical(alignment),
             'timeline.json': canonical(timeline)}
    manifest = dict(schema='video.motion-project.v1', compiler=COMPILER_VERSION,
        template='canvas-queue', engine='canvas', developer_only=True,
        hyperframes=HYPERFRAMES_VERSION, gsap=GSAP_VERSION, width=1280, height=720, fps=FPS,
        animated=True, plan_sha256=sha256(plan), semantic_sha256=semantic_digest(beats),
        timeline_sha256=sha256(timeline), audio_sha256=alignment['audio_sha256'],
        runtime_fingerprint=None, files={name: sha256(raw) for name, raw in sorted(files.items())},
        required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc'])
    files['manifest.json'] = canonical(manifest)
    return files


_PAGE = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=1280,height=720">
<title>Canvas 流量与队列演示</title><script src="assets/gsap.min.js"></script><style>
@font-face{font-family:VideoNoto;src:url('assets/NotoSansCJK-Regular.ttc');font-display:block}
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0b1422;color:#edf5ff;font-family:VideoNoto,sans-serif}
#motion-root{position:relative;width:1280px;height:720px;overflow:hidden}
#scene{position:absolute;inset:0;width:1280px;height:720px}
h1{position:absolute;left:64px;top:46px;width:1152px;margin:0;font-size:34px;line-height:1.45}
.eyebrow{position:absolute;left:64px;top:24px;margin:0;font-size:12px;letter-spacing:3px;color:#77dbd1}
.takeaway{position:absolute;left:64px;top:646px;width:1152px;margin:0;font-size:21px;line-height:32px;color:#c2d0e2}
.accessible{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
#engine-error{position:absolute;inset:180px 64px auto;padding:28px;background:#442332;color:#fff;font-size:24px;z-index:10}
</style></head><body><main id="motion-root" data-composition-id="video-motion" data-width="1280" data-height="720" data-start="0" data-duration="__DURATION__" data-fps="24">
<canvas id="scene" width="1280" height="720" aria-label="请求进入队列，再交给执行节点的二维过程动画"></canvas>
<p class="eyebrow">流量与处理能力</p><h1>__TITLE__</h1><p class="takeaway">__TAKEAWAY__</p>
<p class="accessible" id="scene-description"></p><p id="engine-error" hidden></p>
</main><script>'use strict';const data=__PAYLOAD__;__ENGINE_SCRIPT__</script></body></html>'''


_ENGINE = '''
(()=>{
 const canvas=document.getElementById('scene'),ctx=canvas.getContext('2d',{alpha:false,willReadFrequently:true});
 const state={time:0},duration=data.timeline.duration_seconds,scale=duration/18;
 const enhanced=data.timeline.variant==='candidate',plan=data.plan;
 let latest=null,initialized=false;
 const clamp=x=>Math.max(0,Math.min(1,x)),smooth=x=>{x=clamp(x);return x*x*(3-2*x);};
 const progress=(time,start,length)=>clamp((time-start*scale)/(length*scale));
 const blend=(a,b,p)=>a+(b-a)*p;
 const cards={source:{x:70,y:247,width:260,height:296},queue:{x:465,y:247,width:350,height:296},worker:{x:950,y:247,width:260,height:296}};
 const seeded=index=>{let n=(plan.seed+index*2654435761)>>>0;n^=n<<13;n^=n>>>17;n^=n<<5;return(n>>>0)/4294967296;};
 function packet(index,time){
  const source={x:144+(index%3)*55,y:340+Math.floor(index/3)*49};
  const queue={x:548+(index%4)*61,y:340+Math.floor(index/4)*53};
  const worker={x:1021+(index%3)*55,y:333+Math.floor(index/3)*49};
  const incoming=progress(time,1.5+index*.2,1.25),outgoing=progress(time,6.4+index*.75,1);
  const eased=enhanced?smooth(incoming):incoming,departed=enhanced?smooth(outgoing):outgoing;
  const jitter=(seeded(index)-.5)*14;
  let x=blend(source.x,queue.x,eased),y=blend(source.y,queue.y,eased)+Math.sin(Math.PI*incoming)*jitter;
  if(outgoing>0){x=blend(queue.x,worker.x,departed);y=blend(queue.y,worker.y,departed)+Math.sin(Math.PI*outgoing)*jitter;}
  const opacity=smooth(progress(time,.45+index*.14,.38));
  const status=incoming<1?'incoming':outgoing===0?'queued':outgoing<1?'transferring':progress(time,7.4+index*.75,.45)<1?'processing':'done';
  return{id:'packet-'+(index+1),x,y,radius:19,opacity,status,label:String(index+1).padStart(2,'0')};
 }
 function roundRect(x,y,w,h,r){ctx.beginPath();ctx.roundRect(x,y,w,h,r);}
 function text(label,x,y,size=20,color='#dce9f8',align='left',weight=500){ctx.fillStyle=color;ctx.font=weight+' '+size+'px VideoNoto, sans-serif';ctx.textAlign=align;ctx.textBaseline='middle';ctx.fillText(label,x,y);}
 function paint(time){
  time=Math.max(0,Math.min(duration,time));
  const packets=Array.from({length:plan.packet_count},(_,i)=>packet(i,time));
  const queued=packets.filter(p=>p.status==='queued').length,done=packets.filter(p=>p.status==='done').length;
  const stage=time<4.8*scale?'arrival':time<6.4*scale?'buffer':done===plan.packet_count?'complete':'dispatch';
  ctx.globalAlpha=1;ctx.fillStyle='#0b1422';ctx.fillRect(0,0,1280,720);
  const glow=ctx.createRadialGradient(640,360,40,640,360,690);glow.addColorStop(0,'#15263a');glow.addColorStop(1,'#0b1422');ctx.fillStyle=glow;ctx.fillRect(0,130,1280,475);
  const stages=[['arrival','突增流量'],['buffer','队列缓冲'],['dispatch','按能力处理'],['complete','请求全部完成']];
  stages.forEach(([id,label],i)=>{const active=id===stage,x=64+i*298;roundRect(x,143,278,52,12);ctx.fillStyle=enhanced&&active?'#164d4d':'#142133';ctx.fill();ctx.strokeStyle=enhanced&&active?'#75dfc5':'#273b50';ctx.lineWidth=1.5;ctx.stroke();text(String(i+1).padStart(2,'0'),x+20,170,14,enhanced&&active?'#90ead1':'#73899f');text(label,x+54,170,19,enhanced&&active?'#eefffb':'#b8c9da');});
  Object.entries(cards).forEach(([id,box])=>{
   const active=enhanced&&((stage==='arrival'&&id==='source')||(stage==='buffer'&&id==='queue')||((stage==='dispatch'||stage==='complete')&&id==='worker'));
   roundRect(box.x,box.y,box.width,box.height,20);ctx.fillStyle=active?'#142c3d':'#122235';ctx.fill();ctx.strokeStyle=active?'#69d6ce':'#304459';ctx.lineWidth=active?2.5:1.5;ctx.stroke();
   text(plan.labels[id],box.x+24,box.y+35,24,'#e7f3ff','left',700);
   const subtitle=id==='source'?'同时到来的请求':id==='queue'?'等待资源，不丢请求':'逐个领取并完成';
   text(subtitle,box.x+24,box.y+72,15,'#94acc2');
  });
  // Channels are painted only once a transfer actually begins, never as
  // visible stray lines before the animation has introduced its objects.
  if(time>1.5*scale){for(const [x1,x2,y] of [[342,451,398],[827,936,398]]){
   if(x1>800&&time<=6.4*scale)continue;
   ctx.strokeStyle=enhanced?'#456c80':'#304a61';ctx.lineWidth=2;ctx.setLineDash([5,7]);ctx.beginPath();ctx.moveTo(x1,y);ctx.lineTo(x2-9,y);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(x2,y);ctx.lineTo(x2-10,y-5);ctx.lineTo(x2-10,y+5);ctx.closePath();ctx.fill();
  }}
  packets.forEach(p=>{if(p.opacity<=0)return;ctx.globalAlpha=p.opacity;
   const completed=p.status==='done',running=p.status==='processing';
   if(enhanced&&(running||p.status==='transferring')){ctx.beginPath();ctx.arc(p.x,p.y,26,0,Math.PI*2);ctx.fillStyle='#70e2db22';ctx.fill();}
   ctx.beginPath();ctx.arc(p.x,p.y,p.radius,0,Math.PI*2);ctx.fillStyle=completed?'#195a4b':p.status==='queued'?'#2a405e':'#1e566a';ctx.fill();ctx.strokeStyle=completed?'#7ae0b5':'#82dce4';ctx.lineWidth=1.8;ctx.stroke();text(p.label,p.x,p.y,14,completed?'#aeefd0':'#edffff','center',700);
  });ctx.globalAlpha=1;
  const info=stage==='arrival'?'请求到达速度超过处理速度':stage==='buffer'?'队列暂存请求，等待可用资源':stage==='complete'?'流量恢复平稳，队列已被消化':'执行节点持续领取，排队请求逐渐减少';
  text(info,640,590,22,'#c7deed','center');
  text('排队 '+queued,640,514,17,'#8dcddd','center');text('完成 '+done+' / '+plan.packet_count,1080,514,17,'#8fe2be','center');
  latest={engine:'canvas',compiler:data.timeline.compiler,variant:data.timeline.variant,time,
   width:canvas.width,height:canvas.height,context:'2d',seed:plan.seed,stage,queue_count:queued,completed_count:done,
   objects:packets.map(p=>({...p,bounds:{x:p.x-p.radius,y:p.y-p.radius,width:p.radius*2,height:p.radius*2},
    center_pixel:Array.from(ctx.getImageData(Math.round(p.x),Math.round(p.y),1,1).data)})),
   bitmap_samples:[[90,270],[480,260],[640,600],[50,220],[1200,600]].map(([x,y])=>({x,y,rgba:Array.from(ctx.getImageData(x,y,1,1).data)})),
   bounds:{x:0,y:0,width:canvas.width,height:canvas.height},deterministic:true};
  document.getElementById('scene-description').textContent=plan.labels.source+' → '+plan.labels.queue+' → '+plan.labels.worker+'；'+info+'；排队 '+queued+'；完成 '+done+' / '+plan.packet_count;
 }
 const tl=gsap.timeline({paused:true});
 tl.to(state,{time:duration,duration,ease:'none',onUpdate:()=>{if(initialized)paint(state.time);}},0);
 window.__timelines=window.__timelines||{};window.__timelines['video-motion']=tl;
 const ready=(async()=>{
  if(!ctx)throw Error('Canvas 2D 上下文不可用');
  await document.fonts.load('20px VideoNoto');await document.fonts.ready;
  if(!document.fonts.check('20px VideoNoto'))throw Error('指定字体未能加载');
  initialized=true;paint(state.time);
 })();
 ready.catch(error=>{const box=document.getElementById('engine-error');box.hidden=false;box.textContent='Canvas 预览未就绪：'+error.message;});
 window.__videoMotion={engine:'canvas',compiler:data.timeline.compiler,ready,
  snapshot:()=>{if(!initialized)throw Error('canvas_not_ready');return JSON.parse(JSON.stringify(latest));}};
})();
'''
