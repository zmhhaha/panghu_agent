// Release-owned compositor. Data carries geometry; already admitted scene code
// supplies painters. No recipe_ref, URL, script or external resource is resolved.
import * as surfaceTools from './scene_space_surface.mjs';
export const SCENE_SPACE_RUNTIME_REVISION='scene-space-compositor-2';
const check=(ok,code='scene_space_runtime_invalid')=>{if(!ok)throw Error(code);};
const copy=value=>JSON.parse(JSON.stringify(value));
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const mul=(a,b)=>[a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],
 a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]];
const point=(m,p)=>[m[0]*p[0]+m[2]*p[1]+m[4],m[1]*p[0]+m[3]*p[1]+m[5]];
function matrix(t){const a=t.rotation*Math.PI/180,c=Math.cos(a),s=Math.sin(a);
 return[c*t.scale_x,s*t.scale_x,-s*t.scale_y,c*t.scale_y,t.x,t.y];}
function boundsOf(points){const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
 return{x:Math.min(...xs),y:Math.min(...ys),width:Math.max(...xs)-Math.min(...xs),height:Math.max(...ys)-Math.min(...ys)};}
function keys(rows,frame,names){
 if(frame<=rows[0].frame||rows.length===1)return Object.fromEntries(names.map(n=>[n,rows[0][n]]));
 if(frame>=rows.at(-1).frame)return Object.fromEntries(names.map(n=>[n,rows.at(-1)[n]]));
 const i=rows.findIndex(row=>row.frame>frame),a=rows[i-1],b=rows[i];let t=(frame-a.frame)/(b.frame-a.frame);
 if(b.easing==='smoothstep')t=t*t*(3-2*t);
 return Object.fromEntries(names.map(n=>[n,a[n]+(b[n]-a[n])*t]));
}
function layerMatrix(world,layer,camera,viewport){
 const b=world.space.bounds,p=layer.parallax,s=1+(camera.zoom-1)*p;
 const ox=b.x+b.width/2,oy=b.y+b.height/2,cx=ox+(camera.x-ox)*p,cy=oy+(camera.y-oy)*p;
 check(s>0,'scene_space_projection_singular');return[s,0,0,s,viewport.width/2-cx*s,viewport.height/2-cy*s];
}
export function evaluateSceneFrame(frozen,frame){
 check(frozen?.schema==='video.scene-space-frozen-plan.v1');const plan=frozen.plan;
 check(Number.isInteger(frame)&&frame>=0&&frame<=plan.duration_frames,'scene_space_frame_invalid');
 const shot=frame===plan.duration_frames?plan.shots.at(-1):plan.shots.find(s=>s.start_frame<=frame&&frame<s.end_frame);
 const world=plan.worlds.find(w=>w.id===shot.world_id),camera=keys(shot.camera.keyframes,frame,['x','y','zoom']);
 const values=new Map(shot.local_animation.map(t=>[t.target_id+'\0'+t.property,keys(t.keyframes,frame,['value']).value]));
 const layers=[...world.layers].sort((a,b)=>a.order-b.order).map(l=>({...copy(l),screen_matrix:layerMatrix(world,l,camera,plan.viewport)}));
 const byLayer=new Map(layers.map(l=>[l.id,l])),byId=new Map(world.objects.map(o=>[o.id,o])),geometry=new Map();
 const value=(id,name,fallback)=>values.get(id+'\0'+name)??fallback;
 function resolve(id){if(geometry.has(id))return geometry.get(id);const o=byId.get(id),t={...o.transform};
  for(const name of ['x','y','rotation'])t[name]+=value(id,name,0);
  const scale=value(id,'scale',1);t.scale_x*=scale;t.scale_y*=scale;
  let m=matrix(t),opacity=value(id,'opacity',1);
  if(o.parent_id!==null){const parent=resolve(o.parent_id);m=mul(parent.matrix,m);opacity*=parent.opacity;}
  const g={matrix:m,opacity};geometry.set(id,g);return g;}
 const objects=[...world.objects].sort((a,b)=>byLayer.get(a.layer_id).order-byLayer.get(b.layer_id).order||a.order-b.order||(a.id<b.id?-1:a.id>b.id?1:0));
 const result=objects.map(o=>{const g=resolve(o.id),layer=byLayer.get(o.layer_id),m=mul(layer.screen_matrix,g.matrix),b=o.bounds;
  const corners=[[b.x,b.y],[b.x+b.width,b.y],[b.x+b.width,b.y+b.height],[b.x,b.y+b.height]].map(p=>point(m,p));
  const box=boundsOf(corners),alpha=g.opacity*layer.opacity;
  return{id:o.id,kind:o.kind,layer_id:o.layer_id,parent_id:o.parent_id,depth:copy(o.depth),order:o.order,
   world_matrix:g.matrix,screen_matrix:m,screen_bounds:box,screen_corners:corners,
   screen_anchors:Object.fromEntries(Object.entries(o.anchors).map(([name,p])=>[name,point(m,p)])),
   opacity:alpha,potentially_visible:alpha>0&&box.x<=plan.viewport.width&&box.x+box.width>=0&&box.y<=plan.viewport.height&&box.y+box.height>=0,
   visibility_scope:'bounds_only',pixels_observed:false};});
 const interval=frozen.intervals.find(i=>i.start_frame<=frame&&frame<i.end_frame)??frozen.intervals.at(-1);
 return{schema:'video.scene-space-frame.v1',frame,time_seconds:frame/24,fps:24,plan_id:plan.id,plan_revision:plan.revision,
  plan_sha256:frozen.plan_sha256,world_id:world.id,world_revision:world.revision,shot_id:shot.id,interval_id:interval.id,
  viewport:copy(plan.viewport),camera,layers,objects:result,geometry_scope:'declared_scene_geometry_only',
  visual_observed:false,motion_observed:false,audio_observed:false};
}
function pathRegion(ctx,region){ctx.beginPath();
 if(region.kind==='rect'){const b=region.bounds;ctx.rect(b.x,b.y,b.width,b.height);}
 else{region.points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();}}
const defaultCanvas=()=>document.createElement('canvas');
export function createSceneSpaceRenderer({canvas,binding,paintObject,paintCoverage,createCanvas=defaultCanvas}){
 check(binding?.schema==='video.scene-space-render-binding.v1'&&binding.backend==='canvas');
 check(typeof paintObject==='function'&&typeof createCanvas==='function','scene_space_painter_missing');
 check(paintCoverage===undefined||typeof paintCoverage==='function','scene_space_coverage_painter_invalid');
 const plan=binding.frozen.plan,viewport=plan.viewport;
 check(plan.fps===24&&canvas&&typeof canvas.getContext==='function');
 canvas.width=viewport.width;canvas.height=viewport.height;
 const output=canvas.getContext('2d');check(output,'scene_space_context_missing');
 const caches=new Map(),worlds=new Map(plan.worlds.map(w=>[w.id,w]));let disposed=false;
 const metrics={schema:'video.scene-space-compositor-observation.v1',plan_sha256:binding.frozen.plan_sha256,
  objects_painted:0,frames_composited:0,cache_hits:0,cache_pixels:0,peak_work_pixels:binding.scratch.pixels+viewport.width*viewport.height,
  coverage_callbacks:0,coverage_objects:0,coverage_peak_pixels:0,coverage_contexts:0,surface_helper_calls:{},
  recipes_executed:0,scope:'host_compositor_execution_not_visual_acceptance',runtime_revision:SCENE_SPACE_RUNTIME_REVISION};
 const helperApi=Object.freeze(Object.fromEntries(Object.entries(surfaceTools).map(([name,value])=>[name,
  typeof value==='function'?(...args)=>{const count=(metrics.surface_helper_calls[name]??0)+1;
   check(Number.isSafeInteger(count),'scene_space_surface_call_count_exceeded');metrics.surface_helper_calls[name]=count;
   return value(...args);}:value]))),tools=Object.freeze({surface:helperApi});
 let coverageCanvas,coverageContext;
 for(const row of binding.object_caches){const world=worlds.get(row.world_id),obj=world.objects.find(o=>o.id===row.object_id);
  check(obj&&Number.isInteger(row.width)&&Number.isInteger(row.height)&&row.width>0&&row.height>0);
  const surface=createCanvas();surface.width=row.width;surface.height=row.height;
  const ctx=surface.getContext('2d');check(ctx,'scene_space_context_missing');
  ctx.save();ctx.setTransform(row.density,0,0,row.density,-obj.bounds.x*row.density,-obj.bounds.y*row.density);
  ctx.beginPath();ctx.rect(obj.bounds.x,obj.bounds.y,obj.bounds.width,obj.bounds.height);ctx.clip();
  const result=paintObject(ctx,copy(obj),copy(world),tools);check(!result||typeof result.then!=='function','scene_space_async_painter_not_admitted');ctx.restore();
  check(surface.width===row.width&&surface.height===row.height,'scene_space_painter_resized_cache');
  if(paintCoverage){
   check(row.pixels<=binding.coverage.max_mask_pixels,'scene_space_coverage_budget_changed');
   if(!coverageCanvas){coverageCanvas=createCanvas();coverageContext=coverageCanvas.getContext('2d');
    check(coverageContext,'scene_space_context_missing');metrics.coverage_contexts=1;}
   coverageCanvas.width=row.width;coverageCanvas.height=row.height;
   const mask=coverageContext;mask.save();mask.setTransform(row.density,0,0,row.density,-obj.bounds.x*row.density,-obj.bounds.y*row.density);
   mask.beginPath();mask.rect(obj.bounds.x,obj.bounds.y,obj.bounds.width,obj.bounds.height);mask.clip();
   let coverage;
   try{coverage=paintCoverage(mask,copy(obj),copy(world),tools);
    check(!coverage||typeof coverage.then!=='function','scene_space_async_coverage_not_admitted');}
   finally{mask.restore();}
   check(coverageCanvas.width===row.width&&coverageCanvas.height===row.height,'scene_space_coverage_resized_cache');
   metrics.coverage_callbacks++;metrics.coverage_peak_pixels=Math.max(metrics.coverage_peak_pixels,row.pixels);
   if(coverage!==undefined&&coverage!==null&&coverage!==false){
    check(typeof coverage==='object'&&!Array.isArray(coverage)&&Object.keys(coverage).length===1
     &&Object.hasOwn(coverage,'substrate')&&Array.isArray(coverage.substrate)&&coverage.substrate.length===3
     &&coverage.substrate.every(v=>Number.isInteger(v)&&v>=0&&v<=255),'scene_space_coverage_options_invalid');
    ctx.save();ctx.setTransform(1,0,0,1,0,0);ctx.globalAlpha=1;ctx.filter='none';
    ctx.globalCompositeOperation='destination-over';ctx.fillStyle=`rgb(${coverage.substrate.join(',')})`;ctx.fillRect(0,0,row.width,row.height);
    ctx.globalCompositeOperation='destination-in';ctx.drawImage(coverageCanvas,0,0);ctx.restore();metrics.coverage_objects++;
   }
   coverageCanvas.width=0;coverageCanvas.height=0;
  }
  caches.set(row.world_id+'\0'+row.object_id,surface);metrics.objects_painted++;metrics.cache_pixels+=row.pixels;
 }
 if(coverageCanvas){coverageCanvas.width=0;coverageCanvas.height=0;}
 metrics.peak_work_pixels+=metrics.coverage_peak_pixels;
 check(metrics.cache_pixels===binding.total_cached_pixels,'scene_space_cache_changed');
 const scratch=createCanvas();scratch.width=binding.scratch.width;scratch.height=binding.scratch.height;
 const work=scratch.getContext('2d');check(work,'scene_space_context_missing');
 function renderFrame(frame){check(!disposed,'scene_space_renderer_disposed');
  check(frame>=binding.frame_range.start_frame&&frame<=binding.frame_range.end_frame,'scene_space_frame_outside_binding');
  const state=evaluateSceneFrame(binding.frozen,frame),world=worlds.get(state.world_id),objects=new Map(world.objects.map(o=>[o.id,o]));
  const pad=binding.scratch.padding;
  output.setTransform(1,0,0,1,0,0);output.globalAlpha=1;output.globalCompositeOperation='source-over';output.filter='none';
  output.clearRect(0,0,canvas.width,canvas.height);
  if(binding.background){const [r,g,b,a]=binding.background;output.fillStyle=`rgba(${r},${g},${b},${a/255})`;output.fillRect(0,0,canvas.width,canvas.height);}
  for(const layer of state.layers){if(layer.opacity===0)continue;
   work.setTransform(1,0,0,1,0,0);work.globalAlpha=1;work.globalCompositeOperation='source-over';work.filter='none';
   work.clearRect(0,0,scratch.width,scratch.height);work.save();
   if(layer.mask_region_id!==null){const region=world.layout.regions.find(r=>r.id===layer.mask_region_id);
    const m=layer.screen_matrix;work.setTransform(m[0],m[1],m[2],m[3],m[4]+pad,m[5]+pad);pathRegion(work,region);work.clip();}
   for(const item of state.objects){if(item.layer_id!==layer.id||item.opacity===0)continue;
    const obj=objects.get(item.id),surface=caches.get(state.world_id+'\0'+item.id),m=item.screen_matrix;
    work.setTransform(m[0],m[1],m[2],m[3],m[4]+pad,m[5]+pad);work.globalAlpha=item.opacity/layer.opacity;
    work.drawImage(surface,0,0,surface.width,surface.height,obj.bounds.x,obj.bounds.y,obj.bounds.width,obj.bounds.height);metrics.cache_hits++;
   }
   work.restore();output.globalAlpha=layer.opacity;output.filter=layer.blur?`blur(${layer.blur}px)`:'none';output.drawImage(scratch,-pad,-pad);
  }
  output.globalAlpha=1;output.filter='none';metrics.frames_composited++;
  return state;
 }
 function dispose(){if(disposed)return;disposed=true;for(const surface of caches.values()){surface.width=0;surface.height=0;}
  caches.clear();scratch.width=0;scratch.height=0;metrics.cache_pixels=0;}
 return Object.freeze({renderFrame,seek:seconds=>{check(finite(seconds));return renderFrame(binding.frame_range.start_frame+Math.round(seconds*24));},
  get observation(){return copy({...metrics,surface_field_usage:surfaceTools.surfaceResourceUsage()});},dispose});
}

// Same upper semantics for adapters. These helpers grant no engine, native
// context, geometry reconstruction, mask support or execution admission.
function inverse(m){const d=m[0]*m[3]-m[1]*m[2];check(Math.abs(d)>1e-12,'scene_space_adapter_singular');
 return[m[3]/d,-m[1]/d,-m[2]/d,m[0]/d,(m[2]*m[5]-m[3]*m[4])/d,(m[1]*m[4]-m[0]*m[5])/d];}
function adapterItem(item,state,objects,nativeParent){
 const declared=item.parent_id===null?null:objects.get(item.parent_id);
 if(declared&&nativeParent===declared){const parent=state.objects.find(o=>o.id===item.parent_id);
  return{matrix:mul(inverse(parent.screen_matrix),item.screen_matrix),opacity:parent.opacity===0?0:item.opacity/parent.opacity};}
 check(![...objects.values()].includes(nativeParent),'scene_space_adapter_parent_mismatch');
 return{matrix:item.screen_matrix,opacity:item.opacity};
}
function layerAdapters(state,applyLayer){for(const layer of state.layers){
 if(layer.mask_region_id!==null||layer.blur!==0){check(typeof applyLayer==='function','scene_space_layer_adapter_required');applyLayer(layer,state);}}}
export function applySvgFrame(state,{objects,applyLayer}){
 check(objects instanceof Map,'scene_space_svg_map_required');
 layerAdapters(state,applyLayer);
 for(const item of state.objects){const node=objects.get(item.id);check(node&&typeof node.setAttribute==='function','scene_space_svg_object_missing');
  const local=adapterItem(item,state,objects,node.parentNode);
  node.setAttribute('transform','matrix('+local.matrix.join(' ')+')');node.setAttribute('opacity',String(local.opacity));}
 return{frame:state.frame,scope:'affine_object_groups_masks_owned_by_backend'};
}
export function applyThreeFrame(state,{objects,applyOpacity,applyLayer}){
 check(objects instanceof Map,'scene_space_three_map_required');
 layerAdapters(state,applyLayer);
 for(const item of state.objects){const node=objects.get(item.id);check(node?.matrix&&typeof node.matrix.set==='function','scene_space_three_object_missing');
  const local=adapterItem(item,state,objects,node.parent),m=local.matrix;
  check(local.opacity===0||local.opacity===1||typeof applyOpacity==='function','scene_space_three_material_adapter_required');
  if(typeof applyOpacity==='function')applyOpacity(node,local.opacity,item);
  node.matrixAutoUpdate=false;node.matrix.set(m[0],m[2],0,m[4],m[1],m[3],0,m[5],0,0,1,0,0,0,0,1);
  node.matrixWorldNeedsUpdate=true;node.visible=item.opacity>0;
 }
 return{frame:state.frame,scope:'fixed_view_affine_object_groups_not_reconstructed_3d'};
}
