// Authored object-local 2.5D form cues. This is not observed or calibrated depth.
// No Canvas is created here; painters retain the host's transform and clip.
import {reserveFieldBytes,MAX_FIELDS,MAX_TOTAL_FIELD_BYTES,
 surfaceResourceUsage} from './scene_space_surface_budget.mjs';
export {MAX_FIELDS,MAX_TOTAL_FIELD_BYTES,surfaceResourceUsage};
export const SURFACE_FIELD_REVISION='scene-local-surface-1';
export const APPEARANCE_REVISION='scene-appearance-cues-1';
export const APPEARANCE_LIMITS=Object.freeze({coordinate:1000000,material_scales:3,
 material_min_frequency:.000001,material_max_frequency:1024,seed_max:4294967295,
 atmosphere_density:64,height_falloff:64});
export const MAX_FIELD_BYTES=1024*1024;
export const MAX_WORK_BYTES=2*1024*1024;
export const MAX_WASH_ROWS=8192;
export const MAX_WASH_STOPS=524288;
export const SURFACE_LIMITS=Object.freeze({outline_points:256,features:32,feature_segments:256,
 grid_side:128,grid_nodes:16384,build_work:4000000,field_numeric_bytes:MAX_FIELD_BYTES,
 work_numeric_bytes:MAX_WORK_BYTES,realm_fields:MAX_FIELDS,realm_numeric_bytes:MAX_TOTAL_FIELD_BYTES,
 wash_rows:MAX_WASH_ROWS,wash_gradient_stops:MAX_WASH_STOPS});
const EPS=1e-8,COORD=1000000,states=new WeakMap();
const check=(ok,code='scene_surface_invalid')=>{if(!ok)throw Error(code);};
const number=(n,low=-COORD,high=COORD)=>{check(typeof n==='number'&&Number.isFinite(n)&&n>=low&&n<=high);return n;};
const integer=(n,low,high)=>{check(Number.isInteger(n)&&n>=low&&n<=high);return n;};
const fields=(v,required,optional=[])=>{check(v!==null&&typeof v==='object'&&!Array.isArray(v));
 const keys=Object.keys(v);check(required.every(k=>keys.includes(k))&&keys.every(k=>required.includes(k)||optional.includes(k)));};
const point=(p,size)=>{check(Array.isArray(p)&&p.length===size);return p.map(n=>number(n));};
const freezePoint=p=>Object.freeze(p);
const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
const option=(obj,key,fallback)=>obj[key]===undefined?fallback:obj[key];
const smooth=t=>{t=clamp(t,0,1);return t*t*(3-2*t);};
function unit2(x,y,fallback=[0,1]){const length=Math.hypot(x,y);return length>1e-12?[x/length,y/length]:fallback;}
function unit3(x,y,z){const length=Math.hypot(x,y,z);check(length>1e-12,'scene_surface_light_invalid');return[x/length,y/length,z/length];}
function onSegment(x,y,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy);
 return Math.abs((x-a[0])*dy-(y-a[1])*dx)<=EPS*Math.max(1,length)
  &&x>=Math.min(a[0],b[0])-EPS&&x<=Math.max(a[0],b[0])+EPS
  &&y>=Math.min(a[1],b[1])-EPS&&y<=Math.max(a[1],b[1])+EPS;}
function inside(x,y,polygon){let result=false;
 for(let i=0,j=polygon.length-1;i<polygon.length;j=i++){
  const a=polygon[j],b=polygon[i];if(onSegment(x,y,a,b))return true;
  if((a[1]>y)!==(b[1]>y)&&x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0])result=!result;
 }return result;}
function crossing(a,b,c,d){const cross=(p,q,r)=>(q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0]);
 const aa=cross(a,b,c),bb=cross(a,b,d),cc=cross(c,d,a),dd=cross(c,d,b);
 return (aa*bb<0&&cc*dd<0)||onSegment(c[0],c[1],a,b)||onSegment(d[0],d[1],a,b)
  ||onSegment(a[0],a[1],c,d)||onSegment(b[0],b[1],c,d);}
function distanceToSegment(x,y,a,b){const dx=b[0]-a[0],dy=b[1]-a[1],den=dx*dx+dy*dy;
 const t=den>0?clamp(((x-a[0])*dx+(y-a[1])*dy)/den,0,1):0;
 return{distance:Math.hypot(x-a[0]-dx*t,y-a[1]-dy*t),t,dx,dy};}
function edgeDistance(x,y,outline){let result=Infinity;
 for(let i=0;i<outline.length;i++)result=Math.min(result,distanceToSegment(x,y,outline[i],outline[(i+1)%outline.length]).distance);
 return result;}
function parseOutline(value,bounds){check(Array.isArray(value)&&value.length>=3&&value.length<=257);
 const result=value.map(p=>point(p,2));
 if(result.length>3&&result[0][0]===result.at(-1)[0]&&result[0][1]===result.at(-1)[1])result.pop();
 check(result.length<=SURFACE_LIMITS.outline_points);
 const seen=new Set();let area=0;
 for(let i=0;i<result.length;i++){
  const p=result[i],q=result[(i+1)%result.length],key=p.join(',');check(!seen.has(key));seen.add(key);
  check(p[0]>=bounds.x&&p[0]<=bounds.x+bounds.width&&p[1]>=bounds.y&&p[1]<=bounds.y+bounds.height);
  area+=p[0]*q[1]-q[0]*p[1];
 }
 check(Math.abs(area)>1e-8*Math.max(1,bounds.width*bounds.height),'scene_surface_outline_degenerate');
 for(let i=0;i<result.length;i++)for(let j=i+1;j<result.length;j++){
  if(j===i+1||(i===0&&j===result.length-1))continue;
  check(!crossing(result[i],result[(i+1)%result.length],result[j],result[(j+1)%result.length]),'scene_surface_outline_self_intersection');
 }
 return Object.freeze(result.map(freezePoint));
}
function parseFeatures(rows,sign,bounds,outline,ids){check(Array.isArray(rows)&&rows.length<=SURFACE_LIMITS.features);
 return rows.map(row=>{fields(row,['id','points'],['width']);
  check(typeof row.id==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/.test(row.id)
   &&!['__proto__','prototype','constructor'].includes(row.id)&&!ids.has(row.id));ids.add(row.id);
  check(Array.isArray(row.points)&&row.points.length>=2&&row.points.length<=257);
  const points=row.points.map(p=>{const q=point(p,3);check(q[2]>=0&&inside(q[0],q[1],outline));return q;});
  for(let i=1;i<points.length;i++)check(points[i][0]!==points[i-1][0]||points[i][1]!==points[i-1][1]);
  const width=number(option(row,'width',Math.min(bounds.width,bounds.height)*.18),.001,COORD);
  return{id:row.id,points,width,sign};
 });
}
function featureAt(x,y,features){let ridge=0,valley=0,nearest=null,best=Infinity;
 for(const feature of features){let distance=Infinity,nearestSegment=null;
  for(let j=1;j<feature.points.length;j++){
   const a=feature.points[j-1],b=feature.points[j],hit=distanceToSegment(x,y,a,b);
   if(hit.distance<distance){distance=hit.distance;nearestSegment={...hit,a,b};}
  }
  const hit=nearestSegment,ratio=distance/feature.width,influence=Math.exp(-.5*ratio*ratio);
  const amplitude=(hit.a[2]+(hit.b[2]-hit.a[2])*hit.t)*influence;
  if(feature.sign>0)ridge=Math.max(ridge,amplitude);else valley=Math.max(valley,amplitude);
  if(ratio<best){best=ratio;let dx=hit.dx,dy=hit.dy;
   const dz=hit.b[2]-hit.a[2];if(dz>0||(dz===0&&(dy<0||(dy===0&&dx<0)))){dx=-dx;dy=-dy;}
   nearest=unit2(dx,dy);
  }
 }
 return{height:ridge-valley,tangent:nearest};
}

/** Create an immutable handle over bounded private numeric buffers.
 * outline is an object-local simple polygon; z values describe authored form,
 * not world-space depth. Ridge heights and valley depths are nonnegative.
 * The grid is a sampling aid and is not a wireframe to draw into the artwork.
 */
export function createSurfaceField(options){
 fields(options,['bounds','outline'],['ridges','valleys','grid','baseHeight','boundaryFade']);
 fields(options.bounds,['x','y','width','height']);
 const bounds={x:number(options.bounds.x),y:number(options.bounds.y),
  width:number(options.bounds.width,.001,COORD),height:number(options.bounds.height,.001,COORD)};
 number(bounds.x+bounds.width);number(bounds.y+bounds.height);
 const outline=parseOutline(options.outline,bounds),ids=new Set();
 const features=[...parseFeatures(option(options,'ridges',[]),1,bounds,outline,ids),
  ...parseFeatures(option(options,'valleys',[]),-1,bounds,outline,ids)];
 check(features.length<=SURFACE_LIMITS.features,'scene_surface_feature_budget');
 const segments=features.reduce((sum,line)=>sum+line.points.length-1,0);
 check(segments<=SURFACE_LIMITS.feature_segments,'scene_surface_feature_budget');
 const grid=option(options,'grid',{columns:48,rows:48});fields(grid,['columns','rows']);
 const columns=integer(grid.columns,2,SURFACE_LIMITS.grid_side),rows=integer(grid.rows,2,SURFACE_LIMITS.grid_side),nodes=columns*rows;
 check(nodes<=SURFACE_LIMITS.grid_nodes&&nodes*2*(outline.length+segments)+outline.length**2<=SURFACE_LIMITS.build_work,
  'scene_surface_work_budget');
 const base=number(option(options,'baseHeight',0)),fade=number(option(options,'boundaryFade',Math.min(bounds.width,bounds.height)*.09),.001,COORD);
 const requestedBytes=nodes*6*8;
 check(requestedBytes+nodes*8<=MAX_FIELD_BYTES,'scene_surface_memory_budget');
 const commitField=reserveFieldBytes(requestedBytes);
 const heights=new Float64Array(nodes),normals=new Float64Array(nodes*3),flows=new Float64Array(nodes*2);
 const dx=bounds.width/(columns-1),dy=bounds.height/(rows-1);
 let minHeight=Infinity,maxHeight=-Infinity;
 for(let row=0;row<rows;row++)for(let col=0;col<columns;col++){
  const x=bounds.x+col*dx,y=bounds.y+row*dy,i=row*columns+col;
  if(inside(x,y,outline)){
   const value=featureAt(x,y,features);
   heights[i]=clamp((base+value.height)*smooth(edgeDistance(x,y,outline)/fade),-COORD,COORD);
  }
  minHeight=Math.min(minHeight,heights[i]);maxHeight=Math.max(maxHeight,heights[i]);
 }
 for(let row=0;row<rows;row++)for(let col=0;col<columns;col++){
  const x=bounds.x+col*dx,y=bounds.y+row*dy,i=row*columns+col;
  const left=Math.max(0,col-1),right=Math.min(columns-1,col+1),top=Math.max(0,row-1),bottom=Math.min(rows-1,row+1);
  const gx=(heights[row*columns+right]-heights[row*columns+left])/((right-left)*dx);
  const gy=(heights[bottom*columns+col]-heights[top*columns+col])/((bottom-top)*dy);
  const normal=unit3(-gx,-gy,1);normals.set(normal,i*3);
  const tangent=featureAt(x,y,features).tangent??[0,1],down=unit2(-gx,-gy,[0,0]);
  // Keep authored ridge direction while allowing cross-slope turns. No clock,
  // random noise or fixed vertical-line pattern determines this direction.
  flows.set(unit2(tangent[0]*.7+down[0]*.3,tangent[1]*.7+down[1]*.3),i*2);
 }
 const numericBytes=heights.byteLength+normals.byteLength+flows.byteLength;
 check(numericBytes===requestedBytes,'scene_surface_memory_budget');
 const field=Object.freeze({schema:'video.scene-local-surface-field.v1',revision:SURFACE_FIELD_REVISION,
  bounds:Object.freeze(bounds),outline,grid:Object.freeze({columns,rows}),node_count:nodes,
  numeric_bytes:numericBytes,min_height:minHeight,max_height:maxHeight,
  scope:'authored_local_2_5d_form_hypothesis_not_observed_or_calibrated_depth'});
 states.set(field,{heights,normals,flows,columns,rows,dx,dy});commitField();return field;
}
function stateOf(field){const state=states.get(field);check(state,'scene_surface_field_invalid');return state;}
function location(field,state,x,y){const b=field.bounds;
 const xx=clamp((x-b.x)/state.dx,0,state.columns-1),yy=clamp((y-b.y)/state.dy,0,state.rows-1);
 const col=Math.min(state.columns-2,Math.floor(xx)),row=Math.min(state.rows-2,Math.floor(yy));
 return{index:row*state.columns+col,tx:xx-col,ty:yy-row};}
function bilinear(values,index,columns,tx,ty,stride=1,channel=0){
 const a=values[index*stride+channel],b=values[(index+1)*stride+channel];
 const c=values[(index+columns)*stride+channel],d=values[(index+columns+1)*stride+channel];
 return(a+(b-a)*tx)*(1-ty)+(c+(d-c)*tx)*ty;
}
function lightOf(light){check(Array.isArray(light)&&light.length===3,'scene_surface_light_invalid');return unit3(...light.map(n=>number(n)));}
function shadeOf(nx,ny,nz,light){return 1-clamp(nx*light[0]+ny*light[1]+nz*light[2],0,1);}
export function sampleSurface(field,x,y,options={}){
 const state=stateOf(field);number(x);number(y);fields(options,[],['light']);
 const light=lightOf(option(options,'light',[-.45,-.35,1])),b=field.bounds;
 const covered=x>=b.x&&x<=b.x+b.width&&y>=b.y&&y<=b.y+b.height&&inside(x,y,field.outline);
 if(!covered)return{coverage:0,height:0,normal:[0,0,1],flow:[0,1],shade:0};
 const {index,tx,ty}=location(field,state,x,y),interpolate=(data,stride=1,channel=0)=>bilinear(data,index,state.columns,tx,ty,stride,channel);
 const normal=unit3(interpolate(state.normals,3,0),interpolate(state.normals,3,1),interpolate(state.normals,3,2));
 const flow=unit2(interpolate(state.flows,2,0),interpolate(state.flows,2,1));
 return{coverage:1,height:interpolate(state.heights),normal,flow,shade:shadeOf(...normal,light)};
}
function rgb(value){check(Array.isArray(value)&&value.length===3);return value.map(n=>integer(n,0,255));}
function colorAt(paper,ink,tone){return'rgb('+paper.map((v,i)=>Math.round(v+(ink[i]-v)*clamp(tone,0,1))).join(',')+')';}
function washRows(ctx,bounds,columns){
 const matrix=typeof ctx.getTransform==='function'?ctx.getTransform():{a:1,b:0,c:0,d:1,e:0,f:0};
 check(matrix&&['a','b','c','d','e','f'].every(key=>typeof matrix[key]==='number'&&Number.isFinite(matrix[key])
  &&Math.abs(matrix[key])<=1e12),'scene_surface_transform_invalid');
 const density=Math.hypot(matrix.c,matrix.d);check(density>1e-12,'scene_surface_transform_invalid');
 let rows,start,step;
 if(Math.abs(matrix.b)<=1e-12&&Math.abs(matrix.c)<=1e-12){
  // In the normal object-cache context, align both strip edges to physical
  // pixel rows. Fractional local origins otherwise create AA seams even when
  // adjacent mathematical rectangles touch exactly.
  const y0=bounds.y*matrix.d+matrix.f,y1=(bounds.y+bounds.height)*matrix.d+matrix.f;
  check(Math.abs(y0)<=1e12&&Math.abs(y1)<=1e12,'scene_surface_transform_invalid');
  const first=Math.floor(Math.min(y0,y1)),last=Math.ceil(Math.max(y0,y1));
  rows=last-first;start=(first-matrix.f)/matrix.d;step=1/matrix.d;
 }else{
  // A rotated/sheared caller retains its own transform and clip. One local
  // strip projects to at most one pixel along the transformed local Y axis.
  rows=Math.ceil(bounds.height*density);start=bounds.y;step=bounds.height/rows;
 }
 check(Number.isSafeInteger(rows)&&rows>=1&&rows<=MAX_WASH_ROWS&&rows*columns<=MAX_WASH_STOPS,
  'scene_surface_wash_work_budget');
 return{rows,start,step,density};
}
/** Opaque substrate/wash values clipped to the authored outline, not a mesh.
 * Existing transform, clip and alpha are respected and restored. For a pale
 * solid surface choose inkRange rather than fading its whole object layer.
 * Each physical scanline uses one full-width gradient with bilinearly sampled
 * field columns, so no cell edge introduces a separate gradient or overlap.
 * Numeric-buffer limits exclude the browser's native Canvas/JS allocator.
 */
export function paintSurfaceWash(ctx,field,options={}){
 const state=stateOf(field);fields(options,[],['paper','ink','light','inkRange']);
 const paper=rgb(option(options,'paper',[239,234,221])),ink=rgb(option(options,'ink',[33,37,32])),light=lightOf(option(options,'light',[-.45,-.35,1]));
 const range=option(options,'inkRange',[.025,.38]);check(Array.isArray(range)&&range.length===2);
 const low=number(range[0],0,1),high=number(range[1],low,1);
 check(ctx&&['save','restore','beginPath','moveTo','lineTo','closePath','clip','fillRect','createLinearGradient'].every(name=>typeof ctx[name]==='function'),
  'scene_surface_context_invalid');
 const bands=washRows(ctx,field.bounds,state.columns);
 const tones=new Float64Array(field.node_count);
 for(let i=0;i<tones.length;i++)tones[i]=low+(high-low)*shadeOf(state.normals[i*3],state.normals[i*3+1],state.normals[i*3+2],light);
 ctx.save();
 try{
  ctx.beginPath();field.outline.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();ctx.clip();
  const b=field.bounds;
  for(let row=0;row<bands.rows;row++){
   const y=bands.start+row*bands.step,sampleY=y+bands.step/2;
   const gy=clamp((sampleY-b.y)/state.dy,0,state.rows-1),top=Math.min(state.rows-2,Math.floor(gy)),t=gy-top;
   const gradient=ctx.createLinearGradient(b.x,sampleY,b.x+b.width,sampleY);
   for(let col=0;col<state.columns;col++){
    const a=tones[top*state.columns+col],z=tones[(top+1)*state.columns+col];
    gradient.addColorStop(col/(state.columns-1),colorAt(paper,ink,a+(z-a)*t));
   }
   ctx.fillStyle=gradient;ctx.fillRect(b.x,y,b.width,bands.step);
  }
 }finally{ctx.restore();}
 return{schema:'video.scene-local-surface-wash.v1',cells:(state.columns-1)*(state.rows-1),
  cells_scope:'source_field_cells_not_draw_calls',strips:bands.rows,gradient_stops:bands.rows*state.columns,
  raster_method:'continuous_horizontal_scanlines',numeric_work_bytes:field.numeric_bytes+tones.byteLength,
  scope:'authored_2_5d_form_wash_not_observed_lighting_or_visual_acceptance'};
}

// These pure appearance cues do not allocate a Canvas or consume/modify a
// coverage mask. Positions, normals and lights must be supplied in the same
// declared coordinate system; no screen-Y depth or camera transform is inferred.
function appearanceUnit(value,code){
 check(Array.isArray(value)&&value.length===3,code);
 const vector=value.map(v=>number(v)),length=Math.hypot(...vector);
 check(length>1e-12,code);return vector.map(v=>v/length||0);
}
function range(value,minimum,maximum){
 check(Array.isArray(value)&&value.length===2);
 const low=number(value[0],minimum,maximum),high=number(value[1],low,maximum);
 return[low,high];
}

/** Surface-to-light direction and surface normal use the same world/object XYZ
 * basis. "diffuse" is an ambient + Lambert cue, not PBR or shadow tracing.
 * "ink" maps the dot product through an authored smooth lightRange, then applies
 * ambient. ink_density is pigment guidance, never opacity or solid coverage.
 */
export function evaluateLighting(normal,options){
 fields(options,['light'],['mode','ambient','lightRange','inkRange']);
 const N=appearanceUnit(normal,'scene_surface_normal_invalid'),L=appearanceUnit(options.light,'scene_surface_light_invalid'),
  mode=option(options,'mode','diffuse'),ambient=number(option(options,'ambient',0),0,1),
  lightRange=range(option(options,'lightRange',[0,1]),-1,1),inkRange=range(option(options,'inkRange',[0,1]),0,1);
 check(mode==='diffuse'||mode==='ink','scene_surface_lighting_mode_invalid');
 check(lightRange[1]>lightRange[0],'scene_surface_light_range_invalid');
 const dot=clamp(N[0]*L[0]+N[1]*L[1]+N[2]*L[2],-1,1),diffuse=Math.max(0,dot),
  response=mode==='ink'?smooth((dot-lightRange[0])/(lightRange[1]-lightRange[0])):diffuse,
  lit=ambient+(1-ambient)*response,shade=1-lit;
 return Object.freeze({schema:'video.scene-lighting-cue.v1',revision:APPEARANCE_REVISION,mode,
  normal:Object.freeze(N),light:Object.freeze(L),dot,diffuse,lit,shade,
  ink_density:inkRange[0]+(inkRange[1]-inkRange[0])*shade,
  scope:'authored_directional_response_not_shadows_pbr_or_coverage'});
}

function latticeValue(x,y,z,seed){
 let hash=(seed^0x9e3779b9)>>>0;
 hash=Math.imul(hash^(x|0),0x85ebca6b);hash=Math.imul(hash^(y|0),0xc2b2ae35);
 hash=Math.imul(hash^(z|0),0x27d4eb2d);hash^=hash>>>16;
 hash=Math.imul(hash,0x85ebca6b);hash^=hash>>>13;
 return(hash>>>0)/4294967295;
}
function materialNoise(position,frequency,seed){
 const scaled=position.map(v=>v*frequency),cell=scaled.map(Math.floor),fraction=scaled.map((v,i)=>v-cell[i]),
  blend=fraction.map(smooth),derivative=fraction.map(t=>6*t*(1-t)),values=[];
 for(let z=0;z<2;z++)for(let y=0;y<2;y++)for(let x=0;x<2;x++)
  values.push(latticeValue(cell[0]+x,cell[1]+y,cell[2]+z,seed));
 const lerp=(a,b,t)=>a+(b-a)*t,
  x00=lerp(values[0],values[1],blend[0]),x10=lerp(values[2],values[3],blend[0]),
  x01=lerp(values[4],values[5],blend[0]),x11=lerp(values[6],values[7],blend[0]),
  y0=lerp(x00,x10,blend[1]),y1=lerp(x01,x11,blend[1]),
  dx=lerp(lerp(values[1]-values[0],values[3]-values[2],blend[1]),
   lerp(values[5]-values[4],values[7]-values[6],blend[1]),blend[2])*derivative[0]*frequency,
  dy=lerp(x10-x00,x11-x01,blend[2])*derivative[1]*frequency,
  dz=(y1-y0)*derivative[2]*frequency;
 return{value:lerp(y0,y1,blend[2])*2-1,gradient:[dx*2,dy*2,dz*2]};
}

/** Three bounded spatial frequencies anchored to supplied coordinates. For a
 * moving object, supply its local coordinates to keep the material attached.
 * space labels that caller-owned basis; this function never transforms it.
 * value/roughness/normal_delta are variation cues, not a finished BRDF or alpha.
 */
export function sampleMaterial(position,options){
 fields(options,['seed'],['space','scales','weights','base','amplitude','roughness','roughnessVariation','normalStrength']);
 const P=point(position,3),seed=integer(options.seed,0,APPEARANCE_LIMITS.seed_max),space=option(options,'space','object'),
  scales=option(options,'scales',[.25,1,4]),weights=option(options,'weights',[.5,.3,.2]);
 check(space==='object'||space==='world','scene_surface_material_space_invalid');
 check(Array.isArray(scales)&&scales.length===3&&Array.isArray(weights)&&weights.length===3);
 const frequencies=scales.map(v=>number(v,APPEARANCE_LIMITS.material_min_frequency,APPEARANCE_LIMITS.material_max_frequency)),
  W=weights.map(v=>number(v,0,1)),total=W.reduce((sum,v)=>sum+v,0);
 check(frequencies[0]<frequencies[1]&&frequencies[1]<frequencies[2]&&total>0,'scene_surface_material_scales_invalid');
 const base=number(option(options,'base',.5),0,1),amplitude=number(option(options,'amplitude',.15),0,1),
  roughness=number(option(options,'roughness',.6),0,1),roughnessVariation=number(option(options,'roughnessVariation',.15),0,1),
  normalStrength=number(option(options,'normalStrength',.1),0,1);
 let variation=0;const gradient=[0,0,0];
 for(let i=0;i<3;i++){
  const sample=materialNoise(P,frequencies[i],(seed+Math.imul(i,0x9e3779b9))>>>0),weight=W[i]/total;
  variation+=sample.value*weight;for(let axis=0;axis<3;axis++)gradient[axis]+=sample.gradient[axis]*weight;
 }
 variation=clamp(variation,-1,1);
 return Object.freeze({schema:'video.scene-material-cue.v1',revision:APPEARANCE_REVISION,space,seed,
  variation,value:clamp(base+amplitude*variation,0,1),roughness:clamp(roughness+roughnessVariation*variation,0,1),
  normal_delta:Object.freeze(gradient.map(v=>clamp(v,-1,1)*normalStrength||0)),
  scope:'anchored_three_scale_variation_not_brdf_geometry_or_coverage'});
}

/** Distance/height color-mixing cue, not volume scattering or visibility.
 * distance, near/far and height/baseHeight share the caller's world units.
 * far is the end of the distance ramp; maxAmount bounds the color mixture.
 * transmittance is 1-amount for this mixture and must not replace object alpha.
 */
export function sampleAtmosphere(distance,options={}){
 fields(options,[],['near','far','density','strength','maxAmount','height','baseHeight','heightFalloff','color']);
 number(distance,0,COORD);
 const near=number(option(options,'near',0),0,COORD),far=number(option(options,'far',100),.000001,COORD),
  density=number(option(options,'density',3),0,APPEARANCE_LIMITS.atmosphere_density),
  strength=number(option(options,'strength',1),0,1),maxAmount=number(option(options,'maxAmount',.95),0,1),
  height=number(option(options,'height',0)),baseHeight=number(option(options,'baseHeight',0)),
  heightFalloff=number(option(options,'heightFalloff',0),0,APPEARANCE_LIMITS.height_falloff),
  color=rgb(option(options,'color',[238,233,220]));
 check(far>near,'scene_surface_atmosphere_range_invalid');
 const distanceFraction=smooth((distance-near)/(far-near)),heightWeight=Math.exp(-heightFalloff*Math.max(0,height-baseHeight)),
  amount=maxAmount*strength*(-Math.expm1(-density*distanceFraction*heightWeight));
 return Object.freeze({schema:'video.scene-atmosphere-cue.v1',revision:APPEARANCE_REVISION,
  amount,transmittance:1-amount,color:Object.freeze(color),distance_fraction:distanceFraction,height_weight:heightWeight,
  scope:'distance_height_color_mix_not_volume_scattering_or_coverage'});
}
