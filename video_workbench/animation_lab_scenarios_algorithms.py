"""Finite, cross-domain algorithm scenarios with executable visual traces.

These are developer samples, not an arbitrary code execution API.  Traces are
computed from declared inputs and both presentations replay that same trace.
"""
from __future__ import annotations

from copy import deepcopy
from collections import deque
import html
import json


SCHEMA = 'video.animation-lab-scenario-plan.v1'


def _selection_trace(items):
    values = deepcopy(items)
    trace = []
    for index in range(len(values) - 1):
        minimum = min(range(index, len(values)), key=lambda i: values[i]['price'])
        before = [row['id'] for row in values]
        values[index], values[minimum] = values[minimum], values[index]
        trace.append(dict(index=index, minimum=minimum, before=before,
                          after=[row['id'] for row in values],
                          prices=[row['price'] for row in values],
                          fixed=values[index]['id']))
    return trace


def _binary_trace(values, target):
    low, high, trace = 0, len(values) - 1, []
    while low <= high:
        middle = (low + high) // 2
        next_low, next_high = low, high
        relation = 'found' if values[middle] == target else 'below' if values[middle] < target else 'above'
        if relation == 'below':
            next_low = middle + 1
        elif relation == 'above':
            next_high = middle - 1
        trace.append(dict(low=low, high=high, middle=middle, value=values[middle],
                          relation=relation, next_low=next_low, next_high=next_high))
        if relation == 'found':
            break
        low, high = next_low, next_high
    return trace


def _bfs_trace(nodes, edges, start):
    adjacency = {row['id']: [] for row in nodes}
    for source, target in edges:
        adjacency[source].append(target)
        adjacency[target].append(source)
    queue, distance, parent, levels = deque([start]), {start: 0}, {start: None}, []
    while queue:
        current = queue.popleft()
        depth = distance[current]
        if len(levels) <= depth:
            levels.append([])
        levels[depth].append(current)
        for following in adjacency[current]:
            if following not in distance:
                distance[following], parent[following] = depth + 1, current
                queue.append(following)
    return dict(levels=levels, distance=distance, parent=parent)


def _lru_trace(requests, capacity):
    slots, trace = [], []
    for key in requests:
        before, hit, evicted = slots[:], key in slots, None
        if hit:
            slots.remove(key)
        elif len(slots) == capacity:
            evicted = slots.pop(0)
        slots.append(key)
        trace.append(dict(key=key, before=before, after=slots[:], hit=hit, evicted=evicted))
    return trace


_PRODUCTS = [dict(id='p1', name='商品 A', price=48), dict(id='p2', name='商品 B', price=18),
             dict(id='p3', name='商品 C', price=25), dict(id='p4', name='商品 D', price=30),
             dict(id='p5', name='商品 E', price=36)]
_CHAPTERS = [3, 9, 14, 21, 28, 35, 42]
_STATIONS = [dict(id='A', name='起点', x=170, y=345), dict(id='B', name='北站', x=410, y=220),
             dict(id='C', name='南站', x=410, y=470), dict(id='D', name='北枢纽', x=660, y=220),
             dict(id='E', name='南枢纽', x=660, y=470), dict(id='F', name='终点', x=910, y=345)]
_EDGES = [['A', 'B'], ['A', 'C'], ['B', 'D'], ['C', 'E'], ['D', 'F'], ['E', 'F']]


def _requirement(audience, goal, questions, entities, outcome, boundaries):
    return dict(audience=audience, goal=goal, questions=questions,
                required_entities=entities, expected_outcome=outcome, boundaries=boundaries)


SCENARIOS = {
    'algorithm-shop-selection': dict(
        module='algorithms', domain='commerce', domain_label='电商与消费',
        expression='algorithm-trace', engine='svg', difficulty='basic', duration_seconds=22,
        title='商品按价格排序，为什么要反复找最小值？',
        description='五件商品保留各自身份，演示选择排序的四轮交换与已排好区间。',
        takeaway='每轮固定一个最低价商品；商品身份不变，最后得到 18、25、30、36、48 元。',
        narrative='商品价格是四十八、十八、二十五、三十和三十六元。每一轮只在尚未排序的部分寻找最小值，把它交换到左边。第一轮固定十八元，第二轮固定二十五元，接着是三十元和三十六元。四十八元的商品逐步向右移动，最终整组按价格升序排列。',
        requirement=_requirement('刚接触算法的商品运营人员', '用真实商品对象解释选择排序的交换与不变区间',
            ['每轮搜索哪个区间？', '为什么左边不再参与交换？', '交换后原商品去了哪里？'],
            ['五个有固定身份的商品', '价格', '已排序边界', '本轮最小值', '交换对象'],
            '五个商品无重复无丢失，价格升序，四轮交换均可追踪',
            ['仅五件固定商品，价格均为示意', '不把本例作为排序性能测量', '相等价格的稳定性不在本例结论内']),
        tags=['selection-sort', 'identity', 'swap', 'invariant'],
        data=dict(algorithm='selection-sort', products=deepcopy(_PRODUCTS), trace=_selection_trace(_PRODUCTS))),
    'algorithm-library-binary': dict(
        module='algorithms', domain='education', domain_label='教育与知识检索',
        expression='algorithm-trace', engine='svg', difficulty='intermediate', duration_seconds=18,
        title='在有序目录中，怎样快速找到第 28 页？',
        description='在七个已排序的页码中定位目标，展示中点比较、区间缩小和精确命中。',
        takeaway='比较 21、35、28 三次后找到目标；二分查找成立的前提是目录有序。',
        narrative='目录的页码已经从小到大排好，现在要找二十八。先看中间的二十一，目标更大，因此排除左边。剩下区间的中点是三十五，目标更小，再排除右边。最后只剩二十八，第三次比较就命中。每次排除都有比较依据，不能在无序列表里照搬。',
        requirement=_requirement('查阅课程资料的初学者', '解释二分查找如何排除范围而非顺序扫过每项',
            ['为什么可以排除一半？', '当前左右边界在哪？', '目标命中与排除有什么区别？'],
            ['有序页码', '目标页码 28', '低高边界', '中点指针', '命中状态'],
            '依次比较 21、35、28，最后只保留索引 4 的命中项',
            ['输入已经升序排列', '本例演示目标存在；未找到需另设需求', '索引与页码分开显示']),
        tags=['binary-search', 'ordered-input', 'range-elimination'],
        data=dict(algorithm='binary-search', values=_CHAPTERS[:], target=28, trace=_binary_trace(_CHAPTERS, 28))),
    'algorithm-metro-bfs': dict(
        module='algorithms', domain='transport', domain_label='交通与城市',
        expression='algorithm-trace', engine='svg', difficulty='intermediate', duration_seconds=24,
        title='换乘站数最少的路线，怎样一圈圈找出来？',
        description='以无权交通网络演示广度优先搜索，保留层级、发现顺序与父节点路线。',
        takeaway='终点首次出现在第 3 层；A → B → D → F 是一条 3 段最短路线。',
        narrative='把每段线路都看作同样的一步，从起点 A 向外一圈圈扩展。先发现 B 和 C，再从它们发现 D 和 E。第三圈首次到达终点 F，因此最少需要三段。沿着首次发现时记录的父节点倒推，就得到 A、B、D、F。这里求的是段数最少，不是实际乘车时间最短。',
        requirement=_requirement('需要理解搜索过程的交通科普观众', '用层级扩展解释无权图的最少边数路径',
            ['为什么按圈扩展？', '两条路线都到 F 时如何避免重复？', '如何从发现记录还原路径？'],
            ['六个车站', '六条双向边', '当前层', '已发现集合', '父节点', '返回路线'],
            '发现层为 [A]、[B,C]、[D,E]、[F]，终点距离为 3 且节点不重复加入',
            ['边权相同', '线路为教学示意而非真实城市地图', '不把最少边数称为最少分钟']),
        tags=['breadth-first-search', 'graph', 'frontier', 'shortest-hop'],
        data=dict(algorithm='breadth-first-search', nodes=deepcopy(_STATIONS), edges=deepcopy(_EDGES),
                  start='A', target='F', trace=_bfs_trace(_STATIONS, _EDGES, 'A'), route=['A', 'B', 'D', 'F'])),
    'algorithm-device-lru': dict(
        module='algorithms', domain='industry', domain_label='工业与设备',
        expression='algorithm-trace', engine='svg', difficulty='boundary', duration_seconds=24,
        title='缓存只有两个位置，重复访问与淘汰怎样发生？',
        description='设备依次读取 A、B、A、C、B；演示命中刷新顺序、淘汰和再次未命中。',
        takeaway='访问 A 会让它变新；随后淘汰 B，再次访问 B 仍未命中，最终缓存是 C、B。',
        narrative='设备缓存只能保存两种配置。读取 A 和 B 后缓存已满，再读 A 是命中，但它会移动到最近使用的一端。读取 C 时，最久没有使用的 B 被淘汰。接着又要读 B，这次就不是命中，而要重新载入并淘汰 A。容量没变，变化的是访问顺序。',
        requirement=_requirement('维护边缘设备的工程入门者', '解释 LRU 中命中刷新顺序与被淘汰数据再次访问的边界',
            ['命中为什么也改变缓存？', 'C 到来时淘汰谁？', 'B 曾出现过为何仍未命中？'],
            ['容量为 2 的缓存', 'A/B/C 配置身份', '五次请求', '最久与最近位置', '命中统计', '淘汰区'],
            '五次请求恰有一次命中，依次淘汰 B、A，最终从旧到新为 C、B',
            ['只有缓存逻辑，没有真实设备读写', '不宣称此序列代表一般缓存命中率', '不将 LRU 与先进先出混淆']),
        tags=['lru', 'cache-hit', 'eviction', 'revisit'],
        data=dict(algorithm='lru', capacity=2, requests=['A', 'B', 'A', 'C', 'B'],
                  trace=_lru_trace(['A', 'B', 'A', 'C', 'B'], 2))),
}


def _text(x, y, text, size=22, ident=None, fill='#e8f2ff', anchor='start'):
    identity = f' id="{ident}"' if ident else ''
    return (f'<text{identity} x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}">{html.escape(str(text), quote=True)}</text>')


def _rect(ident, x, y, width, height, fill='#12253a', stroke='#456582'):
    identity = f' id="{ident}"' if ident else ''
    return (f'<rect{identity} x="{x}" y="{y}" width="{width}" height="{height}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('&', '\\u0026')


def _action(target, start, duration, kind='move'):
    return dict(target=target, start=start, duration=duration, kind=kind)


def _common(plan, variant, ids, source):
    # Actual DOM attributes and visible text are returned separately from the
    # derived trace state so callers can check the claimed operation against it.
    return ('const algorithmData='+_json(plan['data'])+';const algorithmCandidate='
        +str(variant == 'candidate').lower()+';const algorithmIds='+_json(ids)+';let algorithmSnapshot={};\n'
        'function algorithmP(t,s,d){const p=progress(t,s,d);return algorithmCandidate?smooth(p):p;}\n'
        'function effectState(){return {...algorithmSnapshot,objects:algorithmIds.map(id=>{const n=$(id);return '
        '{id,text:n.textContent,transform:n.getAttribute("transform"),fill:n.getAttribute("fill"),'
        'opacity:n.getAttribute("opacity"),state:n.getAttribute("data-state")};})};}\n'+source)


def _selection(plan, variant):
    d = plan['data']
    ids = ['sort-fixed', 'sort-operation'] + ['sort-'+row['id'] for row in d['products']]
    markup = (_text(76, 152, '选择排序 · 左侧固定，右侧继续寻找最小值', 23)
              +_text(76, 192, '价格为示意数据；商品编号在移动中保持不变', 17, fill='#a8bfd4')
              +_rect('sort-fixed', 82, 246, 0, 156, '#17483f', '#78dfb7'))
    for index, row in enumerate(d['products']):
        markup += (f'<g id="sort-{row["id"]}" transform="translate({100+index*190} 265)">'
                   +_rect(None, 0, 0, 156, 116)+_text(78, 39, row['name'], 21, anchor='middle')
                   +_text(78, 84, str(row['price'])+' 元', 29, anchor='middle')+'</g>')
    markup += (_text(76, 458, '尚未开始交换', 23, 'sort-operation')
               +_text(76, 509, '每轮：寻找未排序区间的最小值 → 与最左项交换 → 固定一项', 20, fill='#b8cee0')
               +_text(76, 558, '0 项已固定', 20, 'sort-count', '#8ce3c4'))
    source = r'''
const sortStarts=[3,7,11,15],sortDuration=2;
function effectRender(t){
 const initial=algorithmData.products.map(row=>row.id);let order=initial.slice(),completed=0,active=-1;
 const positions=Object.fromEntries(initial.map((id,i)=>[id,i]));
 for(let i=0;i<algorithmData.trace.length;i++){
  const row=algorithmData.trace[i],p=algorithmP(t,sortStarts[i],sortDuration);
  if(t>=sortStarts[i])active=i;
  if(p>0){for(const id of initial)positions[id]=row.before.indexOf(id)+(row.after.indexOf(id)-row.before.indexOf(id))*p;}
  if(p>=1){order=row.after.slice();completed=i+1;}
 }
 for(const row of algorithmData.products){const id='sort-'+row.id,p=positions[row.id],
  changing=active>=0&&t<sortStarts[active]+sortDuration&&algorithmData.trace[active].before.indexOf(row.id)!==algorithmData.trace[active].after.indexOf(row.id);
  const arc=changing&&algorithmCandidate?Math.sin(Math.PI*progress(t,sortStarts[active],sortDuration))*38:0;
  move(id,100+p*190,265+(row.id==='p1'?-arc:arc));
  attrs(id,{'data-state':order.indexOf(row.id)<completed?'sorted':changing?'swapping':'waiting','data-value':String(row.price),'data-slot':String(order.indexOf(row.id)),opacity:algorithmCandidate&&completed>0&&order.indexOf(row.id)>=completed?.8:1});
 }
 attrs('sort-fixed',{width:completed*190- (completed?16:0),opacity:completed?1:0});
 const current=active>=0?algorithmData.trace[active]:null;
 label('sort-operation',current?'第 '+(active+1)+' 轮：把 '+algorithmData.products.find(row=>row.id===current.fixed).price+' 元商品放到第 '+(active+1)+' 位':'尚未开始：先观察原始价格');
 label('sort-count',completed+' 项已固定'+(completed===4?'；最后一项自然归位':''));
 algorithmSnapshot={algorithm:'selection-sort',completed_passes:completed,active_pass:active,order,
  prices:order.map(id=>algorithmData.products.find(row=>row.id===id).price),positions};
}
'''
    return dict(markup=markup, script=_common(plan, variant, ids, source), object_ids=ids,
                actions=[_action('sort-p1', start, 2) for start in (3, 7, 11, 15)])


def _binary(plan, variant):
    d = plan['data']
    ids = ['binary-range', 'binary-mid', 'binary-hit', 'binary-operation'] + ['binary-cell-'+str(i) for i in range(7)]
    markup = (_text(76, 152, '有序目录 · 目标页码 28', 25)
              +_text(76, 195, '范围边框保留候选项；指针指出本轮中点', 18, fill='#a8bfd4')
              +_rect('binary-range', 79, 257, 1086, 133, '#112c3a', '#67d9cf'))
    for index, value in enumerate(d['values']):
        markup += (f'<g id="binary-cell-{index}">'+_rect(None, 90+index*154, 274, 132, 98)
                   +_text(156+index*154, 315, str(value), 29, anchor='middle')
                   +_text(156+index*154, 352, '索引 '+str(index), 16, fill='#a8bfd4', anchor='middle')+'</g>')
    markup += ('<g id="binary-mid" transform="translate(618 232)"><path d="M-12 -20L12 -20L0 0Z" fill="#ffd58a"/></g>'
               +_rect('binary-hit', 706, 274, 132, 98, 'none', '#9beaab')
               +_text(76, 455, '候选范围：索引 0 至 6', 23, 'binary-operation')
               +_text(76, 505, '中点尚未比较', 21, 'binary-relation', '#ffd58a')
               +_text(76, 557, '输入必须有序；索引 4 与页码 28 表示不同概念', 19, fill='#adc7dc'))
    source = r'''
const binaryStarts=[2.5,6.5,10.5],binaryDuration=2;
function effectRender(t){
 let low=0,high=algorithmData.values.length-1,mid=3,index=-1,found=false;
 let visualLow=low,visualHigh=high;
 for(let i=0;i<algorithmData.trace.length;i++){
  const row=algorithmData.trace[i],p=algorithmP(t,binaryStarts[i],binaryDuration);
  if(t>=binaryStarts[i]){index=i;mid=row.middle;
   visualLow=row.low+(row.next_low-row.low)*p;visualHigh=row.high+(row.next_high-row.high)*p;}
  if(p>=1){low=row.next_low;high=row.next_high;if(row.relation==='found')found=true;}
 }
 attrs('binary-range',{x:79+154*visualLow,width:(visualHigh-visualLow+1)*154+8});
 move('binary-mid',156+154*mid,232);
 attrs('binary-hit',{opacity:found?1:0,'stroke-width':algorithmCandidate?4:2,'data-state':found?'found':'pending'});
 for(let i=0;i<algorithmData.values.length;i++)attrs('binary-cell-'+i,{opacity:i<low||i>high?(algorithmCandidate?.25:.55):1,'data-state':i<low||i>high?'excluded':found&&i===mid?'found':'candidate'});
 label('binary-operation','候选范围：索引 '+low+' 至 '+high+(found?' · 命中页码 28':''));
 const row=index>=0?algorithmData.trace[index]:null;
 label('binary-relation',row?'第 '+(index+1)+' 次比较：'+row.value+(row.relation==='below'?' < 28，排除左侧':row.relation==='above'?' > 28，排除右侧':' = 28，精确命中'):'中点尚未比较');
 algorithmSnapshot={algorithm:'binary-search',comparison:index+1,low,high,middle:mid,found,target:28,
  excluded:algorithmData.values.map((v,i)=>i<low||i>high?i:null).filter(v=>v!==null)};
}
'''
    return dict(markup=markup, script=_common(plan, variant, ids, source), object_ids=ids,
                actions=[_action('binary-range', 2.5, 2), _action('binary-range', 6.5, 2),
                         _action('binary-hit', 10.5, 2, 'reveal')])


def _bfs(plan, variant):
    d = plan['data']
    ids = ['bfs-level-'+str(i) for i in range(4)] + ['bfs-route', 'bfs-operation']
    nodes = {row['id']: row for row in d['nodes']}
    markup = (_text(76, 145, '无权线路网 · 每条边都算一步', 23)
              +_text(1030, 245, '发现层', 20, fill='#a8bfd4'))
    for source, target in d['edges']:
        a, b = nodes[source], nodes[target]
        markup += f'<path d="M{a["x"]} {a["y"]}L{b["x"]} {b["y"]}" stroke="#40556b" stroke-width="4" fill="none"/>'
    points = ' '.join(str(nodes[i]['x'])+','+str(nodes[i]['y']) for i in d['route'])
    markup += f'<polyline id="bfs-route" points="{points}" stroke="#ffd58a" stroke-width="7" fill="none" opacity="0"/>'
    for depth, level in enumerate(d['trace']['levels']):
        markup += f'<g id="bfs-level-{depth}" opacity="0">'
        for ident in level:
            n = nodes[ident]
            markup += (f'<circle cx="{n["x"]}" cy="{n["y"]}" r="39" fill="#14483e" stroke="#82e0bd" stroke-width="3"/>'
                       +_text(n['x'], n['y']+7, ident, 27, anchor='middle')
                       +_text(n['x'], n['y']+67, n['name'], 18, anchor='middle'))
        markup += _text(1030, 291+depth*49, str(depth)+'：'+', '.join(level), 20)+'</g>'
    markup += (_text(76, 583, '尚未发现起点', 21, 'bfs-operation')
               +_text(1030, 507, '去重后入队', 18, fill='#a8bfd4'))
    source = r'''
const bfsStarts=[2,7,12,17],bfsDuration=2;
function effectRender(t){
 let depth=-1;const discovered=[];
 for(let i=0;i<algorithmData.trace.levels.length;i++){
  const p=algorithmP(t,bfsStarts[i],bfsDuration);if(p>=1){depth=i;discovered.push(...algorithmData.trace.levels[i]);}
  fade('bfs-level-'+i,p);
  attrs('bfs-level-'+i,{'data-state':p>=1?'discovered':p>0?'frontier':'undiscovered'});
 }
 const done=depth===3,p=progress(t,19,2);fade('bfs-route',done?(algorithmCandidate?smooth(p):p):0);
 attrs('bfs-route',{'data-state':done?'three-hop-shortest':'pending'});
 label('bfs-operation',depth<0?'尚未发现起点':depth<3?'已完成第 '+depth+' 层；下一圈只加入尚未发现的车站':'首次到达 F：3 段；父节点路线 A → B → D → F');
 algorithmSnapshot={algorithm:'breadth-first-search',depth,discovered,
  frontier:depth<0?[]:algorithmData.trace.levels[depth],target_distance:done?3:null,
  route:done?algorithmData.route:[],parent:algorithmData.trace.parent};
}
'''
    return dict(markup=markup, script=_common(plan, variant, ids, source), object_ids=ids,
                actions=[_action('bfs-level-'+str(i), start, 2, 'reveal')
                         for i, start in enumerate((2, 7, 12, 17))]
                         +[_action('bfs-route', 19, 2, 'reveal')])


def _lru(plan, variant):
    d = plan['data']
    ids = ['cache-slots', 'cache-moving', 'cache-evicted', 'cache-operation',
           'cache-old', 'cache-new', 'cache-evicted-key']
    markup = (_text(76, 150, '配置缓存 · 容量固定为 2', 24)
              +_text(76, 189, '请求顺序', 18, fill='#a8bfd4'))
    for index, key in enumerate(d['requests']):
        markup += (f'<g id="cache-request-{index}">'+_rect(None, 76+index*105, 208, 84, 65)
                   +_text(118+index*105, 249, key, 28, anchor='middle')+'</g>')
    markup += ('<g id="cache-slots">'+_rect('cache-frame', 390, 330, 520, 145)
               +_text(520, 369, '最久未用 LRU', 18, fill='#a8bfd4', anchor='middle')
               +_text(780, 369, '最近使用 MRU', 18, fill='#a8bfd4', anchor='middle')
               +_text(520, 431, '空', 33, 'cache-old', anchor='middle')
               +_text(780, 431, '空', 33, 'cache-new', anchor='middle')+'</g>'
               +'<g id="cache-moving" transform="translate(118 294)" opacity="0"><circle r="23" fill="#7ee0cb"/>'
               +_text(0, 8, 'A', 23, 'cache-moving-key', '#092022', 'middle')+'</g>'
               +'<g id="cache-evicted" transform="translate(1030 415)" opacity="0">'
               +_rect(None, -50, -49, 140, 98, '#452d35', '#d58a9a')
               +_text(20, -16, '被淘汰', 17, fill='#e6a5b3', anchor='middle')
               +_text(20, 25, '—', 29, 'cache-evicted-key', anchor='middle')+'</g>'
               +_text(76, 538, '尚未访问，缓存为空', 22, 'cache-operation')
               +_text(76, 581, '命中 0 / 0 · 旧 → 新', 19, 'cache-count', '#8ce3c4'))
    source = r'''
const cacheStarts=[2,6,10,14,18],cacheDuration=2;
function effectRender(t){
 let slots=[],active=-1,completed=0,hits=0,evicted=null,movingP=0;
 for(let i=0;i<algorithmData.trace.length;i++){
  const row=algorithmData.trace[i],p=algorithmP(t,cacheStarts[i],cacheDuration);
  if(t>=cacheStarts[i]){active=i;movingP=p;}
  if(p>=1){slots=row.after.slice();completed=i+1;hits+=row.hit?1:0;if(row.evicted)evicted=row.evicted;}
 }
 label('cache-old',slots.length===2?slots[0]:'空');label('cache-new',slots.length?slots[slots.length-1]:'空');
 attrs('cache-slots',{'data-state':slots.join(',')||'empty'});
 const row=active<0?null:algorithmData.trace[active],moving=row&&movingP>0&&movingP<1;
 fade('cache-moving',moving?1:0);
 const sourceX=active<0?118:118+active*105;
 move('cache-moving',sourceX+(780-sourceX)*movingP,294+(414-294)*movingP);
 label('cache-moving-key',row?row.key:'A');
 fade('cache-evicted',evicted?1:0);label('cache-evicted-key',evicted||'—');
 attrs('cache-frame',{stroke:algorithmCandidate&&row?(row.hit?'#8ce3c4':'#e6be7c'):'#456582'});
 for(let i=0;i<algorithmData.requests.length;i++)attrs('cache-request-'+i,{opacity:algorithmCandidate&&active>=0&&i!==active?.5:1,'data-state':i<completed?'completed':i===active?'current':'pending'});
 label('cache-operation',row?'第 '+(active+1)+' 次读取 '+row.key+'：'+(row.hit?'命中，刷新到最近位置':row.evicted?'未命中，淘汰 '+row.evicted:'未命中，加入空位'):'尚未访问，缓存为空');
 label('cache-count','命中 '+hits+' / '+completed+' · 旧 → 新'+(completed===5?'；最终 C、B':''));
 algorithmSnapshot={algorithm:'lru',completed,active,slots,hits,last_evicted:evicted,
  current_request:row?row.key:null,current_hit:row?row.hit:null};
}
'''
    return dict(markup=markup, script=_common(plan, variant, ids, source), object_ids=ids,
                actions=[_action('cache-slots', start, 2, 'set-state') for start in (2, 6, 10, 14, 18)])


_BUILDERS = {'selection-sort': _selection, 'binary-search': _binary,
             'breadth-first-search': _bfs, 'lru': _lru}


def _registered(plan):
    if (type(plan) is not dict or type(plan.get('scenario_id')) is not str
            or plan['scenario_id'] not in SCENARIOS):
        raise ValueError('animation_lab_algorithm_scenario_invalid')
    expected = dict(schema=SCHEMA, scenario_id=plan['scenario_id'], **deepcopy(SCENARIOS[plan['scenario_id']]))
    if plan != expected:
        raise ValueError('animation_lab_algorithm_scenario_invalid')
    return deepcopy(plan)


def build_recipe(plan, variant):
    """Compile only a complete registered requirement and its frozen input."""
    plan = _registered(plan)
    if type(variant) is not str or variant not in {'baseline', 'candidate'}:
        raise ValueError('animation_lab_algorithm_scenario_invalid')
    return _BUILDERS[plan['data']['algorithm']](plan, variant)


def expected_observations(plan, time):
    """Independent Python trace oracle for measured DOM, never JS self-reports.

    Both comparison variants use identical event boundaries and discrete facts.
    Only stable categorical/value attributes are compared, leaving animation
    interpolation to the separate geometry and screenshot checks.
    """
    import math
    plan = _registered(plan)
    if type(time) not in (int, float) or not math.isfinite(time) or not 0 <= time <= plan['duration_seconds']:
        raise ValueError('animation_lab_algorithm_time_invalid')
    d = plan['data']
    if d['algorithm'] == 'selection-sort':
        starts = [3, 7, 11, 15]
        completed = sum(time >= start+2 for start in starts)
        active = max((i for i, start in enumerate(starts) if time >= start), default=-1)
        order = [r['id'] for r in d['products']] if not completed else d['trace'][completed-1]['after']
        result = {}
        for item in d['products']:
            changing = (active >= 0 and time < starts[active]+2
                and d['trace'][active]['before'].index(item['id']) != d['trace'][active]['after'].index(item['id']))
            result['sort-'+item['id']] = dict(attributes={
                'data-state': 'sorted' if order.index(item['id']) < completed else 'swapping' if changing else 'waiting',
                'data-value': str(item['price']), 'data-slot': str(order.index(item['id']))})
        return result
    if d['algorithm'] == 'binary-search':
        completed = [i for i, start in enumerate([2.5, 6.5, 10.5]) if time >= start+2]
        last = d['trace'][completed[-1]] if completed else None
        low, high = (last['next_low'], last['next_high']) if last else (0, 6)
        found = bool(last and last['relation'] == 'found')
        result = {'binary-hit': dict(attributes={'data-state': 'found' if found else 'pending', 'opacity': '1' if found else '0'}),
                  'binary-operation': dict(text='候选范围：索引 '+str(low)+' 至 '+str(high)+(' · 命中页码 28' if found else ''))}
        # The first, target and last cells provide independent evidence that
        # range elimination agrees with what remains visible and selectable.
        for i in [0, 4, 6]:
            result['binary-cell-'+str(i)] = dict(attributes={
                'data-state': 'excluded' if i < low or i > high else 'found' if found and i == 4 else 'candidate'})
        return result
    if d['algorithm'] == 'breadth-first-search':
        result = {}
        for i, start in enumerate([2, 7, 12, 17]):
            result['bfs-level-'+str(i)] = dict(attributes={
                'data-state': 'discovered' if time >= start+2 else 'frontier' if time > start else 'undiscovered'})
        result['bfs-route'] = dict(attributes={'data-state': 'three-hop-shortest' if time >= 19 else 'pending'})
        return result
    completed = sum(time >= start+2 for start in [2, 6, 10, 14, 18])
    completed_trace = d['trace'][:completed]
    slots = completed_trace[-1]['after'] if completed_trace else []
    evicted = next((r['evicted'] for r in reversed(completed_trace) if r['evicted']), None)
    return {'cache-slots': dict(attributes={'data-state': ','.join(slots) or 'empty'}),
            'cache-old': dict(text=slots[0] if len(slots) == 2 else '空'),
            'cache-new': dict(text=slots[-1] if slots else '空'),
            'cache-evicted-key': dict(text=evicted or '—')}
