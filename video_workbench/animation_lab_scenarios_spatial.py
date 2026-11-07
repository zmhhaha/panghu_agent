"""Finite spatial explanations using actual local Three.js meshes and clipping.

The clock reconstructs part transforms and a material clipping plane. These are
illustrative models, not engineering drawings or physical simulation outputs.
No remote models, textures, arbitrary scripts or continuously running RAF loop
are needed. The shared scenario host owns the absolute render clock.
"""
from __future__ import annotations

from copy import deepcopy
import html
import json
import math

SCHEMA = 'video.animation-lab-scenario-plan.v1'


def _brief(audience, goal, questions, entities, outcome, boundary):
    return dict(audience=audience, goal=goal, questions=questions, required_entities=entities,
                expected_outcome=outcome, boundaries=['所有尺寸与结构均为示意，不替代工程设计。', boundary])


def _part(identity, label, shape, position, exploded, color, **dimensions):
    return dict(id=identity, label=label, shape=shape, position=position,
                exploded=exploded, color=color, **dimensions)


SCENARIOS = {
    'spatial-bolt-assembly': dict(module='spatial', domain='mechanics', domain_label='机械装配',
        engine='three', expression='spatial-cutaway', title='一颗螺栓如何把两块板连接起来？',
        description='真实带孔板、螺栓、垫圈与螺母沿同一轴装配，再用移动剖切平面检查内部。',
        takeaway='沿同一轴装配，再通过剖切检查贴合；示意不计算预紧力。',
        narrative='先把螺栓、两块带孔板、垫圈和螺母分开看。装配时，它们沿同一条轴线靠拢。接着让剖切平面穿过连接处，观察螺栓穿过板孔，垫圈位于螺母与板之间。颜色变化只是帮助找到部件，不能代表连接已经满足强度要求。',
        requirement=_brief('机械结构入门学习者', '通过装配与剖切理解连接件相对位置',
            ['板孔与螺栓轴线如何对齐？', '垫圈放在哪里？'],
            ['螺栓头', '螺杆', '两块带孔板', '垫圈', '带孔螺母', '移动剖切平面'],
            '能指出垫圈在下板与螺母之间，并通过剖切观察轴线。', '未模拟螺纹、接触、拧紧扭矩或受力。'),
        duration_seconds=24, difficulty='intermediate', tags=['真实网格', '同轴装配', '局部剖切'],
        checks=['部件沿同轴移动', '材质启用真实裁剪平面', '剖切与装配在倒放后完全恢复'],
        data=dict(axis=0, cut=dict(start=8, duration=5, initial=3.4, final=.02),
            assembly=dict(mode='assemble', start=2, duration=5), focus=['washer', 'nut'], focus_at=14,
            result=dict(start=19, duration=3), stages=[[0, '识别独立部件'], [2, '沿轴装配'], [8, '剖开连接处'], [14, '核对垫圈与螺母'], [19, '保留剖面检查贴合']],
            parts=[
                _part('head', '螺栓头', 'cylinder', [0, 1.08, 0], [0, 3.5, 0], '#70c7ee', radius=.58, height=.36, segments=6),
                _part('shaft', '螺杆', 'cylinder', [0, -.10, 0], [0, 2.32, 0], '#70c7ee', radius=.22, height=2.08, segments=24),
                _part('upper', '上板与孔', 'plate', [0, .67, 0], [0, 1.2, 0], '#b5c6d8', width=3.1, height=.42, depth=2.1, hole=.28),
                _part('lower', '下板与孔', 'plate', [0, .19, 0], [0, -.55, 0], '#e1b66d', width=3.1, height=.42, depth=2.1, hole=.28),
                _part('washer', '垫圈', 'ring', [0, -.25, 0], [0, -1.45, 0], '#a9e8cd', radius=.39, tube=.10),
                _part('nut', '带孔螺母', 'nut', [0, -.58, 0], [0, -2.45, 0], '#bf9ae6', radius=.52, height=.40, hole=.24),
            ])),
    'spatial-building-cutaway': dict(module='spatial', domain='architecture', domain_label='建筑空间',
        engine='three', expression='spatial-cutaway', title='从外壳看到楼板和竖向支撑',
        description='先切去前半建筑，再抬起屋顶与楼板，核对内部空间和支撑的相对位置。',
        takeaway='剖切用于看内部，分解用于辨认部件；两种操作含义不同。',
        narrative='从外面看，墙体挡住了内部。先让剖切面从前向后移动，露出两层空间。再暂时抬起屋顶和楼板，辨认各自位置。竖向支撑保持不动。最后把部件放回，保留剖面，检查楼板和支撑如何对应。这只是空间示意，不能用来判断建筑是否安全。',
        requirement=_brief('建筑空间入门读者', '区分剖切观察和部件分解',
            ['剖切去掉的是哪一侧？', '抬起楼板时支撑是否也移动？'],
            ['外墙', '底板', '中间楼板', '屋顶', '竖向支撑', '前向剖面'],
            '区分前半裁剪、楼板分离与结构复位三个阶段。', '不包含建筑规范校核、配筋或承载分析。'),
        duration_seconds=24, difficulty='basic', tags=['建筑剖面', '层间空间', '装配复位'],
        checks=['剖面从前向后推进', '只有指定板件上移', '支撑位置保持不动'],
        data=dict(axis=2, cut=dict(start=2, duration=5, initial=2.6, final=.08),
            assembly=dict(mode='explode-return', start=8, duration=4, return_start=19, return_duration=3),
            focus=['floor', 'column'], focus_at=14, result=dict(start=19, duration=3),
            stages=[[0, '外部整体'], [2, '切去前半空间'], [8, '分离屋顶与楼板'], [14, '辨认支撑位置'], [19, '板件复位并保留剖面']],
            parts=[
                _part('base', '底板', 'box', [0, -1.65, 0], [0, -1.65, 0], '#879eb4', width=4.5, height=.22, depth=3.4),
                _part('rear', '后墙', 'box', [0, 0, -1.6], [0, 0, -1.6], '#90b3c8', width=4.5, height=3.1, depth=.16),
                _part('front', '前墙', 'box', [0, 0, 1.6], [0, 0, 1.6], '#90b3c8', width=4.5, height=3.1, depth=.16),
                _part('side', '左墙', 'box', [-2.17, 0, 0], [-2.17, 0, 0], '#90b3c8', width=.16, height=3.1, depth=3.2),
                _part('floor', '中间楼板', 'box', [0, 0, 0], [0, .9, 0], '#e1b66d', width=4.2, height=.16, depth=3.1),
                _part('roof', '屋顶', 'box', [0, 1.65, 0], [0, 3.3, 0], '#a4dcc7', width=4.5, height=.18, depth=3.4),
                _part('column', '竖向支撑', 'box', [-1.3, 0, -.5], [-1.3, 0, -.5], '#b596dc', width=.26, height=3.1, depth=.26),
            ])),
    'spatial-geology-section': dict(module='spatial', domain='geology', domain_label='地质与环境',
        engine='three', expression='spatial-cutaway', title='地表之下，地层按什么顺序排列？',
        description='裁开真实三维层状体，再以明确的示意分解距离辨认四层地层。',
        takeaway='分层展开是说明手段，不代表地层真实移动或地下水流速。',
        narrative='完整地块只露出表层。剖切后，从上到下可以看到表土、砂层、黏土层和基岩。为了辨认边界，暂时把各层拉开；这不是地层运动。重点观察砂层与下面黏土层的位置关系，最后让四层复位。这里不能仅凭颜色判断真实地下水如何流动。',
        requirement=_brief('地质科普读者', '从三维剖面辨认层序及相邻关系',
            ['砂层位于哪两层之间？', '展开距离是否代表真实地层厚度？'],
            ['表土', '砂层', '黏土层', '基岩', '剖面', '示意分层展开'],
            '按表土、砂层、黏土、基岩顺序复述层序。', '颜色与厚度为示意，不推断渗透参数、流速或真实地层年代。'),
        duration_seconds=24, difficulty='boundary', tags=['地质剖面', '相邻关系', '示意边界'],
        checks=['层序始终不变', '切面封口与实际剖面重合', '展开结束能够复位'],
        data=dict(axis=0, cut=dict(start=2, duration=5, initial=3.2, final=.05),
            assembly=dict(mode='explode-return', start=8, duration=5, return_start=19, return_duration=3),
            focus=['sand', 'clay'], focus_at=14, result=dict(start=19, duration=3),
            stages=[[0, '完整地块'], [2, '裁开地层'], [8, '示意拉开层间距离'], [14, '辨认砂层与黏土'], [19, '保持层序复位']],
            parts=[
                _part('soil', '表土', 'box', [0, 1.2, 0], [0, 2.25, 0], '#9cae72', width=4.8, height=.75, depth=3.0),
                _part('sand', '砂层', 'box', [0, .4, 0], [0, .75, 0], '#deb56e', width=4.8, height=.75, depth=3.0),
                _part('clay', '黏土层', 'box', [0, -.4, 0], [0, -.75, 0], '#b67c71', width=4.8, height=.75, depth=3.0),
                _part('rock', '基岩', 'box', [0, -1.2, 0], [0, -2.25, 0], '#899bb8', width=4.8, height=.75, depth=3.0),
            ])),
    'spatial-battery-teardown': dict(module='spatial', domain='energy', domain_label='电池封装',
        engine='three', expression='spatial-cutaway', title='电池包里面，外壳与电芯如何分工？',
        description='抬起上盖与绝缘板，剖切壳体，辨认四个圆柱电芯及连接排，再复位。',
        takeaway='拆解是结构示意；不执行充放电，也不展示真实拆电池操作。',
        narrative='先看一个封闭的示意电池包。抬起上盖与绝缘板以后，里面是四个圆柱电芯。移动剖面，可以看见壳体、电芯和上方连接排的相对位置。最后把板和盖放回。这个模型只解释封装结构，不包含真实电路、带电拆解步骤或安全认证。',
        requirement=_brief('能源产品科普读者', '通过分解与剖切辨认封装层级',
            ['电芯与外壳是否是同一层级？', '连接排与绝缘板分别位于哪里？'],
            ['下壳体', '上盖', '绝缘板', '四个圆柱电芯', '连接排', '局部剖面'],
            '区分外壳保护、电芯实体与连接排的位置。', '不是拆装指南；未模拟电气连接、热失控、功率或安全性。'),
        duration_seconds=24, difficulty='intermediate', tags=['封装拆解', '圆柱网格', '复位'],
        checks=['上盖与板件沿固定轴分离', '四个电芯各自为真实圆柱', '复位后部件相对位置准确'],
        data=dict(axis=0, cut=dict(start=8, duration=5, initial=3.2, final=.12),
            assembly=dict(mode='explode-return', start=2, duration=5, return_start=19, return_duration=3),
            focus=['cell1', 'busbar'], focus_at=14, result=dict(start=19, duration=3),
            stages=[[0, '封装整体'], [2, '抬起上盖与绝缘板'], [8, '剖开壳体观察电芯'], [14, '辨认电芯与连接排'], [19, '按原位置复位']],
            parts=[
                _part('tray', '下壳体', 'box', [0, -1.05, 0], [0, -1.05, 0], '#7f9bb6', width=4.2, height=.32, depth=2.7),
                _part('lid', '上盖', 'box', [0, 1.15, 0], [0, 3.25, 0], '#9eb9cf', width=4.2, height=.16, depth=2.7),
                _part('insulator', '绝缘板', 'box', [0, .94, 0], [0, 2.15, 0], '#c6ad7a', width=3.8, height=.10, depth=2.3),
                *[_part('cell'+str(i+1), '电芯'+str(i+1), 'cylinder', [x, 0, 0], [x, 0, 0], '#85cbb5', radius=.31, height=1.6, segments=24)
                  for i, x in enumerate([-1.2, -.4, .4, 1.2])],
                _part('busbar', '连接排', 'box', [0, .84, .5], [0, .84, .5], '#d8a27a', width=3.5, height=.10, depth=.18),
            ])),
}


def plan_for(identity):
    return deepcopy(dict(schema=SCHEMA, scenario_id=identity, **SCENARIOS[identity]))


def _progress(time, start, duration):
    return max(0., min(1., (time-start)/duration))


def _smooth(value):
    return value*value*(3-2*value)


def spatial_state(plan, time):
    """Independent finite oracle; no measured renderer state is an input."""
    time = max(0., min(float(time), plan['duration_seconds']))
    data = plan['data']
    assembly = data['assembly']
    p = _smooth(_progress(time, assembly['start'], assembly['duration']))
    if assembly['mode'] == 'assemble':
        separation = 1-p
        metric = p
    else:
        separation = p*(1-_smooth(_progress(time, assembly['return_start'], assembly['return_duration'])))
        metric = separation
    cut = data['cut']
    cut_fraction = _smooth(_progress(time, cut['start'], cut['duration']))
    constant = cut['initial']+(cut['final']-cut['initial'])*cut_fraction
    focus_id = data['focus'][0 if time < data['focus_at']+1.5 else 1]
    result = _smooth(_progress(time, data['result']['start'], data['result']['duration']))
    return dict(time=time, separation=separation, assembly_metric=metric,
        cut_fraction=cut_fraction, constant=constant, focus=focus_id, result=result,
        phase=next(text for at, text in reversed(data['stages']) if time >= at),
        parts={part['id']: [a+(b-a)*separation for a,b in zip(part['position'],part['exploded'])]
               for part in data['parts']})


def expected_observations(plan, time):
    state = spatial_state(plan, time)
    part = next(row for row in plan['data']['parts'] if row['id'] == state['focus'])
    mode = '装配进度' if plan['data']['assembly']['mode'] == 'assemble' else '展开程度'
    result = {
        'spatial-phase': dict(text=state['phase']),
        'spatial-cut': dict(text=f"剖切推进 {math.floor(state['cut_fraction']*100+.5)}%",
                            attributes={'data-value': f"{state['constant']:.3f}"}),
        'spatial-assembly': dict(text=f"{mode} {math.floor(state['assembly_metric']*100+.5)}%"),
        'spatial-focus': dict(text='当前观察：'+part['label']),
        'spatial-result': dict(text=plan['takeaway'] if state['result'] > 0 else '',
                               attributes={'data-value': f"{state['result']:.3f}"}),
    }
    for index, part in enumerate(plan['data']['parts']):
        position = state['parts'][part['id']]
        clipped = position[plan['data']['axis']] > state['constant']
        result['spatial-label-'+part['id']] = dict(text=f"{index+1} · {part['label']}",
            attributes={'data-value': ','.join(f'{v:.3f}' for v in position),
                        'data-state': 'sectioned' if clipped else 'retained'})
    return result


def build_recipe(plan, variant):
    if plan != plan_for(plan.get('scenario_id')) or variant not in {'baseline', 'candidate'}:
        raise ValueError('animation_lab_spatial_plan_invalid')
    e = lambda value: html.escape(str(value), quote=True)
    labels = ''.join(f'<div id="spatial-label-{e(p["id"])}" class="spatial-part-label" '
        f'style="top:{110+i*32}px;border-left:4px solid {e(p["color"])}">{i+1} · {e(p["label"])}</div>'
        for i,p in enumerate(plan['data']['parts']))
    markers = ''.join(f'<span id="spatial-marker-{e(p["id"])}" class="spatial-marker" '
        f'style="background:{e(p["color"])}">{i+1}</span>' for i,p in enumerate(plan['data']['parts']))
    markup = '''<style>
.spatial-canvas{position:absolute;left:24px;top:0;width:880px;height:500px;border:1px solid #29445e;border-radius:16px}
.spatial-panel{position:absolute;left:926px;top:0;width:304px;height:500px;border:1px solid #29445e;border-radius:14px;background:#112236;padding:14px}
.spatial-panel p{margin:0;font-size:19px;line-height:28px}.spatial-panel #spatial-cut{position:absolute;top:12px;left:14px}
#spatial-assembly{position:absolute;top:42px;left:14px}#spatial-focus{position:absolute;top:76px;left:14px;font-size:17px}
.spatial-part-label{position:absolute;left:14px;width:274px;height:28px;padding:3px 8px;font-size:17px;line-height:22px;white-space:nowrap;border-radius:4px}
.spatial-marker{position:absolute;display:block;width:22px;height:22px;border:1px solid #ebf5ff;border-radius:50%;text-align:center;font-size:14px;line-height:20px;color:#0a1727;pointer-events:none}
#spatial-result{position:absolute;left:14px;right:14px;top:406px;font-size:16px;line-height:24px;color:#d3e8df}
#spatial-phase{position:absolute;left:42px;top:462px;font-size:17px;line-height:26px;max-width:838px;color:#d6e6f5;background:#0d1a2bdb;padding:2px 10px;border-radius:6px;margin:0}
</style><canvas id="spatial-viewport" class="spatial-canvas" width="880" height="500" aria-label="真实三维部件与剖切画布"></canvas>'''+markers+'<aside class="spatial-panel"><p id="spatial-cut"></p><p id="spatial-assembly"></p><p id="spatial-focus"></p>'+labels+'<p id="spatial-result"></p></aside><p id="spatial-phase"></p>'
    ids = ['spatial-viewport', 'spatial-phase', 'spatial-cut', 'spatial-assembly', 'spatial-focus', 'spatial-result']
    ids += ['spatial-label-'+p['id'] for p in plan['data']['parts']]
    ids += ['spatial-marker-'+p['id'] for p in plan['data']['parts']]
    d = plan['data']
    actions = [dict(target='spatial-cut',kind='clip',start=d['cut']['start'],duration=d['cut']['duration']),
        dict(target='spatial-assembly',kind='assemble',start=d['assembly']['start'],duration=d['assembly']['duration']),
        dict(target='spatial-focus',kind='focus',start=d['focus_at'],duration=3),
        dict(target='spatial-result',kind='reveal',start=d['result']['start'],duration=d['result']['duration'])]
    actions.sort(key=lambda row: row['start'])
    script = 'const spatialEnhanced='+str(variant == 'candidate').lower()+';\n'+_SCRIPT
    if plan['scenario_id'] == 'spatial-bolt-assembly':
        # The compact fastener occupies too little of the shared architectural
        # camera after assembly. The optical zoom follows absolute assembly;
        # it does not alter dimensions or weaken the measured pixel boundary.
        script = script.replace('spatialPlane.constant=s.constant;',
            'spatialCamera.zoom=1+1.2*(1-s.separation);spatialCamera.updateProjectionMatrix();spatialPlane.constant=s.constant;')
        script = script.replace('fov:spatialCamera.fov,aspect:', 'zoom:spatialCamera.zoom,fov:spatialCamera.fov,aspect:')
    return dict(markup=markup, script=script, object_ids=ids, actions=actions)


def verify_native_observation(plan, time, variant, state):
    from .animation_lab_scenarios_spatial_review import verify_native_observation as verify
    return verify(plan, time, variant, state)


_SCRIPT = r'''
let spatialTHREE,spatialScene,spatialCamera,spatialRenderer,spatialPlane,spatialReadyPromise,spatialLastTime=0;
const spatialMeshes=new Map(),spatialCaps=new Map(),spatialCameraPosition=[9,7,11],spatialCameraTarget=[0,.3,0];
const spatialVector=v=>v.toArray().map(n=>Math.abs(n)<1e-12?0:n);
const spatialRound=n=>Math.abs(n)<.0005?'0.000':n.toFixed(3);
function spatialGeometry(p){const T=spatialTHREE;let g;
 if(p.shape==='box')g=new T.BoxGeometry(p.width,p.height,p.depth);
 else if(p.shape==='cylinder')g=new T.CylinderGeometry(p.radius,p.radius,p.height,p.segments,1,false);
 else if(p.shape==='ring'){g=new T.TorusGeometry(p.radius,p.tube,8,32);g.rotateX(Math.PI/2);}
 else {const shape=new T.Shape();
  if(p.shape==='plate'){shape.moveTo(-p.width/2,-p.depth/2);shape.lineTo(p.width/2,-p.depth/2);shape.lineTo(p.width/2,p.depth/2);shape.lineTo(-p.width/2,p.depth/2);shape.closePath();}
  else {for(let i=0;i<6;i++){const x=p.radius*Math.cos(i*Math.PI/3),y=p.radius*Math.sin(i*Math.PI/3);if(i)shape.lineTo(x,y);else shape.moveTo(x,y);}shape.closePath();}
  const hole=new T.Path();hole.absarc(0,0,p.hole,0,Math.PI*2,true);shape.holes.push(hole);
  g=new T.ExtrudeGeometry(shape,{depth:p.height,bevelEnabled:false,curveSegments:24});g.translate(0,0,-p.height/2);g.rotateX(-Math.PI/2);
 }g.computeBoundingBox();return g;
}
function effectReady(){if(spatialReadyPromise)return spatialReadyPromise;
 spatialReadyPromise=(async()=>{const T=spatialTHREE=await import('./assets/three.module.min.js');
  spatialScene=new T.Scene();spatialCamera=new T.PerspectiveCamera(40,880/500,.1,100);
  spatialCamera.position.fromArray(spatialCameraPosition);spatialCamera.lookAt(...spatialCameraTarget);spatialCamera.updateMatrixWorld(true);
  spatialRenderer=new T.WebGLRenderer({canvas:$('spatial-viewport'),antialias:false,alpha:false,preserveDrawingBuffer:true});
  spatialRenderer.setPixelRatio(1);spatialRenderer.setSize(880,500,false);spatialRenderer.setClearColor('#0d1a2b',1);
  spatialRenderer.localClippingEnabled=true;spatialRenderer.info.autoReset=true;
  const normal=new T.Vector3();normal.setComponent(plan.data.axis,-1);spatialPlane=new T.Plane(normal,plan.data.cut.initial);
  spatialScene.add(new T.HemisphereLight(0xddeeff,0x263b4f,2.4));
  const key=new T.DirectionalLight(0xffffff,3.1);key.position.set(6,10,9);spatialScene.add(key);
  const rim=new T.DirectionalLight(0x8dbaff,1.5);rim.position.set(-7,4,-5);spatialScene.add(rim);
  for(const p of plan.data.parts){const material=new T.MeshStandardMaterial({color:p.color,roughness:.55,metalness:.12,side:T.DoubleSide,clippingPlanes:[spatialPlane]});
   const mesh=new T.Mesh(spatialGeometry(p),material);mesh.name=p.id;mesh.frustumCulled=false;spatialMeshes.set(p.id,mesh);spatialScene.add(mesh);
   if(p.shape==='box'){const width=plan.data.axis===0?p.depth:p.width,g=new T.PlaneGeometry(width,p.height);
    const cap=new T.Mesh(g,new T.MeshStandardMaterial({color:p.color,roughness:.9,metalness:0,side:T.DoubleSide}));
    if(plan.data.axis===0)cap.rotation.y=Math.PI/2;cap.name='section-cap-'+p.id;cap.frustumCulled=false;
    spatialCaps.set(p.id,cap);spatialScene.add(cap);}
  }
  effectRender(0);
 })();return spatialReadyPromise;
}
function spatialClock(time){const d=plan.data,a=d.assembly,p=smooth(progress(time,a.start,a.duration));
 const separation=a.mode==='assemble'?1-p:p*(1-smooth(progress(time,a.return_start,a.return_duration)));
 const cut=smooth(progress(time,d.cut.start,d.cut.duration)),constant=d.cut.initial+(d.cut.final-d.cut.initial)*cut;
 return {separation,metric:a.mode==='assemble'?p:separation,cut,constant,
  focus:d.focus[time<d.focus_at+1.5?0:1],result:smooth(progress(time,d.result.start,d.result.duration)),
  phase:[...d.stages].reverse().find(([at])=>time>=at)[1]};
}
function effectRender(time){if(!spatialRenderer)throw Error('spatial_not_ready');
 spatialLastTime=Math.max(0,Math.min(plan.duration_seconds,time));const s=spatialClock(spatialLastTime),axis=plan.data.axis;
 spatialPlane.constant=s.constant;label('spatial-phase',s.phase);label('spatial-cut','剖切推进 '+Math.round(s.cut*100)+'%');
 attrs('spatial-cut',{'data-value':spatialRound(s.constant)});
 label('spatial-assembly',(plan.data.assembly.mode==='assemble'?'装配进度 ':'展开程度 ')+Math.round(s.metric*100)+'%');
 label('spatial-focus','当前观察：'+plan.data.parts.find(p=>p.id===s.focus).label);
 label('spatial-result',s.result>0?plan.takeaway:'');attrs('spatial-result',{'data-value':spatialRound(s.result)});fade('spatial-result',s.result);
 for(const p of plan.data.parts){const mesh=spatialMeshes.get(p.id),position=p.position.map((v,i)=>v+(p.exploded[i]-v)*s.separation);
  mesh.position.fromArray(position);mesh.rotation.set(0,0,0);mesh.scale.set(1,1,1);
  mesh.material.emissive.setHex(spatialEnhanced&&p.id===s.focus&&spatialLastTime>=plan.data.focus_at?0x163c34:0);
  mesh.material.emissiveIntensity=spatialEnhanced?1:0;mesh.updateMatrixWorld(true);
  const projected=mesh.position.clone().project(spatialCamera),marker=$('spatial-marker-'+p.id),clipped=position[axis]>s.constant;
  marker.style.left=(24+(projected.x+1)*440-11)+'px';marker.style.top=((1-projected.y)*250-11)+'px';
  marker.style.opacity=p.id===s.focus?(clipped?'.15':'.92'):'0';
  const row=$('spatial-label-'+p.id);row.setAttribute('data-value',position.map(spatialRound).join(','));row.setAttribute('data-state',clipped?'sectioned':'retained');
  row.style.background=spatialEnhanced&&p.id===s.focus?'#203b4b':'transparent';row.style.opacity=clipped?'.55':'1';
  const cap=spatialCaps.get(p.id);if(cap){const half=(axis===0?p.width:p.depth)/2;
   cap.visible=s.constant>position[axis]-half+.001&&s.constant<position[axis]+half-.001;
   cap.position.copy(mesh.position);cap.position.setComponent(axis,s.constant-.002);cap.updateMatrixWorld(true);}
 }
 spatialScene.updateMatrixWorld(true);spatialRenderer.render(spatialScene,spatialCamera);
}
function effectState(){const s=spatialClock(spatialLastTime);return {scenario:plan.scenario_id,domain:plan.domain,
 phase:s.phase,cut_constant:s.constant,separation:s.separation,focus:s.focus,clock:'absolute_deterministic'};}
function spatialGeometryState(geometry){geometry.computeBoundingBox();const p=geometry.parameters||{};
 return {type:geometry.type,position_count:geometry.getAttribute('position').count,
  position_item_size:geometry.getAttribute('position').itemSize,index_count:geometry.index?geometry.index.count:0,
  bounds:{min:spatialVector(geometry.boundingBox.min),max:spatialVector(geometry.boundingBox.max)},
  shape_holes:p.shapes&&p.shapes.holes?p.shapes.holes.length:0,
  hole_radii:p.shapes&&p.shapes.holes?p.shapes.holes.map(h=>h.curves.filter(c=>typeof c.xRadius==='number').map(c=>[c.xRadius,c.yRadius])):[],
  parameters:{...Object.fromEntries(['width','height','depth','radiusTop','radiusBottom','radialSegments','radius','tube'].filter(k=>typeof p[k]==='number').map(k=>[k,p[k]])),
   ...(p.options&&typeof p.options.depth==='number'?{depth:p.options.depth}:{})}};
}
function spatialPixelProbe(){const gl=spatialRenderer.getContext(),width=gl.drawingBufferWidth,height=gl.drawingBufferHeight;
 const pixels=new Uint8Array(width*height*4);gl.readPixels(0,0,width,height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
 let samples=0,foreground=0,hash=2166136261;const colors=new Set(),background=[...pixels.slice(0,3)];
 for(let y=10;y<height;y+=20)for(let x=10;x<width;x+=20){const i=(y*width+x)*4,rgb=[pixels[i],pixels[i+1],pixels[i+2]];
  samples++;if(rgb.some((v,k)=>Math.abs(v-background[k])>12))foreground++;colors.add(rgb.join(','));
  for(const v of rgb){hash^=v;hash=Math.imul(hash,16777619)>>>0;}}
 return {source:'webgl_readPixels',sample_count:samples,foreground_samples:foreground,unique_colors:colors.size,checksum:hash.toString(16).padStart(8,'0'),gl_error:gl.getError()};
}
function effectNativeState(){if(!spatialRenderer)throw Error('spatial_not_ready');
 const r=$('spatial-viewport').getBoundingClientRect(),gl=spatialRenderer.getContext();
 const bounds=node=>{const a=node.getBoundingClientRect();return {x:a.x,y:a.y,width:a.width,height:a.height};};
 const parts=plan.data.parts.map(p=>{const mesh=spatialMeshes.get(p.id),projected=mesh.position.clone().project(spatialCamera),marker=$('spatial-marker-'+p.id);
  return {id:p.id,is_mesh:mesh.isMesh===true,visible:mesh.visible,position:spatialVector(mesh.position),rotation:[mesh.rotation.x,mesh.rotation.y,mesh.rotation.z],
   scale:spatialVector(mesh.scale),matrix_world:mesh.matrixWorld.toArray(),geometry:spatialGeometryState(mesh.geometry),
   material:{type:mesh.material.type,side:mesh.material.side,opacity:mesh.material.opacity,
    clipping_planes:mesh.material.clippingPlanes.map(p=>({normal:spatialVector(p.normal),constant:p.constant}))},
   projection:{ndc:spatialVector(projected),pixel:[r.x+(projected.x+1)*r.width/2,r.y+(1-projected.y)*r.height/2]},
   marker:{text:marker.textContent,bounds:bounds(marker),font_size:parseFloat(getComputedStyle(marker).fontSize),opacity:Number(getComputedStyle(marker).opacity)}};});
 return {engine:'three',source:'actual_three_scene_and_webgl',time:spatialLastTime,
  renderer:{is_webgl_renderer:spatialRenderer.isWebGLRenderer===true,local_clipping_enabled:spatialRenderer.localClippingEnabled,
   width:gl.drawingBufferWidth,height:gl.drawingBufferHeight,context_lost:gl.isContextLost(),calls:spatialRenderer.info.render.calls,triangles:spatialRenderer.info.render.triangles},
  canvas_rect:{x:r.x,y:r.y,width:r.width,height:r.height},
  camera:{type:spatialCamera.type,position:spatialVector(spatialCamera.position),target:spatialCameraTarget,
   matrix_world:spatialCamera.matrixWorld.toArray(),matrix_world_inverse:spatialCamera.matrixWorldInverse.toArray(),
   projection_matrix:spatialCamera.projectionMatrix.toArray(),fov:spatialCamera.fov,aspect:spatialCamera.aspect,near:spatialCamera.near,far:spatialCamera.far},
  parts,caps:[...spatialCaps].map(([id,mesh])=>({id,visible:mesh.visible,position:spatialVector(mesh.position),matrix_world:mesh.matrixWorld.toArray(),geometry:spatialGeometryState(mesh.geometry)})),
  pixel_probe:spatialPixelProbe()};
}
'''
