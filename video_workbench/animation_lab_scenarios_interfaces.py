"""Finite, cross-domain HTML interface demonstrations for developer evaluation.

These are local illustrations, never browser automation of a live application.
Every input, selection and message is reconstructed from the absolute clock.
"""
from __future__ import annotations

from copy import deepcopy
import html
import json


SCHEMA = 'video.animation-lab-scenario-plan.v1'


def _requirement(audience, goal, questions, entities, outcome):
    return dict(audience=audience, goal=goal, questions=questions,
                required_entities=entities, expected_outcome=outcome,
                boundaries=['全部名称与数值均为演示数据', '不连接真实服务，不执行真实操作'])


SCENARIOS = {
    'interface-library-search': dict(
        module='interface', domain='education', domain_label='教育与知识检索',
        expression='interface-demo', engine='html', title='从检索词到书目详情',
        description='逐字输入检索词，查看匹配结果，再选择书目核对所在位置。',
        takeaway='检索缩小范围，详情帮助确认是否正是需要的书。',
        narrative='先输入机器学习。点击检索以后，四本书中有两本匹配。再选择第二本，右侧详情显示作者和索书号。找到书名后，仍需要核对详情，才能去正确的位置取书。',
        requirement=_requirement('第一次使用馆藏检索的学生', '解释检索与详情核对的区别',
            ['关键词如何缩小范围？', '找到标题后如何定位馆藏？'],
            ['关键词输入框', '结果列表', '书目详情', '索书号'], '两条匹配结果中选中第二条，并显示对应索书号'),
        duration_seconds=22, difficulty='basic', tags=['检索', '列表详情', '输入状态'],
        data=dict(query='机器学习', items=[
            dict(id='b1', title='机器学习入门', author='示例作者甲', shelf='TP181 / 01'),
            dict(id='b2', title='机器学习实践', author='示例作者乙', shelf='TP181 / 02'),
            dict(id='b3', title='计算机网络', author='示例作者丙', shelf='TP393 / 03'),
            dict(id='b4', title='统计学基础', author='示例作者丁', shelf='O212 / 04')],
            match_ids=['b1', 'b2'], selected_id='b2', events=[
                dict(at=2, duration=2, operation='type_query', target='library-query'),
                dict(at=6, duration=1.5, operation='search', target='library-results'),
                dict(at=10, duration=1.5, operation='select_book', target='library-row-b2'),
                dict(at=15, duration=1.5, operation='inspect_detail', target='library-detail')]),
    ),
    'interface-shop-filter': dict(
        module='interface', domain='commerce', domain_label='电商与消费决策',
        expression='interface-demo', engine='html', title='先筛选，再按价格比较',
        description='对产品应用两个独立条件，按价格排序后查看符合预算的选择。',
        takeaway='先满足条件，再比较价格，避免只看最便宜的一件。',
        narrative='这里有四款耳机。先筛选无线款，再把预算限制在三百元以内，结果只剩两款。按价格从低到高排序后，一百五十九元的型号排在前面。筛选改变候选范围，排序只改变剩余项目的顺序。',
        requirement=_requirement('需要理解商品筛选的消费者', '区分筛选与排序，并保持结果数据一致',
            ['两个条件如何组合？', '排序是否会改变符合条件的商品？'],
            ['无线条件', '价格上限', '商品列表', '排序选项'], '保留两款无线且不超过300元的商品，按159元、249元排列'),
        duration_seconds=22, difficulty='intermediate', tags=['双条件', '过滤', '排序', '空余布局'],
        data=dict(limit=300, products=[
            dict(id='p1', name='轻听 A', price=249, wireless=True),
            dict(id='p2', name='长线 B', price=89, wireless=False),
            dict(id='p3', name='畅听 C', price=399, wireless=True),
            dict(id='p4', name='随行 D', price=159, wireless=True)],
            final_ids=['p4', 'p1'], events=[
                dict(at=2, duration=1.5, operation='wireless_filter', target='shop-wireless'),
                dict(at=6, duration=1.5, operation='budget_filter', target='shop-budget'),
                dict(at=10, duration=1.5, operation='sort_price', target='shop-products'),
                dict(at=15, duration=1.5, operation='inspect_product', target='shop-selection')]),
    ),
    'interface-clinic-validation': dict(
        module='interface', domain='healthcare', domain_label='医疗预约流程',
        expression='interface-demo', engine='html', title='预约失败后，定位并修正问题',
        description='先展示不完整输入导致的校验，再选择时段、修正电话并核对确认结果。',
        takeaway='错误提示要指出具体字段，修正后仍需核对预约内容。',
        narrative='第一次提交时，没有选择时段，联系电话也不完整。界面在字段旁指出两个问题。选好周二上午，再补全演示号码，错误提示随之消失。最后确认的是时段和联系方式；这里展示的是本地流程，没有真的提交医疗预约。',
        requirement=_requirement('需要理解表单错误的初次使用者', '解释校验、纠正和确认三个阶段',
            ['失败原因显示在哪里？', '更正后如何确认预约内容？'],
            ['预约时段', '电话输入', '字段错误', '本地确认回执'], '两个字段修正后出现演示确认，不执行真实预约'),
        duration_seconds=24, difficulty='boundary', tags=['表单校验', '错误恢复', '确认状态'],
        data=dict(initial_phone='138', corrected_phone='13800000000', slot='周二 09:30', department='演示咨询',
            events=[
                dict(at=2, duration=1.5, operation='invalid_submit', target='clinic-errors'),
                dict(at=7, duration=1.5, operation='choose_slot', target='clinic-slot'),
                dict(at=12, duration=2, operation='correct_phone', target='clinic-phone'),
                dict(at=18, duration=1.5, operation='confirm', target='clinic-receipt')]),
    ),
    'interface-plant-alarm': dict(
        module='interface', domain='industry', domain_label='工业监控与运维',
        expression='interface-demo', engine='html', title='确认告警，不等于故障已恢复',
        description='从告警列表定位设备，确认接收后查看诊断步骤，最后单独显示传感器恢复。',
        takeaway='已确认表示有人接手，恢复必须由后续状态单独证明。',
        narrative='泵站二号设备出现高温告警。打开这一条记录，才能看到测量值与阈值。确认告警只是表示有人接手，温度仍然超限。按照诊断步骤检查冷却回路后，示意温度回落，状态才变成恢复。确认和恢复不能混为一谈。',
        requirement=_requirement('学习监控台的值班人员', '区分告警确认、诊断和实际恢复',
            ['确认是否代表故障消失？', '何种状态才能标记恢复？'],
            ['设备告警列表', '测量与阈值', '确认状态', '诊断清单', '恢复状态'], '先确认但保留超限，诊断后才显示低于阈值的恢复读数'),
        duration_seconds=24, difficulty='intermediate', tags=['列表定位', '状态机', '确认与恢复'],
        data=dict(device='泵站 P-02', initial_temperature=86, threshold=80, restored_temperature=74,
            other_device='阀门 V-01', diagnosis=['核对传感器读数', '检查冷却回路', '观察温度回落'],
            events=[
                dict(at=2, duration=1.5, operation='select_alarm', target='plant-alarm'),
                dict(at=7, duration=1.5, operation='acknowledge', target='plant-ack'),
                dict(at=12, duration=2, operation='diagnose', target='plant-checklist'),
                dict(at=18, duration=1.5, operation='restore', target='plant-reading')]),
    ),
}


def _e(value):
    return html.escape(str(value), quote=True)


def _data(plan):
    return json.dumps(plan['data'], ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('&', '\\u0026')


_CSS = '''<style>
.ui-demo{position:absolute;left:48px;top:12px;width:1184px;height:495px;background:#111e30;border:1px solid #34506b;color:#ecf5ff;font:18px/1.35 VideoNoto,sans-serif;overflow:hidden}
.ui-demo *{box-sizing:border-box}.ui-demo h2,.ui-demo h3,.ui-demo p{margin:0}.ui-demo h2{font-size:23px}.ui-demo h3{font-size:20px}
.ui-demo .ui-bar{height:52px;display:flex;align-items:center;justify-content:space-between;padding:0 22px;background:#192b42;border-bottom:1px solid #34506b}
.ui-demo .ui-badge{font-size:15px;color:#8fdcc7}.ui-demo .ui-body{display:grid;grid-template-columns:182px minmax(0,1fr);gap:20px;padding:18px 22px;height:441px}
.ui-demo .ui-steps{padding:14px 12px;background:#142438;border:1px solid #29445b}.ui-demo .ui-step{padding:12px 7px;margin:7px 0;font-size:16px;border-left:3px solid #2a465e}
.ui-demo .ui-content{position:relative;min-width:0;height:403px}.ui-demo .ui-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,.9fr);gap:18px}
.ui-demo .ui-panel{background:#15273c;border:1px solid #34506b;padding:16px;min-width:0}.ui-demo .ui-note{font-size:16px;line-height:1.5;color:#adbed1}.ui-demo .ui-heading{font-size:17px;color:#a7c5dd;margin-bottom:10px}
.ui-demo input{font:18px VideoNoto,sans-serif;color:#ecf5ff;border:1px solid #5a7690;background:#0d1a2b;height:42px;padding:8px 12px;width:100%;border-radius:0}
.ui-demo button{font:17px VideoNoto,sans-serif;color:#d9f7ee;background:#245e59;border:1px solid #64b8a3;padding:9px 15px;border-radius:0;opacity:1}
.ui-demo .ui-row{display:flex;align-items:center;gap:12px}.ui-demo .ui-list{list-style:none;margin:10px 0 0;padding:0}.ui-demo .ui-item{display:flex;justify-content:space-between;align-items:center;gap:12px;height:49px;padding:10px;border-bottom:1px solid #35506a;font-size:17px;background:#142438;white-space:nowrap}
.ui-demo .ui-status{font-size:16px;color:#9ee5c4;margin:12px 0}.ui-demo .ui-large{font-size:28px;font-weight:700;color:#8fe2cc}.ui-demo .ui-error{font-size:15px;color:#ffb2aa;min-height:24px}.ui-demo .ui-chip{font-size:16px;padding:9px 12px;border:1px solid #52718c;background:#152a42;color:#d3e7f7}
.ui-demo .ui-footer{position:absolute;left:0;right:0;bottom:0;min-height:46px;padding:9px 12px;background:#1a3048;font-size:17px;color:#d5e9f9}
.ui-demo .ui-clinic-form{padding:12px 16px}.ui-demo .ui-clinic-form .ui-heading{margin-bottom:0}.ui-demo .ui-clinic-form .ui-note{font-size:14px;line-height:1.2}.ui-demo .ui-clinic-form .ui-chip{font-size:14px;line-height:1.25;padding:5px 10px;margin:3px 0}.ui-demo .ui-clinic-form .ui-error{font-size:14px;line-height:1.2;min-height:20px}.ui-demo .ui-clinic-form input{height:34px;font-size:16px}.ui-demo .ui-clinic-form button{font-size:16px;padding:5px 12px}
</style>'''


def _shell(title, steps, content):
    step_markup = ''.join(f'<div class="ui-step" id="ui-step-{i}">{i+1:02d} · {_e(value)}</div>'
                          for i, value in enumerate(steps))
    return (_CSS + '<article class="ui-demo" id="ui-demo"><header class="ui-bar"><h2>' + _e(title)
            + '</h2><span class="ui-badge">本地界面示意 · 不执行真实操作</span></header><div class="ui-body">'
            + '<aside class="ui-steps"><h3>操作步骤</h3>' + step_markup
            + '</aside><section class="ui-content">' + content + '</section></div></article>')


_COMMON = '''
const uiInput=(id,value)=>{const n=$(id);n.value=String(value);n.setAttribute('value',String(value));n.setAttribute('data-value',String(value));};
const uiState=(id,state)=>{$(id).setAttribute('data-state',String(state));};
const uiOpacity=(id,value)=>{$(id).style.opacity=String(value);};
function uiFocus(id,active){const n=$(id),strength=variant==='candidate'?clamp(active):0;n.style.outline=strength>0?'2px solid rgba(146,239,208,'+strength.toFixed(4)+')':'0px solid transparent';n.style.outlineOffset='3px';n.style.boxShadow=strength>0?'0 0 18px rgba(99,217,181,'+(strength*.16).toFixed(4)+')':'none';}
function uiSteps(active){for(let i=0;i<4;i++){const n=$('ui-step-'+i);n.style.borderLeftColor=variant==='candidate'&&i===active?'#92efd0':'#2a465e';n.style.background=variant==='candidate'&&i===active?'#244b50':'#142438';n.setAttribute('data-state',i===active?'active':i<active?'complete':'pending');}}
function uiRead(ids){return ids.map(id=>{const n=$(id);return {id,text:n.textContent,value:n.getAttribute('value'),state:n.getAttribute('data-state'),opacity:n.style.opacity||'1'};});}
'''


def _library(plan):
    d = plan['data']
    rows = ''.join(f'<li class="ui-item" id="library-row-{_e(row["id"])}"><span>{_e(row["title"])}</span><span class="ui-note">详情 →</span></li>' for row in d['items'])
    content = (f'<div class="ui-row"><input id="library-query" readonly value="" aria-label="检索词"/><button type="button" disabled>检索</button></div>'
        '<p class="ui-status" id="library-count">全部馆藏：4 条</p><div class="ui-grid">'
        '<section class="ui-panel" id="library-results"><h3>书目列表</h3><ul class="ui-list">' + rows + '</ul></section>'
        '<section class="ui-panel" id="library-detail"><p class="ui-heading">书目详情</p><h3 id="library-detail-title">请选择一本书</h3>'
        '<p class="ui-status" id="library-author">作者：待选择</p><p class="ui-note">索书号</p><p class="ui-large" id="library-shelf">—</p>'
        '<p class="ui-note" style="margin-top:18px">先核对书名，再确认取书位置。</p></section></div>'
        '<p class="ui-footer" id="library-caption">先用关键词缩小检索范围。</p>')
    script = 'const D=' + _data(plan) + ';\n' + _COMMON + '''
function effectRender(t){
 const searched=t>=6,selected=t>=10,inspected=t>=15,typing=progress(t,2,2);
 uiInput('library-query',D.query.slice(0,Math.floor(D.query.length*typing)));
 uiState('library-results',searched?'filtered':'all');
 const visible=D.items.filter(row=>!searched||D.match_ids.includes(row.id));
 D.items.forEach(row=>{const id='library-row-'+row.id,match=visible.some(item=>item.id===row.id),chosen=selected&&row.id===D.selected_id;
  uiOpacity(id,match?1:0);$(id).style.height=match?'49px':'0px';$(id).style.padding=match?'10px':'0px';$(id).style.overflow='hidden';$(id).style.borderBottomWidth=match?'1px':'0px';
  $(id).style.background=chosen?'#245951':'#142438';uiState(id,chosen?'selected':match?'available':'filtered-out');});
 label('library-count',(searched?'匹配结果：':'全部馆藏：')+visible.length+' 条');
 const book=D.items.find(row=>row.id===D.selected_id);
 label('library-detail-title',selected?book.title:'请选择一本书');label('library-author',selected?'作者：'+book.author:'作者：待选择');label('library-shelf',selected?book.shelf:'—');
 uiState('library-detail',inspected?'inspected':selected?'selected':'empty');$('library-detail').style.borderColor=variant==='candidate'&&inspected?'#92efd0':'#34506b';
 label('library-caption',inspected?'这本书的索书号为 '+book.shelf:selected?'标题已选中，再核对右侧详情。':searched?'两本书匹配关键词，选择第二本查看详情。':typing>0?'正在输入关键词：'+D.query:'先用关键词缩小检索范围。');
 uiFocus('library-query',t>=2&&!searched?smooth(typing):0);uiFocus('library-results',searched&&!selected?smooth(progress(t,6,.5)):0);uiFocus('library-row-b2',selected&&!inspected?smooth(progress(t,10,.5)):0);uiFocus('library-detail',inspected?smooth(progress(t,15,.5)):0);
 uiSteps(inspected?3:selected?2:searched?1:0);
}
function effectState(){return {scenario:plan.scenario_id,view:uiRead(['library-query','library-results','library-row-b2','library-detail','library-count','library-shelf'])};}
'''
    return _shell('馆藏检索', ['输入关键词', '检索结果', '选择书目', '核对位置'], content), script


def _shop(plan):
    rows = ''.join(f'<li class="ui-item" id="shop-row-{_e(row["id"])}"><span>{_e(row["name"])} · {"无线" if row["wireless"] else "有线"}</span><strong>¥{row["price"]}</strong></li>' for row in plan['data']['products'])
    content = ('<div class="ui-row"><div class="ui-chip" id="shop-wireless">□ 仅无线</div><div class="ui-chip" id="shop-budget">预算：不限</div>'
        '<div class="ui-chip" id="shop-sort">排序：默认</div></div><p class="ui-status" id="shop-count">4 款可选</p>'
        '<div class="ui-grid"><section class="ui-panel"><h3>候选商品</h3><ul class="ui-list" id="shop-products" style="display:flex;flex-direction:column">' + rows + '</ul></section>'
        '<section class="ui-panel" id="shop-selection"><p class="ui-heading">比较后的选择</p><h3 id="shop-selected-name">等待筛选</h3>'
        '<p class="ui-large" id="shop-selected-price" style="margin-top:18px">—</p><p class="ui-note" style="margin-top:18px">筛选决定谁留下。<br/>排序决定怎样排列。</p>'
        '<p class="ui-status" id="shop-criteria">尚未应用条件</p></section></div><p class="ui-footer" id="shop-caption">先确定必须满足的条件。</p>')
    script = 'const D=' + _data(plan) + ';\n' + _COMMON + '''
function effectRender(t){
 const wireless=t>=2,budget=t>=6,sorted=t>=10,selected=t>=15;
 const visible=D.products.filter(p=>(!wireless||p.wireless)&&(!budget||p.price<=D.limit));if(sorted)visible.sort((a,b)=>a.price-b.price);
 label('shop-wireless',(wireless?'☑':'□')+' 仅无线');uiState('shop-wireless',wireless?'enabled':'disabled');
 label('shop-budget',budget?'预算：≤ '+D.limit+' 元':'预算：不限');uiState('shop-budget',budget?'enabled':'disabled');label('shop-sort',sorted?'排序：价格从低到高':'排序：默认');
 D.products.forEach((p,index)=>{const n=$('shop-row-'+p.id),position=visible.findIndex(x=>x.id===p.id),shown=position>=0;
  n.style.order=String(shown?position:D.products.length+index);n.style.height=shown?'49px':'0px';n.style.padding=shown?'10px':'0px';n.style.overflow='hidden';n.style.opacity=shown?'1':'0';n.style.borderBottomWidth=shown?'1px':'0px';
  n.style.background=selected&&p.id===D.final_ids[0]?'#245951':'#142438';uiState('shop-row-'+p.id,shown?'visible-'+position:'filtered-out');});
 uiState('shop-products',sorted?'price-sorted':budget?'two-filters':wireless?'wireless':'all');label('shop-count',visible.length+' 款可选');
 const chosen=D.products.find(p=>p.id===D.final_ids[0]);label('shop-selected-name',selected?chosen.name:'等待比较');label('shop-selected-price',selected?'¥'+chosen.price:'—');
 label('shop-criteria',budget?'无线 + 不超过 '+D.limit+' 元':wireless?'无线':'尚未应用条件');uiState('shop-selection',selected?'selected':'pending');$('shop-selection').style.borderColor=variant==='candidate'&&selected?'#92efd0':'#34506b';
 label('shop-caption',selected?'两款均满足条件；'+chosen.name+' 的价格更低。':sorted?'仍是两款商品，只把价格低的排在前面。':budget?'应用两个条件，只剩两款。':wireless?'有线款被排除，还剩三款。':'先确定必须满足的条件。');uiFocus('shop-wireless',wireless&&!budget?smooth(progress(t,2,.5)):0);uiFocus('shop-budget',budget&&!sorted?smooth(progress(t,6,.5)):0);uiFocus('shop-products',sorted&&!selected?smooth(progress(t,10,.5)):0);uiFocus('shop-selection',selected?smooth(progress(t,15,.5)):0);uiSteps(selected?3:sorted?2:budget?1:0);
}
function effectState(){return {scenario:plan.scenario_id,view:uiRead(['shop-wireless','shop-budget','shop-products','shop-count','shop-selection','shop-selected-price']),order:D.products.map(p=>({id:p.id,order:$('shop-row-'+p.id).style.order,state:$('shop-row-'+p.id).getAttribute('data-state')}))};}
'''
    return _shell('商品筛选与比较', ['仅看无线', '限制预算', '价格排序', '核对选择'], content), script


def _clinic(plan):
    content = ('<div class="ui-grid"><section class="ui-panel ui-clinic-form"><p class="ui-heading">演示咨询预约</p><p class="ui-note">预约时段</p>'
        '<div class="ui-chip" id="clinic-slot">请选择时段</div><p class="ui-error" id="clinic-slot-error"></p>'
        '<p class="ui-note">联系电话（演示号码）</p><input id="clinic-phone" readonly value="138" aria-label="联系电话"/>'
        '<p class="ui-error" id="clinic-phone-error"></p><button type="button" disabled>核对并提交</button>'
        '<p class="ui-error" id="clinic-errors" style="margin-top:6px">尚未校验</p></section>'
        '<section class="ui-panel" id="clinic-receipt"><p class="ui-heading">本地确认回执</p><h3 id="clinic-receipt-status">等待资料完整</h3>'
        '<p class="ui-status" id="clinic-receipt-slot">时段：待确认</p><p class="ui-note" id="clinic-receipt-phone">电话：待确认</p>'
        '<p class="ui-note" style="margin-top:24px">本例只展示界面状态。<br/>没有提交真实预约。</p></section></div>'
        '<p class="ui-footer" id="clinic-caption">不完整的表单，先定位问题再修改。</p>')
    script = 'const D=' + _data(plan) + ';\n' + _COMMON + '''
function effectRender(t){
 const attempted=t>=2,slot=t>=7,correction=progress(t,12,2),phone=D.initial_phone+D.corrected_phone.slice(D.initial_phone.length, D.initial_phone.length+Math.floor((D.corrected_phone.length-D.initial_phone.length)*correction));
 const phoneValid=phone.length===D.corrected_phone.length,confirmed=t>=18;
 uiInput('clinic-phone',phone);uiState('clinic-phone',phoneValid?'valid':attempted?'invalid':'unvalidated');
 label('clinic-slot',slot?D.slot:'请选择时段');uiState('clinic-slot',slot?'selected':attempted?'invalid':'unselected');
 label('clinic-slot-error',attempted&&!slot?'请选择一个预约时段':'');label('clinic-phone-error',attempted&&!phoneValid?'请补全 11 位联系电话':'');
 const count=(slot?0:1)+(phoneValid?0:1);label('clinic-errors',confirmed?'演示确认完成':attempted?(count?'还有 '+count+' 项需要修正':'资料完整，可以核对'):'尚未校验');uiState('clinic-errors',confirmed?'confirmed':attempted?'errors-'+count:'unvalidated');
 $('clinic-phone').style.borderColor=attempted&&!phoneValid?'#ed9c91':phoneValid?'#7fd3b7':'#5a7690';$('clinic-slot').style.borderColor=attempted&&!slot?'#ed9c91':'#52718c';
 label('clinic-receipt-status',confirmed?'资料已核对 · 演示成功':'等待资料完整');label('clinic-receipt-slot',confirmed?'时段：'+D.slot:'时段：待确认');label('clinic-receipt-phone',confirmed?'电话：'+D.corrected_phone:'电话：待确认');
 uiState('clinic-receipt',confirmed?'confirmed':'pending');$('clinic-receipt').style.borderColor=variant==='candidate'&&confirmed?'#92efd0':'#34506b';
 label('clinic-caption',confirmed?'先核对时段和电话；本例未执行真实预约。':phoneValid?'两处问题已修正，最后核对再确认。':slot?'时段已选好，接着补全演示号码。':attempted?'错误分别标在对应字段旁，便于逐项修正。':'不完整的表单，先定位问题再修改。');uiFocus('clinic-errors',attempted&&!slot?smooth(progress(t,2,.5)):0);uiFocus('clinic-slot',slot&&t<12?smooth(progress(t,7,.5)):0);uiFocus('clinic-phone',t>=12&&!confirmed?smooth(progress(t,12,.5)):0);uiFocus('clinic-receipt',confirmed?smooth(progress(t,18,.5)):0);uiSteps(confirmed?3:t>=12?2:slot?1:0);
}
function effectState(){return {scenario:plan.scenario_id,view:uiRead(['clinic-slot','clinic-phone','clinic-errors','clinic-slot-error','clinic-phone-error','clinic-receipt','clinic-receipt-status'])};}
'''
    return _shell('预约表单校验', ['查看错误', '选择时段', '修正电话', '核对确认'], content), script


def _plant(plan):
    d = plan['data']
    items = ''.join(f'<li class="ui-item" id="plant-check-{index}"><span>○ {_e(text)}</span></li>' for index, text in enumerate(d['diagnosis']))
    content = ('<div class="ui-grid"><section class="ui-panel"><h3>设备告警</h3><ul class="ui-list">'
        f'<li class="ui-item" id="plant-alarm"><strong>{_e(d["device"])}</strong><span id="plant-list-status">高温</span></li>'
        f'<li class="ui-item"><span>{_e(d["other_device"])}</span><span class="ui-note">正常</span></li></ul>'
        '<p class="ui-heading" style="margin-top:18px">接收状态</p><div class="ui-chip" id="plant-ack">未确认</div>'
        '<p class="ui-note" style="margin-top:12px">确认只表示有人接手，<br/>不会更改测量读数。</p></section>'
        '<section class="ui-panel"><p class="ui-heading">设备详情</p><div class="ui-row" id="plant-reading"><strong class="ui-large" id="plant-temperature">—</strong>'
        f'<span class="ui-note">阈值 {_e(d["threshold"])} °C</span></div><p class="ui-status" id="plant-condition">请选择告警记录</p>'
        '<ul class="ui-list" id="plant-checklist">' + items + '</ul></section></div>'
        '<p class="ui-footer" id="plant-caption">先定位设备，再区分确认与恢复。</p>')
    script = 'const D=' + _data(plan) + ';\n' + _COMMON + '''
function effectRender(t){
 const selected=t>=2,acked=t>=7,diagnosing=t>=12,restored=t>=18,diagnosticProgress=progress(t,12,2);
 uiState('plant-alarm',restored?'restored':selected?'selected':'unselected');$('plant-alarm').style.background=selected?'#3a4138':'#142438';
 label('plant-list-status',restored?'已恢复':'高温');label('plant-ack',acked?'已确认 · 已有人接手':'未确认');uiState('plant-ack',acked?'acknowledged':'unacknowledged');
 label('plant-temperature',selected?(restored?D.restored_temperature:D.initial_temperature)+' °C':'—');uiState('plant-reading',restored?'below-threshold':selected?'above-threshold':'unselected');
 label('plant-condition',restored?'温度低于阈值，状态恢复':acked?'已确认，但温度仍然超限':selected?'温度超限，等待接手':'请选择告警记录');
 D.diagnosis.forEach((text,index)=>{const completed=restored||(diagnosing&&index<2&&diagnosticProgress>=(index+1)/2),id='plant-check-'+index;
  label(id,(completed?'✓ ':'○ ')+text);uiState(id,completed?'complete':'pending');$(id).style.color=completed?'#9ee5c4':'#adbed1';});
 uiState('plant-checklist',restored?'complete':diagnosing?'diagnosing':'pending');$('plant-reading').style.background=variant==='candidate'&&restored?'#245951':'transparent';
 label('plant-caption',restored?'读数降到 '+D.restored_temperature+' °C，低于阈值，才能标记恢复。':diagnosing?'按步骤核查冷却回路，等待测量状态变化。':acked?'已确认不等于已恢复；当前仍为 '+D.initial_temperature+' °C。':selected?'测量值 '+D.initial_temperature+' °C，高于阈值 '+D.threshold+' °C。':'先定位设备，再区分确认与恢复。');uiFocus('plant-alarm',selected&&!acked?smooth(progress(t,2,.5)):0);uiFocus('plant-ack',acked&&!diagnosing?smooth(progress(t,7,.5)):0);uiFocus('plant-checklist',diagnosing&&!restored?smooth(progress(t,12,.5)):0);uiFocus('plant-reading',restored?smooth(progress(t,18,.5)):0);uiSteps(restored?3:diagnosing?2:acked?1:0);
}
function effectState(){return {scenario:plan.scenario_id,view:uiRead(['plant-alarm','plant-ack','plant-reading','plant-temperature','plant-condition','plant-checklist'])};}
'''
    return _shell('监控台告警处理', ['定位设备', '确认接手', '逐项诊断', '核对恢复'], content), script


_BUILDERS = {
    'interface-library-search': _library,
    'interface-shop-filter': _shop,
    'interface-clinic-validation': _clinic,
    'interface-plant-alarm': _plant,
}


def build_recipe(plan, variant):
    """Build only an exact bundled requirement; never interpret supplied HTML."""
    if type(plan) is not dict or type(plan.get('scenario_id')) is not str or plan['scenario_id'] not in SCENARIOS:
        raise ValueError('animation_lab_interface_scenario_invalid')
    scenario_id = plan['scenario_id']
    expected = dict(schema=SCHEMA, scenario_id=scenario_id, **SCENARIOS[scenario_id])
    if plan != expected or type(variant) is not str or variant not in {'baseline', 'candidate'}:
        raise ValueError('animation_lab_interface_scenario_invalid')
    markup, script = _BUILDERS[scenario_id](plan)
    # Read actual HTML ids, including static panels; this enables a native DOM
    # observer to associate changed states with the corresponding UI objects.
    import re
    ids = re.findall(r'\bid="([a-z][a-z0-9-]*)"', markup)
    if len(ids) != len(set(ids)):
        raise ValueError('animation_lab_interface_duplicate_id')
    actions = [dict(target=row['target'], start=row['at'], duration=row['duration'], kind=row['operation'].replace('_', '-'))
               for row in plan['data']['events']]
    return dict(markup=markup, script=script, object_ids=ids, actions=deepcopy(actions))


def expected_observations(plan, time):
    """Independently derive key DOM facts from the frozen requirement and clock.

    Native observers supply actual text/attributes/input properties. These
    expectations do not rely on the JavaScript semantic self-report.
    """
    scenario_id = plan.get('scenario_id') if type(plan) is dict else None
    if (type(scenario_id) is not str or scenario_id not in SCENARIOS or plan != dict(schema=SCHEMA, scenario_id=scenario_id, **SCENARIOS[scenario_id])
            or type(time) not in (int, float)):
        raise ValueError('animation_lab_interface_scenario_invalid')
    import math
    if not math.isfinite(time) or not 0 <= time <= plan['duration_seconds']:
        raise ValueError('animation_lab_interface_time_invalid')
    d = plan['data']
    if scenario_id == 'interface-library-search':
        searched, selected, inspected = time >= 6, time >= 10, time >= 15
        count = len(d['match_ids']) if searched else len(d['items'])
        book = next(row for row in d['items'] if row['id'] == d['selected_id'])
        typed = d['query'][:int(len(d['query']) * max(0, min(1, (time-2)/2)))]
        return {
            'library-query': dict(value=typed, attributes={'data-value': typed}),
            'library-count': dict(text=('匹配结果：' if searched else '全部馆藏：')+str(count)+' 条'),
            'library-row-b2': dict(attributes={'data-state': 'selected' if selected else 'available'}),
            'library-detail': dict(attributes={'data-state': 'inspected' if inspected else 'selected' if selected else 'empty'}),
            'library-shelf': dict(text=book['shelf'] if selected else '—'),
        }
    if scenario_id == 'interface-shop-filter':
        wireless, budget, sorted_, selected = time >= 2, time >= 6, time >= 10, time >= 15
        products = [p for p in d['products'] if (not wireless or p['wireless']) and (not budget or p['price'] <= d['limit'])]
        chosen = next(p for p in d['products'] if p['id'] == d['final_ids'][0])
        return {
            'shop-wireless': dict(text=('☑' if wireless else '□')+' 仅无线'),
            'shop-budget': dict(text='预算：≤ '+str(d['limit'])+' 元' if budget else '预算：不限'),
            'shop-products': dict(attributes={'data-state': 'price-sorted' if sorted_ else 'two-filters' if budget else 'wireless' if wireless else 'all'}),
            'shop-count': dict(text=str(len(products))+' 款可选'),
            'shop-selected-price': dict(text='¥'+str(chosen['price']) if selected else '—'),
        }
    if scenario_id == 'interface-clinic-validation':
        attempted, slot, confirmed = time >= 2, time >= 7, time >= 18
        correction = max(0, min(1, (time-12)/2))
        prefix = len(d['initial_phone'])
        phone = d['initial_phone']+d['corrected_phone'][prefix:prefix+int((len(d['corrected_phone'])-prefix)*correction)]
        valid = len(phone) == len(d['corrected_phone'])
        count = int(not slot) + int(not valid)
        return {
            'clinic-phone': dict(value=phone, attributes={'data-state': 'valid' if valid else 'invalid' if attempted else 'unvalidated'}),
            'clinic-slot': dict(text=d['slot'] if slot else '请选择时段'),
            'clinic-slot-error': dict(text='请选择一个预约时段' if attempted and not slot else ''),
            'clinic-phone-error': dict(text='请补全 11 位联系电话' if attempted and not valid else ''),
            'clinic-errors': dict(text='演示确认完成' if confirmed else ('还有 '+str(count)+' 项需要修正' if count else '资料完整，可以核对') if attempted else '尚未校验'),
        }
    selected, acknowledged, diagnosing, restored = time >= 2, time >= 7, time >= 12, time >= 18
    return {
        'plant-ack': dict(text='已确认 · 已有人接手' if acknowledged else '未确认'),
        'plant-reading': dict(attributes={'data-state': 'below-threshold' if restored else 'above-threshold' if selected else 'unselected'}),
        'plant-temperature': dict(text=str(d['restored_temperature'] if restored else d['initial_temperature'])+' °C' if selected else '—'),
        'plant-condition': dict(text='温度低于阈值，状态恢复' if restored else '已确认，但温度仍然超限' if acknowledged else '温度超限，等待接手' if selected else '请选择告警记录'),
        'plant-checklist': dict(attributes={'data-state': 'complete' if restored else 'diagnosing' if diagnosing else 'pending'}),
    }
