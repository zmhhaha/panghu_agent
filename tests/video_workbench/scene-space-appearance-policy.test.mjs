import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {createSceneSpaceRenderer} from '../../video_workbench/motion_renderer/scene_space_runtime.mjs';
import {sampleMaterial} from '../../video_workbench/motion_renderer/scene_space_surface.mjs';

function canvas(){
 const context={save(){},restore(){},setTransform(){},beginPath(){},rect(){},clip(){},
  clearRect(){},fillRect(){},drawImage(){}};
 return{width:0,height:0,getContext:()=>context};
}
function binding(policy){
 const object={id:'rock',kind:'mountain',layer_id:'land',order:0,parent_id:null,
  bounds:{x:0,y:0,width:4,height:4},transform:{x:0,y:0,scale_x:1,scale_y:1,rotation:0},anchors:{},depth:{kind:'ordinal',value:1}};
 return{schema:'video.scene-space-render-binding.v1',backend:'canvas',
  frozen:{schema:'video.scene-space-frozen-plan.v1',plan_sha256:'fixture',intervals:[{id:'one',start_frame:0,end_frame:1}],
   plan:{id:'fixture',revision:1,fps:24,duration_frames:1,viewport:{width:4,height:4},
    worlds:[{id:'world',revision:1,space:{bounds:{x:0,y:0,width:4,height:4}},objects:[object],
     layers:[{id:'land',order:0,parallax:1,opacity:1,blur:0,mask_region_id:null}]}],
    shots:[{id:'shot',world_id:'world',start_frame:0,end_frame:1,camera:{keyframes:[{frame:0,x:2,y:2,zoom:1}]},local_animation:[]}]}},
  frame_range:{start_frame:0,end_frame:1},scratch:{width:4,height:4,padding:0,pixels:16},
  object_caches:[{world_id:'world',object_id:'rock',width:4,height:4,pixels:16,density:1}],total_cached_pixels:16,
  ...(policy?{appearance_policy:policy}:{})};
}
const policy=(quality,limit=100)=>({schema:'video.scene-appearance-policy.v1',
 requested_parameters:{material_quality:'full'},actual_parameters:{material_quality:quality},
 estimated_material_samples:8,material_noise_limit:limit});
const P=[.37,.91,1.43];
const options={seed:631907,space:'world',scales:[.28,1.3,5.4],weights:[.48,.33,.19],
 base:.5,amplitude:.26,roughness:.65,roughnessVariation:.14,normalStrength:.12};
function execute(selected,draw){
 return createSceneSpaceRenderer({canvas:canvas(),binding:binding(selected),createCanvas:canvas,
  paintObject:(_,object,world,tools)=>draw(tools,object,world)});
}
const fullExpected=sampleMaterial(P,options);
const coarseExpected=sampleMaterial(P,{...options,weights:[1,0,0],normalStrength:0});
// A production page owns one immutable policy and one canonical allowance.
// Separate processes model page isolation; a fresh compositor is not a reset.
function inPolicy(selected,body){
 const script=`import assert from 'node:assert/strict';
 const selected=${JSON.stringify(selected)};
 const freeze=v=>{if(v&&typeof v==='object'){Object.values(v).forEach(freeze);Object.freeze(v);}return v;};
 if(selected)Object.defineProperty(globalThis,'__trustedSceneAppearancePolicy',{
  value:freeze(selected),writable:false,configurable:false});
 const {createSceneSpaceRenderer}=await import(${JSON.stringify(new URL('../../video_workbench/motion_renderer/scene_space_runtime.mjs',import.meta.url).href)});
 const {sampleMaterial}=await import(${JSON.stringify(new URL('../../video_workbench/motion_renderer/scene_space_surface.mjs',import.meta.url).href)});
 ${canvas.toString()}
 ${binding.toString()}
 ${execute.toString()}
 const P=${JSON.stringify(P)},options=${JSON.stringify(options)};
 const fullExpected=${JSON.stringify(fullExpected)},coarseExpected=${JSON.stringify(coarseExpected)};
 ${body}`;
 const result=spawnSync(process.execPath,['--input-type=module','-e',script],{encoding:'utf8',timeout:10000});
 assert.ifError(result.error);
 assert.equal(result.status,0,result.stderr||result.stdout);
}

test('legacy binding executes original material parameters and retains its observation shape',()=>{
 let actual;
 const renderer=execute(null,tools=>{actual=tools.surface.sampleMaterial(P,options);});
 assert.deepEqual(actual,sampleMaterial(P,options));
 assert.equal('appearance_observation' in renderer.observation,false);
 assert.equal(renderer.observation.surface_helper_calls.sampleMaterial,1);
 renderer.dispose();
});

test('full policy keeps authors material values while accounting for actual work',()=>{
 inPolicy(policy('full'),`
 let actual;
 const renderer=execute(selected,tools=>{actual=tools.surface.sampleMaterial(P,options);});
 assert.deepEqual(actual,fullExpected);
 const observed=renderer.observation.appearance_observation;
 assert.equal(observed.actual_parameters.material_quality,'full');
 assert.equal(observed.material_samples,1);
 assert.equal(observed.material_noise_evaluations,actual.work.noise_evaluations);
 assert.equal(observed.material_lattice_evaluations,actual.work.lattice_evaluations);
 assert.equal(observed.material_derivative_evaluations,actual.work.derivative_evaluations);
 assert.equal(observed.scope,'per_page_cache_initialization');
 renderer.dispose();
 `);
});

test('coarse policy overrides bands and normals without mutating or discarding other author options',()=>{
 inPolicy(policy('coarse'),`
 const original=structuredClone(options);let actual;
 const renderer=execute(selected,tools=>{actual=tools.surface.sampleMaterial(P,options);});
 assert.deepEqual(actual,coarseExpected);assert.deepEqual(options,original);
 assert.notEqual(actual.value,fullExpected.value);
 assert.deepEqual(actual.normal_delta,[0,0,0]);
 assert.equal(actual.work.noise_evaluations,1);assert.equal(actual.work.derivative_evaluations,0);
 const observed=renderer.observation.appearance_observation;
 assert.equal(observed.requested_parameters.material_quality,'full');
 assert.equal(observed.actual_parameters.material_quality,'coarse');
 assert.equal(observed.material_noise_evaluations,1);
 assert.equal(observed.material_lattice_evaluations,8);
 assert.equal(observed.material_gradient_components,0);
 renderer.dispose();
 `);
});

test('work accumulates across cached objects and stops when actual noise exceeds the frozen limit',()=>{
 inPolicy(policy('coarse',1),`
 assert.throws(()=>execute(selected,tools=>{
  tools.surface.sampleMaterial(P,options);
  tools.surface.sampleMaterial([1,2,3],options);
 }),/scene_space_material_noise_budget_exceeded/);
 `);
 inPolicy(policy('full',2),`assert.throws(()=>execute(selected,tools=>tools.surface.sampleMaterial(P,options)),
  /scene_space_material_noise_budget_exceeded/);`);
 inPolicy(policy('full',3),`assert.doesNotThrow(()=>execute(selected,tools=>tools.surface.sampleMaterial(P,options)).dispose());`);
});

test('camera seeks reuse cached pigment and cannot reset or multiply the work counter',()=>{
 inPolicy(policy('coarse',1),`
 let paints=0;
 const renderer=execute(selected,tools=>{paints++;tools.surface.sampleMaterial(P,options);});
 const before=renderer.observation.appearance_observation;
 renderer.renderFrame(1);renderer.renderFrame(0);renderer.renderFrame(0);
 assert.equal(paints,1);
 assert.deepEqual(renderer.observation.appearance_observation,before);
 assert.equal(renderer.observation.objects_painted,1);
 assert.equal(renderer.observation.frames_composited,3);
 renderer.dispose();
 assert.throws(()=>execute(selected,tools=>tools.surface.sampleMaterial(P,options)),
  /scene_space_material_noise_budget_exceeded/);
 `);
});

test('an invalid frozen quality or limit is rejected before the painter runs',()=>{
 let paints=0;
 for(const value of [{...policy('full'),actual_parameters:{material_quality:'metadata-only'}},
  {...policy('full'),material_noise_limit:-1},{...policy('full'),material_noise_limit:1.1},
  {...policy('full'),schema:'untrusted'}]){
  assert.throws(()=>execute(value,()=>{paints++;}),/scene_space_appearance_policy_invalid/);
 }
 assert.equal(paints,0);
});

test('a policy binding requires the same host-installed authority before any painter runs',()=>{
 let paints=0;
 assert.throws(()=>execute(policy('full'),()=>{paints++;}),
  /scene_space_appearance_policy_host_binding_missing_or_changed/);
 assert.equal(paints,0);
 inPolicy(policy('coarse'),`
 let paints=0;
 for(const changed of [null,{...selected,material_noise_limit:1000}]){
  assert.throws(()=>execute(changed,()=>{paints++;}),
   /scene_space_appearance_policy_host_binding_missing_or_changed/);
 }
 assert.equal(paints,0);
 `);
});
