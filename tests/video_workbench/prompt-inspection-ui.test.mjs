import test from 'node:test';
import assert from 'node:assert/strict';
import {createPromptInspection,operationInputPath} from '../../video_workbench/animation_lab_web/prompt-inspection.mjs';

const root=new URL('https://video-service.panghuer.top/dev/animations/');
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.handlers={};this.dataset={};this.attributes={};this._text='';this.open=false;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this._text='';this.children=children;}
  addEventListener(name,handler){this.handlers[name]=handler;}
  setAttribute(name,value){this.attributes[name]=value;}
  focus(){this.focused=true;}
  descendants(tag){return this.children.flatMap(child=>[child,...child.descendants(tag)]).filter(child=>!tag||child.tagName===tag.toUpperCase());}
}
const doc={createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment')};
const operation=(id='op-1')=>({operation_id:id,stage:'creator',mode:'create',round:1,status:'completed',available:true,
  input_schema:'video.animation-author-input.v1',input_sha256:'a'.repeat(64),input_bytes:123,
  candidate_ids:['clip-'+id],author_operation_id:'author-'+id,
  input_url:'/dev/animations/api/method-lab/commissions/c-1/operations/'+id+'/input'});
const submitted={title:'独立对象汇成群体',goal:'看懂触发与协同',feedback:'增加响应的错峰节奏',duration_seconds:8,
  criteria:[{id:'rhythm',description:'动作因果清楚'}],budget:{max_rounds:2,max_seconds:900},
  capability_selection:{creation:['canvas'],motion:['gsap']},reference:{url:'https://example.com/ref',start_seconds:21,end_seconds:28},
  base_commission_id:'base',base_candidate_id:'baseline'};
const snapshot=(changes={})=>({schema:'video.animation-prompt-inspection.v1',commission_id:'c-1',request_id:'request',
  scope:'method_lab_agent_inputs',submission:{origin:'captured_request',is_original:true,document:submitted,sha256:'b'.repeat(64),limitations:[]},
  effective_request:{document:submitted,sha256:'b'.repeat(64)},adopted_capabilities:{creation:['canvas'],motion:['gsap']},
  operations:[operation()],limitations:['Manim recipe 本轮不在此输入索引中。'],...changes});
const frozen=(id='op-1',input={goal:'这是实际冻结输入',skill_snapshot:{entry:'保存的当次知识'}})=>({
  schema:'video.animation-operation-input.v1',commission_id:'c-1',operation_id:id,stage:'create',mode:'ordinary',round:0,
  input_schema:'video.animation-author-input.v1',input_sha256:'a'.repeat(64),input,
  limitations:['未保存的系统提示与固定执行合同不提供。']});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const find=(view,key)=>view.descendants('details').find(node=>node.dataset.methodDisclosure===key);
const button=(view,label)=>view.descendants('button').find(node=>node.textContent===label);
async function open(section){section.open=true;await section.handlers.toggle();await tick();}
function harness(handler=()=>snapshot(),options={}) {
  const requests=[],downloads=[],copied=[];
  const inspection=createPromptInspection({root,request:async(path,requestOptions)=>{
    requests.push({path,requestOptions});return handler(path,requestOptions);
  },clipboard:{writeText:async text=>copied.push(text)},download:(name,value)=>downloads.push({name,value}),...options});
  return {inspection,requests,downloads,copied,render:(id='c-1',states={})=>inspection.render(doc,{id},states)};
}

test('default collapsed inspection performs no read until opened and readable submission includes provenance and form fields',async()=>{
  const h=harness(),view=h.render();
  assert.deepEqual(view.descendants('summary').map(row=>row.textContent),['本次提交需求','各轮 Agent 输入']);
  assert.ok(view.descendants('details').every(row=>!row.open));assert.equal(h.requests.length,0);
  await open(find(view,'prompt-submission'));
  assert.equal(h.requests.length,1);assert.equal(h.requests[0].path,'api/method-lab/commissions/c-1/prompt-inspection');
  assert.match(view.textContent,/来源：原始提交记录/);
  for(const label of ['表达目标','本次修改意见','验收标准','预算','允许使用的能力','参考视频与片段','继承与迁移关系'])assert.match(view.textContent,new RegExp(label));
  assert.equal(find(view,'prompt-submission-json').open,false);
  await button(view,'复制需求文本').handlers.click();
  assert.match(h.copied[0],/表达目标\n看懂触发与协同/);assert.match(h.copied[0],/本次修改意见\n增加响应的错峰节奏/);
  button(view,'下载 JSON').handlers.click();assert.deepEqual(h.downloads[0].value,submitted);
  assert.ok(h.requests.every(row=>row.requestOptions===undefined));h.inspection.close();
});

test('reconstructed requests state their limits and loaded data is text rather than markup',async()=>{
  const hostile='<img src=x onerror=alert(1)>';
  const h=harness(()=>snapshot({submission:{origin:'reconstructed_frozen_fields',is_original:false,
    document:{...submitted,goal:hostile},sha256:'c'.repeat(64),limitations:['原始 HTTP 提交未留存。']}}));
  const view=h.render();await open(find(view,'prompt-submission'));
  assert.match(view.textContent,/从冻结字段还原；不是原始提交记录/);assert.match(view.textContent,/原始 HTTP 提交未留存/);
  assert.ok(view.textContent.includes(hostile));assert.equal(view.descendants('img').length,0);
  await button(view,'复制需求文本').handlers.click();assert.match(h.copied[0],/非原始提交/);h.inspection.close();
});

test('historical frozen execution budgets are never labelled as submitted exploration options in view or copied text',async()=>{
  const document={goal:'历史需求',budget:{creator:3,review:2,render:2,seconds:900}};
  const h=harness(()=>snapshot({submission:{origin:'reconstructed_frozen_fields',is_original:false,
    document,limitations:[]},effective_request:{document}})),view=h.render();
  await open(find(view,'prompt-submission'));
  assert.match(view.textContent,/执行预算（历史冻结值）/);assert.doesNotMatch(view.textContent,/提交预算/);
  assert.match(view.textContent,/原提交的探索轮数选项无法从历史冻结执行预算中还原/);
  await button(view,'复制需求文本').handlers.click();
  assert.match(h.copied[0],/执行预算（历史冻结值）/);assert.doesNotMatch(h.copied[0],/提交预算/);
  assert.match(h.copied[0],/探索轮数选项无法.*还原/);h.inspection.close();
});

test('operation index is lazy and each opened round reads exact frozen input for copy and JSON download',async()=>{
  const value=frozen();
  const h=harness(path=>path.endsWith('/input')?value:snapshot()),view=h.render();
  await open(find(view,'prompt-operations'));
  assert.equal(h.requests.length,1);assert.match(view.textContent,/表达目标不是完整 prompt/);
  assert.match(view.textContent,/Manim recipe 本轮不在此输入索引中/);
  const round=find(view,'prompt-operation-op-1');assert.equal(round.open,false);
  await open(round);assert.equal(h.requests.length,2);
  assert.equal(h.requests[1].path,root.origin+operation().input_url);
  assert.match(round.textContent,/clip-op-1/);assert.match(round.textContent,/author-op-1/);
  assert.match(round.textContent,/这是实际冻结输入/);assert.match(round.textContent,/未保存的系统提示与固定执行合同不提供/);
  await button(round,'复制冻结 JSON').handlers.click();assert.deepEqual(JSON.parse(h.copied[0]),value.input);
  button(round,'下载 JSON').handlers.click();assert.deepEqual(h.downloads[0].value,value.input);
  round.open=false;round.handlers.toggle();await open(round);assert.equal(h.requests.length,2);
  h.inspection.close();
});

test('clipboard failure exposes selectable content and does not create another Agent request',async()=>{
  const h=harness(()=>snapshot(),{clipboard:{writeText:async()=>{throw Error('denied');}}}),view=h.render();
  await open(find(view,'prompt-submission'));
  const copy=button(view,'复制需求文本');await copy.handlers.click();await copy.handlers.click();
  const fallback=view.descendants('pre').filter(row=>row.className==='prompt-copy-fallback');
  assert.equal(fallback.length,1);assert.match(fallback[0].textContent,/看懂触发与协同/);
  assert.equal(fallback[0].tabIndex,0);assert.equal(fallback[0].focused,true);
  assert.match(view.textContent,/复制未成功，可在下方选中并复制文本/);assert.equal(h.requests.length,1);h.inspection.close();
});

test('input links must target this origin, experiment and exact operation without alternate query or credentials',()=>{
  const accepted='api/method-lab/commissions/c-1/operations/op-1/input';
  assert.equal(operationInputPath(accepted,root,'c-1','op-1'),new URL(accepted,root).href);
  assert.equal(operationInputPath('/'+accepted,root,'c-1','op-1'),new URL(accepted,root).href);
  for(const path of ['https://evil.example/'+accepted,'/dev/animations/'+accepted+'?token=oops',
    '/dev/animations/'+accepted+'#fragment','/dev/animations/'+accepted.replace('c-1','c-2'),
    '/dev/animations/'+accepted.replace('op-1','op-2'),'javascript:alert(1)',
    'https://user:secret@video-service.panghuer.top/dev/animations/'+accepted]) {
    assert.throws(()=>operationInputPath(path,root,'c-1','op-1'),/链接/);
  }
});

test('switching experiments ignores previous inflight reads and releasing or closing a view removes updates',async()=>{
  let resolve;
  const slow=new Promise(done=>{resolve=done;});
  const h=harness(path=>path.includes('/c-1/')?slow:snapshot({commission_id:'c-2',effective_request:{document:{goal:'第二个实验'}},
    submission:{...snapshot().submission,document:{goal:'第二个实验'}}}));
  const old=h.render();old.children[0].open=true;old.children[0].handlers.toggle();
  const current=h.render('c-2');await open(find(current,'prompt-submission'));
  assert.match(current.textContent,/第二个实验/);resolve(snapshot());await tick();
  assert.doesNotMatch(current.textContent,/看懂触发与协同/);
  h.inspection.releaseView();const before=current.textContent;h.inspection.close();
  assert.equal(current.textContent,before);
});

test('same-experiment rebuild preserves disclosure intent and reuses current reads while switching clears its cache',async()=>{
  const h=harness(path=>path.endsWith('/input')?frozen():snapshot()),states={};
  let view=h.render('c-1',states);await open(find(view,'prompt-operations'));await open(find(view,'prompt-operation-op-1'));
  assert.equal(h.requests.length,2);h.inspection.releaseView();
  view=h.render('c-1',states);await tick();assert.equal(find(view,'prompt-operations').open,true);
  assert.equal(find(view,'prompt-operation-op-1').open,true);assert.match(view.textContent,/这是实际冻结输入/);assert.equal(h.requests.length,2);
  h.render('c-2');view=h.render('c-1');await open(find(view,'prompt-submission'));assert.equal(h.requests.length,3);h.inspection.close();
});

test('input caching is bounded and evicted rounds can be explicitly read again',async()=>{
  const h=harness(path=>path.endsWith('/input')?frozen(path.split('/operations/')[1].split('/')[0]):
    snapshot({operations:[operation('op-1'),operation('op-2'),operation('op-3')]}),{maxInputBytes:10000});
  const view=h.render();await open(find(view,'prompt-operations'));
  for(const id of ['op-1','op-2','op-3'])await open(find(view,'prompt-operation-'+id));
  assert.equal(h.requests.length,4);
  assert.doesNotMatch(find(view,'prompt-operation-op-1').textContent,/这是实际冻结输入/);
  await button(find(view,'prompt-operation-op-1'),'读取本轮输入').handlers.click();await tick();
  assert.equal(h.requests.length,5);assert.match(find(view,'prompt-operation-op-1').textContent,/这是实际冻结输入/);h.inspection.close();
});

test('read failures show an explicit GET retry and malformed or cross-operation payloads stay unavailable',async()=>{
  let calls=0;
  const h=harness(path=>{
    if(path.endsWith('/input'))return frozen('wrong-operation');
    if(++calls===1)throw Error('读取失败');return snapshot();
  }),view=h.render();await open(find(view,'prompt-operations'));
  assert.match(view.textContent,/读取失败/);await button(view,'重试读取').handlers.click();await tick();
  await open(find(view,'prompt-operation-op-1'));
  assert.match(view.textContent,/冻结输入与当前记录不一致/);assert.equal(button(view,'复制冻结 JSON'),undefined);
  assert.equal(h.requests.length,3);assert.ok(h.requests.every(row=>row.requestOptions===undefined));h.inspection.close();
});

test('planning and creation labels use real round meaning and unavailable input explains the reason without a read',async()=>{
  const h=harness(()=>snapshot({operations:[{...operation('plan-op'),mode:'plan',round:0},
    {...operation('missing-op'),available:false,input_url:null,unavailable_reason:'unsupported_input_contract'}]}));
  const view=h.render();await open(find(view,'prompt-operations'));
  assert.equal(find(view,'prompt-operation-plan-op').children[0].textContent,'制作前规划 · 已完成');
  assert.equal(find(view,'prompt-operation-missing-op').children[0].textContent,'创作 · 第 1 轮 · 已完成');
  await open(find(view,'prompt-operation-missing-op'));
  assert.match(view.textContent,/历史记录的输入格式暂不支持读取/);assert.equal(h.requests.length,1);h.inspection.close();
});

test('rounds show readable requirements and keep knowledge and full frozen JSON in closed subdisclosures',async()=>{
  const input={goal:'解释波的局部抵消',feedback:'让波峰相遇时的变化可追踪',criteria:[{description:'抵消区域清晰'}],
    constraints:{result_requirements:['先建立两列波','清楚显示局部叠加'],creative_direction:{rhythm:'让传播连续'}},
    knowledge_snapshot:{pages:['真实保存的知识']},methods:[{guidance:'已保存的方法'}]};
  const h=harness(path=>path.endsWith('/input')?frozen('op-1',input):snapshot()),view=h.render();
  await open(find(view,'prompt-operations'));await open(find(view,'prompt-operation-op-1'));
  const round=find(view,'prompt-operation-op-1');assert.match(round.textContent,/本轮结果要求/);
  assert.match(round.textContent,/先建立两列波/);assert.match(round.textContent,/本轮创作指导/);
  assert.equal(find(view,'prompt-operation-op-1-context').open,false);assert.equal(find(view,'prompt-operation-op-1-json').open,false);
  await button(round,'复制本轮需求文本').handlers.click();assert.match(h.copied[0],/本轮修改意见\n让波峰相遇时的变化可追踪/);
  assert.doesNotMatch(h.copied[0],/knowledge_snapshot|真实保存的知识/);h.inspection.close();
});

test('releasing an inflight view leaves detached nodes untouched and a same-experiment rebuild reads the saved result',async()=>{
  let resolve;
  const response=new Promise(done=>{resolve=done;}),h=harness(()=>response),states={};
  const previous=h.render('c-1',states);find(previous,'prompt-submission').open=true;find(previous,'prompt-submission').handlers.toggle();
  const loading=previous.textContent;h.inspection.releaseView();resolve(snapshot());await tick();
  assert.equal(previous.textContent,loading);
  const current=h.render('c-1',states);await tick();assert.match(current.textContent,/看懂触发与协同/);
  assert.equal(h.requests.length,1);h.inspection.close();
});

test('an inflight round cannot populate another experiment and changed frozen hashes cannot be copied',async()=>{
  let resolve;
  const pending=new Promise(done=>{resolve=done;});
  const h=harness(path=>path.endsWith('/input')?pending:path.includes('/c-2/')
    ?snapshot({commission_id:'c-2',operations:[],submission:{...snapshot().submission,document:{goal:'另一个独立实验'}}}):snapshot());
  const original=h.render();await open(find(original,'prompt-operations'));
  find(original,'prompt-operation-op-1').open=true;find(original,'prompt-operation-op-1').handlers.toggle();
  const current=h.render('c-2');await open(find(current,'prompt-submission'));const before=current.textContent;
  resolve(frozen());await tick();assert.equal(current.textContent,before);assert.doesNotMatch(current.textContent,/这是实际冻结输入/);
  h.inspection.close();
  const invalid=harness(path=>path.endsWith('/input')?{...frozen(),input_sha256:'c'.repeat(64)}:snapshot()),view=invalid.render();
  await open(find(view,'prompt-operations'));await open(find(view,'prompt-operation-op-1'));
  assert.match(view.textContent,/冻结输入与当前记录不一致/);assert.equal(button(view,'复制冻结 JSON'),undefined);invalid.inspection.close();
});
