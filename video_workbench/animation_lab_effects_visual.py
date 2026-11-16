"""Fixed visual explanation recipes for the local animation effect library.

The same content is used in both presentations. Every rendered attribute is
derived from the absolute clock; no frame history, network or model is used.
"""
from __future__ import annotations

from copy import deepcopy
import html
import json


RECIPES = {
    'architecture-expand': dict(module='diagram', title='复杂架构：先看整体，再拆开一层',
        takeaway='展开的是局部职责，保留的是它在整体中的位置。',
        labels=dict(overview='完整服务', entry='请求入口', orchestration='任务编排', storage='数据存储',
                    expanded='展开任务编排', queue='排队', execute='执行', result='汇总结果'),
        description='同一架构从总览展开任务编排，再回到完整服务；比较层次和焦点保持。',
        checks=['展开内容与原模块是否对应', '局部展开时能否找到整体位置', '回到总览是否自然']),
    'focus-shift': dict(module='diagram', title='视觉焦点：跟着解释逐项看清楚',
        takeaway='逐项强调，最后把各环节重新连成整体。',
        labels=dict(entry='请求进入', queue='排队等待', worker='执行处理',
                    entry_detail='先接住输入', queue_detail='协调处理顺序', worker_detail='交付处理结果'),
        description='三个环节依次聚焦和局部放大，保留周围上下文，最后恢复整体。',
        checks=['焦点是否清楚', '放大是否遮挡其他对象', '焦点移动和恢复是否平稳']),
    'data-compare': dict(module='diagram', title='数据对比：时间花在哪里，优化改了什么？',
        takeaway='总耗时从 400 毫秒降至 240 毫秒，减少了 40%。',
        labels=dict(before='优化前', after='优化后', queue='等待', work='处理', total='总耗时',
                    trend='连续请求的响应时间', unit='毫秒'),
        description='柱形、响应曲线和数值使用同一组数据，分阶段解释优化效果。',
        checks=['柱形与数值是否一致', '两组数据是否易比较', '趋势与结论是否讲同一件事']),
    'svg-morph': dict(module='diagram', title='形态变化：对象不换，含义逐步展开',
        takeaway='用连续的轮廓变化，说明同一对象经历的三个状态。',
        labels=dict(start='原始数据', middle='结构化结果', end='验证通过'),
        description='真实 SVG 路径描边与相同命令结构的形变；保持对象身份并显示阶段含义。',
        checks=['描边是否完整', '路径变形是否连续', '倒序拖动后轮廓和状态能否恢复']),
}


def _e(value):
    return html.escape(value, quote=True)


def _action(target, start, duration, kind='move'):
    return dict(target=target, start=start, duration=duration, kind=kind)


def _state(effect, identities, phase):
    # Reading actual SVG attributes makes this supplementary semantic record
    # independent from the last requested time. The host also observes the DOM.
    return ('const visualObjectIds='+json.dumps(identities)+';\n'
        'function effectState(){return {effect:'+json.dumps(effect)+',phase:$('+json.dumps(phase)+').textContent,'
        'objects:visualObjectIds.map(id=>{const n=$(id);return {id,tag:n.tagName.toLowerCase(),'
        "transform:n.getAttribute('transform')||'',opacity:Number(n.getAttribute('opacity')??1),"
        "x:n.getAttribute('x'),y:n.getAttribute('y'),width:n.getAttribute('width'),height:n.getAttribute('height'),"
        "path:n.getAttribute('d'),fill:n.getAttribute('fill'),stroke:n.getAttribute('stroke'),"
        "dash:n.getAttribute('stroke-dashoffset'),text:n.textContent};})};}\n")


def _architecture(plan):
    l = plan['labels']
    identities = ['arch-overview', 'arch-focus', 'arch-detail', 'arch-worker-signal', 'arch-route', 'arch-summary']
    markup = f'''
<g id="arch-overview"><rect x="84" y="162" width="320" height="382" rx="22" fill="#132238" stroke="#3b536b"/>
<text x="112" y="204" fill="#edf5ff" font-size="23">{_e(l['overview'])}</text>
<rect x="112" y="230" width="264" height="74" rx="12" fill="#1d354a"/><text x="134" y="275" fill="#dbeaf9" font-size="21">{_e(l['entry'])}</text>
<rect x="112" y="322" width="264" height="104" rx="12" fill="#1e3e4d"/><text x="134" y="368" fill="#b1f1df" font-size="22">{_e(l['orchestration'])}</text><text x="134" y="398" fill="#9cbbcd" font-size="14">一层里，还有几个协作环节</text>
<rect x="112" y="444" width="264" height="72" rx="12" fill="#253148"/><text x="134" y="488" fill="#dbeaf9" font-size="21">{_e(l['storage'])}</text></g>
<rect id="arch-focus" x="106" y="316" width="276" height="116" rx="16" fill="none" stroke="#8fe5c8" stroke-width="3" opacity="0"/>
<path id="arch-route" d="M394 372 C436 372 438 280 478 280" pathLength="1" fill="none" stroke="#81d9c3" stroke-width="3" stroke-dasharray="1" stroke-dashoffset="1"/>
<g id="arch-detail" opacity="0"><rect x="482" y="162" width="710" height="382" rx="22" fill="#122a36" stroke="#4a837d"/>
<text x="514" y="208" fill="#b2f1de" font-size="24">{_e(l['expanded'])}</text><text x="514" y="241" fill="#9fbecb" font-size="15">同一个模块，在这里拆开解释</text>
<rect x="514" y="286" width="180" height="124" rx="16" fill="#1b3d4c"/><text x="541" y="334" fill="#ecf7fc" font-size="22">{_e(l['queue'])}</text><text x="541" y="370" fill="#a2c4d2" font-size="15">先后有序</text>
<path d="M708 346 C727 336 737 356 756 346" fill="none" stroke="#88cfc8" stroke-width="3"/>
<rect x="768" y="286" width="180" height="124" rx="16" fill="#243e59"/><text x="795" y="334" fill="#ecf7fc" font-size="22">{_e(l['execute'])}</text><text x="795" y="370" fill="#a2c4d2" font-size="15">分工处理</text>
<path d="M962 346 C981 336 991 356 1010 346" fill="none" stroke="#88cfc8" stroke-width="3"/>
<rect x="1022" y="286" width="138" height="124" rx="16" fill="#304555"/><text x="1039" y="338" fill="#ecf7fc" font-size="19">{_e(l['result'])}</text><text x="1039" y="370" fill="#a2c4d2" font-size="14">交付结果</text>
<rect x="514" y="456" width="646" height="36" rx="12" fill="#0d1d2b"/><rect id="arch-worker-signal" x="514" y="456" width="18" height="36" rx="12" fill="#76cbb5"/>
<text x="514" y="523" fill="#9fbecb" font-size="14">编排负责协调，执行负责把工作做完</text></g>
<g id="arch-summary" opacity="0"><rect x="84" y="568" width="1108" height="48" rx="12" fill="#173d3c"/><text id="arch-phase" x="108" y="600" fill="#bcefdc" font-size="19">从整体定位任务编排</text></g>'''
    script = _state('architecture-expand', identities, 'arch-phase') + '''
function effectRender(t){
 const ease=p=>variant==='candidate'?smooth(p):p;
 const focus=ease(progress(t,1,2)), expand=ease(progress(t,4,3)), run=ease(progress(t,8,3)), back=ease(progress(t,14,2));
 fade('arch-focus',focus*(1-.72*back));
 attrs('arch-focus',{x:106-4*focus,y:316-2*focus,width:276+8*focus,height:116+4*focus,stroke:variant==='candidate'?'#9af1d2':'#76c7ba'});
 fade('arch-overview',1-(variant==='candidate'?.26:.1)*expand*(1-back));
 fade('arch-detail',expand*(1-.72*back));
 attrs('arch-detail',{transform:'translate('+(-34*(1-expand)+16*back)+' '+(12*(1-expand)+8*back)+')'});
 attrs('arch-route',{'stroke-dashoffset':1-expand,opacity:expand*(1-.78*back)});
 attrs('arch-worker-signal',{width:18+628*run,fill:variant==='candidate'?'#8ce0c2':'#76cbb5'});
 fade('arch-summary',focus);
 label('arch-phase',back>.5?'回到整体：局部职责，仍属于同一个完整服务':run>.1?'任务在编排内部依次协作':expand>.2?'展开局部，同时保留它在整体中的位置':'从整体定位任务编排');
}'''
    actions = [_action('arch-focus', 1, 2, 'reveal'), _action('arch-detail', 4, 3, 'reveal'),
               _action('arch-worker-signal', 8, 3), _action('arch-detail', 14, 2)]
    return dict(markup=markup, script=script, object_ids=identities, actions=actions)


def _focus(plan):
    l = plan['labels']
    identities = ['focus-entry', 'focus-queue', 'focus-worker', 'focus-step']
    cards = []
    for index, (key, x, color) in enumerate([('entry', 86, '#75ceb8'), ('queue', 480, '#77aee2'), ('worker', 874, '#c7a2df')]):
        cards.append(f'''<g id="focus-{key}"><rect x="{x}" y="230" width="310" height="230" rx="22" fill="#182b40" stroke="{color}" stroke-width="2"/>
<text x="{x+24}" y="270" fill="{color}" font-size="15">0{index+1}</text><text x="{x+24}" y="325" fill="#eef6ff" font-size="27">{_e(l[key])}</text>
<rect x="{x+24}" y="351" width="50" height="5" rx="2" fill="{color}"/><text x="{x+24}" y="405" fill="#afc4d8" font-size="19">{_e(l[key+'_detail'])}</text></g>''')
    markup = '''<g fill="#9fb8cb" font-size="15"><text x="86" y="173">一次解释一件事，上下文始终留在画面里</text></g>'''+''.join(cards)+'''
<path id="focus-route-entry" d="M414 347 C433 337 442 357 461 347" stroke="#5b7a91" stroke-width="3" fill="none" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1" opacity="0"/>
<path id="focus-route-worker" d="M808 347 C827 337 836 357 855 347" stroke="#5b7a91" stroke-width="3" fill="none" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1" opacity="0"/>
<g id="focus-step"><rect x="86" y="526" width="1098" height="68" rx="16" fill="#132c36"/><text id="focus-phase" x="112" y="570" fill="#b8e8df" font-size="21">先看三个环节如何连起来</text></g>'''
    script = _state('focus-shift', identities, 'focus-phase') + '''
function effectRender(t){
 const ease=p=>variant==='candidate'?smooth(p):p;
 const a=ease(progress(t,1,2)), b=ease(progress(t,5,2)), c=ease(progress(t,9,2)), end=ease(progress(t,14,2));
 const weights=[a*(1-b),b*(1-c),c*(1-end)], any=Math.max(...weights);
 const ids=['focus-entry','focus-queue','focus-worker'], centers=[241,635,1029];
 for(let i=0;i<ids.length;i++){
  const w=weights[i],scale=1+(variant==='candidate'?.08:.025)*w;
  attrs(ids[i],{transform:'translate('+centers[i]+' 345) scale('+scale+') translate('+(-centers[i])+' -345)',opacity:variant==='candidate'?1-.46*any+.46*w:.88+.12*w});
 }
 attrs('focus-step',{transform:'translate(0 '+(-5*any)+')'});
 attrs('focus-route-entry',{'stroke-dashoffset':1-b,opacity:b});
 attrs('focus-route-worker',{'stroke-dashoffset':1-c,opacity:c});
 label('focus-phase',end>.5?'把焦点收回：三个环节共同完成请求':weights[2]>.1?'执行处理：把工作转化为结果':weights[1]>.1?'排队等待：协调处理顺序':weights[0]>.1?'请求进入：先接住输入':'先看三个环节如何连起来');
}'''
    return dict(markup=markup, script=script, object_ids=identities,
        actions=[_action('focus-entry',1,2),_action('focus-queue',5,2),_action('focus-worker',9,2),_action('focus-worker',14,2)])


def _data(plan):
    l = plan['labels']
    identities = ['data-before', 'data-after', 'data-trend', 'data-result']
    bars = {'before': [], 'after': []}
    categories = [('queue', 160), ('work', 380), ('total', 600)]
    for key, x in categories:
        for series, offset, color in [('before', 0, '#8b9cb3'), ('after', 70, '#77d3bc')]:
            bars[series].append(f'<rect id="data-{series}-{key}" x="{x+offset}" y="499.5" width="54" height="0.5" rx="6" fill="{color}"/>'
                f'<text id="data-{series}-{key}-value" x="{x+offset+27}" y="486" text-anchor="middle" fill="{color}" font-size="18">0</text>')
    markup = f'''
<text x="92" y="166" fill="#d9e7f4" font-size="21">耗时分布 <tspan fill="#839eb6" font-size="14">/ {_e(l['unit'])}</tspan></text>
<rect x="92" y="188" width="14" height="14" rx="3" fill="#8b9cb3"/><text x="115" y="201" fill="#b6c6d8" font-size="15">{_e(l['before'])}</text>
<rect x="218" y="188" width="14" height="14" rx="3" fill="#77d3bc"/><text x="241" y="201" fill="#b6c6d8" font-size="15">{_e(l['after'])}</text>
<path d="M104 248 L104 500 L766 500" fill="none" stroke="#3b5268" stroke-width="2"/>
<text x="83" y="506" fill="#809bb2" font-size="14">0</text><text x="66" y="259" fill="#809bb2" font-size="14">400</text>
<g id="data-before" opacity="0">{''.join(bars['before'])}</g><g id="data-after" opacity="0">{''.join(bars['after'])}</g>
<text x="222" y="536" text-anchor="middle" fill="#bacbda" font-size="19">{_e(l['queue'])}</text><text x="442" y="536" text-anchor="middle" fill="#bacbda" font-size="19">{_e(l['work'])}</text><text x="662" y="536" text-anchor="middle" fill="#bacbda" font-size="19">{_e(l['total'])}</text>
<rect x="812" y="160" width="386" height="372" rx="20" fill="#13263a" stroke="#304a62"/><text x="838" y="204" fill="#d9e7f4" font-size="20">{_e(l['trend'])}</text>
<path d="M840 256 L840 465 L1166 465" stroke="#3b5268" stroke-width="2" fill="none"/>
<path id="data-trend-before" d="M854 282 C895 286 906 294 932 287 S995 278 1010 287 S1086 290 1150 282" fill="none" stroke="#8b9cb3" stroke-width="3" opacity="0"/>
<path id="data-trend" d="M854 282 C887 293.9 901 310.6 932 316.86 S990 335.7 1010 339.1 S1090 351.7 1150 355.2" fill="none" stroke="#77d3bc" stroke-width="4" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1"/>
<text x="848" y="494" fill="#89a5bb" font-size="14">连续请求 →</text><text x="1090" y="242" fill="#89a5bb" font-size="14">毫秒</text>
<g id="data-result" opacity="0"><rect x="92" y="562" width="1106" height="54" rx="14" fill="#173d3b"/><text id="data-phase" x="116" y="598" fill="#baf0dc" font-size="22">总耗时：400 → 240 毫秒，减少 40%</text></g>'''
    script = _state('data-compare', identities, 'data-phase') + '''
function effectRender(t){
 const ease=p=>variant==='candidate'?smooth(p):p;
 const old=ease(progress(t,1,3)), next=ease(progress(t,5,3)), trend=ease(progress(t,9,3)), result=ease(progress(t,13,2));
 const values={before:[120,280,400],after:[70,170,240]}, amounts={before:old,after:next}, keys=['queue','work','total'];
 for(const series of ['before','after']){
  fade('data-'+series,amounts[series]);
  for(let i=0;i<keys.length;i++){
   const value=Math.round(values[series][i]*amounts[series]),height=Math.max(.5,value*.605);
   attrs('data-'+series+'-'+keys[i],{y:500-height,height,stroke:variant==='candidate'&&i===2&&series==='after'?'#c7ffe8':'none','stroke-width':2});
   attrs('data-'+series+'-'+keys[i]+'-value',{y:486-height});label('data-'+series+'-'+keys[i]+'-value',String(value));
  }
 }
 attrs('data-trend',{'stroke-dashoffset':1-trend,stroke:variant==='candidate'?'#8ef0cb':'#77d3bc'});
 fade('data-trend-before',.72*trend);
 fade('data-result',result);attrs('data-result',{transform:'translate(0 '+(8*(1-result))+')'});
 label('data-phase','总耗时：400 → 240 毫秒，减少 40%');
}'''
    return dict(markup=markup, script=script, object_ids=identities,
        actions=[_action('data-before',1,3,'reveal'),_action('data-after',5,3,'reveal'),
                 _action('data-trend',9,3,'draw'),_action('data-result',13,2,'reveal')])


def _morph(plan):
    l = plan['labels']
    identities = ['morph-body', 'morph-stage-start', 'morph-stage-middle', 'morph-stage-end', 'morph-result']
    markup = f'''
<g id="morph-stage-start"><rect x="92" y="194" width="250" height="86" rx="16" fill="#183349" stroke="#70b9c7"/><text x="116" y="246" fill="#d9f0f7" font-size="22">01 · {_e(l['start'])}</text></g>
<g id="morph-stage-middle"><rect x="92" y="310" width="250" height="86" rx="16" fill="#183349" stroke="#70b9c7"/><text x="116" y="362" fill="#d9f0f7" font-size="22">02 · {_e(l['middle'])}</text></g>
<g id="morph-stage-end"><rect x="92" y="426" width="250" height="86" rx="16" fill="#183349" stroke="#70b9c7"/><text x="116" y="478" fill="#d9f0f7" font-size="22">03 · {_e(l['end'])}</text></g>
<path id="morph-body" transform="translate(658 350)" d="M0 -110 C60.75 -110 110 -60.75 110 0 C110 60.75 60.75 110 0 110 C-60.75 110 -110 60.75 -110 0 C-110 -60.75 -60.75 -110 0 -110 Z" fill="#70cbb6" fill-opacity=".08" stroke="#8ee0cd" stroke-width="4" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1"/>
<g id="morph-result" opacity="0"><circle cx="658" cy="350" r="40" fill="#8ce5c1"/><path d="M638 350 L653 365 L681 336" fill="none" stroke="#143f39" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/></g>
<text x="928" y="288" fill="#dceef9" font-size="25">同一个对象</text><text x="928" y="330" fill="#9ebbd0" font-size="18">轮廓在变</text><text x="928" y="364" fill="#9ebbd0" font-size="18">身份保持</text>
<text id="morph-phase" x="458" y="592" fill="#a9dfd3" font-size="20">先把轮廓画出来</text>'''
    script = _state('svg-morph', identities, 'morph-phase') + '''
const visualShapes=[
 [0,-110,60.75,-110,110,-60.75,110,0,110,60.75,60.75,110,0,110,-60.75,110,-110,60.75,-110,0,-110,-60.75,-60.75,-110,0,-110],
 [0,-126,104,-126,112,-118,112,0,112,118,104,126,0,126,-104,126,-112,118,-112,0,-112,-118,-104,-126,0,-126],
 [0,-130,90,-110,110,-70,110,-10,110,70,50,120,0,145,-50,120,-110,70,-110,-10,-110,-70,-90,-110,0,-130]
];
function visualPath(values){let d='M'+values[0]+' '+values[1];for(let i=2;i<values.length;i+=6)d+=' C'+values.slice(i,i+6).join(' ');return d+' Z';}
function effectRender(t){
 const ease=p=>variant==='candidate'?smooth(p):p;
 const drawn=ease(progress(t,1,2)), structured=ease(progress(t,5,3)), verified=ease(progress(t,10,3)), result=ease(progress(t,15,1.5));
 const shape=visualShapes[0].map((value,i)=>(value+(visualShapes[1][i]-value)*structured)*(1-verified)+visualShapes[2][i]*verified);
 attrs('morph-body',{d:visualPath(shape),'stroke-dashoffset':1-drawn,'fill-opacity':.08+.1*drawn,stroke:variant==='candidate'?'#a5f6db':'#8ee0cd',transform:'translate(658 350)'});
 const weights=[1-structured,structured*(1-verified),verified];
 for(let i=0;i<3;i++){const id=['morph-stage-start','morph-stage-middle','morph-stage-end'][i],w=weights[i];fade(id,.6+.4*w);attrs(id,{transform:'translate('+(variant==='candidate'?10*w:0)+' 0)'});}
 fade('morph-result',result);attrs('morph-result',{transform:'translate(658 350) scale('+(1-.12*(1-result))+') translate(-658 -350)'});
 label('morph-phase',result>.5?'验证通过：同一对象完成了状态变化':verified>.1?'轮廓过渡到保护与验证状态':structured>.1?'从原始数据变成结构化结果':'先把轮廓画出来');
}'''
    return dict(markup=markup, script=script, object_ids=identities,
        actions=[_action('morph-body',1,2,'draw'),_action('morph-body',5,3,'morph'),
                 _action('morph-body',10,3,'morph'),_action('morph-result',15,1.5,'reveal')])


_BUILDERS = {'architecture-expand': _architecture, 'focus-shift': _focus,
             'data-compare': _data, 'svg-morph': _morph}


def build_recipe(plan, variant):
    if type(plan) is not dict or type(variant) is not str or variant not in {'baseline', 'candidate'}:
        raise ValueError('animation_lab_visual_recipe_invalid')
    identity = plan.get('effect')
    if type(identity) is not str or identity not in RECIPES:
        raise ValueError('animation_lab_visual_recipe_unknown')
    recipe = RECIPES[identity]
    expected = dict(schema='video.animation-lab-effect-plan.v1', effect=identity,
                    title=recipe['title'], takeaway=recipe['takeaway'], labels=recipe['labels'])
    if plan != expected:
        raise ValueError('animation_lab_visual_recipe_plan_mismatch')
    return _BUILDERS[identity](deepcopy(plan))
