"""Finite cross-domain chart requirements and deterministic linked SVG scenes.

The figures are explicitly illustrative inputs, not production telemetry,
research findings, or an arbitrary chart/code execution interface.  Both A/B
presentations use exactly the same datasets, units and explanatory timeline.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import html
import json
import math


SCHEMA = 'video.animation-lab-scenario-plan.v1'


def _requirement(audience, goal, questions, entities, outcome, boundaries):
    return dict(audience=audience, goal=goal, questions=questions,
                required_entities=entities, expected_outcome=outcome, boundaries=boundaries)


SCENARIOS = {
    'chart-compute-backlog': dict(
        module='charts', domain='computing', domain_label='计算机系统',
        expression='chart-linkage', engine='svg', title='扩容之后，排队和延迟如何一起变化？',
        description='通过六个采样窗口联动到达量、实际完成量、队列和等待延迟。',
        takeaway='示意数据：队列 0 → 14 → 0；扩容后的处理能力逐步消化积压。',
        narrative='先观察正常负载，四个请求到达，也有四个完成。流量突增时，处理能力没有同步增加，队列涨到十四。扩容后每个窗口最多处理八个，积压逐步减少，延迟也回到八十毫秒。最后一个窗口只有四个待处理任务，实际完成量也只能是四个。',
        requirement=_requirement('理解系统性能的初学者', '解释扩容与积压、延迟的联系',
            ['为什么扩容后队列不会立刻消失？', '处理能力与实际完成量有什么区别？'],
            ['到达量', '实际完成量', '积压队列', '容量', '示意延迟'],
            '能够从相同时间窗口追踪队列守恒，并区分容量与吞吐。',
            ['所有数字均为示意，不代表实测服务表现。', '延迟使用明确的示意线性模型，不声称真实系统必然如此。',
             '采样点之间采用线性插值引导阅读，不表示真实系统在连续时刻的实测值。']),
        duration_seconds=22, difficulty='intermediate', tags=['队列守恒', '双指标', '非单调'],
        checks=['队列等于累计到达减累计完成', '实际完成不能超过容量或待处理量', '曲线与指标使用同一时间'],
        data=dict(windows=['正常', '突增1', '突增2', '扩容1', '扩容2', '恢复'],
            arrivals=[4, 10, 12, 4, 2, 0], capacity=[4, 4, 4, 8, 8, 8],
            completed=[4, 4, 4, 8, 8, 4], backlog=[0, 6, 14, 10, 4, 0],
            latency_ms=[80, 200, 360, 280, 160, 80], queue_scale_max=16,
            latency_scale_max=400, latency_model=dict(base_ms=80, per_queued_ms=20))),
    'chart-energy-balance': dict(
        module='charts', domain='energy', domain_label='能源与环境',
        expression='chart-linkage', engine='svg', title='太阳下山之后，供电缺口由谁补上？',
        description='以统一兆瓦尺度展示太阳能、电网与储能如何共同满足不同时刻的需求。',
        takeaway='示意数据：21 时需求 6 MW，由电网 4 MW 与储能 2 MW 共同满足。',
        narrative='清晨太阳能还没有出力，电网和储能共同供电。中午太阳能达到八兆瓦，但需求是九兆瓦，仍要从电网补上一兆瓦。傍晚和夜间太阳能减少，电网和储能再补上缺口。每一根堆叠柱都要和同一时刻的需求点一样高。',
        requirement=_requirement('能源科普读者', '解释多种电源在同一时刻的供需平衡',
            ['太阳能最多时是否就完全不需要电网？', '夜间六兆瓦需求由哪些部分组成？'],
            ['太阳能', '电网', '储能放电', '需求', '时刻', '功率单位'],
            '能按颜色分解每根堆叠柱，并核对三项功率之和等于需求。',
            ['仅为五个时刻的功率快照，不推算电量或电池续航。', '忽略输电损耗，数据为示意。']),
        duration_seconds=24, difficulty='boundary', tags=['堆叠', '供需平衡', '零值', '单位'],
        checks=['三项功率之和等于需求', '共用从零开始的MW坐标', '夜间太阳能为零'],
        data=dict(hours=[6, 9, 12, 17, 21], demand=[5, 6, 9, 7, 6],
            solar=[0, 3, 8, 4, 0], grid=[3, 2, 1, 2, 4], battery=[2, 1, 0, 1, 2],
            scale_max=10, unit='MW')),
    'chart-logistics-stock': dict(
        module='charts', domain='logistics', domain_label='物流与供应链',
        expression='chart-linkage', engine='svg', title='到货不少，为什么仓库一会儿满一会儿空？',
        description='累计入库线与累计出库线的垂直差额，对应当时仍在仓库的货物。',
        takeaway='示意数据：累计入库与出库最终都为 36 箱，库存回到 0。',
        narrative='第一批十二箱到货后，发走四箱，仓库还剩八箱。第二个窗口没有新货，库存被清空。第三个窗口又集中到货十六箱，库存升到十二。看两条累计曲线之间的差，就能知道此刻还有多少箱没有发走。最后累计入库和出库都到三十六，库存再次归零。',
        requirement=_requirement('业务分析与物流学习者', '区分流量、累计量和存量',
            ['累计入库一直增加，为什么库存仍可能归零？', '两条曲线的差额代表什么？'],
            ['每窗口入库', '每窗口出库', '累计入库', '累计出库', '库存'],
            '能用库存=累计入库−累计出库解释两次清空与一次再次积压。',
            ['仓库初始库存为零；没有损耗、退货或其他进出通道。', '所有箱数均为示意。']),
        duration_seconds=22, difficulty='intermediate', tags=['累计与存量', '清零', '双曲线'],
        checks=['累计值来自每窗口箱数', '库存等于两条曲线差额', '库存不得出现负数'],
        data=dict(windows=['第一批', '清空', '第二批', '追赶', '全部发出'],
            incoming=[12, 0, 16, 0, 8], outgoing=[4, 8, 4, 12, 8],
            cumulative_in=[12, 12, 28, 28, 36], cumulative_out=[4, 12, 16, 28, 36],
            stock=[8, 0, 12, 0, 0], scale_max=40, unit='箱')),
    'chart-learning-gains': dict(
        module='charts', domain='education', domain_label='教育与学习',
        expression='chart-linkage', engine='svg', title='同样达到八成，进步幅度为什么不同？',
        description='同一个30人示意班级，对照四类题目前后掌握率及百分点变化。',
        takeaway='示意数据：推理题 40% → 80%，增加 40 个百分点；不等于只增长 40%。',
        narrative='先看同一个三十人班级的前测和后测。语法题从百分之五十到八十，推理题从百分之四十到八十，虽然最后一样高，进步幅度却不同。推理题多了十二位掌握者，也就是增加四十个百分点。图形题只增加十个百分点。这里比较的是两次测量，不能仅凭这张图证明某种教学法有效。',
        requirement=_requirement('教师与数据阅读初学者', '理解掌握率比较和百分点',
            ['同样80%的后测结果是否意味着相同进步？', '增加40个百分点与相对增长40%是否一样？'],
            ['同一组30人', '四类题目', '前测', '后测', '掌握人数', '百分点差'],
            '能够按同一分母核对百分比，分清后测水平、百分点差和相对增长。',
            ['纯示意班级，不代表真实教育研究结果。', '前后变化不等于教学干预的因果证据。']),
        duration_seconds=24, difficulty='boundary', tags=['百分点', '同一分母', '分类对照'],
        checks=['人数除以30等于掌握率', '差额使用百分点单位', '不能把变化解释为因果证明'],
        data=dict(topics=['语法题', '推理题', '图形题', '应用题'], total=30,
            before_count=[15, 12, 21, 18], after_count=[24, 24, 24, 27],
            before_rate=[50, 40, 70, 60], after_rate=[80, 80, 80, 90],
            gain_pp=[30, 40, 10, 30], scale_max=100, unit='%')),
}


def _e(value):
    return html.escape(str(value), quote=True)


def _text(identity, x, y, text, size=18, color='#c4d7e8', anchor='start'):
    ident = f' id="{_e(identity)}"' if identity else ''
    return f'<text{ident} x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}">{_e(text)}</text>'


def _panel(x, y, width, height):
    # Straight corners keep reverse-seek rasterization stable across Chromium.
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="#112236" stroke="#2d455e"/>'


def _card(identity, x, y, label, value, unit, color='#8de4c6'):
    return (f'<g id="{identity}">'+_panel(x, y, 270, 105)
            +_text(None, x+18, y+28, label, 17, '#9eb8cd')
            +_text(identity+'-value', x+18, y+73, value, 32, color)
            +_text(None, x+245, y+73, unit, 16, '#9eb8cd', 'end')+'</g>')


def _footer():
    return (_panel(64, 552, 1152, 54)
            +_text('chart-phase', 82, 586, '', 20, '#bcebdc'))


def _action(target, start, duration, kind='move'):
    return dict(target=target, start=start, duration=duration, kind=kind)


def _common_script(plan, variant, identities):
    encoded = json.dumps(plan['data'], ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('&', '\\u0026')
    ids = json.dumps(identities)
    return (f"const chartData={encoded};const chartEnhanced={str(variant == 'candidate').lower()};\n"
            f"const chartObjectIds={ids};\n"
            "const chartRound=n=>Number(n.toFixed(2));\n"
            "function chartInterpolate(values,pos){const index=Math.min(values.length-2,Math.floor(pos)),p=pos-index;return values[index]+(values[index+1]-values[index])*p;}\n"
            "function chartPosition(t,start,end,count){const p=progress(t,start,end-start);return p*(count-1);}\n"
            "const chartCoordinate=n=>String(chartRound(n));\n"
            "function chartPath(values,pos,x,y,width,height,max){let d='';const last=Math.floor(pos);for(let i=0;i<=last;i++)d+=(i?' L':'M')+chartCoordinate(x+i*width/(values.length-1))+' '+chartCoordinate(y-values[i]*height/max);if(pos>last)d+=' L'+chartCoordinate(x+pos*width/(values.length-1))+' '+chartCoordinate(y-chartInterpolate(values,pos)*height/max);return d;}\n"
            "function chartRead(id){const node=$(id);return {id,text:node.textContent,attributes:{value:node.getAttribute('data-value'),step:node.getAttribute('data-step'),path:node.getAttribute('d'),state:node.getAttribute('data-state')}};}\n"
            "function effectState(){return {scenario:plan.scenario_id,domain:plan.domain,phase:$('chart-phase').textContent,objects:chartObjectIds.map(chartRead)};}\n")


def _compute(plan, variant):
    ids = ['compute-backlog-path', 'compute-latency-path', 'compute-cursor',
           'compute-queue', 'compute-latency', 'compute-throughput', 'compute-window', 'chart-phase',
           'compute-queue-value', 'compute-latency-value', 'compute-throughput-value']
    markup = (_text(None, 64, 134, '示意输入 · 同一时间窗口，两个独立单位', 16, '#91a9c0')
        +_panel(64, 153, 850, 367)
        +_text(None, 86, 187, '队列（个）', 18, '#8de4c6')
        +_text(None, 884, 187, '延迟（毫秒）', 18, '#e8bf82', 'end')
        +'<path d="M112 220V450H872" fill="none" stroke="#35516a" stroke-width="2"/>'
        +_text(None, 92, 455, '0', 14, '#819eb6', 'end')
        +_text(None, 92, 234, '16', 14, '#819eb6', 'end')
        +_text(None, 887, 234, '400', 14, '#c3a478')
        +_text(None, 887, 455, '0', 14, '#c3a478')
        +'<path id="compute-backlog-path" d="M112 450" fill="none" stroke="#8de4c6" stroke-width="4"/>'
        +'<path id="compute-latency-path" d="M112 406" fill="none" stroke="#e8bf82" stroke-width="3"/>'
        +'<path id="compute-cursor" d="M112 220V450" fill="none" stroke="#88aed3" stroke-width="2" opacity="0"/>'
        +''.join(_text(None, 112+i*152, 480, value, 15, '#9fb7ca', 'middle')
                  for i, value in enumerate(plan['data']['windows']))
        +_text('compute-window', 112, 507, '', 17, '#b6cee0')
        +_card('compute-queue', 940, 153, '等待队列', '0', '个')
        +_card('compute-latency', 940, 275, '示意延迟', '80', '毫秒', '#e8bf82')
        +_card('compute-throughput', 940, 397, '实际完成 / 处理容量', '4 / 4', '个')+_footer())
    script = _common_script(plan, variant, ids)+'''
function effectRender(t){
 const pos=chartPosition(t,1,18,chartData.windows.length),step=Math.min(5,Math.floor(pos));
 const queue=chartInterpolate(chartData.backlog,pos),latency=chartInterpolate(chartData.latency_ms,pos);
 attrs('compute-backlog-path',{d:chartPath(chartData.backlog,pos,112,450,760,220,16),'data-value':chartRound(queue)});
 attrs('compute-latency-path',{d:chartPath(chartData.latency_ms,pos,112,450,760,220,400),'data-value':chartRound(latency)});
 attrs('compute-cursor',{d:'M'+(112+pos*152)+' 220V450',opacity:chartEnhanced?.8:.22});
 attrs('compute-queue',{'data-state':chartRound(queue)});attrs('compute-queue-value',{'data-value':chartRound(queue)});label('compute-queue-value',chartRound(queue));
 attrs('compute-latency',{'data-state':chartRound(latency)});attrs('compute-latency-value',{'data-value':chartRound(latency)});label('compute-latency-value',chartRound(latency));
 const completed=chartInterpolate(chartData.completed,pos),capacity=chartInterpolate(chartData.capacity,pos);
 attrs('compute-throughput',{'data-state':chartRound(completed)+'/'+chartRound(capacity)});
 label('compute-throughput-value',chartRound(completed)+' / '+chartRound(capacity));
 attrs('compute-window',{'data-step':step});label('compute-window','窗口：'+chartData.windows[step]+' · 到达 '+chartData.arrivals[step]+' · 完成 '+chartData.completed[step]);
 label('chart-phase',pos<1?'处理能力足够：完成量跟上到达量':pos<2?'流量突增，积压和示意延迟一起上升':pos<4?'容量增加后，仍需时间消化先前积压':'积压归零；实际完成量受待处理任务数量限制');
}
'''
    return dict(markup=markup, script=script, object_ids=ids,
        actions=[_action('compute-backlog-path', 1, 4), _action('compute-queue', 5, 4),
                 _action('compute-throughput', 9, 4), _action('compute-latency-path', 14, 4)])


def _energy(plan, variant):
    ids = ['energy-columns', 'energy-demand', 'energy-selected', 'energy-solar',
           'energy-grid', 'energy-battery', 'energy-total', 'chart-phase',
           'energy-solar-value', 'energy-grid-value', 'energy-battery-value']
    ids += [f'energy-{key}-{index}' for index in range(5) for key in ('solar', 'grid', 'battery')]
    columns = []
    colors = dict(solar='#edcf83', grid='#83b8ee', battery='#8de4c6')
    for index, hour in enumerate(plan['data']['hours']):
        x = 132+150*index
        for key in ('solar', 'grid', 'battery'):
            columns.append(f'<rect id="energy-{key}-{index}" x="{x}" y="452" width="72" height="0" fill="{colors[key]}"/>')
        columns.append(_text(None, x+36, 482, f'{hour}时', 16, '#9fb7ca', 'middle'))
    markup = (_text(None, 64, 134, '示意输入 · 功率快照（MW），不代表电量', 16, '#91a9c0')
        +_panel(64, 153, 850, 367)
        +_text(None, 86, 188, '供电构成与需求 · 共用零基线', 20)
        +'<path d="M112 224V452H866" fill="none" stroke="#35516a" stroke-width="2"/>'
        +_text(None, 92, 456, '0', 14, '#819eb6', 'end')
        +_text(None, 92, 232, '10', 14, '#819eb6', 'end')
        +'<g id="energy-columns">'+''.join(columns)+'</g>'
        +'<path id="energy-demand" d="M168 340" fill="none" stroke="#f4efff" stroke-width="3"/>'
        +'<rect id="energy-selected" x="124" y="217" width="88" height="244" fill="none" stroke="#dce8f5" stroke-width="2" opacity="0"/>'
        +_text(None, 130, 507, '太阳能', 15, colors['solar'])+_text(None, 278, 507, '电网', 15, colors['grid'])
        +_text(None, 405, 507, '储能', 15, colors['battery'])+_text(None, 554, 507, '白线：需求', 15)
        +_card('energy-solar', 940, 153, '太阳能', '0', 'MW', colors['solar'])
        +_card('energy-grid', 940, 275, '电网', '0', 'MW', colors['grid'])
        +_card('energy-battery', 940, 397, '储能放电', '0', 'MW', colors['battery'])
        +_text('energy-total', 82, 541, '', 17, '#bcebdc')+_footer())
    script = _common_script(plan, variant, ids)+'''
function effectRender(t){
 const pos=chartPosition(t,1,20,chartData.hours.length),step=Math.min(4,Math.floor(pos));
 for(let i=0;i<5;i++){
  const shown=clamp(pos-i+1);let height=0;
  for(const key of ['solar','grid','battery']){
   const value=chartData[key][i]*shown,h=value*22;
   attrs('energy-'+key+'-'+i,{y:452-height-h,height:h,opacity:chartEnhanced?(i===step?1:.62):.85,'data-value':value});height+=h;
  }
 }
 attrs('energy-columns',{'data-step':chartRound(pos)});
 attrs('energy-demand',{d:chartPath(chartData.demand,pos,168,452,600,220,10),'data-value':chartRound(chartInterpolate(chartData.demand,pos))});
 attrs('energy-selected',{x:124+step*150,opacity:chartEnhanced?.9:.18});
 for(const key of ['solar','grid','battery']){
  const value=chartData[key][step];attrs('energy-'+key,{'data-state':step+':'+value});
  attrs('energy-'+key+'-value',{'data-value':value});label('energy-'+key+'-value',value);
 }
 const sum=chartData.solar[step]+chartData.grid[step]+chartData.battery[step];
 attrs('energy-total',{'data-step':step,'data-value':sum});
 label('energy-total',chartData.hours[step]+'时：'+chartData.solar[step]+' + '+chartData.grid[step]+' + '+chartData.battery[step]+' = '+sum+' MW（需求 '+chartData.demand[step]+'）');
 label('chart-phase',step===0?'清晨：太阳能为零，电网与储能一起供电':step===2?'中午：太阳能 8 MW，仍要从电网补 1 MW':step===4?'夜间：太阳能为零；电网 4 MW + 储能 2 MW':'逐项相加：堆叠柱总高应等于同一时刻的需求');
}
'''
    return dict(markup=markup, script=script, object_ids=ids,
        actions=[_action('energy-columns', 1, 4), _action('energy-demand', 5, 5),
                 _action('energy-selected', 10, 5), _action('energy-total', 15, 5)])


def _logistics(plan, variant):
    ids = ['logistics-in', 'logistics-out', 'logistics-gap', 'logistics-stock',
           'logistics-in-total', 'logistics-out-total', 'logistics-window', 'chart-phase',
           'logistics-stock-value', 'logistics-in-total-value', 'logistics-out-total-value']
    markup = (_text(None, 64, 134, '示意输入 · 箱数；库存 = 累计入库 − 累计出库', 16, '#91a9c0')
        +_panel(64, 153, 850, 367)
        +_text(None, 86, 188, '累计入库', 18, '#8de4c6')
        +_text(None, 270, 188, '累计出库', 18, '#83b8ee')
        +'<path d="M112 224V452H872" fill="none" stroke="#35516a" stroke-width="2"/>'
        +_text(None, 92, 456, '0', 14, '#819eb6', 'end')
        +_text(None, 92, 232, '40', 14, '#819eb6', 'end')
        +'<path id="logistics-in" d="M112 386" fill="none" stroke="#8de4c6" stroke-width="4"/>'
        +'<path id="logistics-out" d="M112 430" fill="none" stroke="#83b8ee" stroke-width="4"/>'
        +'<path id="logistics-gap" d="M112 386V430" fill="none" stroke="#edcf83" stroke-width="12" opacity="0"/>'
        +''.join(_text(None, 112+i*190, 482, value, 15, '#9fb7ca', 'middle')
                  for i, value in enumerate(plan['data']['windows']))
        +_text('logistics-window', 112, 507, '', 17)
        +_card('logistics-stock', 940, 153, '当前库存 · 两线差额', '8', '箱', '#edcf83')
        +_card('logistics-in-total', 940, 275, '累计入库', '12', '箱')
        +_card('logistics-out-total', 940, 397, '累计出库', '4', '箱', '#83b8ee')+_footer())
    script = _common_script(plan, variant, ids)+'''
function effectRender(t){
 const pos=chartPosition(t,1,18,chartData.windows.length),step=Math.min(4,Math.floor(pos));
 const incoming=chartInterpolate(chartData.cumulative_in,pos),outgoing=chartInterpolate(chartData.cumulative_out,pos),stock=incoming-outgoing,x=112+pos*190;
 attrs('logistics-in',{d:chartPath(chartData.cumulative_in,pos,112,452,760,220,40),'data-value':chartRound(incoming)});
 attrs('logistics-out',{d:chartPath(chartData.cumulative_out,pos,112,452,760,220,40),'data-value':chartRound(outgoing)});
 attrs('logistics-gap',{d:'M'+chartCoordinate(x)+' '+chartCoordinate(452-incoming*5.5)+'V'+chartCoordinate(452-outgoing*5.5),opacity:chartEnhanced?.85:.2,'data-value':chartRound(stock)});
 for(const [id,value] of [['logistics-stock',stock],['logistics-in-total',incoming],['logistics-out-total',outgoing]]){
  attrs(id,{'data-state':chartRound(value)});attrs(id+'-value',{'data-value':chartRound(value)});label(id+'-value',chartRound(value));
 }
 attrs('logistics-window',{'data-step':step});label('logistics-window','窗口：'+chartData.windows[step]+' · 入库 '+chartData.incoming[step]+' · 出库 '+chartData.outgoing[step]);
 label('chart-phase',pos<1?'第一批到货后，尚有 8 箱未发出':pos<2?'先清空，再迎来第二批集中到货':pos<3?'累计线只会上升，库存却会随两线差额增减':'两条累计线最终相遇：36 − 36 = 0 箱');
}
'''
    return dict(markup=markup, script=script, object_ids=ids,
        actions=[_action('logistics-in', 1, 4), _action('logistics-stock', 5, 4),
                 _action('logistics-out', 9, 4), _action('logistics-in-total', 14, 4)])


def _learning(plan, variant):
    ids = ['learning-before', 'learning-after', 'learning-focus', 'learning-gains',
           'learning-count', 'learning-delta', 'learning-caution', 'chart-phase',
           'learning-count-value', 'learning-delta-value', 'learning-gain-1', 'learning-gain-2']
    ids += [f'learning-{key}-{index}' for index in range(4) for key in ('before', 'after')]
    bars = {key: [] for key in ('before', 'after')}
    gains = []
    for index, topic in enumerate(plan['data']['topics']):
        x = 150+180*index
        for key, dx, color in [('before', 0, '#83a3c2'), ('after', 66, '#8de4c6')]:
            bars[key].append(f'<rect id="learning-{key}-{index}" x="{x+dx}" y="452" width="46" height="0" fill="{color}"/>'
                +_text(f'learning-{key}-value-{index}', x+dx+23, 442, '0%', 16, color, 'middle'))
        gains.append(_text(f'learning-gain-{index}', x+56, 513, f'+{plan["data"]["gain_pp"][index]} 个百分点', 14, '#edcf83', 'middle'))
    markup = (_text(None, 64, 134, '示意输入 · 同一个 30 人班级；百分比均以 30 为分母', 16, '#91a9c0')
        +_panel(64, 153, 850, 385)
        +_text(None, 86, 188, '前测', 18, '#83a3c2')+_text(None, 224, 188, '后测', 18, '#8de4c6')
        +'<path d="M112 224V452H872" fill="none" stroke="#35516a" stroke-width="2"/>'
        +_text(None, 92, 456, '0%', 14, '#819eb6', 'end')
        +_text(None, 92, 232, '100%', 14, '#819eb6', 'end')
        +'<g id="learning-before">'+''.join(bars['before'])+'</g>'
        +'<g id="learning-after">'+''.join(bars['after'])+'</g>'
        +'<rect id="learning-focus" x="320" y="214" width="132" height="312" fill="none" stroke="#edcf83" stroke-width="2" opacity="0"/>'
        +''.join(_text(None, 206+i*180, 480, topic, 18, '#bbcee0', 'middle') for i, topic in enumerate(plan['data']['topics']))
        +'<g id="learning-gains" opacity="0">'+''.join(gains)+'</g>'
        +_card('learning-count', 940, 153, '推理题掌握人数', '12 → 24', '/ 30')
        +_card('learning-delta', 940, 275, '推理题掌握率变化', '+40', '百分点', '#edcf83')
        +'<g id="learning-caution" opacity="0">'+_panel(940, 397, 270, 125)
        +_text(None, 958, 431, '解释边界', 18, '#edcf83')
        +_text(None, 958, 466, '前后变化 ≠ 因果证明', 17)
        +_text(None, 958, 496, '百分点 ≠ 相对增长率', 17)+'</g>'+_footer())
    script = _common_script(plan, variant, ids)+'''
function effectRender(t){
 const before=progress(t,1,4),after=progress(t,6,4),difference=progress(t,11,4),caution=progress(t,17,3);
 for(const [series,p] of [['before',before],['after',after]]){
  const eased=chartEnhanced?smooth(p):p;attrs('learning-'+series,{'data-state':eased,opacity:eased});
  for(let i=0;i<4;i++){
   const value=chartData[series+'_rate'][i],height=chartRound(value*2.2);
   attrs('learning-'+series+'-'+i,{y:chartRound(452-height),height,'data-value':value});
   attrs('learning-'+series+'-value-'+i,{y:442-height,'data-value':value});label('learning-'+series+'-value-'+i,value+'%');
  }
 }
 fade('learning-gains',difference);attrs('learning-gains',{'data-state':difference});
 attrs('learning-focus',{opacity:difference*(chartEnhanced?.95:.25)});
 attrs('learning-count',{'data-state':after,opacity:after});label('learning-count-value','12 → 24');
 attrs('learning-delta',{'data-state':difference,opacity:difference});label('learning-delta-value','+40');
 fade('learning-caution',caution);
 label('chart-phase',t<6?'先看前测：四类题目的起点不同':t<11?'再看后测：前三类题目都达到 80%':t<17?'推理题从 40% 到 80%，增加 40 个百分点':'只描述变化；不把前后测差异当作教学效果的因果证明');
}
'''
    return dict(markup=markup, script=script, object_ids=ids,
        actions=[_action('learning-before', 1, 4), _action('learning-after', 6, 4),
                 _action('learning-gains', 11, 4, 'reveal'), _action('learning-caution', 17, 3, 'reveal')])


_BUILDERS = {'chart-compute-backlog': _compute, 'chart-energy-balance': _energy,
             'chart-logistics-stock': _logistics, 'chart-learning-gains': _learning}


def build_recipe(plan, variant):
    """Only registered requirement/data pairs can produce executable markup."""
    if type(plan) is not dict or type(variant) is not str or variant not in {'baseline', 'candidate'}:
        raise ValueError('animation_lab_chart_recipe_invalid')
    identity = plan.get('scenario_id')
    if type(identity) is not str or identity not in SCENARIOS:
        raise ValueError('animation_lab_chart_recipe_unknown')
    expected = dict(schema=SCHEMA, scenario_id=identity, **deepcopy(SCENARIOS[identity]))
    if plan != expected:
        raise ValueError('animation_lab_chart_plan_mismatch')
    return _BUILDERS[identity](plan, variant)


def _js_fixed(value):
    """Mirror Number(value.toFixed(2)) for nonnegative finite scene values."""
    rounded = Decimal.from_float(float(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return format(rounded, 'f').rstrip('0').rstrip('.') if rounded else '0'


def _position(time, start, end, count):
    return max(0., min(1., (time-start)/(end-start)))*(count-1)


def _interpolate(values, position):
    index = min(len(values)-2, math.floor(position))
    fraction = position-index
    return values[index]+(values[index+1]-values[index])*fraction


def _expected_path(values, position, x, y, width, height, maximum):
    """Derive the plotted line from frozen values and the actual chart scale."""
    last = math.floor(position)
    points = [('M' if index == 0 else ' L')+_js_fixed(x+index*width/(len(values)-1))
              +' '+_js_fixed(y-values[index]*height/maximum) for index in range(last+1)]
    if position > last:
        points.append(' L'+_js_fixed(x+position*width/(len(values)-1))+' '
                      +_js_fixed(y-_interpolate(values, position)*height/maximum))
    return ''.join(points)


def expected_observations(plan, time):
    """Compute selected actual-DOM expectations independently of JavaScript.

    This is a finite data oracle, not an assertion that snapshots or generated
    pixels passed. The native capture reviewer must compare these values with
    observations bound to the real compiled artifact and render timestamp.
    """
    identity = plan.get('scenario_id') if type(plan) is dict else None
    if identity not in SCENARIOS or plan != dict(schema=SCHEMA, scenario_id=identity, **SCENARIOS[identity]):
        raise ValueError('animation_lab_chart_plan_mismatch')
    if type(time) not in (int, float) or not math.isfinite(time) or not 0 <= time <= plan['duration_seconds']:
        raise ValueError('animation_lab_chart_time_invalid')
    values = plan['data']
    if identity == 'chart-compute-backlog':
        pos = _position(time, 1, 18, len(values['windows']))
        queue = _js_fixed(_interpolate(values['backlog'], pos))
        latency = _js_fixed(_interpolate(values['latency_ms'], pos))
        done = _js_fixed(_interpolate(values['completed'], pos))
        capacity = _js_fixed(_interpolate(values['capacity'], pos))
        return {'compute-queue-value': dict(text=queue, attributes={'data-value': queue}),
                'compute-latency-value': dict(text=latency, attributes={'data-value': latency}),
                'compute-throughput-value': dict(text=done+' / '+capacity),
                'compute-backlog-path': dict(attributes={'d': _expected_path(values['backlog'], pos, 112, 450, 760, 220, 16),
                                                        'data-value': queue}),
                'compute-latency-path': dict(attributes={'d': _expected_path(values['latency_ms'], pos, 112, 450, 760, 220, 400),
                                                        'data-value': latency})}
    if identity == 'chart-energy-balance':
        pos = _position(time, 1, 20, len(values['hours']))
        step = min(4, math.floor(pos))
        result = {f'energy-{key}-value': dict(text=str(values[key][step]), attributes={'data-value': str(values[key][step])})
                  for key in ('solar', 'grid', 'battery')}
        total = values['solar'][step]+values['grid'][step]+values['battery'][step]
        result['energy-total'] = dict(text=f"{values['hours'][step]}时：{values['solar'][step]} + {values['grid'][step]} + {values['battery'][step]} = {total} MW（需求 {values['demand'][step]}）",
                                      attributes={'data-value': str(total)})
        result['energy-demand'] = dict(attributes={'d': _expected_path(values['demand'], pos, 168, 452, 600, 220, 10)})
        below = 0
        for key in ('solar', 'grid', 'battery'):
            height = values[key][step]*22
            result[f'energy-{key}-{step}'] = dict(attributes={'height': str(height), 'y': str(452-below-height)})
            below += height
        return result
    if identity == 'chart-logistics-stock':
        pos = _position(time, 1, 18, len(values['windows']))
        incoming, outgoing = (_interpolate(values[key], pos) for key in ('cumulative_in', 'cumulative_out'))
        result = {f'{key}-value': dict(text=_js_fixed(value), attributes={'data-value': _js_fixed(value)})
                  for key, value in [('logistics-in-total', incoming), ('logistics-out-total', outgoing),
                                     ('logistics-stock', incoming-outgoing)]}
        result['logistics-in'] = dict(attributes={'d': _expected_path(values['cumulative_in'], pos, 112, 452, 760, 220, 40)})
        result['logistics-out'] = dict(attributes={'d': _expected_path(values['cumulative_out'], pos, 112, 452, 760, 220, 40)})
        result['logistics-gap'] = dict(attributes={'d': 'M'+_js_fixed(112+pos*190)+' '+_js_fixed(452-incoming*5.5)+'V'+_js_fixed(452-outgoing*5.5)})
        return result
    result = {'learning-count-value': dict(text='12 → 24'),
              'learning-delta-value': dict(text='+40'),
              'learning-gain-1': dict(text='+40 个百分点'),
              'learning-gain-2': dict(text='+10 个百分点')}
    for index in range(4):
        for key in ('before', 'after'):
            height = float(_js_fixed(values[key+'_rate'][index]*2.2))
            result[f'learning-{key}-{index}'] = dict(attributes={
                'height': _js_fixed(height), 'y': _js_fixed(452-height), 'data-value': str(values[key+'_rate'][index])})
    return result
