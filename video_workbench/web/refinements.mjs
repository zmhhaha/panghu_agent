/** Formal-film rounds share one lineage; laboratory experiments stay independent. */
const TERMINAL=new Set(['completed','failed','cancelled']);
const STATUS={admitting:'确认打磨额度',running:'打磨中',completed:'本轮完成',failed:'本轮未完成',cancelled:'已取消'};
const PHASE={allowance:'确认额度',review:'检查整片画面',revision:'定向修改动画',render:'合成新草稿',finished:'已结束'};
const LABEL={automatic:'默认打磨',user_requested:'用户追加打磨',requirements:'按问题列表修改'};
const validSource=value=>Boolean(value&&typeof value.run_id==='string'&&value.run_id
  &&typeof value.version_id==='string'&&value.version_id&&typeof value.sha256==='string'&&/^[0-9a-f]{64}$/.test(value.sha256));
const sameSource=(a,b)=>validSource(a)&&validSource(b)&&a.run_id===b.run_id&&a.version_id===b.version_id&&a.sha256===b.sha256;
const pathFor=id=>`/api/v1/projects/${encodeURIComponent(id)}/refinements`;
export function refinementErrorLabel(code) {
  return ({production_refinement_disabled:'整片打磨尚未开放，已有草稿仍可查看。',
    film_review_unavailable:'画面检查服务暂未就绪，没有开始本轮打磨。',
    production_sequence_allowance_consumed:'追加打磨次数已用完，请填写明确的问题与期望。',
    production_sequence_initial_round_required:'请先等待默认的一轮审阅与打磨结束。',
    production_sequence_work_in_progress:'已有一轮修改正在进行，请等待结束或申请取消。',
    production_refinement_source_not_registered:'当前草稿尚未登记完成，请重新读取制作状态。',
    production_refinement_configuration_changed:'制作服务已更新，请先重新读取项目与草稿。',
    production_refinement_user_input_too_large:'单个片段的修改要求过长，请精简或拆分具体需求后再提交；本次未消耗打磨额度。',
    production_refinement_budget_exhausted:'本轮预算已用完，已有结果保留；请核对尚未解决的问题。'})[code]??null;
}

export function createRefinementRequest(project,source,kind,fields,sequence,{requestId,unsaved=false,busy=false}={}) {
  if(!project?.id||!validSource(source)||!sequence?.sources?.some(row=>sameSource(row,source))) throw Error('请先选择已登记的整片草稿。');
  if(unsaved)throw Error('请先保存或重新载入项目修改，再打磨草稿。');
  if(busy||sequence.active)throw Error('项目还有任务正在执行或确认状态，请等待完成。');
  if(typeof requestId!=='string'||!requestId||requestId.length>128)throw Error('本次修改请求未能保存，请重新读取状态。');
  const body={source:{...source},kind,request_id:requestId};
  if(kind==='user_requested') {
    if(sequence.automatic_finished!==true)throw Error('请先等待默认的一轮审阅与打磨结束。');
    if(!(sequence.remaining?.user_requested>0))throw Error('两轮追加打磨已用完，请填写具体问题与期望。');
    const instruction=fields?.instruction?.trim();
    if(!instruction||instruction.length>16000)throw Error('请填写这轮希望改善的方向。');
    body.instruction=instruction;
  }else if(kind==='requirements') {
    if(!Array.isArray(fields?.items)||fields.items.length<1||fields.items.length>64)throw Error('请至少填写一项明确问题。');
    body.items=fields.items.map(row=>{
      const problem=row.problem?.trim(),expected=row.expected?.trim(),scope=row.scope;
      if(!problem||!expected||problem.length>8000||expected.length>8000)throw Error('每项都需要填写现有问题与希望达到的效果。');
      if(scope?.kind!=='whole_film'&&(scope?.kind!=='time_range'
        ||!Number.isFinite(scope.start_seconds)||!Number.isFinite(scope.end_seconds)
        ||scope.start_seconds<0||scope.end_seconds<=scope.start_seconds||scope.end_seconds>86400))throw Error('请检查问题的起止时间。');
      return {scope:scope.kind==='whole_film'?{kind:'whole_film'}:{kind:'time_range',start_seconds:scope.start_seconds,end_seconds:scope.end_seconds},problem,expected};
    });
    body.budget={max_agent_tasks:Math.min(64,Math.max(1,(project.scenes?.length??0)+1)),max_renders:1};
  }else throw Error('请选择用户追加打磨或按问题列表修改。');
  return {path:pathFor(project.id),body};
}

export function createRefinementsPanel({document,getContext,request,refresh,onError,onSuccess,onChange=()=>{},onMediaPlay=()=>{},
  storage=globalThis.localStorage,makeRequestId=()=>`film-refinement:${globalThis.crypto.randomUUID()}`}) {
  const cache=new Map(),selected=new Map(),drafts=new Map(),unresolved=new Map(),busy=new Set(),readErrors=new Map(),tickets=new Map();
  let sequence=0,mediaKey=null;
  const context=()=>getContext();
  const same=value=>value.project?.id===context().project?.id&&value.epoch===context().epoch;
  const node=(tag,text,className)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;if(className)el.className=className;return el;};
  const section=node('section',null,'production-runs-panel');section.id='formal-refinements-panel';
  const heading=node('h3','整片审阅与打磨'),note=node('p',null,'field-note'),error=node('p',null,'message error-message');
  const chooser=node('select');chooser.id='refinement-source';
  const sourceLabel=node('label','当前审阅的草稿');sourceLabel.append(chooser);
  const history=node('div'),player=node('video'),download=node('a','下载所选草稿 MP4','button secondary');
  player.id='refinement-video';
  player.controls=true;player.preload='none';player.setAttribute('aria-label','打磨草稿对比预览');
  const form=node('form'),instruction=node('textarea');instruction.rows=3;instruction.maxLength=16000;
  const instructionLabel=node('label','这轮希望改善什么');instructionLabel.append(instruction);
  const acknowledged=node('input');acknowledged.type='checkbox';
  const acknowledgeLabel=node('label',null,'checkbox-label');acknowledgeLabel.append(acknowledged,node('span','我已查看所选草稿，希望继续修改'));
  const broad=node('button','再打磨一轮','button secondary');broad.type='button';
  const requirements=node('details'),requirementsTitle=node('summary','按明确问题修改'),rows=node('div');
  const add=node('button','增加问题','text-button');add.type='button';
  const targeted=node('button','按问题列表修改一轮','button secondary');targeted.type='button';
  const budgetNote=node('p','按明确问题定向修改一轮，最多重新合成一次；已有录音和未受影响的资源继续复用。','field-note');
  requirements.append(requirementsTitle,rows,add,budgetNote,targeted);
  form.append(instructionLabel,acknowledgeLabel,broad,requirements);
  section.append(heading,note,error,sourceLabel,player,download,history,form);
  document.getElementById('production-runs-panel').append(section);
  const storageKey=value=>`video.formal-refinement.pending.v1:${encodeURIComponent(value.owner??'')}:${encodeURIComponent(value.project.id)}`;
  const draft=id=>{if(!drafts.has(id))drafts.set(id,{instruction:'',items:[{scope:{kind:'whole_film'},problem:'',expected:''}]});return drafts.get(id);};
  const hasUnresolvedOperation=(id=context().project?.id)=>Boolean(id&&(busy.has(id)||unresolved.has(id)
    ||cache.get(id)?.operations?.some(row=>!TERMINAL.has(row.status))));
  const pause=()=>player.pause();

  function sourceOptions(value,data) {
    const refs=new Map(),run=value.run;
    if(validSource(run?.sequence_registration?.source))refs.set(run.sequence_registration.source.version_id,
      {source:run.sequence_registration.source,label:'原始草稿 · 未经人工认可'});
    for(const row of [...(data?.operations??[])].reverse()) {
      const source=row.result?.source;
      if(row.status==='completed'&&validSource(source)&&!refs.has(source.version_id))refs.set(source.version_id,
        {source,label:(LABEL[row.kind]??'修改')+'草稿 · 未经人工认可'});
    }
    return [...refs.values()];
  }

  function renderRows(value) {
    const current=draft(value.project.id),elements=[];
    current.items.forEach((item,index)=>{
      const group=node('fieldset'),legend=node('legend',`问题 ${index+1}`),scope=node('select');
      for(const [id,label]of [['whole_film','整片'],['time_range','时间范围']]){const option=node('option',label);option.value=id;scope.append(option);}
      scope.value=item.scope.kind;
      const start=node('input'),end=node('input');
      for(const input of [start,end]){input.type='number';input.min='0';input.step='.1';}
      start.setAttribute('aria-label','开始秒数');end.setAttribute('aria-label','结束秒数');
      start.value=item.scope.start_seconds??'';end.value=item.scope.end_seconds??'';
      start.hidden=end.hidden=item.scope.kind!=='time_range';
      scope.addEventListener('change',()=>{item.scope=scope.value==='whole_film'?{kind:'whole_film'}:{kind:'time_range',start_seconds:0,end_seconds:1};renderRows(value);});
      start.addEventListener('input',()=>{item.scope.start_seconds=Number(start.value);});
      end.addEventListener('input',()=>{item.scope.end_seconds=Number(end.value);});
      const problem=node('textarea'),expected=node('textarea');problem.rows=2;expected.rows=2;problem.maxLength=expected.maxLength=8000;
      problem.value=item.problem;expected.value=item.expected;
      const pLabel=node('label','现有问题'),eLabel=node('label','希望达到的效果');pLabel.append(problem);eLabel.append(expected);
      problem.addEventListener('input',()=>{item.problem=problem.value;});expected.addEventListener('input',()=>{item.expected=expected.value;});
      const remove=node('button','移除此项','text-button');remove.type='button';remove.disabled=current.items.length<2;
      remove.addEventListener('click',()=>{current.items.splice(index,1);renderRows(value);});
      group.append(legend,scope,start,end,pLabel,eLabel,remove);elements.push(group);
    });
    rows.replaceChildren(...elements);
  }

  function render() {
    const value=context(),id=value.project?.id,data=cache.get(id),options=sourceOptions(value,data);
    section.hidden=!id||(!options.length&&!data?.operations?.length&&!data?.enabled);
    if(!id){pause();return;}
    if(form.dataset.projectId!==id){form.dataset.projectId=id;instruction.value=draft(id).instruction;acknowledged.checked=false;renderRows(value);}
    const wanted=selected.get(id),chosen=options.find(row=>row.source.version_id===wanted)??options[0];
    chooser.replaceChildren(...options.map(row=>{const option=node('option',row.label);option.value=row.source.version_id;return option;}));
    if(chosen){selected.set(id,chosen.source.version_id);chooser.value=chosen.source.version_id;}
    sourceLabel.hidden=!chosen;player.hidden=download.hidden=!chosen;
    const next=chosen?`${id}:${chosen.source.version_id}`:null;
    if(next!==mediaKey){pause();mediaKey=next;if(chosen){player.src=`/api/v1/renders/${encodeURIComponent(chosen.source.version_id)}/video`;download.href=player.src;download.setAttribute('download',`draft-${chosen.source.version_id}.mp4`);}else{player.removeAttribute('src');download.removeAttribute('href');}}
    const line=data?.sequences?.find(row=>row.sources.some(source=>sameSource(source,chosen?.source)));
    const remaining=line?.remaining?.user_requested??0;
    const blocked=!data?.enabled||!chosen||!line||line.id!==value.run?.sequence_id||value.run?.current!==true
      ||value.unsaved||value.projectBusy||hasUnresolvedOperation(id)||readErrors.has(id);
    broad.disabled=blocked||remaining<1||line?.automatic_finished!==true||!acknowledged.checked;
    targeted.disabled=blocked||!acknowledged.checked;
    instruction.disabled=blocked;
    broad.textContent=`再打磨一轮（剩余 ${remaining} 轮）`;
    note.textContent=!data?.enabled?'自动审阅与打磨暂未开放，已有草稿仍可查看。'
      :unresolved.has(id)?'正在查询刚才请求的真实结果，不会重复提交。'
      :hasUnresolvedOperation(id)?'本轮正在执行或确认停止；可查看进度或申请取消。'
      :line?.automatic_finished!==true?'默认的一轮审阅与打磨尚未结束。已有原始草稿可先查看。'
      :remaining>0?'整片默认打磨一轮；你可以主动追加最多两轮，也可直接填写具体问题。'
      :'追加两轮已用完；继续优化需要填写具体问题与希望达到的效果。';
    note.textContent+=' 抽帧检查不能替代完整播放和试听；所有结果仍是未经人工认可的草稿。';
    error.hidden=!readErrors.has(id);error.textContent=readErrors.get(id)??'';
    const entries=(data?.operations??[]).map(row=>{
      const entry=node('div',null,'production-run-item');
      entry.append(node('strong',`${LABEL[row.kind]??'打磨'} · ${STATUS[row.status]??'状态待确认'} · ${PHASE[row.phase]??''}`));
      if(row.usage&&row.budget)entry.append(node('p',`本轮任务 ${row.usage.agent_tasks}/${row.budget.max_agent_tasks}，合成 ${row.usage.renders}/${row.budget.max_renders}。`,'field-note'));
      if(row.review){entry.append(node('p',row.review.summary??'已保存画面检查结果。'));for(const issue of row.review.issues??[])entry.append(node('p',`${issue.start_seconds}–${issue.end_seconds} 秒：${issue.problem}`));}
      if(row.result?.revision_performed===false)entry.append(node('p','本轮未发现足以据此修改的画面问题，保留原草稿。','field-note'));
      if(row.result?.revision_performed===true)entry.append(node('p','新草稿已生成，可在上方切换比较；尚未再次完整审阅或试听。','field-note'));
      if(row.error)entry.append(node('p','本轮有未解决的问题，已有结果保留；请重新读取状态或补充具体意见。','batch-item-error'));
      if(!TERMINAL.has(row.status)){const cancel=node('button','申请取消本轮','text-button');cancel.type='button';cancel.disabled=busy.has(id);cancel.addEventListener('click',()=>cancelRound(row));entry.append(cancel);}
      return entry;
    });history.replaceChildren(...entries);
  }

  async function load(projectId,epoch) {
    const value=context();if(value.project?.id!==projectId||value.epoch!==epoch)return;
    const ticket=++sequence;tickets.set(projectId,ticket);
    try {
      const data=await request(pathFor(projectId));if(!same(value)||tickets.get(projectId)!==ticket)return;
      cache.set(projectId,data);readErrors.delete(projectId);
      if(!unresolved.has(projectId)){const raw=storage?.getItem(storageKey(value));if(raw){const pending=JSON.parse(raw);if(pending.project_id===projectId&&typeof pending.request_id==='string')unresolved.set(projectId,pending);}}
      const pending=unresolved.get(projectId);
      if(pending&&data.operations?.some(row=>row.request_id===pending.request_id&&sameSource(row.source,pending.source))){unresolved.delete(projectId);storage?.removeItem(storageKey(value));}
      render();onChange();
    }catch(error){if(same(value)&&tickets.get(projectId)===ticket){readErrors.set(projectId,'打磨记录暂时无法读取。已有草稿保留，请重新读取状态。');render();if(error?.status===401)onError(error);}}
  }

  async function start(kind) {
    const value=context(),id=value.project?.id,data=cache.get(id);if(!id||!data?.enabled||hasUnresolvedOperation(id)||!acknowledged.checked)return;
    const chosen=sourceOptions(value,data).find(row=>row.source.version_id===selected.get(id));
    const line=data.sequences.find(row=>row.sources.some(source=>sameSource(source,chosen?.source)));
    let intent;
    try {
      intent=createRefinementRequest(value.project,chosen?.source,kind,draft(id),line,{requestId:makeRequestId(),unsaved:value.unsaved,busy:value.projectBusy});
      if(line.id!==value.run?.sequence_id||value.run?.current!==true)throw Error('这份草稿不对应当前项目内容，请重新读取。');
      const pending={project_id:id,source:intent.body.source,request_id:intent.body.request_id},raw=JSON.stringify(pending);
      if(!storage)throw Error('无法保存本次请求，请检查浏览器存储后再试。');
      storage.setItem(storageKey(value),raw);if(storage.getItem(storageKey(value))!==raw)throw Error('本次请求未能可靠保存。');
      unresolved.set(id,pending);
    }catch(error){onError(error);return;}
    busy.add(id);tickets.set(id,++sequence);render();onChange();
    try {
      const result=await request(intent.path,{method:'POST',body:intent.body});
      if(result?.project_id!==id||result.request_id!==intent.body.request_id||!sameSource(result.source,intent.body.source))throw Error('修改请求结果尚未确认。');
      const old=cache.get(id)??{sequences:[]};cache.set(id,{...old,operations:[result,...(old.operations??[]).filter(row=>row.id!==result.id)]});
      unresolved.delete(id);storage.removeItem(storageKey(value));acknowledged.checked=false;
      if(same(value))onSuccess('本轮修改已开始；完成后请比较整片草稿。');
    }catch(error){if(Number.isInteger(error?.status)&&(error.status<500
      ||['production_refinement_disabled','film_review_unavailable'].includes(error.code))){unresolved.delete(id);storage.removeItem(storageKey(value));}if(same(value))onError(error);}
    finally{busy.delete(id);if(same(value)){try{await refresh();}catch(error){onError(error);}render();onChange();}}
  }

  async function cancelRound(row) {
    const value=context(),id=value.project?.id;if(!id||busy.has(id)||TERMINAL.has(row.status))return;
    busy.add(id);render();onChange();
    try{await request(`/api/v1/refinements/${encodeURIComponent(row.id)}/cancel`,{method:'POST',body:{}});if(same(value))onSuccess('已申请取消，将继续等待本轮真实停止。');}
    catch(error){if(same(value))onError(error);}
    finally{busy.delete(id);if(same(value)){try{await refresh();}catch(error){onError(error);}render();onChange();}}
  }

  chooser.addEventListener('change',()=>{const id=context().project?.id;if(id){selected.set(id,chooser.value);acknowledged.checked=false;render();}});
  instruction.addEventListener('input',()=>{const id=context().project?.id;if(id)draft(id).instruction=instruction.value;});
  acknowledged.addEventListener('change',render);
  broad.addEventListener('click',()=>start('user_requested'));targeted.addEventListener('click',()=>start('requirements'));
  form.addEventListener('submit',event=>event.preventDefault());
  add.addEventListener('click',()=>{const value=context();if(!value.project)return;const current=draft(value.project.id);if(current.items.length<64){current.items.push({scope:{kind:'whole_film'},problem:'',expected:''});renderRows(value);}});
  player.addEventListener('play',onMediaPlay);
  return {render,load,pause,start,hasUnresolvedOperation,hasActiveOperation:hasUnresolvedOperation,
    hasRefreshableOperation:hasUnresolvedOperation,hasProjectDrafts:id=>Boolean(drafts.get(id)?.instruction?.trim()
      ||drafts.get(id)?.items.some(row=>row.problem.trim()||row.expected.trim())),
    nodes:{section,note,error,chooser,player,download,instruction,acknowledged,broad,targeted,history,requirements,rows,add}};
}
