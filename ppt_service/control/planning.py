"""Strict source-preserving projections for known PPT Master planning contracts.

Recommendations are display metadata. Only the website database can confirm a
plan; no source selected/confirmed field can issue a new confirmation here.
"""
from __future__ import annotations
import copy,json,math,re
from .execution import ControlError
ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z')

def bad(code='planning_contract_invalid'):raise ControlError(code)
def bounded(value):
    count=0
    def visit(node,depth=0):
        nonlocal count
        count+=1
        if depth>32 or count>100000:bad('planning_size_limit')
        if isinstance(node,dict):
            for k,v in node.items():
                if not isinstance(k,str) or len(k)>200:bad()
                visit(v,depth+1)
        elif isinstance(node,list):
            for item in node:visit(item,depth+1)
        elif isinstance(node,str):
            if len(node)>64000:bad('planning_size_limit')
        elif isinstance(node,float):
            if not math.isfinite(node):bad()
        elif node is not None and type(node) not in (int,bool):bad()
    visit(value)
    try:
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())>2*1024**2:bad('planning_size_limit')
    except (TypeError,ValueError):bad()
def text(value,limit,*,empty=False):
    if not isinstance(value,str) or len(value)>limit or (not empty and not value.strip()) or any(ord(c)<32 and c not in '\n\r\t' for c in value):bad()
    return value
def pages(plan,expected=None):
    rows=plan.get('pages') if isinstance(plan,dict) else plan if isinstance(plan,list) else None
    if not isinstance(rows,list) or not 3<=len(rows)<=20:bad('complete_page_plan_required')
    if expected is not None and len(rows)!=expected:bad('planning_page_count_mismatch')
    seen=set()
    for i,row in enumerate(rows,1):
        if not isinstance(row,dict):bad('complete_page_plan_required')
        number=row.get('ordinal',row.get('number'))
        if type(number) is not int or number!=i or ('ordinal' in row and 'number' in row and row['ordinal']!=row['number']):bad('page_order_invalid')
        text(row.get('title'),500)
        if row.get('id') is not None:
            key=text(row['id'],100)
            if key in seen:bad('duplicate_page_id')
            seen.add(key)
        descriptive=any(isinstance(row.get(k),str) and row[k].strip() for k in ('core_message','purpose','body','summary'))
        content=any(isinstance(row.get(k),list) and any((isinstance(v,str) and v.strip()) or (isinstance(v,dict) and bool(v)) for v in row[k]) for k in ('content','points'))
        if not descriptive and not content:bad('complete_page_plan_required')
    return len(rows)
def source_info(manifest,shape,source_hash):
    if source_hash is not None and (not isinstance(source_hash,str) or not re.fullmatch(r'[0-9a-f]{64}',source_hash)):bad('invalid_source_manifest_hash')
    return {'schema_version':1,'shape':shape,'source_manifest_sha256':source_hash,'source_input_snapshot_hash':manifest.get('input_snapshot_sha256'),'confirmation_authority':'website_database_only'}
def recommendation(container,ids):
    if 'selected' not in container or container['selected'] is None:return None
    selected=container['selected']
    if type(selected) is int:
        if not 0<=selected<len(ids):bad('invalid_recommendation')
        index=selected
    elif isinstance(selected,str) and selected in ids:index=ids.index(selected)
    else:bad('invalid_recommendation')
    reason=container.get('reason','');text(reason,12000,empty=True)
    return {'direction_id':ids[index],'source_index':index,'selection_is':'agent_recommendation_only_not_user_confirmation','source_selection_is':container.get('selection_is'),'reason':reason}

def project_stage1(proposal,*,source_manifest_sha256=None):
    """Explicit Stage1 proposal body; the frozen artifact is not a full manifest."""
    bounded(proposal)
    if not isinstance(proposal,dict) or proposal.get('stage')!='stage1' or proposal.get('status')!='awaiting_confirmation':bad('stage1_contract_invalid')
    for key in ('title','primary_language','audience','communication_intent'):
        value=proposal.get(key)
        if not isinstance(value,dict):bad('stage1_contract_invalid')
        text(value.get('value'),12000)
    template=proposal.get('template_choice')
    if not isinstance(template,dict) or template.get('value') not in ('free_design','templates'):bad('stage1_contract_invalid')
    return {'outline':{'communication':copy.deepcopy(proposal)},'design':{'communication':copy.deepcopy(proposal),'source_plan':copy.deepcopy(proposal),'source_projection':source_info({},'official_stage1_proposal',source_manifest_sha256)}}

def project_planning(manifest,*,source_manifest_sha256=None,expected_page_count=None):
    bounded(manifest)
    if not isinstance(manifest,dict) or manifest.get('phase') not in ('plan_initial','plan_options') or manifest.get('status')!='awaiting_confirmation' or not isinstance(manifest.get('plan'),dict):bad()
    phase=manifest['phase'];plan=manifest['plan']
    if expected_page_count is not None and (type(expected_page_count) is not int or not 3<=expected_page_count<=20):bad('planning_page_count_mismatch')
    if phase=='plan_initial':
        if plan.get('stage')=='stage1':
            result=project_stage1(plan,source_manifest_sha256=source_manifest_sha256)
            result['design']['source_projection']['source_input_snapshot_hash']=manifest.get('input_snapshot_sha256');return result
        if isinstance(plan.get('communication'),dict) and plan['communication'].get('stage')=='stage1':
            result=project_stage1(plan['communication'],source_manifest_sha256=source_manifest_sha256)
            result['design']['source_plan']=copy.deepcopy(plan);result['design']['source_projection']['source_input_snapshot_hash']=manifest.get('input_snapshot_sha256');return result
        if not isinstance(plan.get('outline'),dict) or not isinstance(plan.get('design'),dict):bad('stage1_contract_invalid')
        design=copy.deepcopy(plan['design']);design['source_plan']=copy.deepcopy(plan);design['source_projection']=source_info(manifest,'direct_stage1',source_manifest_sha256)
        return {'outline':copy.deepcopy(plan['outline']),'design':design}
    official=plan.get('options')
    direct=plan.get('design')
    if isinstance(official,dict) and 'design_directions' in official:
        if direct is not None:bad('ambiguous_planning_shape')
        if official.get('stage')!='stage2' or official.get('status')!='awaiting_confirmation':bad()
        container=official['design_directions']
        if not isinstance(container,dict):bad()
        candidates=container.get('candidates');shared=plan.get('page_plan');production={k:copy.deepcopy(v) for k,v in official.items() if k!='design_directions'}
        if not isinstance(shared,dict):bad('complete_page_plan_required')
        shape='official_nested_stage2';pointer='/plan/options/design_directions/candidates'
        count=pages(shared,expected_page_count)
        declared=official.get('page_count')
        if not isinstance(declared,dict) or str(declared.get('value'))!=str(count):bad('planning_page_count_mismatch')
    elif isinstance(direct,dict):
        present=[k for k in ('directions','options') if k in direct]
        if len(present)!=1 or isinstance(official,dict):bad('ambiguous_planning_shape')
        candidates=direct[present[0]];container=direct;shared=plan.get('page_plan',direct.get('page_plan'));production=direct.get('production_parameters')
        shape='direct_design_'+present[0];pointer='/plan/design/'+present[0]
    else:bad('unsupported_planning_shape')
    if not isinstance(candidates,list) or len(candidates)!=3:bad('three_real_directions_required')
    directions=[];ids=[]
    for i,candidate in enumerate(candidates):
        if not isinstance(candidate,dict):bad()
        key=candidate.get('id')
        if not isinstance(key,str) or not ID.fullmatch(key):bad('invalid_direction_id')
        if key in ids:bad('duplicate_direction_id')
        ids.append(key);name=text(candidate.get('name'),200);note=text(candidate.get('note',''),12000,empty=True)
        page_plan=candidate.get('page_plan',shared);pages(page_plan,expected_page_count)
        params=candidate.get('production_parameters',production)
        if not isinstance(params,dict) or not params:bad('production_parameters_required')
        direction=copy.deepcopy(candidate)
        # Confirmation-like Actor fields remain solely in the immutable source
        # namespace; they cannot masquerade as the website's confirmation state.
        for field in ('confirmed_at','confirmation_selection','confirmed','selected'):direction.pop(field,None)
        direction.update({'id':key,'name':name,'note':note,'page_plan':copy.deepcopy(page_plan),'production_parameters':copy.deepcopy(params),'source_pointer':pointer+'/'+str(i)})
        directions.append(direction)
    if shared is not None:
        pages(shared,expected_page_count)
        outline=copy.deepcopy(shared)
        if isinstance(outline,list):outline={'pages':outline}
    else:
        outline=copy.deepcopy(plan.get('outline',{}))
    if not isinstance(outline,dict):bad('complete_page_plan_required')
    design={'directions':directions,'source_plan':copy.deepcopy(plan),'source_projection':source_info(manifest,shape,source_manifest_sha256)}
    if shared is not None:design['page_plan']=copy.deepcopy(shared)
    if isinstance(production,dict):design['production_parameters']=copy.deepcopy(production)
    suggested=recommendation(container,ids)
    if suggested is not None:design['recommendation']=suggested
    if isinstance(plan.get('questions'),list):design['questions']=copy.deepcopy(plan['questions'])
    elif isinstance(official,dict) and isinstance(official.get('questions'),list):design['questions']=copy.deepcopy(official['questions'])
    return {'outline':outline,'design':design}
