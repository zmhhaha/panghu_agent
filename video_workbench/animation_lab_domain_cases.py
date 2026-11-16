"""Cross-domain finite requirements using the existing five real compilers.

Counts, topology, retry events, scene count and layer count vary in addition to
language. Briefs explicitly restrict what these illustrative traces establish.
"""
from copy import deepcopy
import io
import wave

from .motion import example_plan, make_beats, estimated_alignment, sha256
from .examples.build_process_sample import process_plan
from .animation_lab_canvas import example_canvas_plan
from .animation_lab_three import SCHEMA as THREE_SCHEMA


def silence(seconds):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0' * (int(seconds * 24000) * 2))
    return output.getvalue()


def _brief(module, identity, domain, domain_label, title, text, labels, *, seconds=18,
           difficulty='basic', goal='', expected='', **parameters):
    return dict(id=identity, module=module, domain=domain, domain_label=domain_label,
        expression={'diagram':'relationships', 'process':'state-process', 'continuity':'cross-scene',
                    'canvas':'queue-flow', 'three':'spatial-layers'}[module],
        engine={'diagram':'html-svg', 'process':'html-svg', 'continuity':'html-svg',
                'canvas':'canvas', 'three':'three'}[module],
        title=title, description=goal or title, narrative=text, takeaway=expected,
        requirement=dict(audience='需要直观理解该过程的非专业观众', goal=goal or title,
            questions=['哪些实体在参与？', '过程变化后，什么保留、什么改变？'],
            required_entities=labels, expected_outcome=expected,
            boundaries=['有限示意数据，不代表真实系统测量',
                        '只评价所列对象与动作，不声称任意领域仿真']),
        duration_seconds=seconds, difficulty=difficulty, tags=[domain_label, title.split('：')[0]],
        checks=['实体、状态和结论是否一致', '领域术语与动作是否对应', '倒放后是否恢复'],
        parameters=parameters)


DOMAIN_CASES = {}
def _add(row):
    DOMAIN_CASES[row['id']] = row


for row in (
    _brief('diagram','domain-diagram-water','environment','环境与水资源','雨水利用：从整体看三个环节',
        '雨水落入地表。水经过汇集进入蓄水区。蓄存的水用于浇灌。最后回到整体看各环节的关系。',
        ['降雨','汇集','用水'], goal='用三环节结构解释水的流转', expected='流转对象连续，环节分工清楚', template='flow'),
    _brief('diagram','domain-diagram-learning','education','教育与学习','学习反馈：保留整体再看局部',
        '先明确学习目标。通过练习发现问题。反馈说明本次的薄弱点。下一轮重点据此调整。最后保留反馈结论。',
        ['学习目标','练习','反馈','下轮重点'], difficulty='intermediate',
        goal='在整体结构中强调反馈结论', expected='反馈结论单独保留，上下文仍可定位', template='focus', conclusion=True),
    _brief('diagram','domain-diagram-retail','commerce','电商与供应链','订单履约：四步关系如何串起来',
        '顾客先提交订单。仓库收到配货任务。配送把包裹交给收件人。签收确认整个流程结束。',
        ['订单','配货','配送','签收'], seconds=22, difficulty='boundary',
        goal='检查四节点关系和较长业务词', expected='四步顺序完整，签收节点保留', template='focus'),
    _brief('process','domain-process-parcel','logistics','物流与配送','包裹转运：一次通过的路径',
        '包裹进入收件站。收件站把它交给分拣台。分拣完成后进入配送站。途中没有失败返回。最后确认转运完成。停留观察这条路径。',
        ['收件站','分拣台','配送站'], seconds=22,
        goal='区分正常传递和失败重试', expected='同一包裹沿正向路径一次完成', retry=False),
    _brief('process','domain-process-lab','science','科研与实验','实验样本：复检保留同一身份',
        '样本登记并进入待检区。样本交给检测台。检测不合格，需要返回待检区。调整后同一样本再次检测。复检通过，记录完成。停留检查样本身份。',
        ['样本登记','待检区','检测台'], seconds=24, difficulty='intermediate',
        goal='解释失败后复检不等于换一个样本', expected='返回和再次检测均保留样本01身份', retry=True),
    _brief('process','domain-process-manufacturing','industry','工业与设备','设备工单：完成后突出结果',
        '先登记维修工单。工单进入待修区。维修台领取并处理。处理完成后记录结果。最后保持维修台的完成状态。停留核对工单身份。',
        ['工单登记','待修区','维修台'], seconds=18, difficulty='boundary',
        goal='检查快速过程与完成状态保持', expected='工单只处理一次，结尾维持完成状态', retry=False),
    _brief('continuity','domain-continuity-delivery','logistics','物流与配送','跨镜包裹：交接后继续跟踪',
        '第一镜包裹进入分拣区。第二镜同一包裹继续送到配送站。对象身份不变。',
        ['收件站','分拣区','配送站'], goal='跨镜保持同一包裹', expected='镜头切换不重建包裹', scenes=2, copy=False),
    _brief('continuity','domain-continuity-course','education','教育与学习','三镜作业：评阅后返回修订',
        '学生先提交作业到修订区。完成初稿后进入评阅区。评阅反馈使同一份作业返回修订区，连续两次切镜保留其身份。',
        ['提交区','修订区','评阅区'], seconds=24, difficulty='boundary',
        goal='检查三镜连续承接以及返回路径', expected='同一份作业最后回到修订区，保留评阅反馈', scenes=3, copy=False),
    _brief('continuity','domain-continuity-invoice','commerce','电商与供应链','跨镜凭证：留存原件再处理副本',
        '先登记原始凭证。下一镜复制一份凭证用于处理。原件仍留存，副本有明确来源。',
        ['登记区','处理区','存储区'], seconds=22, difficulty='intermediate',
        goal='区分连续移动和复制新对象', expected='副本继续处理，原件可辨认', scenes=2, copy=True),
    _brief('canvas','domain-canvas-clinic','healthcare','医疗流程示意','门诊排队：少量对象的顺序',
        '四张示意登记单先后进入待诊区。诊室逐一领取。等待数量下降，完成数量增加。',
        ['登记区','待诊区','诊室'], goal='观察少量队列对象与顺序', expected='4个对象身份守恒，按顺序完成', count=4, seed=29),
    _brief('canvas','domain-canvas-warehouse','logistics','物流与配送','仓库拣货：批量积压的消退',
        '八张拣货单批量到来。待拣区先接住积压。拣货台逐一领取，直到全部完成。',
        ['订单区','待拣区','拣货台'], seconds=22, difficulty='intermediate',
        goal='观察批量输入和积压消退', expected='8个对象在等待和处理间连续转移', count=8, seed=97),
    _brief('canvas','domain-canvas-sensors','industry','工业与设备','设备采样：密集输入仍能追踪',
        '十二条采样记录集中到达。缓冲区暂存记录。校验台逐个处理，编号和总数始终保持。',
        ['采样端','缓冲区','校验台'], seconds=24, difficulty='boundary',
        goal='检查较多对象和较长观察时间下的拥挤', expected='12个对象不丢失，完成计数达到12', count=12, seed=301),
):
    _add(row)

for identity, domain, label, names, details in (
    ('retail','commerce','电商与供应链',['交易入口','订单规则','订单存储'],['接收购买操作','核对库存和价格','记录交易状态']),
    ('school','education','教育与学习',['学生端','课程管理','作业评阅','资料存储'],['展示学习任务','组织课程结构','跟踪作业状态','保存学习资料']),
    ('factory','industry','工业与设备',['监控入口','告警分流','工单调度','维修执行','历史记录'],['展示设备概况','确定告警类别','分配维修任务','反馈处理状态','保留全过程记录']),
):
    _add(_brief('three','domain-three-'+identity,domain,label,
        f'{len(names)}层平台：展开后找到各自职责',
        '先看完整平台。依次展开各个层级。逐层观察职责。最后重新组合回整体。', names,
        seconds=18 if len(names)==3 else 24, difficulty='basic' if len(names)==3 else 'boundary',
        goal=f'检查{len(names)}层结构与标签的对应、展开和归位', expected=f'{len(names)}个层级始终保留，职责对应',
        details=details))


def _aligned(text, seconds):
    audio = silence(seconds)
    beats = make_beats(text)
    return beats, estimated_alignment(beats, sha256(audio), seconds), audio


def inputs_for(identity):
    row = deepcopy(DOMAIN_CASES[identity])
    params = row['parameters']; module = row['module']
    beats, alignment, audio = _aligned(row['narrative'], row['duration_seconds'])
    labels = row['requirement']['required_entities']
    if module == 'diagram':
        plan = example_plan(beats, template=params['template'])
        plan.update(title=row['title'], takeaway=row['takeaway'])
        for item, text in zip([x for x in plan['elements'] if x['kind']=='card'], labels):
            item.update(label=text, detail='')
        next(x for x in plan['elements'] if x['kind']=='token')['label']='流转对象'
        # Unlike example_plan's compact three-beat demonstration, every
        # declared domain stage gets its own beat and directed transfer.
        cards=[item for item in plan['elements'] if item['kind']=='card']
        plan['actions']=[]
        for i,item in enumerate(cards):
            beat=beats['segments'][i]['id']
            plan['actions'].append(dict(beat_id=beat,target=item['id'],kind='highlight',placement='start',pace='normal',to_slot=None))
            if i:
                plan['actions'].extend([
                    dict(beat_id=beat,target=f'edge{i-1}',kind='draw',placement='start',pace='quick',to_slot=None),
                    dict(beat_id=beat,target='request',kind='move',placement='middle',pace='normal',to_slot='token_'+item['slot'])])
        if params.get('conclusion'):
            plan['elements'].append(dict(id='conclusion',kind='card',slot='note',label='反馈决定下一轮重点',detail=''))
            plan['actions'].append(dict(beat_id=beats['segments'][-1]['id'],target='conclusion',kind='reveal',placement='start',pace='normal',to_slot=None))
    elif module == 'process':
        ids=[item['id'] for item in beats['segments']]
        def action(index,target,kind,placement='start',pace='normal',**options):
            return dict(beat_id=ids[index],target=target,kind=kind,placement=placement,pace=pace,**options)
        noun={'logistics':'包裹 01','science':'样本 01','industry':'工单 01'}[row['domain']]
        plan=dict(schema='video.process-plan.v1',template='flow',title=row['title'],takeaway=row['takeaway'],
            elements=[dict(id=slot,kind='card',slot=slot,label=name,detail='')
                      for slot,name in zip(('source','queue','worker'),labels)]
                +[dict(id='request',kind='token',slot='token_source',label=noun,detail='')],
            relations=[dict(id='source_queue',source='source',target='queue',label='交给'+labels[1]),
                       dict(id='queue_worker',source='queue',target='worker',label='交给'+labels[2])],
            actions=[action(0,slot,'reveal') for slot in ('source','queue','worker')]
                    +[action(0,'request','reveal','middle','quick')])
        first,dispatch=(0,1) if params['retry'] else (1,2)
        plan['actions'] += [action(first,'source_queue','draw'),
            action(first,'request','transfer','end' if params['retry'] else 'middle',relation_id='source_queue'),
            action(dispatch,'queue','set_state',state='waiting'),action(dispatch,'queue_worker','draw'),
            action(dispatch,'request','transfer','middle',relation_id='queue_worker'),
            action(dispatch,'worker','set_state','end',state='running')]
        if params['retry']:
            plan['relations'].append(dict(id='worker_queue',source='worker',target='queue',label='返回'+labels[1]))
            plan['actions'] += [action(2,'worker','set_state',state='failed'),action(2,'worker_queue','draw'),
                action(2,'request','transfer','middle',relation_id='worker_queue'),
                action(3,'request','transfer',relation_id='queue_worker'),action(3,'worker','set_state','middle',state='running')]
        plan['actions'].append(action(4,'worker','set_state','middle',state='completed'))
    elif module == 'canvas':
        plan=example_canvas_plan()
        noun={'healthcare':'登记单','logistics':'拣货单','industry':'采样记录'}[row['domain']]
        plan.update(schema='video.canvas-plan.v2',title=row['title'],takeaway=row['takeaway'],seed=params['seed'],packet_count=params['count'],
                    labels=dict(zip(('source','queue','worker'),labels)),context=dict(noun=noun,
                    source_detail='批量到来的'+noun,queue_detail='按顺序等待处理',worker_detail='逐个完成并保留编号',
                    phase_intro='先观察'+str(params['count'])+'个'+noun,
                    phase_queue=noun+'进入等待区',phase_work='按顺序领取和处理',phase_done=noun+'全部完成'))
    elif module == 'three':
        colors=['#68d5bc','#75aef2','#d7a7f1','#e7b66e','#ee9eaa']
        plan=dict(schema='video.animation-lab-three-plan.v2',title=row['title'],takeaway=row['takeaway'],layers=[
            dict(id=f'layer-{i+1}',label=name,detail=params['details'][i],color=colors[i]) for i,name in enumerate(labels)],
            context=dict(phase_whole='完整平台：各层共同协作',phase_expand='展开层级：观察职责'))
    else:
        scenes=[]; audios=[]
        for i in range(params['scenes']):
            seconds=row['duration_seconds']/params['scenes']
            # Integer-duration fixture clock; all selected totals divide evenly.
            segment_text=(['学生提交作业。作业进入修订区。保留作业身份。',
                           '学生完成初稿。作业交给评阅区。保留其来源。',
                           '评阅提出反馈。同一份作业返回修订区。保留完整流转身份。'][i]
                          if params['scenes']==3 else f'观察第{i+1}镜中的同一对象。对象继续交接。保留其来源和状态。')
            scene_beats,scene_alignment,raw=_aligned(segment_text,seconds)
            ids=[x['id'] for x in scene_beats['segments']]
            source,dest=(('source','queue') if i==0 or params['copy'] else ('queue','worker'))
            if params['scenes']==3:
                source,dest=[('source','queue'),('queue','worker'),('worker','queue')][i]
            plan=dict(schema='video.process-plan.v1',template='flow',title=row['title'],takeaway=row['takeaway'],
                elements=[dict(id=slot,kind='card',slot=slot,label=name,detail='') for slot,name in zip(('source','queue','worker'),labels)]
                    +[dict(id='object',kind='token',slot='token_'+source,label='对象 01',detail='')],
                relations=[dict(id='route',source=source,target=dest,label='交接')],
                actions=[dict(kind='draw',target='route',beat_id=ids[0],placement='start',pace='quick'),
                    dict(kind='reveal',target='object',beat_id=ids[0],placement='middle',pace='quick'),
                    dict(kind='transfer',target='object',relation_id='route',beat_id=ids[1],placement='start',pace='normal'),
                    dict(kind='set_state',target=dest,state='running',beat_id=ids[2],placement='start',pace='quick')])
            scenes.append(dict(id=f'domain-scene-{i+1}',title=row['title'],audio=dict(id=f'domain-audio-{i+1}',sha256=sha256(raw)),
                motion=dict(id=f'domain-motion-{i+1}',plan=plan,beats=scene_beats,alignment=scene_alignment,compiler_version='motion-process-compiler-2'),
                timing=dict(leading_seconds=0,trailing_seconds=0)))
            audios.append(raw)
        return dict(scenes=scenes),audios
    return dict(plan=plan,beats=beats,alignment=alignment),[audio]
