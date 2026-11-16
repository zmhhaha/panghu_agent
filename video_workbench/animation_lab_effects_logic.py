"""Four finite, seekable logic effects for the local developer sample library.

Both variants explain the same event trace. The candidate changes focus and
interpolation, while the baseline remains a usable, complete explanation.
"""
from __future__ import annotations

from copy import deepcopy
import html
import json


RECIPES = {
    'condition-branch': dict(
        module='process', title='条件决定路线，结果在同一处汇合',
        takeaway='普通请求走常规处理，紧急请求走优先处理；两条路线汇入同一结果。',
        description='用两个有身份的请求先后演示条件判断、实际分流和结果汇合。',
        checks=['请求身份和路线始终一致', '两条分支都真正执行', '汇合不改变已完成结果'],
        labels=dict(source='请求入口', decision='是否紧急？', normal='常规处理', priority='优先处理',
                    result='统一结果', request_a='A', request_b='B', ordinary='普通请求 A',
                    urgent='紧急请求 B', undecided='等待判断', no='否 · 常规', yes='是 · 优先',
                    ready='两个请求依次到达', route_a='A 不是紧急请求，进入常规路线',
                    done_a='A 已完成，接着判断 B', route_b='B 是紧急请求，进入优先路线',
                    done='两条路线都完成，结果在这里汇合', completed='已完成')),
    'parallel-wait': dict(
        module='process', title='同时开始，不等于同时完成',
        takeaway='三个任务并行执行；汇合点必须收齐全部结果，才能继续。',
        description='三个不同耗时的并行任务分别完成，汇合计数保持真实等待条件。',
        checks=['三个任务同时开始', '先完成的任务保留结果', '收齐三个结果之前不能继续'],
        labels=dict(source='并行启动', task_a='读取配置', task_b='查询数据', task_c='检查权限',
                    result='等待全部结果', waiting='等待', running='执行中', done='已完成',
                    count='已收齐', ready='三件独立的事，可以一起做',
                    started='一起开始，各自推进', first='配置先完成，汇合点继续等待',
                    second='已有两个结果，还差权限检查', complete='三个结果都到齐，现在可以继续',
                    next='继续下一步')),
    'code-execution': dict(
        module='process', title='代码走到哪一行，变量就更新到哪一步',
        takeaway='循环依次累加 12、8、5，变量 total 最终变为 25。',
        description='代码行、当前迭代值、累加变量和输出使用同一条绝对时间事件轨迹。',
        checks=['高亮代码行与当前操作一致', '只在累加行修改 total', '输出等于最终变量值'],
        labels=dict(code='逐行执行', variables='运行时变量', price='当前 price', total='累计 total',
                    output='程序输出', absent='尚未读取', pending='尚未输出',
                    initialize='先把累计值设为 0', read='读取本轮价格', add='执行累加',
                    print='循环结束，输出累计结果', complete='每一步都能对应到代码和变量')),
    'local-replace': dict(
        module='diagram', title='只替换瓶颈，其余结构保持稳定',
        takeaway='入口、队列和存储保持不动，只替换中间处理模块，并验证处理能力变化。',
        description='在固定上下文中局部比较单路处理和双路处理，突出被替换部分及原因。',
        checks=['外围节点位置始终不变', '变化仅发生在处理模块', '指标明确是示意参数而非实测结论'],
        labels=dict(source='请求入口', queue='缓冲队列', storage='结果存储', old='单路处理',
                    new='双路处理', old_rate='示意：1 个 / 秒', new_rate='示意：2 个 / 秒',
                    old_detail='一条处理通道', new_detail='两条独立通道',
                    context='上下文不变：入口 → 队列 → 处理模块 → 存储',
                    locate='先定位中间模块：请求在这里排队',
                    replace='保留接口，只替换这个处理模块',
                    verify='同样两个请求，新的模块可以同时接收',
                    complete='局部变化可比较，周围结构不用重新理解',
                    before='替换前', after='替换后')),
}


def _t(text):
    return html.escape(str(text), quote=True)


def _text(x, y, text, size=22, *, fill='#e4effa', anchor='start', ident=None):
    ident = f' id="{ident}"' if ident else ''
    return (f'<text{ident} x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}">{_t(text)}</text>')


def _card(ident, x, y, w, h, title, subtitle='', color='#6db8ff', content=''):
    return (f'<g id="{ident}"><rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'rx="18" fill="#13263b" stroke="{color}" stroke-width="2"/>'
            + _text(x+20, y+41, title, 25)
            + (_text(x+20, y+78, subtitle, 18, fill='#b6cbe0') if subtitle else '') + content + '</g>')


def _packet(ident, letter, color):
    return (f'<g id="{ident}" transform="translate(190 390)"><circle r="17" fill="{color}"/>'
            + _text(0, 6, letter, 17, fill='#081320', anchor='middle') + '</g>')


def _path(ident, d, color='#6c91ad'):
    return (f'<path id="{ident}" d="{d}" fill="none" stroke="{color}" '
            'stroke-width="3" stroke-linejoin="round" stroke-linecap="round" opacity="0"/>')


def _script(plan, variant, source):
    encoded = json.dumps(plan['labels'], ensure_ascii=False, separators=(',', ':'))
    encoded = encoded.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    return ('const L='+encoded+';const logicCandidate='+str(variant == 'candidate').lower()+';\n'
            'let logicSnapshot={};\n'
            'function logicP(t,s,d){const p=progress(t,s,d);return logicCandidate?smooth(p):p;}\n'
            'function logicPoint(a,b,p){return [a[0]+(b[0]-a[0])*p,a[1]+(b[1]-a[1])*p];}\n'
            'function logicRoute(points,p){const lengths=points.slice(1).map((b,i)=>Math.hypot(b[0]-points[i][0],b[1]-points[i][1]));let distance=lengths.reduce((a,b)=>a+b,0)*clamp(p);for(let i=0;i<lengths.length;i++){if(distance<=lengths[i])return logicPoint(points[i],points[i+1],distance/lengths[i]);distance-=lengths[i];}return points[points.length-1].slice();}\n'
            'function logicFocus(ids,active){for(const id of ids)fade(id,logicCandidate&&!active.includes(id)?.54:1);}\n'
            'function effectState(){return JSON.parse(JSON.stringify(logicSnapshot));}\n' + source)


def _action(start, duration, kind, target):
    return dict(start=start, duration=duration, kind=kind, target=target)


def _branch(plan, variant):
    l = plan['labels']
    markup = ''.join([
        _path('branch-line-in', 'M294 390H330V365H355'),
        _path('branch-line-a', 'M498 365L540 365V250H570'),
        _path('branch-line-b', 'M498 365L540 365V540H570'),
        _path('branch-line-a-out', 'M800 250H875V385H950'),
        _path('branch-line-b-out', 'M800 540H875V420H950'),
        _card('branch-source', 64, 272, 230, 180, l['source'], l['ordinary'],
              content=_text(84, 420, l['urgent'], 18, fill='#f3ba71')),
        '<g id="branch-decision"><path d="M430 290L505 365L430 440L355 365Z" '
        'fill="#16314a" stroke="#78bfff" stroke-width="2"/>'
        + _text(430, 344, l['decision'], 19, anchor='middle')
        + _text(430, 405, l['undecided'], 16, anchor='middle', ident='branch-choice')+'</g>',
        _card('branch-normal', 570, 145, 230, 140, l['normal'], l['ordinary']),
        _card('branch-priority', 570, 435, 230, 140, l['priority'], l['urgent'], '#e9ad62'),
        _card('branch-result', 950, 265, 250, 200, l['result'],
              content=_text(975, 345, l['completed']+' 0 / 2', 20, ident='branch-count')),
        _packet('branch-request-a', l['request_a'], '#72c8ff'),
        _packet('branch-request-b', l['request_b'], '#ffc478'),
        _text(640, 610, l['ready'], 22, anchor='middle', ident='branch-caption'),
    ])
    script = _script(plan, variant, r'''
function effectRender(t){
 const a0=[190,390],decision=[430,365],upper=[685,250],aEnd=[1075,385],lower=[685,540],bEnd=[1130,420];
 let a=a0,b=a0;
 if(t>=1)a=logicRoute([a0,[330,390],[330,365],decision],logicP(t,1,3));
 if(t>=5)a=logicRoute([decision,[540,365],[540,250],upper],logicP(t,5,2));
 if(t>=7.2)a=logicRoute([upper,[875,250],[875,385],aEnd],logicP(t,7.2,1.8));
 if(t>=9)b=logicRoute([a0,[330,390],[330,365],decision],logicP(t,9,2));
 if(t>=12)b=logicRoute([decision,[540,365],[540,540],lower],logicP(t,12,2));
 if(t>=14.5)b=logicRoute([lower,[875,540],[875,420],bEnd],logicP(t,14.5,2));
 move('branch-request-a',...a);move('branch-request-b',...b);
 fade('branch-request-a',1);fade('branch-request-b',progress(t,9,.3));
 const completed=(t>=9?1:0)+(t>=16.5?1:0);
 const choice=t<4.25?L.undecided:t<11.45?L.no:L.yes;
 label('branch-choice',choice);label('branch-count',L.completed+' '+completed+' / 2');
 const phase=t<4.25?'arrival-a':t<9?'normal-route':t<11.45?'arrival-b':t<16.5?'priority-route':'merged';
 label('branch-caption',t<4.25?L.ready:t<9?L.route_a:t<11.45?L.done_a:t<16.5?L.route_b:L.done);
 for(const [id,at] of [['branch-line-in',1],['branch-line-a',5],['branch-line-a-out',7.2],['branch-line-b',12],['branch-line-b-out',14.5]])fade(id,progress(t,at,.4));
 const groups=['branch-source','branch-decision','branch-normal','branch-priority','branch-result'];
 const active=t<4.25?['branch-source','branch-decision']:t<9?['branch-normal','branch-result']:t<11.45?['branch-source','branch-decision']:t<16.5?['branch-priority','branch-result']:groups;
 logicFocus(groups,active);
 logicSnapshot={phase,choice,completed,requests:[{id:'A',position:a,route:'normal',done:t>=9},{id:'B',position:b,visible:t>9,route:'priority',done:t>=16.5}]};
}
''')
    return dict(markup=markup, script=script, object_ids=[
        'branch-source', 'branch-decision', 'branch-normal', 'branch-priority',
        'branch-result', 'branch-request-a', 'branch-request-b'], actions=[
        _action(1, 3, 'approach-decision', 'branch-request-a'),
        _action(4, .5, 'choose-normal', 'branch-decision'),
        _action(5, 2, 'normal-route', 'branch-request-a'),
        _action(7.2, 1.8, 'normal-merge', 'branch-request-a'),
        _action(9, 2, 'approach-decision', 'branch-request-b'),
        _action(11.2, .5, 'choose-priority', 'branch-decision'),
        _action(12, 2, 'priority-route', 'branch-request-b'),
        _action(14.5, 2, 'priority-merge', 'branch-request-b')])


def _parallel(plan, variant):
    l = plan['labels']
    rows = [('a', 140, l['task_a']), ('b', 300, l['task_b']), ('c', 460, l['task_c'])]
    parts = [_path('parallel-in', 'M294 365H340V200H400M340 365H400M340 365V520H400'),
             _path('parallel-out', 'M780 200H850V365H930M780 360H930M780 520H850V365'),
             _card('parallel-source', 64, 275, 230, 180, l['source'],
                   content=_text(84, 360, '3 '+l['running'], 19, ident='parallel-start')),
             _card('parallel-result', 930, 260, 270, 225, l['result'],
                   content=_text(950, 345, l['count']+' 0 / 3', 22, ident='parallel-count')
                   + _text(950, 410, l['waiting'], 23, ident='parallel-next'))]
    for ident, y, title in rows:
        parts.append(f'<g id="parallel-task-{ident}"><rect x="400" y="{y}" width="380" height="120" '
                     'rx="16" fill="#13263b" stroke="#70b6ee" stroke-width="2"/>'
                     + _text(420, y+35, title, 24)
                     + _text(420, y+67, l['waiting'], 18, ident=f'parallel-status-{ident}')
                     + f'<rect x="420" y="{y+84}" width="340" height="13" rx="6" fill="#243c50"/>'
                     + f'<rect id="parallel-bar-{ident}" x="420" y="{y+84}" width="0" height="13" rx="6" fill="#70d5bd"/></g>')
    parts.append(_text(640, 615, l['ready'], 22, anchor='middle', ident='parallel-caption'))
    script = _script(plan, variant, r'''
function effectRender(t){
 const tasks=[['a',4],['b',7],['c',10]],results=[];
 for(const [id,duration] of tasks){
  const p=progress(t,3,duration),done=t>=3+duration;
  attrs('parallel-bar-'+id,{width:340*p});
  label('parallel-status-'+id,t<3?L.waiting:done?L.done+' 100%':L.running+' '+Math.floor(p*100)+'%');
  fade('parallel-task-'+id,logicCandidate&&t>=3&&!done?.78:1);
  results.push({id,progress:p,status:t<3?'waiting':done?'done':'running'});
 }
 const completed=results.filter(row=>row.status==='done').length,canContinue=completed===3;
 label('parallel-start',t<3?L.waiting:'3 '+L.running);
 label('parallel-count',L.count+' '+completed+' / 3');
 label('parallel-next',canContinue?L.next:L.waiting);
 label('parallel-caption',t<3?L.ready:t<7?L.started:t<10?L.first:t<13?L.second:L.complete);
 fade('parallel-in',progress(t,1,1));fade('parallel-out',progress(t,3,1));
 fade('parallel-result',logicCandidate&&!canContinue?.68:1);fade('parallel-source',logicCandidate&&t>=3?.54:1);
 logicSnapshot={phase:t<3?'ready':canContinue?'continue':'waiting-for-all',completed,can_continue:canContinue,tasks:results};
}
''')
    return dict(markup=''.join(parts), script=script,
                object_ids=['parallel-source', 'parallel-task-a', 'parallel-task-b', 'parallel-task-c', 'parallel-result'],
                actions=[_action(1, 2, 'start-parallel', 'parallel-source'),
                         _action(3, 4, 'complete-task', 'parallel-task-a'),
                         _action(3, 7, 'complete-task', 'parallel-task-b'),
                         _action(3, 10, 'complete-task', 'parallel-task-c'),
                         _action(12.8, .7, 'release-join', 'parallel-result')])


def _code(plan, variant):
    l = plan['labels']
    lines = [('1', 'total = 0'), ('2', 'for price in [12, 8, 5]:'),
             ('3', '    total += price'), ('4', 'print(total)')]
    parts = [_card('code-panel', 64, 140, 660, 420, l['code']),
             '<g id="code-cursor" transform="translate(0 0)"><rect x="84" y="222" width="620" '
             'height="49" rx="7" fill="#376f9c" opacity=".5"/></g>']
    for index, (number, source) in enumerate(lines):
        y = 255+70*index
        parts.extend([_text(104, y, number, 22, fill='#8ea6be'),
                      _text(155, y, source.replace(' ', '\u00a0'), 24)])
    parts.extend([_text(790, 166, l['variables'], 25),
                  _card('code-price', 790, 200, 410, 100, l['price'],
                        content=_text(1170, 264, l['absent'], 24, anchor='end', ident='code-price-value')),
                  _card('code-total', 790, 325, 410, 100, l['total'],
                        content=_text(1170, 390, '0', 30, anchor='end', ident='code-total-value')),
                  _card('code-output', 790, 450, 410, 110, l['output'],
                        content=_text(1170, 520, l['pending'], 24, anchor='end', ident='code-output-value')),
                  _text(640, 615, l['initialize'], 22, anchor='middle', ident='code-caption')])
    script = _script(plan, variant, r'''
function effectRender(t){
 const events=[{at:0,line:0,price:null,total:0},{at:2.5,line:1,price:12,total:0},
  {at:4.5,line:2,price:12,total:12},{at:6.5,line:1,price:8,total:12},
  {at:8.5,line:2,price:8,total:20},{at:10.5,line:1,price:5,total:20},
  {at:12.5,line:2,price:5,total:25},{at:14.5,line:3,price:5,total:25}];
 let index=0;for(let i=0;i<events.length;i++)if(t>=events[i].at)index=i;
 const current=events[index];let y=current.line*70;
 if(logicCandidate&&index>0){const previous=events[index-1];y=(previous.line+(current.line-previous.line)*smooth(progress(t,current.at,.35)))*70;}
 move('code-cursor',0,y);fade('code-cursor',1);
 label('code-price-value',current.price===null?L.absent:current.price);
 label('code-total-value',current.total);label('code-output-value',index===7?'25':L.pending);
 const phase=index===0?'initialize':index===7?'output':index%2?'read':'add';
 label('code-caption',t>=16?L.complete:phase==='initialize'?L.initialize:phase==='read'?L.read+' '+current.price:phase==='add'?L.add+'：total = '+current.total:L.print);
 logicFocus(['code-price','code-total','code-output'],phase==='read'?['code-price']:phase==='output'?['code-output']:['code-total']);
 logicSnapshot={phase,line:current.line+1,price:current.price,total:current.total,output:index===7?25:null};
}
''')
    return dict(markup=''.join(parts), script=script,
                object_ids=['code-panel', 'code-cursor', 'code-price', 'code-total', 'code-output'], actions=[
                    _action(2, 1, 'read-price', 'code-price'),
                    _action(4, 1, 'add-price', 'code-total'),
                    _action(6, 1, 'read-price', 'code-price'),
                    _action(8, 1, 'add-price', 'code-total'),
                    _action(10, 1, 'read-price', 'code-price'),
                    _action(12, 1, 'add-price', 'code-total'),
                    _action(14, 1, 'show-output', 'code-output'),
                    _action(14, 1, 'highlight-output', 'code-cursor')])


def _replace(plan, variant):
    l = plan['labels']
    parts = [_path('replace-connectors', 'M259 355H310M505 355H575M885 355H945'),
             _card('replace-source', 64, 280, 195, 150, l['source']),
             _card('replace-queue', 310, 280, 195, 150, l['queue']),
             _card('replace-storage', 945, 280, 255, 150, l['storage']),
             '<g id="replace-focus" opacity="0"><rect x="555" y="175" width="350" height="360" '
             'rx="24" fill="#1b2c45" stroke="#f7c27e" stroke-width="3"/></g>',
             '<rect id="replace-module-backplate" x="575" y="235" width="310" height="245" '
             'fill="#153349" stroke="#7fbef3" stroke-width="2"/>'
             '<g id="replace-module">'
             + _text(595, 274, l['before'], 18, ident='replace-stage')
             + _text(595, 316, l['old'], 27, ident='replace-title')
             + _text(595, 358, l['old_detail'], 19, ident='replace-detail')
             + _text(595, 460, l['old_rate'], 23, ident='replace-rate')
             + '<rect id="replace-lane-a" x="600" y="373" width="245" height="14" rx="7" fill="#68b9ef"/>'
             + '<rect id="replace-lane-b" x="600" y="409" width="245" height="14" rx="7" fill="#68d7b9" opacity="0"/></g>',
             _packet('replace-request-a', 'A', '#79caff'), _packet('replace-request-b', 'B', '#7ee4be'),
             _text(640, 565, l['context'], 19, anchor='middle', ident='replace-context'),
             _text(640, 615, l['locate'], 22, anchor='middle', ident='replace-caption')]
    script = _script(plan, variant, r'''
for(const id of ['replace-source','replace-queue','replace-storage'])fade(id,logicCandidate?.68:1);
function effectRender(t){
 const isNew=t>=8,focus=progress(t,2,1.5)*(1-progress(t,14,1.5));
 attrs('replace-focus',{opacity:focus*(logicCandidate?1:.55)});
 label('replace-stage',isNew?L.after:L.before);label('replace-title',isNew?L.new:L.old);
 label('replace-detail',isNew?L.new_detail:L.old_detail);label('replace-rate',isNew?L.new_rate:L.old_rate);
 attrs('replace-lane-a',{width:245,fill:isNew?'#68d7b9':'#68b9ef'});fade('replace-lane-b',progress(t,8,.6));
 fade('replace-connectors',progress(t,.5,1));
 const p=logicP(t,10,3),a=logicPoint([620,380],[820,380],p),b=logicPoint([620,416],[820,416],p);
 move('replace-request-a',...a);move('replace-request-b',...b);
 const visible=progress(t,9.5,.5)*(1-progress(t,13.5,.5));fade('replace-request-a',visible);fade('replace-request-b',visible);
 label('replace-caption',t<5?L.locate:t<10?L.replace:t<14?L.verify:L.complete);
 logicSnapshot={phase:t<5?'locate':t<8?'replace-preparation':t<10?'replaced':t<14?'verify':'complete',
  module:isNew?'dual-lane':'single-lane',illustrative_capacity:isNew?2:1,capacity_unit:'requests-per-second',
  outside_unchanged:true,requests:[{id:'A',position:a},{id:'B',position:b}]};
}
''')
    return dict(markup=''.join(parts), script=script,
                object_ids=['replace-source', 'replace-queue', 'replace-storage', 'replace-focus',
                            'replace-module', 'replace-request-a', 'replace-request-b'], actions=[
                    _action(2, 1.5, 'locate-module', 'replace-focus'),
                    _action(7.5, 1, 'replace-module', 'replace-module'),
                    _action(10, 3, 'verify-lane', 'replace-request-a'),
                    _action(10, 3, 'verify-lane', 'replace-request-b'),
                    _action(14, 1.5, 'restore-context', 'replace-focus')])


_BUILDERS = {'condition-branch': _branch, 'parallel-wait': _parallel,
             'code-execution': _code, 'local-replace': _replace}


def build_recipe(plan, variant):
    """Build one bundled recipe; arbitrary labels or executable plans are rejected."""
    if type(plan) is not dict or type(variant) is not str or variant not in {'baseline', 'candidate'}:
        raise ValueError('animation_lab_logic_recipe_invalid')
    effect = plan.get('effect')
    if type(effect) is not str or effect not in RECIPES:
        raise ValueError('animation_lab_logic_recipe_invalid')
    recipe = RECIPES[effect]
    expected = dict(schema='video.animation-lab-effect-plan.v1', effect=effect,
                    title=recipe['title'], takeaway=recipe['takeaway'], labels=recipe['labels'])
    if plan != expected:
        raise ValueError('animation_lab_logic_recipe_invalid')
    return _BUILDERS[effect](deepcopy(plan), variant)
