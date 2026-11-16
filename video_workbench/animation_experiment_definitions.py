"""Frozen, neutral capability problems, not scene recipes or model results.

Add a new version instead of editing a published version: run identities and
development/held-out reconstruction depend on these exact input documents.
"""
from __future__ import annotations

from copy import deepcopy


NEW_EXPERIMENT_IDS = ('cohort-statistics', 'parameter-geometry', 'event-delay')
CURRENT_VERSIONS = {name: '1' for name in NEW_EXPERIMENT_IDS}


_OBSERVATION_BOUNDARY = (
    'Required semantic markers must be present and visibly rendered from t=0, including the first '
    'frame; do not reveal them with an entrance delay. Keep them visible throughout except that '
    'cohort selection and aggregate metric markers are not checked during the open transition '
    'interval 1<t<4; they must be visible and correct at t<=1 and t>=4. A mathematically '
    'degenerate geometry at a singular matrix is allowed. Required marker nodes and all their '
    'ancestors must use no filter, clip-path or mask. Geometry markers use plain solid paint, '
    'not gradient/pattern paint; place optional decorative effects in a separate layer that '
    'does not wrap the measured markers. Each data-semantic-id is unique across the entire '
    'DOM, including hidden copies; keep the total semantic marker count <=128. Numeric or '
    'unknown metric content belongs in a separate visible span or SVG text node containing '
    'only the pure number or 未知. Put labels, formulas, units and descriptions outside the '
    'measured numeric node.'
)


_COHORT_OBSERVATION = {
    'required_marker_boundary': _OBSERVATION_BOUNDARY,
    'source': 'Trusted host reads actual rendered DOM/SVG nodes; generated self-reports are not evidence.',
    'entities': 'Keep every entity visibly identifiable throughout. Use data-semantic-id="entity-A" '
        'on the actual entity container, "identity-A" on its visible ID text, "value-A" on '
        'its visible numeric value (未知 for null), and "selection-A" on visible status text '
        '(保留 / 排除 / 未知); replace A with each supplied entity ID. Excluded entities remain '
        'visible in context, not silently deleted or relabelled.',
    'metrics': 'Use actual visible pure-number text with data-semantic-id="metric-count", '
        '"metric-known_count", "metric-mean", "metric-weighted_mean", "metric-weight_sum". '
        'An undefined mean is the visible text 未知. Round numeric displays to at least 3 decimals '
        'when needed; integer values need no decimal padding. Put descriptions outside these metric nodes.',
    'rules': 'Initially all entities participate in count; null values are unknown and excluded '
        'from known_count, means and weight_sum. After filtering, only known values >= threshold '
        'participate. weighted_mean=sum(value*weight)/sum(weight), never an unweighted mean of '
        'group means. Missing entities always show 未知 status; known entities initially show 保留. '
        'Metrics and selections are checked before 1s and from 4s onward; intermediate visual '
        'organization is free. Identity/value visibility is checked throughout.',
}

_GEOMETRY_OBSERVATION = {
    'required_marker_boundary': _OBSERVATION_BOUNDARY,
    'source': 'Trusted host measures actual visible SVG geometry and metric text, not data attributes '
        'claiming coordinates or results.',
    'coordinates': 'Use an SVG coordinate system with screen origin=data.origin, scale=data.unit_px, '
        'positive mathematical y pointing up. Give the actual visible circle/ellipse at origin '
        'data-semantic-id="origin"; give each actual point circle/ellipse "point-P" (replace P '
        'with the supplied ID). Each point and its visible ID text "identity-P" must belong '
        'to a common visible SVG g container "entity-P", using data-semantic-id on the text '
        'and container too. Use visible SVG line elements "basis-x" and "basis-y", from '
        'origin to the current images of (1,0) and (0,1); a visible SVG polygon "unit-area" '
        'has ordered corners origin, image(1,0), image(1,1), image(0,1). Layout and annotation '
        'around this measured coordinate view remain freely designed.',
    'metrics': 'Visible pure-number text nodes "metric-determinant" and "metric-area_scale" '
        'show det(current_matrix) and abs(det(current_matrix)). Use data-semantic-id on each '
        'node. Round displays to at least 3 decimals when needed. Keep labels visibly bound '
        'to their point identities, including when points coincide.',
    'rules': 'At t<=1 the matrix is identity. From t=1 through 6.5 use elementwise linear '
        'interpolation current_matrix=(1-u)*I+u*data.matrix where u=clamp((t-1)/5.5,0,1). '
        'All point geometry, basis vectors, unit polygon and both metrics derive from this '
        'same current_matrix. After 6.5 hold the result. A singular result may collapse the '
        'polygon to zero area; do not fabricate a nonzero area to keep it visible.',
}

_EVENT_OBSERVATION = {
    'required_marker_boundary': _OBSERVATION_BOUNDARY,
    'source': 'Trusted host reads actual visible status and numeric text. Build the state from '
        'absolute time; seeking never accumulates events twice.',
    'metrics': 'Visible pure-number nodes data-semantic-id="metric-stock", "metric-in_transit", '
        '"metric-cumulative_arrivals", "metric-cumulative_demand" display exact quantities. '
        'Keep unit and descriptions outside the numeric nodes.',
    'events': 'For every purchase event use actual visible status text data-semantic-id="event-A" '
        '(replace A with its ID): 未下单 before order_time, 在途 from order_time until arrival_time, '
        '已到货 from arrival_time. Demand status "demand-D1" is 待发生 before its time and '
        '已消耗 from its time. Keep event IDs and quantities visibly identifiable.',
    'rules': 'At time t include every order, arrival and demand whose timestamp is <=t. '
        'in_transit sums ordered but not arrived purchase quantities; cumulative_arrivals '
        'sums arrived purchases; cumulative_demand sums consumed demand; stock=initial_stock+'
        'cumulative_arrivals-cumulative_demand. At an equal timestamp include both arrival and '
        'demand. Ordering creates in-transit material only, never immediately available stock. '
        'Show a linked inventory curve and distinguish the order, transit and arrival stages '
        'with your own visual strategy; the pure-number/status nodes expose verifiable state.',
}


# These are versioned test inputs, not example scenes. Held-out inputs are
# separate from the development prompt; a runner submits only the chosen role.
_INPUTS = {
    'cohort-statistics': {'1': {
        'development': dict(entities=[
            dict(id='A', group='G1', value=2, weight=1),
            dict(id='B', group='G1', value=8, weight=2),
            dict(id='C', group='G2', value=None, weight=4),
            dict(id='D', group='G2', value=6, weight=3),
            dict(id='E', group='G2', value=10, weight=1),
        ], threshold=5),
        'held_out': dict(entities=[
            dict(id='A', group='G1', value=None, weight=3),
            dict(id='B', group='G1', value=4, weight=1),
            dict(id='C', group='G1', value=12, weight=4),
            dict(id='D', group='G2', value=7, weight=2),
            dict(id='E', group='G2', value=9, weight=1),
            dict(id='F', group='G2', value=1, weight=2),
        ], threshold=8),
    }},
    'parameter-geometry': {'1': {
        'development': dict(matrix=[[1.2, 0.6], [0.2, 0.9]],
            points=[dict(id='P', xy=[1, 1]), dict(id='Q', xy=[-1, 1]), dict(id='R', xy=[0.5, -1])],
            origin=[640, 390], unit_px=90),
        'held_out': dict(matrix=[[-1, 0.4], [0.25, 0.8]],
            points=[dict(id='P', xy=[1.5, 0.5]), dict(id='Q', xy=[-1, -1]),
                    dict(id='R', xy=[0, 1.5]), dict(id='S', xy=[1, -1])],
            origin=[640, 390], unit_px=90),
    }},
    'event-delay': {'1': {
        'development': dict(initial_stock=4, events=[
            dict(id='A', order_time=1, quantity=3, arrival_time=4),
            dict(id='B', order_time=2, quantity=2, arrival_time=6),
        ], demands=[dict(id='D1', time=2, quantity=2), dict(id='D2', time=5, quantity=3)], unit='件'),
        'held_out': dict(initial_stock=6, events=[
            dict(id='A', order_time=0.5, quantity=2, arrival_time=3),
            dict(id='B', order_time=1.5, quantity=4, arrival_time=5.5),
            dict(id='C', order_time=3, quantity=1, arrival_time=6.5),
        ], demands=[dict(id='D1', time=1, quantity=3), dict(id='D2', time=3, quantity=2),
                    dict(id='D3', time=6, quantity=4)], unit='件'),
    }},
}


_TEXT = {
    'cohort-statistics': dict(title='筛选变化时，怎样同时看懂对象与统计？', capability='群体筛选与加权统计',
        goal='让带身份、数值和组别的对象经历一次筛选与重组；对象的去留、人数、已知值数量、均值与加权均值由同一份数据决定，明确未知值的含义。',
        exploration='自行设计对象组织、筛选路径、焦点和统计解释的顺序。让观众看懂分母和权重如何变化；不要把空值当成零。保留可核对的对象身份。',
        technical=['对象身份、实际可见数值与筛选状态', '集合计数、已知值、均值和权重分母独立复算', '任意时间定位与倒回一致'],
        human=['能否跟随筛选与分组变化', '能否理解分母、权重及未知值', '画面层次与节奏是否清楚']),
    'parameter-geometry': dict(title='一个参数变化，怎样让几何与公式一起讲清楚？', capability='参数、几何与公式联动',
        goal='用二维矩阵的连续变化联动点集、基向量、单位面积、行列式及面积倍率；让观众看见同一参数如何改变空间与数值。',
        exploration='自行安排坐标视图、公式、点的标注和焦点转移。几何必须真实变化，数值与几何共享同一矩阵；自由选择解释方式，不限定卡片或出场模板。',
        technical=['点像、基向量端点与单位多边形实际几何', '行列式、面积倍率及退化/翻转独立复算', '全时钟取样、末态停留与倒回一致'],
        human=['能否理解参数和空间变换的对应', '翻转或退化是否容易辨认', '公式、几何和注意力是否协调']),
    'event-delay': dict(title='有延迟的过程，怎样看清事件和库存的关系？', capability='多轨事件与延迟',
        goal='从订单、在途、到货和消耗的多轨事件展示库存如何变化，配合库存曲线；未到货的物料不能提前计入库存。',
        exploration='自行设计时间组织、物料流动、状态变化及曲线联动，不限定传送带或固定卡片布局。分清订单、物料和库存，允许观众核对延迟的后果。',
        technical=['到货与需求独立事件账及库存守恒', '实际可见的在途/已到货状态与数量', '同刻事件、跳转和倒回不重复累计'],
        human=['能否理解下单和到货之间的延迟', '多轨过程与库存曲线是否容易对应', '事件密度与视觉焦点是否合适']),
}


def _v1_constraints(experiment_id, role='development', version='1'):
    data = deepcopy(_INPUTS[experiment_id][version][role])
    semantic = dict(schema='video.animation-experiment-semantic.v1', kind=experiment_id, data=data)
    if experiment_id == 'cohort-statistics':
        semantic.update(stationary_until_seconds=1, filter_complete_seconds=4,
                        observation_contract=deepcopy(_COHORT_OBSERVATION))
    elif experiment_id == 'parameter-geometry':
        semantic.update(stationary_until_seconds=1, movement_complete_seconds=6.5,
                        observation_contract=deepcopy(_GEOMETRY_OBSERVATION))
    else:
        semantic['observation_contract'] = deepcopy(_EVENT_OBSERVATION)
    return dict(width=1280, height=720, fps=24, duration_seconds=8, allowed_engines=['html-svg'],
                definition_id=experiment_id, definition_version=version, semantic=semantic)


def _v1_document(experiment_id, version='1'):
    text = _TEXT[experiment_id]
    return dict(schema='video.animation-experiment-definition.v1', id=experiment_id, version=version,
        title=text['title'], capability=text['capability'], goal=text['goal'],
        constraints=_v1_constraints(experiment_id, version=version), exploration=text['exploration'],
        source='definition', status='unexecuted',
        evaluation=dict(technical=deepcopy(text['technical']), human=deepcopy(text['human'])),
        note='中性能力目标；由通用 Agent 自由创作 HTML/SVG，尚无作品时仅显示定义；技术通过不自动代表审美改进。')


# Each registry entry owns complete definition and role snapshots. Future
# versions append an entry with their own metadata, constraints and observer
# contract. They must not modify the v1 builder or its source records above.
_REGISTRY = {experiment_id: {'1': dict(
    definition=_v1_document(experiment_id),
    constraints={role: _v1_constraints(experiment_id, role)
                 for role in ('development', 'held_out')})}
    for experiment_id in NEW_EXPERIMENT_IDS}


def versions(experiment_id):
    return tuple(_REGISTRY.get(experiment_id, {}))


def versioned_constraints(experiment_id, role='development', version='1'):
    return deepcopy(_REGISTRY[experiment_id][version]['constraints'][role])


def definition_document(experiment_id, version='1'):
    return deepcopy(_REGISTRY[experiment_id][version]['definition'])
