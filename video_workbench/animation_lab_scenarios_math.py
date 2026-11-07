"""Four finite mathematical derivations rendered as measured SVG terms.

The examples are instructional inputs, never diagnoses or arbitrary symbolic
execution. Both presentations keep the same premises, values, rule order and
duration; the candidate adds a local focus and a progress cue. The Python
oracle derives arithmetic independently and checks the actual SVG term text,
coordinates, paths and transforms rather than the JavaScript self-report.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import html
import json
import math


SCHEMA = 'video.animation-lab-scenario-plan.v1'
_STARTS = (2, 6, 10, 14)
_SECONDS = 3


def _requirement(audience, goal, questions, entities, outcome, boundaries):
    return dict(audience=audience, goal=goal, questions=questions,
                required_entities=entities, expected_outcome=outcome, boundaries=boundaries)


SCENARIOS = {
    'math-commerce-balance': dict(
        module='math', domain='commerce', domain_label='电商与消费',
        expression='symbolic-derivation', engine='svg', difficulty='basic', duration_seconds=22,
        title='三件商品加配送费，怎样反推出单价？',
        description='等式两边同步减去配送费、同步除以件数，再把单价代回原式核对。',
        narrative='三件相同商品，加上六元配送费，一共三十元。把单价记作 x，就有三 x 加六等于三十。先在等式两边都减去六，配送费消去，三件商品只剩二十四元。接着两边都除以三，每件就是八元。最后把八代回原式：三乘八加六，确实等于三十。两边必须做同一个运算，等式才继续成立。',
        takeaway='同减 6，再同除 3：x = 8 元；代回原式得到 30 = 30。',
        requirement=_requirement('学习生活应用题的初学者', '用保持相等的双边操作解释一元一次方程，而非直接报出答案',
            ['为什么不能只在一边减 6？', '三件总价与单件价格有什么区别？', '怎样检验求出的单价？'],
            ['三件同价商品', '未知单价 x', '配送费 6 元', '总价 30 元', '等号', '两边的减法与除法', '代回检验'],
            '先得到 3x=24，再得到 x=8，代回得到 3×8+6=30',
            ['三件商品单价完全相同且无折扣', '配送费为固定 6 元', '除数 3 非零，金额为题设示意数据']),
        tags=['等式性质', '逐项消去', '代回检验'],
        checks=['两边同步减去同一个数', '两边同步除以非零件数', '原式代回核对，而非只核对结果标签'],
        data=dict(kind='linear-equation', count=3, fee=6, total=30, currency='元',
            premise='题设：3 件同价商品 + 固定配送费 6 元 = 30 元；单价记为 x。',
            rules=['两边同减 6：消去配送费，保持等式相等', '两边同除 3：从三件总价得到单件价格',
                   '把求出的 x = 8 代回原来的 3x + 6', '计算左边：3 × 8 + 6 = 30，与右边相等'])),
    'math-geometry-triangle': dict(
        module='math', domain='education', domain_label='几何与教育',
        expression='geometric-derivation', engine='svg', difficulty='intermediate', duration_seconds=22,
        title='三角形面积为什么要除以二？',
        description='按同一比例绘制 12×8 矩形，画对角线并分开两块全等直角三角形，连接图形与面积公式。',
        narrative='这个矩形长十二厘米，高八厘米，面积是九十六平方厘米。从左下角向右上角画一条对角线，矩形就分成两块全等的直角三角形。把其中一块轻轻移开，它们的形状和面积都没有改变。因此一块的面积就是整个矩形的一半，也就是十二乘八再除以二，等于四十八平方厘米。这里的高必须与底边垂直，不能把斜边当成高。',
        takeaway='矩形 12 × 8 = 96 cm²；两块全等三角形各占一半，每块 48 cm²。',
        requirement=_requirement('理解面积公式的几何初学者', '用真实图形分割推导直角三角形面积，不把除以二当成口诀',
            ['对角线把矩形分成什么图形？', '为什么每块正好占一半？', '底与高为何必须垂直？'],
            ['12 cm 底边', '8 cm 垂直高度', '矩形', '角到角的对角线', '两块全等三角形', '分数横线与面积单位'],
            '矩形面积为 96 cm²，两块面积相等，每块 48 cm²，移动不改变图形面积',
            ['图形为直角三角形与矩形的有限示例', '横纵坐标均采用 32 像素每厘米，同一比例尺', '两块形状完全由同一条角到角对角线切分，平移不缩放']),
        tags=['面积守恒', '全等', '底高垂直', '分数'],
        checks=['矩形宽高对应 12:8', '对角线连接两个相对顶点', '两块路径面积各为矩形一半且平移不缩放'],
        data=dict(kind='triangle-area', base_cm=12, height_cm=8, pixels_per_cm=32,
            origin=[170, 250],
            premise='题设：矩形长 12 cm、高 8 cm；横纵同尺，底与高相互垂直。',
            rules=['先求矩形面积：12 × 8 = 96 cm²', '画角到角的对角线，得到两块全等直角三角形',
                   '只平移其中一块：形状和面积保持不变', '每块占一半：A = 12 × 8 ÷ 2 = 48 cm²'])),
    'math-health-bayes': dict(
        module='math', domain='healthcare', domain_label='概率与健康科普',
        expression='probability-derivation', engine='svg', difficulty='boundary', duration_seconds=24,
        title='检测阳性，为什么不等于九成概率患病？',
        description='把先验、敏感度与假阳性率转为同一千人总体中的人数，再从阳性集合计算条件概率。',
        narrative='这是一组假设数据，一千人中有百分之一患病，也就是十人。检测敏感度为百分之九十，因此其中九人会呈阳性。另外九百九十人没有患病，但假阳性率是百分之十，所以会有九十九人也呈阳性。现在只看阳性的这一组，一共是一百零八人，其中九人才属于患病组。阳性后的患病概率就是九除以一百零八，约百分之八点三。敏感度的分母是患病者，阳性后概率的分母却是所有阳性者。',
        takeaway='假设数据：9 个真阳性 + 99 个假阳性；P(患病 | 阳性) = 9 / 108 ≈ 8.3%。',
        requirement=_requirement('希望理解检测结果的概率科普观众', '通过自然频数解释贝叶斯条件概率及分母切换',
            ['90% 的分母是谁？', '99 个假阳性从哪里来？', '为什么分母必须包含两类阳性？'],
            ['1000 人总体', '1% 先验患病率', '90% 敏感度', '10% 假阳性率', '9 个真阳性', '99 个假阳性', '108 人阳性集合'],
            '得到 9/(9+99)=1/12≈8.3%，同时保留总体与阳性集合的不同分母',
            ['全部为概率教学假设，不代表任何真实检测或个人诊断', '假阳性率以未患病者为分母，敏感度以患病者为分母', '人数由该组固定比例精确得到，分组卡片不按总体人数比例绘制；阳性条按人数比例绘制']),
        tags=['贝叶斯', '自然频数', '条件概率', '分母切换'],
        checks=['真阳性和假阳性分别由各自总体计算', '阳性条 9:99 与实际宽度一致', '后验分母是两类阳性之和而非全部 1000 人'],
        data=dict(kind='bayes-frequency', population=1000, prevalence=[1, 100], sensitivity=[9, 10],
            false_positive_rate=[1, 10], positive_pixels_per_person=8,
            premise='假设：1000 人，患病率 1%，敏感度 90%，假阳性率 10%；非诊断建议。',
            rules=['先统一总体：1000 × 1% = 10 人患病，其余 990 人未患病',
                   '患病组中的阳性：10 × 90% = 9 人', '未患病组中的阳性：990 × 10% = 99 人',
                   '只在阳性集合中计算：9 ÷ (9 + 99) = 1/12 ≈ 8.3%'])),
    'math-transport-units': dict(
        module='math', domain='transport', domain_label='交通与物理',
        expression='dimensional-derivation', engine='svg', difficulty='intermediate', duration_seconds=22,
        title='72 千米每小时，三秒到底前进多少米？',
        description='以两个等于一的单位换算因子消去 km 与 h，再将速度乘时间，解释剩余单位。',
        narrative='车辆以七十二千米每小时匀速前进，想知道三秒走多远。先把路程单位从千米换成米，数值乘一千。再把一小时换成三千六百秒，数值除以三千六百。画面上的两个换算分数，分子和分母都表示同一个物理量，所以都等于一。原来的千米和小时单位依次消去，七十二乘一千除以三千六百，得到每秒二十米。匀速距离等于速度乘时间，二十米每秒乘三秒，秒再消去，剩下六十米。这里只讨论匀速，不是刹车距离。',
        takeaway='72 km/h × (1000 m / 1 km) × (1 h / 3600 s) = 20 m/s；匀速 3 s 走 60 m。',
        requirement=_requirement('需要连接物理公式与生活量纲的初学者', '解释单位换算因子为何等于一，以及距离式中单位如何消去',
            ['为什么不能直接把 72 乘 3？', 'km 和 h 分别被哪个因子消去？', '为什么最后只剩米？'],
            ['速度 72 km/h', '1000 m / 1 km', '1 h / 3600 s', '速度 20 m/s', '时间 3 s', '距离 60 m', '相消的单位项'],
            '量纲相消后得到 20 m/s，再由 s=v×t 得到 60 m',
            ['假定全过程匀速直线运动', '1 km=1000 m 且 1 h=3600 s 为单位定义', '不将该距离称为刹车距离或安全跟车距离']),
        tags=['量纲分析', '单位换算', '匀速关系'],
        checks=['换算因子的分子分母表示相同物理量', '真实符号中的 km 与 h 成对消去', 's=v×t 的秒单位相消，最后距离单位为 m'],
        data=dict(kind='unit-conversion', speed_kmh=72, metres_per_kilometre=1000,
            seconds_per_hour=3600, time_seconds=3,
            premise='题设：72 km/h 匀速直线运动 3 s；1 km = 1000 m，1 h = 3600 s。',
            rules=['乘 1000 m / 1 km：分子分母是同一个长度，消去 km',
                   '乘 1 h / 3600 s：分子分母是同一个时长，消去 h',
                   '数值与单位一起化简：72 × 1000 ÷ 3600 = 20 m/s',
                   '匀速距离 = 速度 × 时间；m/s × s = m，得到 60 m'])),
}


def _text(ident, x, y, text, size=26, fill='#eaf3ff', anchor='start'):
    identity = f' id="{ident}"' if ident else ''
    return (f'<text{identity} x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}">{html.escape(str(text))}</text>')


def _rect(ident, x, y, width, height, fill='#12273b', stroke='#34546e'):
    identity = f' id="{ident}"' if ident else ''
    return (f'<rect{identity} x="{x}" y="{y}" width="{width}" height="{height}" '
            f'rx="12" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')


def _path(ident, d, stroke='#f0cc7d', width=3, fill='none'):
    return f'<path id="{ident}" d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'


def _intro(plan):
    return (_text(None, 72, 122, plan['data']['premise'], 17, '#a8c1d6')
            +_text('math-step', 1135, 122, '步骤 0 / 4', 16, '#92b2cc', 'end')
            +_rect('math-rule-panel', 64, 141, 1152, 48)
            +_text('math-rule', 84, 173, '先读题设，确定数据与前提', 20, '#8ee1c8')
            +_rect('math-progress', 64, 190, 0, 3, '#8ee1c8', 'none')
            +_rect('math-focus', 100, 210, 1, 1, 'none', '#8ee1c8'))


def _common(plan, variant, source, focus):
    data = json.dumps(plan['data'], ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('&', '\\u0026')
    return ('const mathData='+data+';const mathEnhanced='+str(variant == 'candidate').lower()
            +';const mathFocus='+json.dumps(focus)+';let mathSnapshot={};\n'+r'''
const mathP=(t,start)=>progress(t,start,3),mathN=value=>Math.round(value);
function mathGuide(t){
 const starts=[2,6,10,14],index=starts.reduce((last,start,i)=>t>=start?i:last,-1);
 const completed=starts.filter(start=>t>=start+3).length;
 label('math-step','步骤 '+completed+' / 4');
 label('math-rule',index<0?'先读题设，确定数据与前提':mathData.rules[index]);
 attrs('math-rule-panel',{stroke:mathEnhanced?'#65d7be':'#34546e'});
 attrs('math-progress',{width:index<0?0:mathN(mathP(t,starts[index])*1152),opacity:mathEnhanced?.9:.2});
 const box=mathFocus[Math.max(0,index)];
 attrs('math-focus',{x:box[0],y:box[1],width:box[2],height:box[3],opacity:mathEnhanced&&index>=0?.9:0});
 mathSnapshot={kind:mathData.kind,completed_steps:completed,current_step:index};
}
function effectState(){return {...mathSnapshot};}
'''+source)


def _recipe(plan, variant, markup, script, ids, targets, focus):
    return dict(markup=_intro(plan)+markup, script=_common(plan, variant, script, focus),
                object_ids=['math-step', 'math-rule', 'math-rule-panel', 'math-progress', 'math-focus', *ids],
                actions=[dict(target=target, kind='derive', start=start, duration=_SECONDS)
                         for target, start in zip(targets, _STARTS)])


def _balance(plan, variant):
    ids = ['balance-coefficient', 'balance-variable', 'balance-fee', 'balance-total',
           'balance-minus-left', 'balance-minus-right', 'balance-divide-left', 'balance-divide-right',
           'balance-cancel', 'balance-check', 'balance-check-left', 'balance-check-right', 'balance-check-status']
    markup = (_rect(None, 220, 220, 360, 210)+_rect(None, 710, 220, 360, 210)
        +_text(None, 400, 252, '商品 + 配送费', 19, '#a8c1d6', 'middle')
        +_text(None, 890, 252, '付款总额', 19, '#a8c1d6', 'middle')
        +_text('balance-coefficient', 325, 315, '3', 44)+_text('balance-variable', 365, 315, 'x', 44)
        +_text('balance-fee', 430, 315, '+ 6', 44)+_text(None, 632, 315, '=', 42, anchor='middle')
        +_text('balance-total', 820, 315, '30', 44)
        +_text('balance-minus-left', 430, 374, '− 6', 30, '#f0cc7d')
        +_text('balance-minus-right', 820, 374, '− 6', 30, '#f0cc7d')
        +_text('balance-divide-left', 330, 396, '÷ 3', 29, '#8ee1c8')
        +_text('balance-divide-right', 820, 396, '÷ 3', 29, '#8ee1c8')
        +_path('balance-cancel', 'M424 300 L424 300', '#f0cc7d')
        +'<g id="balance-check">'+_rect(None, 220, 468, 850, 112)
        +_text(None, 244, 498, '代回原式', 19, '#a8c1d6')
        +_text('balance-check-left', 352, 548, '3 × 8 + 6', 32)
        +_text(None, 600, 548, '=', 32)+_text('balance-check-right', 660, 548, '30', 32)
        +_text('balance-check-status', 790, 548, '等待核对', 22, '#8ee1c8')+'</g>')
    script = r'''
function effectRender(t){
 mathGuide(t);const d=mathData,net=d.total-d.fee,price=net/d.count;
 const sub=mathP(t,2),div=mathP(t,6),put=mathP(t,10),check=mathP(t,14);
 label('balance-total',t>=9?price:t>=5?net:d.total);
 attrs('balance-total',{'data-value':t>=9?price:t>=5?net:d.total,'data-state':t>=9?'unit-price':t>=5?'items-total':'paid-total'});
 fade('balance-fee',t>=5?0:1);fade('balance-coefficient',t>=9?0:1);
 move('balance-variable',mathN(85*div),0);attrs('balance-variable',{'data-value':t>=9?price:'unknown'});
 for(const id of ['balance-minus-left','balance-minus-right']){fade(id,t>=5?0:sub);move(id,0,mathN(20*(1-sub)));}
 for(const id of ['balance-divide-left','balance-divide-right']){fade(id,t>=9?0:div);move(id,0,-mathN(16*div));}
 attrs('balance-cancel',{d:'M424 300 L'+(424+mathN(100*sub))+' '+(300-mathN(20*sub)),opacity:t>=5?0:sub});
 fade('balance-check',put);move('balance-check',0,mathN(24*(1-put)));
 label('balance-check-left',t>=17?d.count*price+d.fee:d.count+' × '+price+' + '+d.fee);
 label('balance-check-right',d.total);
 label('balance-check-status',t>=17?'两边相等':t>=13?'先算左边':'代入 x = '+price);
 attrs('balance-check-status',{'data-state':t>=17?'verified':'pending','data-value':t>=17?d.count*price+d.fee:'pending'});
}
'''
    return _recipe(plan, variant, markup, script, ids,
        ['balance-total', 'balance-variable', 'balance-check', 'balance-check-status'],
        [[300, 265, 650, 132], [300, 265, 650, 145], [330, 509, 410, 55], [330, 509, 670, 55]])


def _triangle(plan, variant):
    d = plan['data']
    x, y = d['origin']
    w, h = d['base_cm']*d['pixels_per_cm'], d['height_cm']*d['pixels_per_cm']
    a = f'M{x} {y} L{x+w} {y} L{x} {y+h} Z'
    b = f'M{x+w} {y} L{x+w} {y+h} L{x} {y+h} Z'
    ids = ['area-rectangle', 'area-diagonal', 'area-first', 'area-second', 'area-second-path',
           'area-width', 'area-height', 'area-right-angle', 'area-rectangle-value',
           'area-numerator', 'area-denominator', 'area-fraction-line', 'area-result']
    markup = (_rect('area-rectangle', x, y, 0, h, '#11283d')
        +_path('area-first', a, '#72c1df', 2, '#205c78')
        +'<g id="area-second">'+_path('area-second-path', b, '#87dfbe', 2, '#1d584d')
        +_text(None, 420, 442, '同样大的一块', 19, '#bceede', 'middle')+'</g>'
        +_path('area-diagonal', f'M{x} {y+h} L{x} {y+h}', '#f0cc7d', 4)
        +_text('area-width', x+w/2, y+h+38, '12 cm', 22, anchor='middle')
        +_text('area-height', x-25, y+h/2, '8 cm', 22, anchor='end')
        +_path('area-right-angle', f'M{x+20} {y} V{y+20} H{x}', '#a8c1d6', 2)
        +_text('area-rectangle-value', 770, 256, '矩形：12 × 8 = 96 cm²', 25)
        +_text(None, 770, 320, '一块三角形的面积', 21, '#a8c1d6')
        +_text('area-numerator', 950, 380, '12 × 8', 34, anchor='middle')
        +_path('area-fraction-line', 'M850 397 H1050', '#eaf3ff', 3)
        +_text('area-denominator', 950, 440, '2', 32, anchor='middle')
        +_text('area-result', 830, 525, '= ?', 36, '#8ee1c8'))
    script = r'''
function effectRender(t){
 mathGuide(t);const d=mathData,[x,y]=d.origin,w=d.base_cm*d.pixels_per_cm,h=d.height_cm*d.pixels_per_cm;
 const box=mathP(t,2),cut=mathP(t,6),separate=mathP(t,10),result=mathP(t,14);
 attrs('area-rectangle',{x,y,width:mathN(w*box),height:h,'data-value':t>=5?d.base_cm*d.height_cm:0});
 attrs('area-diagonal',{d:'M'+x+' '+(y+h)+' L'+(x+mathN(w*cut))+' '+(y+h-mathN(h*cut)),opacity:cut});
 fade('area-first',cut);fade('area-second',cut);move('area-second',mathN(90*separate),mathN(60*separate));
 attrs('area-second',{'data-state':t>=13?'translated-congruent':'same-rectangle'});
 fade('area-width',box);fade('area-height',box);fade('area-right-angle',box);
 fade('area-rectangle-value',box);
 for(const id of ['area-numerator','area-denominator','area-fraction-line'])fade(id,result);
 label('area-result',t>=17?'= '+d.base_cm*d.height_cm/2+' cm²':'= ?');
 attrs('area-result',{'data-value':t>=17?d.base_cm*d.height_cm/2:'unknown',opacity:result});
}
'''
    return _recipe(plan, variant, markup, script, ids,
        ['area-rectangle', 'area-diagonal', 'area-second', 'area-result'],
        [[146, 224, 432, 325], [146, 224, 432, 325], [238, 286, 432, 295], [780, 338, 370, 215]])


def _bayes(plan, variant):
    ids = ['bayes-disease-count', 'bayes-healthy-count', 'bayes-branch-left', 'bayes-branch-right',
           'bayes-tp-rule', 'bayes-fp-rule', 'bayes-true-positive', 'bayes-false-positive',
           'bayes-tp-count', 'bayes-fp-count', 'bayes-numerator', 'bayes-denominator',
           'bayes-fraction-line', 'bayes-posterior', 'bayes-pool-caption']
    markup = (_text(None, 640, 229, '1000 人总体', 25, anchor='middle')
        +_path('bayes-branch-left', 'M580 240 L580 240', '#72bfe5')
        +_path('bayes-branch-right', 'M700 240 L700 240', '#c7abf1')
        +_rect(None, 145, 270, 350, 104)+_rect(None, 785, 270, 350, 104)
        +_text('bayes-disease-count', 320, 309, '患病：?', 26, '#87d8f5', 'middle')
        +_text('bayes-healthy-count', 960, 309, '未患病：?', 26, '#d8c3f3', 'middle')
        +_text('bayes-tp-rule', 320, 348, '10 × 90% = ?', 23, anchor='middle')
        +_text('bayes-fp-rule', 960, 348, '990 × 10% = ?', 23, anchor='middle')
        +_text('bayes-pool-caption', 180, 415, '现在只看阳性者；条宽按人数比例绘制', 20, '#a8c1d6')
        +_rect('bayes-true-positive', 180, 432, 0, 56, '#2781a0', 'none')
        +_rect('bayes-false-positive', 252, 432, 0, 56, '#815ca8', 'none')
        +_text('bayes-tp-count', 216, 468, '9', 24, anchor='middle')
        +_text('bayes-fp-count', 648, 468, '99', 24, anchor='middle')
        +_text(None, 90, 570, 'P(患病 | 阳性) =', 24)
        +_text('bayes-numerator', 570, 546, '9', 30, '#87d8f5', 'middle')
        +_path('bayes-fraction-line', 'M450 557 H690', '#eaf3ff')
        +_text('bayes-denominator', 570, 598, '9 + 99 = 108', 27, anchor='middle')
        +_text('bayes-posterior', 746, 572, '= ?', 29, '#8ee1c8')
        +_text(None, 180, 622, '分组卡片不按总体人数比例；下方阳性条的 9:99 才对应实际宽度。', 16, '#a8c1d6'))
    script = r'''
function effectRender(t){
 mathGuide(t);const d=mathData,disease=d.population*d.prevalence[0]/d.prevalence[1],healthy=d.population-disease,
 tp=disease*d.sensitivity[0]/d.sensitivity[1],fp=healthy*d.false_positive_rate[0]/d.false_positive_rate[1];
 const split=mathP(t,2),trueP=mathP(t,6),falseP=mathP(t,10),posterior=mathP(t,14);
 attrs('bayes-branch-left',{d:'M580 240 L'+(580-mathN(260*split))+' '+(240+mathN(30*split))});
 attrs('bayes-branch-right',{d:'M700 240 L'+(700+mathN(260*split))+' '+(240+mathN(30*split))});
 label('bayes-disease-count',t>=5?'患病：'+disease+' 人':'患病：?');
 label('bayes-healthy-count',t>=5?'未患病：'+healthy+' 人':'未患病：?');
 attrs('bayes-disease-count',{'data-value':t>=5?disease:'unknown'});attrs('bayes-healthy-count',{'data-value':t>=5?healthy:'unknown'});
 label('bayes-tp-rule',disease+' × 90% = '+(t>=9?tp:'?'));
 label('bayes-fp-rule',healthy+' × 10% = '+(t>=13?fp:'?'));
 fade('bayes-tp-rule',trueP);fade('bayes-fp-rule',falseP);
 attrs('bayes-true-positive',{x:180,y:432,width:mathN(tp*d.positive_pixels_per_person*trueP),height:56,'data-value':tp});
 attrs('bayes-false-positive',{x:180+tp*d.positive_pixels_per_person,y:432,width:mathN(fp*d.positive_pixels_per_person*falseP),height:56,'data-value':fp});
 fade('bayes-tp-count',trueP);fade('bayes-fp-count',falseP);label('bayes-tp-count',tp);label('bayes-fp-count',fp);
 label('bayes-pool-caption',t>=13?'阳性 '+(tp+fp)+' 人：蓝色真阳性 '+tp+' 人，紫色假阳性 '+fp+' 人':'现在只看阳性者；条宽按人数比例绘制');
 for(const id of ['bayes-numerator','bayes-denominator','bayes-fraction-line','bayes-posterior'])fade(id,posterior);
 label('bayes-numerator',tp);label('bayes-denominator',tp+' + '+fp+' = '+(tp+fp));
 label('bayes-posterior',t>=17?'= 1 / 12 ≈ '+(100*tp/(tp+fp)).toFixed(1)+'%':'= ?');
 attrs('bayes-posterior',{'data-value':t>=17?tp+'/'+(tp+fp):'pending'});
}
'''
    return _recipe(plan, variant, markup, script, ids,
        ['bayes-disease-count', 'bayes-true-positive', 'bayes-false-positive', 'bayes-posterior'],
        [[132, 264, 1016, 112], [166, 421, 100, 78], [244, 421, 814, 78], [427, 510, 710, 104]])


def _units(plan, variant):
    ids = ['units-speed-source', 'units-source-km', 'units-source-hour', 'units-length-factor',
           'units-length-numerator', 'units-length-denominator', 'units-time-factor',
           'units-time-numerator', 'units-time-denominator', 'units-cancel-km-top', 'units-cancel-km-bottom',
           'units-cancel-hour-top', 'units-cancel-hour-bottom', 'units-speed-result', 'units-speed-value',
           'units-calculation', 'units-distance', 'units-distance-expression', 'units-distance-result',
           'units-source-fraction', 'units-length-fraction', 'units-time-fraction']
    markup = (_text('units-speed-source', 90, 285, '72', 38)
        +_text('units-source-km', 190, 257, 'km', 28, anchor='middle')
        +_path('units-source-fraction', 'M155 273 H225', '#eaf3ff')
        +_text('units-source-hour', 190, 315, 'h', 28, anchor='middle')
        +_text(None, 265, 285, '×', 34)
        +'<g id="units-length-factor">'
        +_text('units-length-numerator', 410, 257, '1000 m', 28, anchor='middle')
        +_path('units-length-fraction', 'M325 273 H495', '#eaf3ff')
        +_text('units-length-denominator', 410, 315, '1 km', 28, anchor='middle')+'</g>'
        +_text(None, 525, 285, '×', 34)
        +'<g id="units-time-factor">'
        +_text('units-time-numerator', 655, 257, '1 h', 28, anchor='middle')
        +_path('units-time-fraction', 'M575 273 H735', '#eaf3ff')
        +_text('units-time-denominator', 655, 315, '3600 s', 28, anchor='middle')+'</g>'
        +_text(None, 780, 285, '=', 34)
        +'<g id="units-speed-result">'+_text('units-speed-value', 855, 285, '?', 38, '#8ee1c8')
        +_text(None, 920, 285, 'm/s', 28, '#8ee1c8')+'</g>'
        +_path('units-cancel-km-top', 'M168 253 L168 253')
        +_path('units-cancel-km-bottom', 'M390 311 L390 311')
        +_path('units-cancel-hour-top', 'M644 253 L644 253')
        +_path('units-cancel-hour-bottom', 'M180 311 L180 311')
        +_text('units-calculation', 118, 405, '72 × 1000 ÷ 3600 = ?', 28)
        +'<g id="units-distance">'+_rect(None, 90, 466, 1100, 114)
        +_text('units-distance-expression', 118, 520, 's = v × t = 20 m/s × 3 s', 30)
        +_text('units-distance-result', 118, 560, '= ?', 27, '#8ee1c8')+'</g>')
    script = r'''
function effectRender(t){
 mathGuide(t);const d=mathData,speed=d.speed_kmh*d.metres_per_kilometre/d.seconds_per_hour;
 const length=mathP(t,2),hours=mathP(t,6),simplify=mathP(t,10),distance=mathP(t,14);
 fade('units-length-factor',length);move('units-length-factor',0,mathN(24*(1-length)));
 fade('units-time-factor',hours);move('units-time-factor',0,mathN(24*(1-hours)));
 for(const [id,x,y,width,p] of [['units-cancel-km-top',168,253,44,length],['units-cancel-km-bottom',390,311+mathN(24*(1-length)),50,length],['units-cancel-hour-top',644,253+mathN(24*(1-hours)),32,hours],['units-cancel-hour-bottom',180,311,24,hours]]){
  attrs(id,{d:'M'+x+' '+y+' L'+(x+mathN(width*p))+' '+(y-mathN(16*p)),opacity:p});
 }
 fade('units-speed-result',simplify);move('units-speed-result',0,mathN(20*(1-simplify)));
 label('units-speed-value',t>=13?speed:'?');attrs('units-speed-value',{'data-value':t>=13?speed:'unknown'});
 label('units-calculation',d.speed_kmh+' × '+d.metres_per_kilometre+' ÷ '+d.seconds_per_hour+' = '+(t>=13?speed:'?'));
 fade('units-distance',distance);move('units-distance',mathN(35*(1-distance)),0);
 label('units-distance-expression','s = v × t = '+speed+' m/s × '+d.time_seconds+' s');
 label('units-distance-result',t>=17?'= '+speed*d.time_seconds+' m（秒相消，只剩米）':'= ?');
 attrs('units-distance-result',{'data-value':t>=17?speed*d.time_seconds:'unknown','data-state':t>=17?'metres':'pending'});
}
'''
    return _recipe(plan, variant, markup, script, ids,
        ['units-length-factor', 'units-time-factor', 'units-speed-result', 'units-distance'],
        [[154, 218, 353, 126], [154, 218, 593, 126], [840, 233, 247, 72], [100, 486, 1072, 87]])


_BUILDERS = {'math-commerce-balance': _balance, 'math-geometry-triangle': _triangle,
             'math-health-bayes': _bayes, 'math-transport-units': _units}


def _registered(plan):
    identity = plan.get('scenario_id') if type(plan) is dict else None
    if (type(identity) is not str or identity not in SCENARIOS
            or plan != dict(schema=SCHEMA, scenario_id=identity, **SCENARIOS[identity])):
        raise ValueError('animation_lab_math_plan_mismatch')
    return deepcopy(plan)


def build_recipe(plan, variant):
    """Compile only these complete reviewed briefs, never caller-supplied code."""
    plan = _registered(plan)
    if type(variant) is not str or variant not in {'baseline', 'candidate'}:
        raise ValueError('animation_lab_math_recipe_invalid')
    return _BUILDERS[plan['scenario_id']](plan, variant)


def _progress(time, start):
    return max(0.0, min(1.0, (time-start)/_SECONDS))


def _round(value):
    return math.floor(value+.5)


def _transform(x, y):
    return f'translate({x} {y})'


def _fact(text=None, **attributes):
    return {**({'text': str(text)} if text is not None else {}),
            **({'attributes': {key: str(value) for key, value in attributes.items()}} if attributes else {})}


def expected_observations(plan, time):
    """Independently derive facts from premises and compare real SVG geometry.

    The host measures these nodes and their actual attributes. Its separate
    review checks visibility, bounds, action changes and reverse seeking.
    """
    plan = _registered(plan)
    if type(time) not in (int, float) or not math.isfinite(time) or not 0 <= time <= plan['duration_seconds']:
        raise ValueError('animation_lab_math_time_invalid')
    d = plan['data']
    stages = [_progress(time, start) for start in _STARTS]
    index = max((i for i, start in enumerate(_STARTS) if time >= start), default=-1)
    completed = sum(time >= start+_SECONDS for start in _STARTS)
    result = {'math-step': _fact(f'步骤 {completed} / 4'),
              'math-rule': _fact(d['rules'][index] if index >= 0 else '先读题设，确定数据与前提')}
    if d['kind'] == 'linear-equation':
        net, price = d['total']-d['fee'], Fraction(d['total']-d['fee'], d['count'])
        assert price.denominator == 1
        price = price.numerator
        paid = price if time >= 9 else net if time >= 5 else d['total']
        left = d['count']*price+d['fee'] if time >= 17 else f"{d['count']} × {price} + {d['fee']}"
        result.update({
            'balance-total': _fact(paid, **{'data-value': paid, 'data-state': 'unit-price' if time >= 9 else 'items-total' if time >= 5 else 'paid-total'}),
            'balance-coefficient': _fact(d['count'], opacity=0 if time >= 9 else 1),
            'balance-variable': _fact('x', transform=_transform(_round(85*stages[1]), 0), **{'data-value': price if time >= 9 else 'unknown'}),
            'balance-fee': _fact(f"+ {d['fee']}", opacity=0 if time >= 5 else 1),
            'balance-minus-left': _fact(f"− {d['fee']}", transform=_transform(0, _round(20*(1-stages[0])))),
            'balance-minus-right': _fact(f"− {d['fee']}", transform=_transform(0, _round(20*(1-stages[0])))),
            'balance-divide-left': _fact(f"÷ {d['count']}", transform=_transform(0, -_round(16*stages[1]))),
            'balance-divide-right': _fact(f"÷ {d['count']}", transform=_transform(0, -_round(16*stages[1]))),
            'balance-cancel': _fact(d=f'M424 300 L{424+_round(100*stages[0])} {300-_round(20*stages[0])}'),
            'balance-check': _fact(transform=_transform(0, _round(24*(1-stages[2])))),
            'balance-check-left': _fact(left), 'balance-check-right': _fact(d['total']),
            'balance-check-status': _fact('两边相等' if time >= 17 else '先算左边' if time >= 13 else f'代入 x = {price}',
                **{'data-state': 'verified' if time >= 17 else 'pending', 'data-value': d['count']*price+d['fee'] if time >= 17 else 'pending'}),
        })
    elif d['kind'] == 'triangle-area':
        x, y = d['origin']
        w, h = d['base_cm']*d['pixels_per_cm'], d['height_cm']*d['pixels_per_cm']
        area = Fraction(d['base_cm']*d['height_cm'], 2)
        result.update({
            'area-rectangle': _fact(x=x, y=y, width=_round(w*stages[0]), height=h, **{'data-value': d['base_cm']*d['height_cm'] if time >= 5 else 0}),
            'area-first': _fact(d=f'M{x} {y} L{x+w} {y} L{x} {y+h} Z'),
            'area-second-path': _fact(d=f'M{x+w} {y} L{x+w} {y+h} L{x} {y+h} Z'),
            'area-diagonal': _fact(d=f'M{x} {y+h} L{x+_round(w*stages[1])} {y+h-_round(h*stages[1])}'),
            'area-second': _fact(transform=_transform(_round(90*stages[2]), _round(60*stages[2])),
                                 **{'data-state': 'translated-congruent' if time >= 13 else 'same-rectangle'}),
            'area-width': _fact(f"{d['base_cm']} cm", x=x+w/2, y=y+h+38),
            'area-height': _fact(f"{d['height_cm']} cm", x=x-25, y=y+h/2),
            'area-right-angle': _fact(d=f'M{x+20} {y} V{y+20} H{x}'),
            'area-rectangle-value': _fact(f"矩形：{d['base_cm']} × {d['height_cm']} = {d['base_cm']*d['height_cm']} cm²"),
            'area-numerator': _fact(f"{d['base_cm']} × {d['height_cm']}", x=950, y=380),
            'area-denominator': _fact('2', x=950, y=440),
            'area-fraction-line': _fact(d='M850 397 H1050'),
            'area-result': _fact(f'= {area} cm²' if time >= 17 else '= ?', **{'data-value': area if time >= 17 else 'unknown'}),
        })
    elif d['kind'] == 'bayes-frequency':
        disease = d['population']*Fraction(*d['prevalence'])
        healthy = d['population']-disease
        tp, fp = disease*Fraction(*d['sensitivity']), healthy*Fraction(*d['false_positive_rate'])
        assert all(value.denominator == 1 for value in (disease, healthy, tp, fp))
        disease, healthy, tp, fp = map(int, (disease, healthy, tp, fp))
        posterior = Fraction(tp, tp+fp)
        result.update({
            'bayes-disease-count': _fact(f'患病：{disease} 人' if time >= 5 else '患病：?', **{'data-value': disease if time >= 5 else 'unknown'}),
            'bayes-healthy-count': _fact(f'未患病：{healthy} 人' if time >= 5 else '未患病：?', **{'data-value': healthy if time >= 5 else 'unknown'}),
            'bayes-branch-left': _fact(d=f'M580 240 L{580-_round(260*stages[0])} {240+_round(30*stages[0])}'),
            'bayes-branch-right': _fact(d=f'M700 240 L{700+_round(260*stages[0])} {240+_round(30*stages[0])}'),
            'bayes-tp-rule': _fact(f"{disease} × 90% = {tp if time >= 9 else '?'}"),
            'bayes-fp-rule': _fact(f"{healthy} × 10% = {fp if time >= 13 else '?'}"),
            'bayes-true-positive': _fact(x=180, y=432, width=_round(tp*d['positive_pixels_per_person']*stages[1]), height=56, **{'data-value': tp}),
            'bayes-false-positive': _fact(x=180+tp*d['positive_pixels_per_person'], y=432, width=_round(fp*d['positive_pixels_per_person']*stages[2]), height=56, **{'data-value': fp}),
            'bayes-tp-count': _fact(tp), 'bayes-fp-count': _fact(fp),
            'bayes-numerator': _fact(tp, x=570, y=546),
            'bayes-denominator': _fact(f'{tp} + {fp} = {tp+fp}', x=570, y=598),
            'bayes-fraction-line': _fact(d='M450 557 H690'),
            'bayes-posterior': _fact(f'= {posterior.numerator} / {posterior.denominator} ≈ {100*float(posterior):.1f}%' if time >= 17 else '= ?',
                                     **{'data-value': f'{tp}/{tp+fp}' if time >= 17 else 'pending'}),
            'bayes-pool-caption': _fact(f'阳性 {tp+fp} 人：蓝色真阳性 {tp} 人，紫色假阳性 {fp} 人' if time >= 13 else '现在只看阳性者；条宽按人数比例绘制'),
        })
    else:
        speed = Fraction(d['speed_kmh']*d['metres_per_kilometre'], d['seconds_per_hour'])
        distance = speed*d['time_seconds']
        assert speed.denominator == distance.denominator == 1
        speed, distance = int(speed), int(distance)
        result.update({
            'units-speed-source': _fact(d['speed_kmh'], x=90, y=285),
            'units-source-km': _fact('km', x=190, y=257), 'units-source-hour': _fact('h', x=190, y=315),
            'units-source-fraction': _fact(d='M155 273 H225'),
            'units-length-fraction': _fact(d='M325 273 H495'),
            'units-time-fraction': _fact(d='M575 273 H735'),
            'units-length-factor': _fact(transform=_transform(0, _round(24*(1-stages[0])))),
            'units-length-numerator': _fact(f"{d['metres_per_kilometre']} m", x=410, y=257),
            'units-length-denominator': _fact('1 km', x=410, y=315),
            'units-time-factor': _fact(transform=_transform(0, _round(24*(1-stages[1])))),
            'units-time-numerator': _fact('1 h', x=655, y=257),
            'units-time-denominator': _fact(f"{d['seconds_per_hour']} s", x=655, y=315),
            'units-speed-result': _fact(transform=_transform(0, _round(20*(1-stages[2])))),
            'units-speed-value': _fact(speed if time >= 13 else '?', **{'data-value': speed if time >= 13 else 'unknown'}),
            'units-calculation': _fact(f"{d['speed_kmh']} × {d['metres_per_kilometre']} ÷ {d['seconds_per_hour']} = {speed if time >= 13 else '?'}"),
            'units-distance': _fact(transform=_transform(_round(35*(1-stages[3])), 0)),
            'units-distance-expression': _fact(f"s = v × t = {speed} m/s × {d['time_seconds']} s"),
            'units-distance-result': _fact(f'= {distance} m（秒相消，只剩米）' if time >= 17 else '= ?',
                **{'data-value': distance if time >= 17 else 'unknown', 'data-state': 'metres' if time >= 17 else 'pending'}),
        })
        for ident, x, y, width, progress in [('units-cancel-km-top', 168, 253, 44, stages[0]),
                ('units-cancel-km-bottom', 390, 311+_round(24*(1-stages[0])), 50, stages[0]),
                ('units-cancel-hour-top', 644, 253+_round(24*(1-stages[1])), 32, stages[1]),
                ('units-cancel-hour-bottom', 180, 311, 24, stages[1])]:
            result[ident] = _fact(d=f'M{x} {y} L{x+_round(width*progress)} {y-_round(16*progress)}')
    return result
