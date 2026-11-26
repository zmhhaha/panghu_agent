// A canonical dependency, deliberately separate from the drawing helper.
// helper.mjs#one and helper.mjs#two both import this exact URL and therefore
// consume one realm's allowance. No source-accessible reset, free or raise API.
export const MAX_TOTAL_FIELD_BYTES=16*1024*1024;
export const MAX_FIELDS=64;
let fieldsReserved=0,fieldsCreated=0,numericBytesReserved=0;

export function surfaceResourceUsage(){
 return Object.freeze({fields_created:fieldsCreated,fields_reserved:fieldsReserved,
  numeric_bytes_reserved:numericBytesReserved,max_fields:MAX_FIELDS,
  max_total_field_bytes:MAX_TOTAL_FIELD_BYTES});
}

export function reserveFieldBytes(bytes){
 if(typeof bytes!=='number'||!(bytes>0&&bytes<=MAX_TOTAL_FIELD_BYTES)||bytes%1!==0)
  throw Error('scene_surface_field_reservation_invalid');
 if(fieldsReserved>=MAX_FIELDS||numericBytesReserved+bytes>MAX_TOTAL_FIELD_BYTES)
  throw Error('scene_surface_retained_budget');
 // Charge before allocation. A later allocation/computation failure keeps the
 // reservation; GC timing is never treated as proof of released storage.
 fieldsReserved++;numericBytesReserved+=bytes;
 let completed=false;
 return()=>{if(completed)throw Error('scene_surface_field_commit_repeated');
  completed=true;fieldsCreated++;};
}

// The trusted host installs this immutable data before any authored script or
// helper module can run. Each helper fragment imports this canonical module,
// so direct helper imports cannot obtain an unmetered copy of the material API.
function frozenPolicy(){
 const value=globalThis.__trustedSceneAppearancePolicy;
 if(value===undefined||value===null)return null;
 const descriptor=Object.getOwnPropertyDescriptor(globalThis,'__trustedSceneAppearancePolicy');
 if(!descriptor||descriptor.value!==value||descriptor.writable!==false||descriptor.configurable!==false)
  throw Error('scene_space_appearance_policy_invalid');
 let nodes=0;
 const inspect=(item,depth)=>{
  if(++nodes>2048||depth>16)throw Error('scene_space_appearance_policy_invalid');
  if(item!==null&&typeof item==='object'){
   if(!Object.isFrozen(item))throw Error('scene_space_appearance_policy_invalid');
   for(const key of Object.keys(item)){
    const property=Object.getOwnPropertyDescriptor(item,key);
    if(!property||!Object.hasOwn(property,'value'))throw Error('scene_space_appearance_policy_invalid');
    inspect(property.value,depth+1);
   }
  }else if(!['string','number','boolean'].includes(typeof item)&&item!==null)
   throw Error('scene_space_appearance_policy_invalid');
 };
 inspect(value,0);
 const limit=value.material_noise_limit,estimate=value.estimated_material_samples;
 if(value.schema!=='video.scene-appearance-policy.v1'
  ||!['full','coarse'].includes(value.actual_parameters?.material_quality)
  ||value.requested_parameters?.material_quality!=='full'
  ||typeof limit!=='number'||!(limit>=1&&limit<=64*1024*1024)||limit%1!==0
  ||typeof estimate!=='number'||!(estimate>=0&&estimate<=64*1024*1024)||estimate%1!==0)
  throw Error('scene_space_appearance_policy_invalid');
 return value;
}
const materialPolicy=frozenPolicy();
let materialSamples=0,noiseEvaluations=0,latticeEvaluations=0,derivativeEvaluations=0,gradientComponents=0;

export function appearancePolicy(){return materialPolicy;}

export function appearanceObservation(){
 if(!materialPolicy)return null;
 return Object.freeze({schema:'video.scene-appearance-observation.v1',scope:'per_page_cache_initialization',
  policy:materialPolicy,requested_parameters:materialPolicy.requested_parameters,
  actual_parameters:materialPolicy.actual_parameters,material_samples:materialSamples,
  material_noise_evaluations:noiseEvaluations,material_lattice_evaluations:latticeEvaluations,
  material_derivative_evaluations:derivativeEvaluations,material_gradient_components:gradientComponents,
  material_noise_limit:materialPolicy.material_noise_limit,
  accounting:'charged_before_material_evaluation_no_refund'});
}

export function applyMaterialPolicy(options){
 return materialPolicy?.actual_parameters.material_quality==='coarse'
  ?{...options,weights:[1,0,0],normalStrength:0}:options;
}

export function chargeMaterialWork(work){
 if(!materialPolicy)return;
 const safe=n=>typeof n==='number'&&n>=0&&n<=9007199254740991&&n%1===0;
 if(work===null||typeof work!=='object'||Array.isArray(work))
  throw Error('scene_space_material_work_invalid');
 // A caller can import this module too. Capture own data exactly once so
 // accessors, prototypes or changing reads cannot turn a charge into a refund.
 const read=key=>{
  const property=Object.getOwnPropertyDescriptor(work,key);
  if(!property||!Object.hasOwn(property,'value'))throw Error('scene_space_material_work_invalid');
  return property.value;
 },bands=read('evaluated_bands'),noise=read('noise_evaluations'),lattice=read('lattice_evaluations'),
  derivatives=read('derivative_evaluations'),components=read('gradient_components'),scope=read('scope');
 if(![bands,noise,lattice,derivatives,components].every(safe)
  ||noise<1||noise>3||bands!==noise||lattice!==noise*8
  ||!(derivatives===0||derivatives===noise)||components!==derivatives*3
  ||scope!=='actual_helper_operations_not_wall_time_or_total_render_cost')
  throw Error('scene_space_material_work_invalid');
 if(materialPolicy.actual_parameters.material_quality==='coarse'
  &&(noise!==1||derivatives!==0))
  throw Error('scene_space_material_work_invalid');
 if(noiseEvaluations+noise>materialPolicy.material_noise_limit)
  throw Error('scene_space_material_noise_budget_exceeded');
 const next=[materialSamples+1,noiseEvaluations+noise,latticeEvaluations+lattice,
  derivativeEvaluations+derivatives,gradientComponents+components];
 if(!next.every(safe))throw Error('scene_space_material_work_exceeded');
 // Charge once before the exact work begins. Public calls can only consume
 // additional allowance; there is no token, settlement, refund or reset API.
 [materialSamples,noiseEvaluations,latticeEvaluations,derivativeEvaluations,gradientComponents]=next;
}
