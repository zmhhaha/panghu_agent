import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import test from 'node:test';

function isolated(code){
 const helper=new URL('../../video_workbench/motion_renderer/scene_space_surface.mjs',import.meta.url).href,
  budget=new URL('../../video_workbench/motion_renderer/scene_space_surface_budget.mjs',import.meta.url).href;
 const setup=`import assert from 'node:assert/strict';
const helper=${JSON.stringify(helper)},budget=${JSON.stringify(budget)};
const P=[.37,.91,1.43],options={seed:631907};
function install(quality,limit){
 const policy={schema:'video.scene-appearance-policy.v1',requested_parameters:{material_quality:'full'},
  actual_parameters:{material_quality:quality},estimated_material_samples:2,material_noise_limit:limit};
 function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;}
 Object.defineProperty(globalThis,'__trustedSceneAppearancePolicy',{value:freeze(policy),writable:false,configurable:false});
 return policy;
}
`;
 const result=spawnSync(process.execPath,['--input-type=module','-e',setup+code],{encoding:'utf8',timeout:15000});
 assert.equal(result.status,0,result.stderr||result.stdout||String(result.error));
}

test('raw and fragment helper imports share one full material limit and reject before further noise work',()=>{
 isolated(`
 const policy=install('full',6),direct=await import(helper),fragment=await import(helper+'#alternate');
 assert.equal(direct.appearanceObservation,fragment.appearanceObservation);
 assert.equal(direct.appearancePolicy(),policy);
 direct.sampleMaterial(P,options);fragment.sampleMaterial(P,options);
 assert.equal(direct.appearanceObservation().material_noise_evaluations,6);
 assert.equal(fragment.appearanceObservation().material_samples,2);
 const imul=Math.imul;let hashes=0;
 try{
  Math.imul=(...args)=>{hashes++;return imul(...args);};
  assert.throws(()=>fragment.sampleMaterial(P,options),/scene_space_material_noise_budget_exceeded/);
 }finally{Math.imul=imul;}
 assert.equal(hashes,0,'the rejected call must not hash even one noise corner');
 assert.equal(direct.appearanceObservation().material_noise_evaluations,6);
 `);
});

test('coarse policy is enforced by the original export and keeps all canonical observations immutable',()=>{
 isolated(`
 const policy=install('coarse',2),direct=await import(helper),fragment=await import(helper+'#raw-fragment');
 const authored={...options,weights:[.5,.3,.2],normalStrength:.9},before=JSON.stringify(authored);
 const a=direct.sampleMaterial(P,authored),b=fragment.sampleMaterial(P,authored);
 assert.deepEqual(a,b);assert.equal(a.work.noise_evaluations,1);
 assert.equal(a.work.derivative_evaluations,0);assert.deepEqual(a.normal_delta,[0,0,0]);
 assert.equal(JSON.stringify(authored),before);
 const observation=direct.appearanceObservation();
 assert.equal(observation.material_noise_evaluations,2);assert.equal(observation.material_lattice_evaluations,16);
 assert.equal(observation.material_derivative_evaluations,0);
 assert.ok(Object.isFrozen(observation)&&Object.isFrozen(observation.actual_parameters));
 assert.throws(()=>{observation.material_noise_evaluations=0;},TypeError);
 assert.throws(()=>{policy.material_noise_limit=100;},TypeError);
 assert.throws(()=>{globalThis.__trustedSceneAppearancePolicy=null;},TypeError);
 assert.throws(()=>Object.defineProperty(globalThis,'__trustedSceneAppearancePolicy',{value:null}),TypeError);
 assert.throws(()=>fragment.sampleMaterial(P,authored),/scene_space_material_noise_budget_exceeded/);
 `);
});

test('public charge and unrelated budget fragments cannot refund or raise the canonical helper allowance',()=>{
 isolated(`
 install('full',3);const authority=await import(budget),direct=await import(helper);
 const work={evaluated_bands:3,noise_evaluations:3,lattice_evaluations:24,derivative_evaluations:0,
  gradient_components:0,scope:'actual_helper_operations_not_wall_time_or_total_render_cost'};
 authority.chargeMaterialWork(work);
 assert.equal(direct.appearanceObservation().material_noise_evaluations,3);
 const before=direct.appearanceObservation(),original=Number.isSafeInteger;
 try{
  Number.isSafeInteger=()=>true;
  for(const invalid of [NaN,Infinity,-1,0,.5,'3',true])
   assert.throws(()=>authority.chargeMaterialWork({...work,noise_evaluations:invalid}),/scene_space_material_work_invalid/);
 }finally{Number.isSafeInteger=original;}
 let accessorReads=0;
 const accessor={...work,get noise_evaluations(){accessorReads++;return accessorReads<5?3:-3;}};
 assert.throws(()=>authority.chargeMaterialWork(accessor),/scene_space_material_work_invalid/);
 assert.equal(accessorReads,0,'charge validation must not invoke authored accessors');
 assert.throws(()=>authority.chargeMaterialWork(Object.create(work)),/scene_space_material_work_invalid/);
 assert.deepEqual(direct.appearanceObservation(),before);
 const unrelated=await import(budget+'#unrelated-authority');
 unrelated.chargeMaterialWork(work);
 assert.deepEqual(direct.appearanceObservation(),before);
 const fragment=await import(helper+'#fresh-helper');
 assert.throws(()=>fragment.sampleMaterial(P,options),/scene_space_material_noise_budget_exceeded/);
 assert.equal(authority.reset,undefined);assert.equal(authority.refund,undefined);assert.equal(authority.raise,undefined);
 `);
});

test('without a host policy the previous numerical response and resource observation shape stay exact',()=>{
 isolated(`
 const direct=await import(helper),fragment=await import(helper+'#no-policy');
 assert.equal(direct.appearancePolicy(),null);assert.equal(direct.appearanceObservation(),null);
 const result=direct.sampleMaterial(P,options);
 assert.equal(result.variation,.05744966479584121);assert.equal(result.value,.5086174497193762);
 assert.equal(result.roughness,.6086174497193761);
 assert.deepEqual(result.normal_delta,[-.009619373655168715,-.01460063825104723,.0899564499458573]);
 assert.deepEqual(fragment.sampleMaterial(P,options),result);
 assert.deepEqual(Object.keys(direct.surfaceResourceUsage()).sort(),[
  'fields_created','fields_reserved','max_fields','max_total_field_bytes','numeric_bytes_reserved'].sort());
 assert.equal(fragment.appearanceObservation(),null);
 `);
});

test('mutable or accessor-backed policy data is rejected instead of becoming a resettable authority',()=>{
 isolated(`
 Object.defineProperty(globalThis,'__trustedSceneAppearancePolicy',{value:{schema:'video.scene-appearance-policy.v1'},
  writable:true,configurable:true});
 await assert.rejects(import(helper),/scene_space_appearance_policy_invalid/);
 `);
 isolated(`
 const policy=Object.freeze({schema:'video.scene-appearance-policy.v1',get actual_parameters(){return {material_quality:'full'};}});
 Object.defineProperty(globalThis,'__trustedSceneAppearancePolicy',{value:policy,writable:false,configurable:false});
 await assert.rejects(import(helper),/scene_space_appearance_policy_invalid/);
 `);
});
