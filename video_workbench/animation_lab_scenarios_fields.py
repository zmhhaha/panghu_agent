"""Bounded Canvas wave/field illustrations, not scientific simulation claims.

Every frame is reconstructed from its absolute time and fixed seed. The native
reviewer derives geometry independently of the JavaScript effectState summary,
then binds actual canvas call records to measured pixels and text metrics.
"""
from __future__ import annotations

from copy import deepcopy
import html
import json
import math


SCHEMA = 'video.animation-lab-scenario-plan.v1'
NATIVE_SCHEMA = 'video.animation-lab-fields-native.v1'
_BACKGROUND = '#0b1422'
_COLORS = dict(a='#61d9f2', b='#c3a0ff', sum='#7ce9b3', positive='#ff9d89', negative='#79baff',
               particle='#72e7c2', raw='#ffba86', filtered='#83cfff', guide='#274258')
_PHASES = ['先看对象与示意边界', '追踪第一个变化', '比较同一时刻的关系', '解释局部变化', '回看结论与限制']


def _brief(audience, goal, questions, entities, result, boundaries):
    return dict(audience=audience, goal=goal, questions=questions, required_entities=entities,
                expected_outcome=result, boundaries=boundaries)


def _seeded(seed, index):
    value = (seed + index * 2654435761) & 0xffffffff
    value ^= (value << 13) & 0xffffffff
    value ^= value >> 17
    value ^= (value << 5) & 0xffffffff
    return (value & 0xffffffff) / 4294967296


_RAW = [round(.62 * math.sin(i * math.pi / 12) + (_seeded(401, i) - .5) * .34, 6)
        for i in range(65)]
_FILTERED = [round(sum(_RAW[max(0, i-1):min(65, i+2)]) / len(_RAW[max(0, i-1):min(65, i+2)]), 6)
             for i in range(65)]


SCENARIOS = {
    'field-acoustics-interference': dict(module='fields', domain='education', domain_label='教育与声音科普',
        expression='wave-superposition', engine='canvas', difficulty='basic', duration_seconds=22,
        title='两列声波，为什么有时增强、有时抵消？',
        description='在同一横坐标比较两列示意波与逐点相加后的波，逐渐改变相位关系。',
        takeaway='相加的是同一位置的位移；反相时合成波减小，不表示两列波消失。',
        narrative='先分别看两列等幅的示意波。开始时它们同相，同一位置的位移相加，合成波更高。接着逐渐改变第二列波的相位。到反相时，一上一下在逐点相加后抵消，合成线接近中线。这里展示相位与叠加关系，不表示真实房间的声压测量。',
        requirement=_brief('学习声音叠加的初学者', '把同一位置的两列波与它们的合成结果联系起来',
            ['比较的是同一个位置吗？', '相位变化如何改变合成波？', '抵消是否意味着输入波消失？'],
            ['波 A', '波 B', '合成 A+B', '共同采样位置', '零位移中线'],
            '同相时合成幅度增强，反相时合成幅度下降，两个输入波仍可见',
            ['等幅单频信号均为示意，不对应真实声压单位', '不模拟房间反射、传播损耗或人耳响度', '不以此说明任意频率组合必然完全抵消']),
        tags=['wave', 'phase', 'same-position'], data=dict(seed=101, points=65, amplitude=32, cycles=2)),
    'field-electric-dipole': dict(module='fields', domain='science', domain_label='科学与物理示意',
        expression='vector-field', engine='canvas', difficulty='intermediate', duration_seconds=22,
        title='电场箭头，表示的是方向还是运动轨迹？',
        description='保留正负两个源，用有限网格的方向箭头解释局部场与对象轨迹的区别。',
        takeaway='箭头表示该处的示意方向；箭头不是某个粒子已经走过的轨迹。',
        narrative='先辨认正源与负源。随后在固定网格点逐步长出局部方向箭头。靠近正源和负源的位置，方向具有不同关系；把相邻位置的方向连起来理解整体。这里没有让粒子沿箭头运动，也没有给出真实场强刻度，箭头长短经过显示归一化。',
        requirement=_brief('刚接触场概念的观众', '区分固定位置的方向向量与实际粒子轨迹',
            ['源对象在哪里？', '箭头属于哪个固定位置？', '为什么不能把箭头当作粒子轨迹？'],
            ['正源', '负源', '固定网格点', '方向箭头', '归一化说明'],
            '箭头在固定锚点长出，正负源保留，画面明确没有计算粒子运动',
            ['只用有限二维点展示方向关系，不是电磁求解器', '向量长度经过归一化，不能读作真实场强', '不模拟边界介质、时间变化电磁场或测试电荷受力轨迹']),
        tags=['vector', 'fixed-anchor', 'normalized'], data=dict(seed=211, columns=6, rows=4,
            positive=[320, 220], negative=[820, 220])),
    'field-river-particles': dict(module='fields', domain='environment', domain_label='环境与河道示意',
        expression='particle-advection', engine='canvas', difficulty='boundary', duration_seconds=24,
        title='河道中的示意粒子，怎样持续追踪同一批对象？',
        description='用固定种子的十八个粒子说明平流、不同横向位置与边界循环，而非真实水动力计算。',
        takeaway='同一批十八个编号对象持续保留；出右边再从左边进入，是显示循环边界。',
        narrative='先看河道边界，再跟踪十八个示意粒子。不同横向位置采用不同的预设移动速度，因此位置逐渐错开。粒子到达右侧后从左侧再次出现，但身份没有改变。这是显示循环，不代表污染物凭空消失，也不是对真实河道水流或扩散的预测。',
        requirement=_brief('了解环境可视化的非专业观众', '保留粒子身份，解释预设平流和显示边界',
            ['移动前后还是同一批对象吗？', '不同速度如何影响相对位置？', '边界再次出现意味着什么？'],
            ['河道上边界', '河道下边界', '18个粒子', '编号01', '循环边界说明'],
            '十八个对象始终无增减，固定编号可持续定位，边界行为有明确解释',
            ['速度由示意公式预设，未求解流体方程', '不代表真实河道、污染物浓度或扩散预测', '循环边界仅用于展示，不能推断真实水体闭合']),
        tags=['particles', 'identity', 'wrap-boundary'], data=dict(seed=307, count=18, speed=38)),
    'field-sensor-waveform': dict(module='fields', domain='industry', domain_label='工业与传感器',
        expression='sampled-waveform', engine='canvas', difficulty='intermediate', duration_seconds=22,
        title='传感器曲线变平滑，就一定更接近真实吗？',
        description='比较固定的原始示意采样与三点均值，联动游标显示同一采样位置。',
        takeaway='平滑减少局部起伏，也改变细节；本例不能判定真实信号或设备故障。',
        narrative='上方是带预设扰动的原始示意采样，下方是三点均值后的结果。游标沿相同采样编号移动，帮助比较同一个位置。均值减少了局部起伏，同时也改变了峰值和细节。没有独立真值，就不能把更平滑直接当作更准确，更不能用这个示例诊断设备。',
        requirement=_brief('阅读设备趋势图的入门工程师', '用同一采样游标解释三点均值的效果与代价',
            ['上下两条曲线比较的是同一采样吗？', '平滑保留和改变了哪些细节？', '更平滑能证明更准确吗？'],
            ['65个原始采样', '三点均值曲线', '共同游标', '样本索引', '无独立真值说明'],
            '同索引标记上下对应，均值由真实相邻数据计算，结论保留精度边界',
            ['所有输入为固定种子的示意数据，不是设备实测', '端点均值只使用实际存在的相邻点', '不作故障诊断，不声称平滑后更接近未知真值']),
        tags=['samples', 'filter', 'same-index'], data=dict(seed=401, raw=_RAW, filtered=_FILTERED)),
}


def _validate(plan):
    if (type(plan) is not dict or type(plan.get('scenario_id')) is not str
            or plan['scenario_id'] not in SCENARIOS
            or plan != dict(schema=SCHEMA, scenario_id=plan['scenario_id'], **SCENARIOS[plan['scenario_id']])):
        raise ValueError('animation_lab_fields_plan_invalid')
    return plan


def _time(plan, time):
    _validate(plan)
    if type(time) not in (int, float) or not math.isfinite(time) or not 0 <= time <= plan['duration_seconds']:
        raise ValueError('animation_lab_fields_time_invalid')
    return float(time)


def _phase(time):
    return 0 if time < 1 else 1 if time < 6 else 2 if time < 11 else 3 if time < 16 else 4


def _focus(plan, time):
    key = plan['scenario_id']
    if key.endswith('interference'):
        return '相位差 '+str(math.floor(180 * min(1, max(0, (time-8)/8)) + .5))+'°'
    if key.endswith('dipole'):
        return '24个固定网格点 · 非运动轨迹'
    if key.endswith('particles'):
        return '18个固定身份 · 循环显示边界'
    return '共同采样索引 '+str(math.floor(min(1, time/plan['duration_seconds']) * 64 + .5))


def expected_observations(plan, time):
    time = _time(plan, time)
    return {'field-canvas': {'attributes': {'data-state': str(_phase(time)),
                                          'data-value': str(math.floor(time*1000+.5))}},
            'field-status': {'text': _PHASES[_phase(time)]},
            'field-focus': {'text': _focus(plan, time)},
            'field-note': {'text': '示意输入；不作真实测量、科学预测或设备诊断。'}}


def build_recipe(plan, variant):
    _validate(plan)
    if type(variant) is not str or variant not in {'baseline', 'candidate'}:
        raise ValueError('animation_lab_fields_variant_invalid')
    markup = '''<canvas id="field-canvas" width="1152" height="440" style="position:absolute;left:64px;top:150px;width:1152px;height:440px;border:1px solid #294055;border-radius:16px" aria-label="'''+html.escape(plan['title'], quote=True)+'''"></canvas>
<p id="field-status" style="position:absolute;left:64px;top:98px;width:720px;margin:0;font-size:21px;line-height:32px"></p>
<p id="field-focus" style="position:absolute;left:790px;top:103px;width:420px;margin:0;text-align:right;font-size:16px;line-height:26px;color:#8fdccf"></p>
<p id="field-note" style="position:absolute;left:64px;top:602px;width:1152px;margin:0;font-size:15px;line-height:26px;color:#9fb3ca">示意输入；不作真实测量、科学预测或设备诊断。</p>'''
    payload = dict(plan=plan, variant=variant, phases=_PHASES, colors=_COLORS)
    script = 'const fieldsData='+json.dumps(payload, ensure_ascii=True, separators=(',', ':'))+';\n'+_SCRIPT
    return dict(markup=markup, script=script,
        object_ids=['field-canvas', 'field-status', 'field-focus', 'field-note'],
        actions=[dict(target='field-canvas', kind='canvas-progress', start=start, duration=3) for start in (1, 6, 11, 16)])


_SCRIPT = r'''
const fieldsCanvas=$('field-canvas'),fieldsCtx=fieldsCanvas.getContext('2d',{alpha:false,willReadFrequently:true});
if(!fieldsCtx)throw Error('fields_canvas_context_unavailable');
const fieldsPlan=fieldsData.plan,fieldsVariant=fieldsData.variant,fieldsColors=fieldsData.colors;
let fieldsNative=null,fieldsSemantic=null,fieldsDraws=[],fieldsTexts=[];
const fieldsClamp=v=>Math.max(0,Math.min(1,v));
function fieldsSeed(index){let n=(fieldsPlan.data.seed+Math.imul(index,2654435761))>>>0;n^=n<<13;n^=n>>>17;n^=n<<5;return(n>>>0)/4294967296;}
function fieldsPath(id,points,color,width){fieldsCtx.beginPath();points.forEach((p,i)=>i?fieldsCtx.lineTo(p[0],p[1]):fieldsCtx.moveTo(p[0],p[1]));fieldsCtx.strokeStyle=color;fieldsCtx.lineWidth=width;fieldsCtx.stroke();fieldsDraws.push({kind:'path',id,points,stroke:color,width});}
function fieldsCircle(id,x,y,r,color){fieldsCtx.beginPath();fieldsCtx.arc(x,y,r,0,Math.PI*2);fieldsCtx.fillStyle=color;fieldsCtx.fill();fieldsDraws.push({kind:'circle',id,x,y,r,fill:color});}
function fieldsTriangle(id,points,color){fieldsCtx.beginPath();fieldsCtx.moveTo(...points[0]);fieldsCtx.lineTo(...points[1]);fieldsCtx.lineTo(...points[2]);fieldsCtx.closePath();fieldsCtx.fillStyle=color;fieldsCtx.fill();fieldsDraws.push({kind:'triangle',id,points,fill:color});}
function fieldsText(text,x,y,size=16,color='#bad5e9'){
 fieldsCtx.font='500 '+size+'px VideoNoto, sans-serif';fieldsCtx.textAlign='left';fieldsCtx.textBaseline='alphabetic';fieldsCtx.fillStyle=color;fieldsCtx.fillText(text,x,y);
 const m=fieldsCtx.measureText(text);fieldsTexts.push({text,font_size:size,opacity:1,bounds:{x:x-m.actualBoundingBoxLeft,y:y-m.actualBoundingBoxAscent,width:m.actualBoundingBoxLeft+m.actualBoundingBoxRight,height:m.actualBoundingBoxAscent+m.actualBoundingBoxDescent}});
}
function fieldsPhase(t){return t<1?0:t<6?1:t<11?2:t<16?3:4;}
function effectRender(time){
 const t=Math.max(0,Math.min(fieldsPlan.duration_seconds,time)),candidate=fieldsVariant==='candidate',width=candidate?3:2,radius=candidate?7:5;
 fieldsDraws=[];fieldsTexts=[];fieldsCtx.globalAlpha=1;fieldsCtx.fillStyle='#0b1422';fieldsCtx.fillRect(0,0,1152,440);
 fieldsDraws.push({kind:'rect',id:'background',x:0,y:0,width:1152,height:440,fill:'#0b1422'});
 let probes=[],strokeProbes=[],focus='',semantic={};
 if(fieldsPlan.scenario_id==='field-acoustics-interference'){
  const phase=Math.PI*fieldsClamp((t-8)/8),n=64,amplitude=fieldsPlan.data.amplitude;
  const waves=[[],[],[]],bases=[85,220,355];
  for(let i=0;i<=n;i++){const angle=2*Math.PI*(fieldsPlan.data.cycles*i/n-t*.12),a=amplitude*Math.sin(angle),b=amplitude*Math.sin(angle+phase),x=80+i*990/n;[a,b,a+b].forEach((v,j)=>waves[j].push([x,bases[j]-v]));}
  ['波 A','波 B','合成 A+B'].forEach((text,j)=>{fieldsPath('wave-zero-'+j,[[80,bases[j]],[1070,bases[j]]],fieldsColors.guide,1);fieldsText(text,22,bases[j]-45,16);const color=[fieldsColors.a,fieldsColors.b,fieldsColors.sum][j];fieldsPath('wave-'+j,waves[j],color,width);strokeProbes.push({id:'wave-stroke-'+j,x:Math.floor(waves[j][16][0]),y:Math.floor(waves[j][16][1])});});
  const index=32;waves.forEach((points,j)=>{const [x,y]=points[index],color=[fieldsColors.a,fieldsColors.b,fieldsColors.sum][j];fieldsCircle('wave-probe-'+j,x,y,radius,color);probes.push({id:'wave-probe-'+j,x:Math.round(x),y:Math.round(y+radius-2),expected:color});});
  focus='相位差 '+Math.round(180*fieldsClamp((t-8)/8))+'°';semantic={phase_radians:phase,inputs_visible:2,comparison_index:index};
 }else if(fieldsPlan.scenario_id==='field-electric-dipole'){
  const grow=fieldsClamp((t-1)/5),sources=[fieldsPlan.data.positive,fieldsPlan.data.negative];
  fieldsText('固定位置的方向向量 · 长度归一化 · 未计算粒子轨迹',22,30,16);
  for(let row=0;row<4;row++)for(let col=0;col<6;col++){
   const x=150+col*164,y=75+row*96;let ex=0,ey=0;
   sources.forEach(([sx,sy],i)=>{const dx=x-sx,dy=y-sy,d=Math.hypot(dx,dy);ex+=(i?-1:1)*dx/(d*d*d);ey+=(i?-1:1)*dy/(d*d*d);});
   if(grow>0){const d=Math.hypot(ex,ey),ux=ex/d,uy=ey/d,len=28*grow,tip=[x+ux*len,y+uy*len],color=fieldsColors.a;
    fieldsPath('vector-'+row+'-'+col,[[x,y],tip],color,width);const h=7*grow,w=4*grow;
    fieldsTriangle('vector-head-'+row+'-'+col,[tip,[tip[0]-ux*h-uy*w,tip[1]-uy*h+ux*w],[tip[0]-ux*h+uy*w,tip[1]-uy*h-ux*w]],color);
    fieldsCircle('vector-anchor-'+row+'-'+col,x,y,3,color);
    if(row===0&&col===0)probes.push({id:'vector-anchor-0-0',x,y,expected:color});
    if(row===0&&col===0&&grow>=.4)strokeProbes.push({id:'vector-stroke-0-0',x:Math.floor((x+tip[0])/2),y:Math.floor((y+tip[1])/2)});
   }
  }
  sources.forEach(([x,y],i)=>{const color=i?fieldsColors.negative:fieldsColors.positive;fieldsCircle('charge-'+i,x,y,18,color);fieldsText(i?'−':'+',x-7,y+6,22,'#0b1422');probes.push({id:'charge-'+i,x,y:y+13,expected:color});});
  focus='24个固定网格点 · 非运动轨迹';semantic={grid_points:24,vector_growth:grow,particle_trajectories:0};
 }else if(fieldsPlan.scenario_id==='field-river-particles'){
  fieldsPath('river-top',[[35,105],[1117,105]],fieldsColors.guide,2);fieldsPath('river-bottom',[[35,355],[1117,355]],fieldsColors.guide,2);
  strokeProbes.push({id:'river-top',x:400,y:105},{id:'river-bottom',x:400,y:355});
  fieldsText('左侧重新进入是显示循环，不表示真实河道闭合',22,33,16);fieldsText('固定对象 18 / 18',22,411,16);
  const particles=[];
  for(let i=0;i<18;i++){const lane=fieldsSeed(i),start=fieldsSeed(i+100),speed=fieldsPlan.data.speed*(.65+lane*.7),x=45+((start*1060+t*speed)%1060),y=133+lane*190+Math.sin(t*.6+i)*8;
   fieldsCircle('particle-'+i,x,y,i===0?radius+2:5.5,fieldsColors.particle);particles.push({id:i,x,y});if(i<3)probes.push({id:'particle-'+i,x:Math.round(x),y:Math.round(y),expected:fieldsColors.particle});
   if(i===0)fieldsText('01',x-9,y-16,14,'#d8fff0');
  }
  focus='18个固定身份 · 循环显示边界';semantic={particle_count:particles.length,tracked_id:0,closed_physical_system:false};
 }else{
  const index=Math.round(fieldsClamp(t/fieldsPlan.duration_seconds)*64),bases=[130,320],arrays=[fieldsPlan.data.raw,fieldsPlan.data.filtered];
  const waves=arrays.map((values,j)=>values.map((v,i)=>[80+i*990/64,bases[j]-v*66]));
  ['原始示意采样','三点均值（端点按可用邻点）'].forEach((text,j)=>{fieldsText(text,22,bases[j]-76,16);fieldsPath('sensor-zero-'+j,[[80,bases[j]],[1070,bases[j]]],fieldsColors.guide,1);fieldsPath('sensor-wave-'+j,waves[j],j?fieldsColors.filtered:fieldsColors.raw,width);strokeProbes.push({id:'sensor-stroke-'+j,x:Math.floor(waves[j][16][0]),y:Math.floor(waves[j][16][1])});});
  fieldsPath('sensor-cursor',[[waves[0][index][0],58],[waves[0][index][0],385]],fieldsColors.guide,1);
  waves.forEach((points,j)=>{const [x,y]=points[index],color=j?fieldsColors.filtered:fieldsColors.raw;fieldsCircle('sensor-probe-'+j,x,y,radius,color);probes.push({id:'sensor-probe-'+j,x:Math.round(x),y:Math.round(y+radius-2),expected:color});});
  fieldsText('平滑不等于更准确；未提供独立真值',22,419,16);focus='共同采样索引 '+index;semantic={index,raw:arrays[0][index],filtered:arrays[1][index],has_ground_truth:false};
 }
 attrs('field-canvas',{'data-state':fieldsPhase(t),'data-value':Math.round(t*1000)});label('field-status',fieldsData.phases[fieldsPhase(t)]);label('field-focus',focus);
 fieldsNative={schema:'video.animation-lab-fields-native.v1',engine:'canvas',context:'2d',variant:fieldsVariant,time:t,seed:fieldsPlan.data.seed,width:1152,height:440,
  draws:fieldsDraws,text_boxes:fieldsTexts,pixels:[{id:'background-probe',x:3,y:3,rgba:Array.from(fieldsCtx.getImageData(3,3,1,1).data)},...probes.map(p=>({id:p.id,x:p.x,y:p.y,rgba:Array.from(fieldsCtx.getImageData(p.x,p.y,1,1).data)}))],
  stroke_samples:strokeProbes.map(p=>({id:p.id,x:p.x,y:p.y,rgba_samples:Array.from({length:9},(_,i)=>Array.from(fieldsCtx.getImageData(p.x+i%3-1,p.y+Math.floor(i/3)-1,1,1).data))}))};
 fieldsSemantic={scenario:fieldsPlan.scenario_id,phase:fieldsPhase(t),...semantic};
}
function effectState(){return JSON.parse(JSON.stringify(fieldsSemantic));}
function effectNativeState(){return JSON.parse(JSON.stringify(fieldsNative));}
'''


def _scene(plan, time, variant):
    """Independent Python geometry oracle; never reads JavaScript summaries."""
    width, radius = (3, 7) if variant == 'candidate' else (2, 5)
    draws = [dict(kind='rect', id='background', x=0, y=0, width=1152, height=440, fill=_BACKGROUND)]
    probes = [dict(id='background-probe', x=3, y=3, color=_BACKGROUND)]
    texts, stroke_probes = [], []

    def path(identity, points, color, line_width):
        draws.append(dict(kind='path', id=identity, points=points, stroke=color, width=line_width))

    def circle(identity, x, y, r, color):
        draws.append(dict(kind='circle', id=identity, x=x, y=y, r=r, fill=color))

    def probe(identity, x, y, color):
        probes.append(dict(id=identity, x=math.floor(x+.5), y=math.floor(y+.5), color=color))

    key, data = plan['scenario_id'], plan['data']
    if key.endswith('interference'):
        phase = math.pi * min(1, max(0, (time-8)/8))
        waves = [[], [], []]
        bases = (85, 220, 355)
        for i in range(65):
            angle = 2*math.pi*(data['cycles']*i/64-time*.12)
            values = (data['amplitude']*math.sin(angle), data['amplitude']*math.sin(angle+phase))
            for j, value in enumerate((*values, sum(values))):
                waves[j].append([80+i*990/64, bases[j]-value])
        for j, (name, points, color) in enumerate(zip(('波 A', '波 B', '合成 A+B'), waves,
                                                    (_COLORS['a'], _COLORS['b'], _COLORS['sum']))):
            path('wave-zero-'+str(j), [[80, bases[j]], [1070, bases[j]]], _COLORS['guide'], 1)
            texts.append(name)
            path('wave-'+str(j), points, color, width)
            stroke_probes.append(dict(id='wave-stroke-'+str(j), x=math.floor(points[16][0]),
                                     y=math.floor(points[16][1]), color=color))
        for j, (points, color) in enumerate(zip(waves, (_COLORS['a'], _COLORS['b'], _COLORS['sum']))):
            x, y = points[32]
            circle('wave-probe-'+str(j), x, y, radius, color)
            probe('wave-probe-'+str(j), x, y+radius-2, color)
    elif key.endswith('dipole'):
        grow = min(1, max(0, (time-1)/5))
        texts.append('固定位置的方向向量 · 长度归一化 · 未计算粒子轨迹')
        for row in range(4):
            for col in range(6):
                x, y = 150+col*164, 75+row*96
                ex = ey = 0
                for sign, (sx, sy) in zip((1, -1), (data['positive'], data['negative'])):
                    dx, dy = x-sx, y-sy
                    distance = math.hypot(dx, dy)
                    ex += sign*dx/distance**3
                    ey += sign*dy/distance**3
                if grow > 0:
                    distance = math.hypot(ex, ey)
                    ux, uy = ex/distance, ey/distance
                    tip = [x+ux*28*grow, y+uy*28*grow]
                    h, w, color = 7*grow, 4*grow, _COLORS['a']
                    path(f'vector-{row}-{col}', [[x, y], tip], color, width)
                    draws.append(dict(kind='triangle', id=f'vector-head-{row}-{col}', points=[tip,
                        [tip[0]-ux*h-uy*w, tip[1]-uy*h+ux*w], [tip[0]-ux*h+uy*w, tip[1]-uy*h-ux*w]], fill=color))
                    circle(f'vector-anchor-{row}-{col}', x, y, 3, color)
                    if row == col == 0:
                        probe('vector-anchor-0-0', x, y, color)
                        if grow >= .4:
                            stroke_probes.append(dict(id='vector-stroke-0-0', x=math.floor((x+tip[0])/2),
                                                     y=math.floor((y+tip[1])/2), color=color))
        for i, (x, y) in enumerate((data['positive'], data['negative'])):
            color = _COLORS['negative' if i else 'positive']
            circle('charge-'+str(i), x, y, 18, color)
            texts.append('−' if i else '+')
            probe('charge-'+str(i), x, y+13, color)
    elif key.endswith('particles'):
        path('river-top', [[35, 105], [1117, 105]], _COLORS['guide'], 2)
        path('river-bottom', [[35, 355], [1117, 355]], _COLORS['guide'], 2)
        stroke_probes.extend(dict(id=identity, x=400, y=y, color=_COLORS['guide'])
                             for identity, y in (('river-top', 105), ('river-bottom', 355)))
        texts.extend(('左侧重新进入是显示循环，不表示真实河道闭合', '固定对象 18 / 18'))
        for i in range(data['count']):
            lane, start = _seeded(data['seed'], i), _seeded(data['seed'], i+100)
            speed = data['speed']*(.65+lane*.7)
            x, y = 45+(start*1060+time*speed)%1060, 133+lane*190+math.sin(time*.6+i)*8
            circle('particle-'+str(i), x, y, radius+2 if i == 0 else 5.5, _COLORS['particle'])
            if i < 3:
                probe('particle-'+str(i), x, y, _COLORS['particle'])
            if i == 0:
                texts.append('01')
    else:
        index = math.floor(min(1, time/plan['duration_seconds'])*64+.5)
        bases, waves = (130, 320), []
        for j, (name, values, color) in enumerate(zip(('原始示意采样', '三点均值（端点按可用邻点）'),
                                                     (data['raw'], data['filtered']), (_COLORS['raw'], _COLORS['filtered']))):
            points = [[80+i*990/64, bases[j]-value*66] for i, value in enumerate(values)]
            waves.append(points)
            texts.append(name)
            path('sensor-zero-'+str(j), [[80, bases[j]], [1070, bases[j]]], _COLORS['guide'], 1)
            path('sensor-wave-'+str(j), points, color, width)
            stroke_probes.append(dict(id='sensor-stroke-'+str(j), x=math.floor(points[16][0]),
                                     y=math.floor(points[16][1]), color=color))
        path('sensor-cursor', [[waves[0][index][0], 58], [waves[0][index][0], 385]], _COLORS['guide'], 1)
        for j, (points, color) in enumerate(zip(waves, (_COLORS['raw'], _COLORS['filtered']))):
            x, y = points[index]
            circle('sensor-probe-'+str(j), x, y, radius, color)
            probe('sensor-probe-'+str(j), x, y+radius-2, color)
        texts.append('平滑不等于更准确；未提供独立真值')
    return draws, probes, texts, stroke_probes


def review_native_observation(plan, time, variant, native_state):
    """Verify actual canvas evidence; do not trust scientific effectState claims."""
    time = _time(plan, time)
    if type(variant) is not str or variant not in {'baseline', 'candidate'}:
        raise ValueError('animation_lab_fields_variant_invalid')

    def require(value, code='animation_lab_fields_native_invalid'):
        if not value:
            raise ValueError(code)

    def near(actual, wanted):
        require(type(actual) in (int, float) and math.isfinite(actual) and abs(actual-wanted) <= 1e-6,
                'animation_lab_fields_geometry_mismatch')

    require(type(native_state) is dict)
    require(native_state.get('schema') == NATIVE_SCHEMA and native_state.get('engine') == 'canvas'
            and native_state.get('context') == '2d' and native_state.get('variant') == variant
            and native_state.get('seed') == plan['data']['seed']
            and native_state.get('width') == 1152 and native_state.get('height') == 440)
    near(native_state.get('time'), time)
    # The native HyperFrames seek retains the requested fractional frame, while
    # GSAP's tweened numeric clock can round that value to six decimal places.
    # First enforce the existing one-microsecond clock binding, then reconstruct
    # geometry at the measured paint time. Keep the coordinate/pixel gates strict;
    # expanding their tolerance would hide actual drawing errors instead.
    expected, probes, texts, stroke_probes = _scene(plan, native_state['time'], variant)
    actual = native_state.get('draws')
    require(type(actual) is list and len(actual) == len(expected), 'animation_lab_fields_draws_missing')
    for recorded, wanted in zip(actual, expected):
        require(type(recorded) is dict and set(recorded) == set(wanted), 'animation_lab_fields_draws_missing')
        for key, value in wanted.items():
            if type(value) in (int, float):
                near(recorded[key], value)
            elif key == 'points':
                points = recorded[key]
                require(type(points) is list and len(points) == len(value), 'animation_lab_fields_geometry_mismatch')
                for point, target in zip(points, value):
                    require(type(point) is list and len(point) == 2, 'animation_lab_fields_geometry_mismatch')
                    near(point[0], target[0]); near(point[1], target[1])
            else:
                require(recorded[key] == value, 'animation_lab_fields_draw_style_mismatch')
    pixels = native_state.get('pixels')
    require(type(pixels) is list and len(pixels) == len(probes), 'animation_lab_fields_pixels_missing')
    for pixel, probe in zip(pixels, probes):
        require(type(pixel) is dict and set(pixel) == {'id', 'x', 'y', 'rgba'}
                and all(pixel.get(key) == probe[key] for key in ('id', 'x', 'y')),
                'animation_lab_fields_pixels_missing')
        rgba = pixel['rgba']
        require(type(rgba) is list and len(rgba) == 4 and all(type(v) is int and 0 <= v <= 255 for v in rgba),
                'animation_lab_fields_pixels_invalid')
        color = [int(probe['color'][i:i+2], 16) for i in (1, 3, 5)]+[255]
        require(all(abs(a-b) <= 2 for a, b in zip(rgba, color)), 'animation_lab_fields_pixel_mismatch')
    strokes = native_state.get('stroke_samples')
    require(type(strokes) is list and len(strokes) == len(stroke_probes), 'animation_lab_fields_stroke_pixels_missing')
    for recorded, wanted in zip(strokes, stroke_probes):
        require(type(recorded) is dict and set(recorded) == {'id', 'x', 'y', 'rgba_samples'}
                and all(recorded.get(key) == wanted[key] for key in ('id', 'x', 'y')),
                'animation_lab_fields_stroke_pixels_missing')
        samples = recorded['rgba_samples']
        require(type(samples) is list and len(samples) == 9 and all(type(v) is list and len(v) == 4
            and all(type(channel) is int and 0 <= channel <= 255 for channel in v) for v in samples),
            'animation_lab_fields_pixels_invalid')
        color = [int(wanted['color'][i:i+2], 16) for i in (1, 3, 5)]+[255]
        # Nine actual neighboring pixels accommodate normal stroke rasterization
        # without accepting a background-only canvas or relying on call records.
        background = [int(_BACKGROUND[i:i+2], 16) for i in (1, 3, 5)]+[255]
        tolerance = min(40, max(abs(a-b) for a, b in zip(background, color))*.3)
        require(any(max(abs(a-b) for a, b in zip(value, color)) <= tolerance for value in samples),
                'animation_lab_fields_stroke_pixel_mismatch')
    measured = native_state.get('text_boxes')
    require(type(measured) is list and [row.get('text') for row in measured if type(row) is dict] == texts,
            'animation_lab_fields_text_missing')
    for row in measured:
        require(type(row.get('font_size')) in (int, float) and math.isfinite(row['font_size'])
                and row['font_size'] >= 14 and row.get('opacity') == 1,
                'animation_lab_fields_text_invalid')
        box = row.get('bounds')
        require(type(box) is dict and set(box) == {'x', 'y', 'width', 'height'}, 'animation_lab_fields_text_invalid')
        require(all(type(v) in (int, float) and math.isfinite(v) for v in box.values()),
                'animation_lab_fields_text_invalid')
        require(0 <= box['x'] < 1152 and 0 <= box['y'] < 440 and box['width'] > 0 and box['height'] > 0
                and box['x']+box['width'] <= 1152 and box['y']+box['height'] <= 440,
                'animation_lab_fields_text_outside_canvas')
    return dict(technical_status='passed', checked_draws=len(expected), checked_pixels=len(probes),
                checked_stroke_pixels=len(stroke_probes)*9, checked_text_boxes=len(measured),
                scientific_validity='illustrative_not_certified')


def verify_native_observation(plan, time, variant, native_state):
    """Return the common positive integer count of independently checked facts."""
    report = review_native_observation(plan, time, variant, native_state)
    return sum(report[key] for key in ('checked_draws', 'checked_pixels', 'checked_stroke_pixels', 'checked_text_boxes'))
