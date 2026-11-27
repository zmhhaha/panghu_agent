// Optional appearance adapter for an already admitted Three mesh. This module
// grants no engine admission and does not implement PBR, shadows or pixel noise.
import {evaluateLighting,sampleMaterial,sampleAtmosphere,appearancePolicy,appearanceObservation}
 from './scene_space_surface.mjs';

export const SPATIAL_THREE_ADAPTER_REVISION='spatial-three-appearance-1';
export const SPATIAL_THREE_LIMITS=Object.freeze({maxVertices:16384,maxIndices:98304,
 maxGeometryBytes:4*1024*1024,maxVertexUpdates:16*1024*1024});
export const SPATIAL_THREE_REALM_LIMITS=Object.freeze({maxInstances:64,maxVertices:262144,
 maxVertexUpdates:16*1024*1024});
// A fragment/query import delegates to this exact canonical module, preventing
// an alternate URL from creating an unobserved adapter or a fresh allowance.
const canonicalURL=new URL(import.meta.url);canonicalURL.hash='';canonicalURL.search='';
const canonical=canonicalURL.href===import.meta.url?null:await import(canonicalURL.href);
const realm={create_attempts:0,instances_reserved:0,instances_created:0,instances_disposed:0,
 vertices_reserved:0,vertex_updates_reserved:0,updates:0,material_samples:0,
 material_noise_evaluations:0,material_lattice_evaluations:0,material_derivative_evaluations:0,
 lighting_evaluations:0,atmosphere_evaluations:0};
const check=(ok,code='spatial_three_appearance_invalid')=>{if(!ok)throw Error(code);};
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
function fields(value,required,optional=[]){check(plain(value));const names=Object.keys(value);
 check(required.every(key=>Object.hasOwn(value,key))&&names.every(key=>required.includes(key)||optional.includes(key)));}
function bounded(value,minimum,maximum){check(finite(value)&&value>=minimum&&value<=maximum);return value;}
function triple(value,minimum=-1e6,maximum=1e6){check(Array.isArray(value)&&value.length===3);
 return value.map(item=>bounded(item,minimum,maximum));}
function immutable(value){if(value&&typeof value==='object'){for(const item of Object.values(value))immutable(item);Object.freeze(value);}return value;}
const copy=value=>JSON.parse(JSON.stringify(value));
function lightOptions(value){
 fields(value,['direction'],['response','ambient','lightRange','inkRange']);
 const response=value.response??'physical';check(['physical','artistic'].includes(response));
 const result={light:triple(value.direction),mode:response==='physical'?'diffuse':'ink'};
 for(const key of ['ambient','lightRange','inkRange'])if(value[key]!==undefined)result[key]=copy(value[key]);
 evaluateLighting([0,0,1],result);return immutable(result);
}
function attribute(value,count,itemSize){check(value?.isBufferAttribute===true
 &&value.isInterleavedBufferAttribute!==true&&value.count===count&&value.itemSize===itemSize
 &&ArrayBuffer.isView(value.array)&&value.array.length===count*itemSize
 &&value.normalized===false,'spatial_three_geometry_unsupported');}
function opacityState(material){return {opacity:material.opacity,transparent:material.transparent,
 depthTest:material.depthTest,depthWrite:material.depthWrite};}
function coverageState(material){
 check(finite(material.opacity)&&material.opacity>=0&&material.opacity<=1
  &&['transparent','depthTest','depthWrite'].every(key=>typeof material[key]==='boolean'),'spatial_three_material_unsupported');
 // Three classifies a material with transparent=false as opaque even when
 // opacity is below one. Only explicit transparency can relax opaque depth.
 check(material.transparent||(material.depthTest&&material.depthWrite),'spatial_three_opaque_depth_required');
 return opacityState(material);
}

export function threeAppearanceObservation(){
 if(canonical)return canonical.threeAppearanceObservation();
 return immutable({schema:'video.spatial-three-realm-observation.v1',revision:SPATIAL_THREE_ADAPTER_REVISION,
  ...realm,limits:{...SPATIAL_THREE_REALM_LIMITS},material_policy:appearanceObservation(),
  accounting:'monotonic_canonical_module_reservations_no_dispose_refund_or_source_reset',
  scope:'one_module_realm_adapter_operations_not_whole_film_cpu_or_visual_acceptance'});
}

/** Cache seeded object-space material once, then relight actual transformed
 * normals and mix actual camera-distance/height atmosphere at each update.
 * Material colors are linear RGB; optional baseColor and fog color are sRGB
 * byte triples converted once. Fog never modifies pigment/object opacity.
 * Only owned clones are changed; the original shared geometry/material survive
 * dispose. Per-instance update limits complement the host's canonical material
 * policy and outer renderer budget; they are not a whole-film CPU limit.
 */
export function createThreeAppearanceAdapter(options){
 if(canonical)return canonical.createThreeAppearanceAdapter(options);
 realm.create_attempts++;
 fields(options,['THREE','mesh','seed','light'],['material','atmosphere','baseColor','limits']);
 const {THREE,mesh}=options,hard=SPATIAL_THREE_LIMITS;
 check(THREE&&['Matrix3','Vector3','Color','BufferAttribute'].every(name=>typeof THREE[name]==='function'),
  'spatial_three_namespace_missing');
 check(mesh?.isMesh===true&&mesh.isSkinnedMesh!==true&&mesh.isInstancedMesh!==true
  &&typeof mesh.updateWorldMatrix==='function','spatial_three_mesh_unsupported');
 const originalGeometry=mesh.geometry,originalMaterial=mesh.material;
 check(originalGeometry?.isBufferGeometry===true&&typeof originalGeometry.clone==='function'
  &&plain(originalGeometry.attributes)&&plain(originalGeometry.morphAttributes)
  &&Object.keys(originalGeometry.morphAttributes).length===0,'spatial_three_geometry_unsupported');
 check(originalMaterial?.isMeshBasicMaterial===true&&typeof originalMaterial.clone==='function'
  &&originalMaterial.map===null&&originalMaterial.alphaMap===null
  &&finite(originalMaterial.opacity)&&originalMaterial.opacity>=0&&originalMaterial.opacity<=1
  &&['transparent','depthTest','depthWrite','vertexColors'].every(key=>typeof originalMaterial[key]==='boolean'),
  'spatial_three_material_unsupported');
 coverageState(originalMaterial);
 check(Number.isInteger(options.seed)&&options.seed>=0&&options.seed<=4294967295,'spatial_three_seed_invalid');
 const limits={...hard};
 if(options.limits!==undefined){fields(options.limits,[],Object.keys(hard));
  for(const [key,value] of Object.entries(options.limits)){
   check(Number.isSafeInteger(value)&&value>=1&&value<=hard[key],'spatial_three_limit_invalid');limits[key]=value;
  }}
 const position=originalGeometry.attributes.position,normal=originalGeometry.attributes.normal,count=position?.count;
 check(Number.isSafeInteger(count)&&count>=3&&count<=limits.maxVertices,'spatial_three_vertex_budget');
 attribute(position,count,3);attribute(normal,count,3);
 let geometryBytes=0;
 for(const attr of Object.values(originalGeometry.attributes)){
  check(attr?.isBufferAttribute===true&&attr.isInterleavedBufferAttribute!==true
   &&attr.count===count&&Number.isInteger(attr.itemSize)&&attr.itemSize>=1&&attr.itemSize<=4
   &&ArrayBuffer.isView(attr.array)&&attr.array.length===count*attr.itemSize,'spatial_three_geometry_unsupported');
  geometryBytes+=attr.array.byteLength;
 }
 const index=originalGeometry.index;
 if(index!==null){check(index?.isBufferAttribute===true&&index.itemSize===1
   &&Number.isSafeInteger(index.count)&&index.count%3===0&&index.count<=limits.maxIndices
   &&ArrayBuffer.isView(index.array)&&index.array.length===index.count,'spatial_three_geometry_unsupported');
  for(const item of index.array)check(Number.isInteger(item)&&item>=0&&item<count,'spatial_three_index_invalid');
  geometryBytes+=index.array.byteLength;
 }else check(count%3===0,'spatial_three_geometry_unsupported');
 const sourceColors=originalGeometry.attributes.color,originalVertexColors=originalMaterial.vertexColors;
 if(originalVertexColors){attribute(sourceColors,count,3);
  for(const value of sourceColors.array)bounded(value,0,1);}
 const colorBytes=count*3*4,materialCacheBytes=count*4*4,sourceColorBytes=originalVertexColors?count*3*4:0,
  accountedPeakNumericBytes=geometryBytes*2+colorBytes+materialCacheBytes+sourceColorBytes;
 // Include the original input, all owned clone arrays (including a replaced
 // preexisting color attribute, without assuming GC), new color and caches.
 // This bounds named numeric buffers, not Three/browser native or JS heap.
 check(accountedPeakNumericBytes<=limits.maxGeometryBytes,'spatial_three_geometry_byte_budget');
 for(let i=0;i<count;i++){
  for(const value of [position.getX(i),position.getY(i),position.getZ(i),normal.getX(i),normal.getY(i),normal.getZ(i)])bounded(value,-1e6,1e6);
  check(Math.hypot(normal.getX(i),normal.getY(i),normal.getZ(i))>1e-12,'spatial_three_normal_invalid');
 }
 let light=lightOptions(options.light);
 const material=options.material===undefined?{}:copy(options.material);
 fields(material,[],['scales','weights','base','amplitude','roughness','roughnessVariation','normalStrength']);
 const sampling=immutable({base:.85,amplitude:.12,normalStrength:0,...material,seed:options.seed,space:'object'});
 let atmosphere=null;
 if(options.atmosphere!==undefined&&options.atmosphere!==null){
  atmosphere=copy(options.atmosphere);fields(atmosphere,[],['near','far','density','strength','maxAmount','baseHeight','heightFalloff','color']);
  sampleAtmosphere(0,atmosphere);immutable(atmosphere);
 }
 const base=originalMaterial.color.clone();
 if(options.baseColor!==undefined){const rgb=triple(options.baseColor,0,255);
  base.setRGB(...rgb.map(value=>value/255),THREE.SRGBColorSpace);}
 check([base.r,base.g,base.b].every(value=>finite(value)&&value>=0&&value<=1),'spatial_three_base_color_invalid');
 // The canonical module captures an immutable policy before authored imports.
 check(appearancePolicy()!==null,'spatial_three_appearance_host_policy_missing');
 check(realm.instances_reserved<SPATIAL_THREE_REALM_LIMITS.maxInstances
  &&realm.vertices_reserved+count<=SPATIAL_THREE_REALM_LIMITS.maxVertices,'spatial_three_realm_geometry_budget');
 realm.instances_reserved++;realm.vertices_reserved+=count;
 const sourceColorValues=originalVertexColors?Float32Array.from(sourceColors.array):null;
 const metrics={schema:'video.spatial-three-appearance-observation.v1',revision:SPATIAL_THREE_ADAPTER_REVISION,
  vertices:count,source_geometry_bytes:geometryBytes,cloned_geometry_bytes:geometryBytes,owned_color_bytes:colorBytes,
  cached_material_bytes:materialCacheBytes,cached_source_color_bytes:sourceColorBytes,
  accounted_peak_numeric_bytes:accountedPeakNumericBytes,
  numeric_memory_scope:'source_buffers_plus_owned_clone_color_and_material_caches_not_total_heap',
  limits:immutable({...limits}),updates:0,reserved_vertex_updates:0,
  material_samples:0,material_noise_evaluations:0,material_lattice_evaluations:0,
  material_derivative_evaluations:0,lighting_evaluations:0,atmosphere_evaluations:0,
  validation_lighting_evaluations:1,validation_atmosphere_evaluations:atmosphere?1:0,
  material_domain:'object',seed:options.seed,lighting_normal_space:'world_inverse_transpose',
  atmosphere_domain:'world_camera_distance_and_world_y',color_space:'linear_rgb_srgb_input_colors',
  coverage:'adapter_never_changes_opacity_or_depth_explicit_transparency_validated_each_update',roughness_applied:false,
  budget_scope:'canonical_host_material_policy_and_per_adapter_vertex_updates_not_whole_film_cpu',
  scope:'actual_adapter_operations_not_native_draw_or_visual_acceptance'};
 let geometry=null,ownedMaterial=null,disposed=false;
 const values=new Float32Array(count),deltas=new Float32Array(count*3);
 try{
  // Validation and material charges precede changing the author's mesh.
  for(let i=0;i<count;i++){
   const result=sampleMaterial([position.getX(i),position.getY(i),position.getZ(i)],sampling);
   values[i]=result.value;deltas.set(result.normal_delta,i*3);metrics.material_samples++;
   realm.material_samples++;
   metrics.material_noise_evaluations+=result.work.noise_evaluations;
   realm.material_noise_evaluations+=result.work.noise_evaluations;
   metrics.material_lattice_evaluations+=result.work.lattice_evaluations;
   realm.material_lattice_evaluations+=result.work.lattice_evaluations;
   metrics.material_derivative_evaluations+=result.work.derivative_evaluations;
   realm.material_derivative_evaluations+=result.work.derivative_evaluations;
  }
  geometry=originalGeometry.clone();ownedMaterial=originalMaterial.clone();
  geometry.setAttribute('color',new THREE.BufferAttribute(new Float32Array(count*3),3));
  ownedMaterial.color.setRGB(1,1,1);ownedMaterial.vertexColors=true;ownedMaterial.fog=false;ownedMaterial.needsUpdate=true;
 }catch(error){geometry?.dispose();ownedMaterial?.dispose();throw error;}
 const positions=geometry.attributes.position,normals=geometry.attributes.normal,colors=geometry.attributes.color,
  normalMatrix=new THREE.Matrix3(),P=new THREE.Vector3(),N=new THREE.Vector3(),cameraPosition=new THREE.Vector3(),
  hazeColor=new THREE.Color(),initialPositionVersion=positions.version,initialNormalVersion=normals.version;
 mesh.geometry=geometry;mesh.material=ownedMaterial;
 realm.instances_created++;
 const snapshot=()=>immutable({...copy(metrics),disposed,material_policy:appearanceObservation(),
  actual_coverage:opacityState(ownedMaterial),light:copy(light)});
 return Object.freeze({
  update({camera,light:newLight}={}){
   check(!disposed,'spatial_three_adapter_disposed');
   check(mesh.geometry===geometry&&mesh.material===ownedMaterial
    &&geometry.attributes.position===positions&&geometry.attributes.normal===normals
    &&geometry.attributes.color===colors&&positions.version===initialPositionVersion
    &&normals.version===initialNormalVersion,'spatial_three_adapter_binding_changed');
   coverageState(ownedMaterial);
   check(camera?.isCamera===true&&typeof camera.updateWorldMatrix==='function','spatial_three_camera_invalid');
   if(newLight!==undefined){light=lightOptions(newLight);metrics.validation_lighting_evaluations++;}
   mesh.updateWorldMatrix(true,false);camera.updateWorldMatrix(true,false);
   check(mesh.matrixWorld?.elements?.length===16&&camera.matrixWorld?.elements?.length===16
    &&[...mesh.matrixWorld.elements,...camera.matrixWorld.elements].every(finite)
    &&finite(mesh.matrixWorld.determinant())&&mesh.matrixWorld.determinant()!==0,'spatial_three_transform_invalid');
   normalMatrix.getNormalMatrix(mesh.matrixWorld);cameraPosition.setFromMatrixPosition(camera.matrixWorld);
   check(metrics.reserved_vertex_updates+count<=limits.maxVertexUpdates
    &&realm.vertex_updates_reserved+count<=SPATIAL_THREE_REALM_LIMITS.maxVertexUpdates,'spatial_three_vertex_update_budget');
   metrics.reserved_vertex_updates+=count;realm.vertex_updates_reserved+=count;
   for(let i=0;i<count;i++){
    N.set(normals.getX(i)+deltas[i*3],normals.getY(i)+deltas[i*3+1],normals.getZ(i)+deltas[i*3+2])
     .normalize().applyMatrix3(normalMatrix).normalize();
    metrics.lighting_evaluations++;realm.lighting_evaluations++;
    const lit=evaluateLighting([N.x,N.y,N.z],light).lit;
    let red=base.r*values[i]*lit,green=base.g*values[i]*lit,blue=base.b*values[i]*lit;
    if(sourceColorValues){red*=sourceColorValues[i*3];green*=sourceColorValues[i*3+1];blue*=sourceColorValues[i*3+2];}
    if(atmosphere){P.fromBufferAttribute(positions,i).applyMatrix4(mesh.matrixWorld);
     metrics.atmosphere_evaluations++;realm.atmosphere_evaluations++;
     const haze=sampleAtmosphere(P.distanceTo(cameraPosition),{...atmosphere,height:P.y});
     hazeColor.setRGB(...haze.color.map(value=>value/255),THREE.SRGBColorSpace);
     red+=(hazeColor.r-red)*haze.amount;green+=(hazeColor.g-green)*haze.amount;blue+=(hazeColor.b-blue)*haze.amount;
    }
    colors.setXYZ(i,red,green,blue);
   }
   colors.needsUpdate=true;metrics.updates++;realm.updates++;return snapshot();
  },
  observation:snapshot,
  dispose(){if(disposed)return;disposed=true;realm.instances_disposed++;
   if(mesh.geometry===geometry)mesh.geometry=originalGeometry;
   if(mesh.material===ownedMaterial)mesh.material=originalMaterial;
   geometry.dispose();ownedMaterial.dispose();
  },
 });
}
