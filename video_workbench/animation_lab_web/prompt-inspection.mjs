// Read-only inspection of persisted requests and frozen Agent inputs.
const API = 'api/method-lab/commissions/';
const INDEX_SCHEMA = 'video.animation-prompt-inspection.v1';
const INPUT_SCHEMA = 'video.animation-operation-input.v1';
const text = value => typeof value === 'string' ? value : '';
const rows = value => Array.isArray(value) ? value : [];
const json = value => JSON.stringify(value, null, 2);
const stageNames = {plan:'规划',create:'创作',review:'评审',component:'部件创作',integrate:'整合',render:'渲染'};
const statusNames = {reserved:'已预留',submitting:'提交中',submission_unknown:'提交待核对',submitted:'已提交',
  queued:'等待执行',running:'执行中',completed:'已完成',failed:'失败',cancelled:'已取消'};
const instructionNames={result_requirements:'本轮结果要求',creative_direction:'本轮创作指导',
  capability_selection:'能力选择',allowed_engines:'允许使用的画面能力',method_application:'方法应用要求',
  skill_contract:'技能使用要求',duration_seconds:'时长（秒）',width:'宽度',height:'高度',fps:'帧率',
  max_rounds:'最多探索轮数',max_seconds:'时间上限（秒）',creation:'画面创作',motion:'动作编排',
  base_commission_id:'基于实验',base_candidate_id:'基于样片',retry_of_commission_id:'技术重试原实验',
  baseline_demonstration_id:'基准样片',role:'任务角色',content_changes:'内容变化',structural_changes:'结构变化'};
function readable(value,depth=0) {
  if(typeof value==='string')return value;
  if(Array.isArray(value))return value.map(row=>'- '+(row&&typeof row==='object'&&row.description
    ?row.description:readable(row,depth+1))).join('\n');
  if(value&&typeof value==='object')return Object.entries(value).map(([key,row])=>
    (instructionNames[key]??key)+'：'+(row&&typeof row==='object'?'\n'+readable(row,depth+1):readable(row,depth+1))).join('\n');
  return String(value);
}

function node(doc, tag, value, className='') {
  const item=doc.createElement(tag);item.className=className;
  if(value!==undefined)item.textContent=value;
  return item;
}
function action(doc, label, handler) {
  const item=node(doc,'button',label,'button secondary');item.type='button';
  item.addEventListener('click',handler);return item;
}
function disclosure(doc,key,title,states) {
  const item=node(doc,'details',undefined,'technical-details prompt-inspection');
  item.dataset.methodDisclosure=key;item.open=states?.[key]===true;
  item.addEventListener('toggle',()=>{if(states)states[key]=item.open;});
  item.append(node(doc,'summary',title));return item;
}
function limitations(doc,value) {
  const lines=rows(value).filter(row=>typeof row==='string'&&row.trim());
  if(!lines.length)return null;
  const item=node(doc,'ul',undefined,'small muted prompt-limitations');
  for(const line of lines)item.append(node(doc,'li',line));return item;
}
function field(doc,host,label,value) {
  if(value===undefined||value===null||value==='')return;
  const item=node(doc,'div',undefined,'prompt-field');
  item.append(node(doc,'h4',label),node(doc,'pre',readable(value),'prompt-readable'));
  host.append(item);
}
function briefText(snapshot) {
  const document=snapshot.submission.document;
  const origin=snapshot.submission.is_original?'原始提交记录':'从冻结字段还原的需求（非原始提交）';
  const fields=[['创作模式',document.creation_mode],['所属效果',document.effect_id],['加载动效 WikiSkill',document.use_wikiskill],
    ['表达目标',document.goal],['本次保留的问题',document.problem_summary],['本次修改意见',document.feedback],['验收标准',document.criteria],
    [snapshot.submission.is_original?'提交预算':'执行预算（历史冻结值）',document.budget],
    ['能力选择',document.capability_selection],['参考',document.reference]];
  return [origin,...fields.filter(([,value])=>value!==undefined&&value!==null&&value!=='')
    .map(([label,value])=>label+'\n'+(typeof value==='string'?value:json(value))),
    ...(!snapshot.submission.is_original?['原提交的探索轮数选项无法从历史冻结执行预算中还原。']:[]),
    '提交需求 JSON\n'+json(document),...rows(snapshot.submission.limitations),...rows(snapshot.limitations)].join('\n\n');
}
function downloadJson(doc,name,value) {
  const url=URL.createObjectURL(new Blob([json(value)+'\n'],{type:'application/json;charset=utf-8'}));
  const link=doc.createElement('a');link.href=url;link.download=name;
  try { link.click(); } finally { setTimeout(()=>URL.revokeObjectURL(url),0); }
}
function portableName(value) { return text(value).replace(/[^a-zA-Z0-9_-]/g,'_').slice(0,100)||'experiment'; }

export function operationInputPath(value,root,commissionId,operationId) {
  let url;
  try {
    const path=text(value);
    url=new URL(path.startsWith('/api/method-lab/')?path.slice(1):path,root);
  }catch {throw Error('输入记录链接无效，请刷新核对。');}
  const expected=new URL(API+encodeURIComponent(commissionId)+'/operations/'+encodeURIComponent(operationId)+'/input',root);
  if(url.origin!==expected.origin||url.pathname!==expected.pathname||url.search||url.hash||url.username||url.password) {
    throw Error('输入记录链接与当前实验不一致，请刷新核对。');
  }
  return url.href;
}

export function createPromptInspection({request,root,clipboard,download,maxInputBytes=8*1024*1024}={}) {
  let currentId=null,generation=0,index={status:'idle'},inputs=new Map(),viewListeners=new Set(),closed=false;
  const emit=()=>{for(const listener of [...viewListeners])listener();};
  const select=id=>{
    if(currentId===id)return;
    generation++;currentId=id;index={status:'idle'};inputs.clear();viewListeners.clear();
  };
  const loadIndex=async(force=false)=>{
    if(closed||!currentId||typeof request!=='function')return;
    if(index.status==='loading')return index.promise;
    if(!force&&index.status==='ready')return index.value;
    const token=generation,id=currentId;
    const state={status:'loading'};index=state;emit();
    state.promise=(async()=>{
      try {
        const value=await request(API+encodeURIComponent(id)+'/prompt-inspection');
        if(closed||token!==generation)return;
        if(value?.schema!==INDEX_SCHEMA||value.commission_id!==id||!value.submission?.document
          ||typeof value.submission.document!=='object'||!Array.isArray(value.operations))throw Error('输入索引格式不完整，请刷新核对。');
        index={status:'ready',value};return value;
      }catch(error){if(!closed&&token===generation)index={status:'error',error:text(error?.message)||'无法读取需求记录。'};}
      finally{if(!closed&&token===generation)emit();}
    })();return state.promise;
  };
  const trimInputs=keep=>{
    let size=[...inputs.values()].reduce((sum,row)=>sum+(row.bytes||0),0);
    for(const [id,state]of inputs) {
      if(inputs.size<=2&&size<=maxInputBytes)break;
      if(id===keep||state.status==='loading')continue;
      size-=state.bytes||0;inputs.delete(id);
    }
  };
  const loadInput=async(operation,retry=false)=>{
    const id=text(operation.operation_id),previous=inputs.get(id);
    if(closed||!currentId||!id||operation.available===false||typeof request!=='function')return;
    if(previous?.status==='loading')return previous.promise;
    if(!retry&&previous?.status==='ready')return previous.value;
    const token=generation,commissionId=currentId,state={status:'loading'};
    inputs.delete(id);inputs.set(id,state);emit();
    state.promise=(async()=>{
      try {
        const path=operationInputPath(operation.input_url,root,commissionId,id);
        const value=await request(path);
        if(closed||token!==generation)return;
        if(value?.schema!==INPUT_SCHEMA||value.commission_id!==commissionId||value.operation_id!==id
          ||value.input_sha256!==operation.input_sha256||value.input_schema!==operation.input_schema
          ||!value.input||typeof value.input!=='object')throw Error('冻结输入与当前记录不一致，请刷新核对。');
        inputs.set(id,{status:'ready',value,bytes:new TextEncoder().encode(json(value)).length});trimInputs(id);return value;
      }catch(error){if(!closed&&token===generation)inputs.set(id,{status:'error',error:text(error?.message)||'无法读取冻结输入。'});}
      finally{if(!closed&&token===generation)emit();}
    })();return state.promise;
  };
  function exports(doc,host,content,value,filename,copyLabel) {
    const actions=node(doc,'div',undefined,'detail-actions'),status=node(doc,'p','','small muted');status.setAttribute('role','status');
    let fallback;
    actions.append(action(doc,copyLabel,async()=>{
      try {
        const copy=clipboard??globalThis.navigator?.clipboard;
        if(typeof copy?.writeText!=='function')throw Error('clipboard unavailable');
        await copy.writeText(content);status.textContent='已复制。';
      }catch {
        status.textContent='复制未成功，可在下方选中并复制文本。';
        if(!fallback) {
          fallback=node(doc,'pre',content,'prompt-copy-fallback');fallback.tabIndex=0;fallback.setAttribute('aria-label','可选中复制的完整文本');host.append(fallback);
        }
        fallback.focus?.();
      }
    }),action(doc,'下载 JSON',()=>{
      try {(download??((name,data)=>downloadJson(doc,name,data)))(filename,value);status.textContent='已下载 JSON。';}
      catch{status.textContent='下载未成功，请展开 JSON 后复制保存。';}
    }));host.append(actions,status);
  }
  function renderBrief(doc,host,snapshot,states) {
    const submission=snapshot.submission,document=submission.document;
    host.append(node(doc,'p',submission.is_original?'来源：原始提交记录':'来源：从冻结字段还原；不是原始提交记录','prompt-origin'));
    field(doc,host,'效果名称',document.title);field(doc,host,'表达目标',document.goal);
    field(doc,host,'创作模式',document.creation_mode);field(doc,host,'所属效果',document.effect_id);
    field(doc,host,'加载动效 WikiSkill',document.use_wikiskill);field(doc,host,'本次保留的问题',document.problem_summary);
    field(doc,host,'本次修改意见',document.feedback);field(doc,host,'验收标准',document.criteria);
    field(doc,host,'期望时长（秒）',document.duration_seconds);
    field(doc,host,submission.is_original?'提交预算':'执行预算（历史冻结值）',document.budget);
    if(!submission.is_original)host.append(node(doc,'p','原提交的探索轮数选项无法从历史冻结执行预算中还原。','small muted'));
    if(snapshot.effective_request?.document?.budget
      &&json(snapshot.effective_request.document.budget)!==json(document.budget)) {
      field(doc,host,'执行预算（规范化后）',snapshot.effective_request.document.budget);
    }
    field(doc,host,'允许使用的能力',document.capability_selection);field(doc,host,'实际采用的能力',snapshot.adopted_capabilities);
    field(doc,host,'参考视频与片段',document.reference);
    const relations=Object.fromEntries(['base_commission_id','base_candidate_id','retry_of_commission_id',
      'baseline_demonstration_id','role','content_changes','structural_changes'].filter(key=>document[key]!==undefined).map(key=>[key,document[key]]));
    if(Object.keys(relations).length)field(doc,host,'继承与迁移关系',relations);
    if(submission.sha256)field(doc,host,'需求快照 SHA-256',submission.sha256);
    host.append(node(doc,'p','这些是本次需求字段，不是完整系统 prompt。历史任务未留存原提交时，仅展示可核对的冻结字段。','small muted'));
    const note=limitations(doc,[...rows(submission.limitations),...rows(snapshot.limitations)]);
    if(note){const evidence=disclosure(doc,'prompt-submission-limitations','记录范围与历史限制',states);evidence.append(note);host.append(evidence);}
    exports(doc,host,briefText(snapshot),document,'experiment-'+portableName(currentId)+'-request.json','复制需求文本');
    const raw=disclosure(doc,'prompt-submission-json','提交需求 JSON',states);raw.append(node(doc,'pre',json(document)));host.append(raw);
    if(snapshot.effective_request?.document&&json(snapshot.effective_request.document)!==json(document)) {
      const effective=disclosure(doc,'prompt-effective-request','执行采用的需求（规范化后）',states);
      effective.append(node(doc,'pre',json(snapshot.effective_request.document)));host.append(effective);
    }
  }
  function renderOperations(doc,host,snapshot,states) {
    const paints=[];
    host.append(node(doc,'p','这里只展示实际保存的冻结 Agent 输入；表达目标不是完整 prompt。未保存的系统 Skill、固定执行合同不作补写；已预留任务不代表已送达 Agent。图像仅保留文件引用与哈希，本页不包含 Manim recipe 等资源制作任务。','small muted'));
    host.append(action(doc,'刷新输入记录',()=>void loadIndex(true)));
    const note=limitations(doc,snapshot.limitations);
    if(note){const evidence=disclosure(doc,'prompt-index-limitations','记录范围说明',states);evidence.append(note);host.append(evidence);}
    if(!snapshot.operations.length)host.append(node(doc,'p','尚无可读取的 Agent 输入记录。','muted'));
    for(const operation of snapshot.operations) {
      const id=text(operation.operation_id),key='prompt-operation-'+id;
      const phase=operation.phase??operation.mode;
      const title=[phase==='plan'?'制作前规划':stageNames[phase]??stageNames[operation.stage]??'Agent 任务',
        operation.round>0?'第 '+operation.round+' 轮':phase==='plan'?'':'轮次未核实',
        statusNames[operation.status]??text(operation.status)].filter(Boolean).join(' · ');
      const item=disclosure(doc,key,title,states),body=node(doc,'div',undefined,'prompt-operation-body');
      item.dataset.promptOperationId=id;item.append(body);
      const paint=()=>{
        const state=inputs.get(id);body.replaceChildren();
        field(doc,body,'任务编号',id);if(operation.component_id)field(doc,body,'协作部件',operation.component_id);
        if(rows(operation.candidate_ids).length)field(doc,body,'关联样片',operation.candidate_ids);
        if(operation.author_operation_id)field(doc,body,'关联创作任务',operation.author_operation_id);
        if(operation.input_schema)field(doc,body,'输入格式',operation.input_schema);
        if(operation.input_sha256)field(doc,body,'冻结输入 SHA-256',operation.input_sha256);
        if(Number.isFinite(operation.input_bytes))field(doc,body,'输入大小（字节）',operation.input_bytes);
        if(operation.available===false) {
          const reason={unsupported_input_contract:'这条历史记录的输入格式暂不支持读取。',
            animation_prompt_inspection_private_data:'这条输入包含执行私有字段，未开放读取。',
            animation_method_operation_frozen_input_changed:'冻结输入校验未通过，暂不可读取。'}[operation.unavailable_reason]
            ??'这条记录没有可读取的冻结输入。';
          body.append(node(doc,'p',reason,'small muted'));return;
        }
        if(state?.status==='ready') {
          const value=state.value;
          const input=value.input,constraints=input.constraints??{};
          field(doc,body,'表达目标',input.goal);field(doc,body,'本轮修改意见',input.feedback);
          field(doc,body,'本轮验收标准',input.criteria);field(doc,body,'本轮分工要求',input.brief);
          for(const key of ['result_requirements','creative_direction','method_application'])field(doc,body,instructionNames[key],constraints[key]);
          const mainText=[['表达目标',input.goal],['本轮修改意见',input.feedback],['本轮验收标准',input.criteria],
            ['本轮分工要求',input.brief],['本轮结果要求',constraints.result_requirements],['本轮创作指导',constraints.creative_direction]]
            .filter(([,row])=>row!==undefined&&row!==null&&row!=='').map(([label,row])=>label+'\n'+readable(row)).join('\n\n');
          exports(doc,body,mainText||'本轮未包含独立需求文本，请查看冻结输入 JSON。',value.input,
            'experiment-'+portableName(currentId)+'-'+portableName(id)+'-input.json','复制本轮需求文本');
          const contexts=Object.fromEntries(['methods','knowledge_snapshot','knowledge','research_records','assets','comparison',
            'dependencies','reference','base_source'].filter(key=>input[key]!==undefined&&input[key]!==null).map(key=>[key,input[key]]));
          if(Object.keys(contexts).length) {
            const context=disclosure(doc,key+'-context','本轮附带上下文（方法、知识与素材）',states);
            context.append(node(doc,'pre',json(contexts)));body.append(context);
          }
          const raw=disclosure(doc,key+'-json','完整冻结输入 JSON',states);
          exports(doc,raw,json(value.input),value.input,'experiment-'+portableName(currentId)+'-'+portableName(id)+'-input.json','复制冻结 JSON');
          raw.append(node(doc,'pre',json(value.input),'prompt-frozen-input'));body.append(raw);
          const note=limitations(doc,value.limitations);
          if(note){const evidence=disclosure(doc,key+'-limitations','本轮记录范围说明',states);evidence.append(note);body.append(evidence);}
        }else if(state?.status==='loading')body.append(node(doc,'p','正在读取冻结输入…','small muted'));
        else if(state?.status==='error')body.append(node(doc,'p',state.error,'message error'),action(doc,'重试读取',()=>void loadInput(operation,true)));
        else body.append(node(doc,'p','读取这一轮真实保存的输入。','small muted'),action(doc,'读取本轮输入',()=>void loadInput(operation)));
      };
      paints.push(paint);paint();
      item.addEventListener('toggle',()=>{states[key]=item.open;if(item.open)void loadInput(operation);});
      host.append(item);if(item.open)queueMicrotask(()=>{if(item.open)void loadInput(operation);});
    }
    return ()=>{for(const paint of paints)paint();};
  }
  return {
    releaseView(){viewListeners.clear();},
    close(){closed=true;generation++;viewListeners.clear();inputs.clear();index={status:'idle'};},
    render(doc,detail,states={}) {
      states??={};
      viewListeners.clear();select(detail.id);
      const fragment=doc.createDocumentFragment();
      const bind=listener=>viewListeners.add(listener);
      for(const [kind,title]of [['submission','本次提交需求'],['operations','各轮 Agent 输入']]) {
        const section=disclosure(doc,'prompt-'+kind,title,states),body=node(doc,'div',undefined,'prompt-inspection-body');
        section.append(body);let painted=null,refreshOperations;
        const paint=()=>{
          if(!section.open)return;
          if(index.status==='ready') {
            if(painted===index.value){refreshOperations?.();return;}
            painted=index.value;body.replaceChildren();
            if(kind==='submission')renderBrief(doc,body,index.value,states);
            else refreshOperations=renderOperations(doc,body,index.value,states);
          }else if(index.status==='loading')body.replaceChildren(node(doc,'p','正在读取保存的输入记录…','small muted'));
          else if(index.status==='error')body.replaceChildren(node(doc,'p',index.error,'message error'),action(doc,'重试读取',()=>void loadIndex(true)));
          else body.replaceChildren(node(doc,'p','展开后读取，不会启动生成或消耗制作额度。','small muted'));
        };
        bind(paint);section.addEventListener('toggle',()=>{
          states['prompt-'+kind]=section.open;paint();if(section.open&&index.status==='idle')void loadIndex();
        });fragment.append(section);paint();if(section.open&&index.status==='idle')void loadIndex();
      }
      return fragment;
    },
  };
}
