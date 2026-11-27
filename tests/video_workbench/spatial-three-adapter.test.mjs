import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import * as THREE from '../../video_workbench/motion_renderer/vendor/three.module.min.js';

const deepFreeze=value=>{if(value&&typeof value==='object'){for(const row of Object.values(value))deepFreeze(row);Object.freeze(value);}return value;};
Object.defineProperty(globalThis,'__trustedSceneAppearancePolicy',{configurable:false,writable:false,
 value:deepFreeze({schema:'video.scene-appearance-policy.v1',requested_parameters:{material_quality:'full'},
  actual_parameters:{material_quality:'full'},material_noise_limit:4096,estimated_material_samples:1024})});
const {createThreeAppearanceAdapter:create,threeAppearanceObservation:realm,
 SPATIAL_THREE_ADAPTER_REVISION,SPATIAL_THREE_LIMITS,SPATIAL_THREE_REALM_LIMITS}
 =await import('../../video_workbench/motion_renderer/spatial_three_adapter.mjs');

function triangle(normal=[0,0,1],material={}){
 const geometry=new THREE.BufferGeometry();
 geometry.setAttribute('position',new THREE.Float32BufferAttribute([0,0,0,1,0,0,0,1,0],3));
 geometry.setAttribute('normal',new THREE.Float32BufferAttribute([...normal,...normal,...normal],3));
 return new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({color:0xffffff,...material}));
}
const camera=()=>{const c=new THREE.PerspectiveCamera(35,1,.01,1000);c.position.z=10;return c;};
const light={direction:[0,0,1]},constant={base:1,amplitude:0,normalStrength:0};
function adapter(mesh,extra={}){return create({THREE,mesh,seed:631907,light,material:constant,...extra});}
const values=mesh=>Array.from(mesh.geometry.attributes.color.array);
const near=(a,b,epsilon=1e-6)=>assert.ok(Math.abs(a-b)<=epsilon,`${a} != ${b}`);

test('a fresh realm without a host policy cannot execute the material adapter',()=>{
 const library=new URL('../../video_workbench/motion_renderer/spatial_three_adapter.mjs',import.meta.url).href;
 const three=new URL('../../video_workbench/motion_renderer/vendor/three.module.min.js',import.meta.url).href;
 const script=`import assert from 'node:assert/strict';
 import * as THREE from ${JSON.stringify(three)};
 import {createThreeAppearanceAdapter} from ${JSON.stringify(library)};
 const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute([0,0,0,1,0,0,0,1,0],3));
 g.setAttribute('normal',new THREE.Float32BufferAttribute([0,0,1,0,0,1,0,0,1],3));
 const mesh=new THREE.Mesh(g,new THREE.MeshBasicMaterial());
 assert.throws(()=>createThreeAppearanceAdapter({THREE,mesh,seed:1,light:{direction:[0,0,1]}}),/host_policy_missing/);
 assert.equal(mesh.geometry,g);`;
 execFileSync(process.execPath,['--input-type=module','-e',script],{timeout:5000,windowsHide:true,stdio:'pipe'});
});

test('actual Three inverse-transpose normals preserve world light under nonuniform scaling and camera movement',()=>{
 const mesh=triangle([1,1,0]),c=camera();mesh.scale.set(2,1,1);
 const a=adapter(mesh,{light:{direction:[1,0,0]}});
 a.update({camera:c});near(values(mesh)[0],1/Math.sqrt(5));
 c.position.set(20,-5,30);c.rotation.set(.4,.9,.2);a.update({camera:c});near(values(mesh)[0],1/Math.sqrt(5));
 mesh.rotation.z=Math.PI/2;a.update({camera:c});near(values(mesh)[0],0);
 a.update({camera:c,light:{direction:[0,1,0]}});near(values(mesh)[0],1/Math.sqrt(5));
 assert.equal(a.observation().lighting_normal_space,'world_inverse_transpose');
 assert.equal(a.observation().lighting_evaluations,12);a.dispose();
});

test('object-coordinate material is cached across absolute forward/reverse/repeated transforms',()=>{
 const mesh=triangle(),c=camera(),a=adapter(mesh,{light:{direction:[0,0,1],ambient:1},
  material:{base:.6,amplitude:.2,normalStrength:0}});
 const byFrame=new Map();
 for(const frame of [0,12,24,12,24,0]){
  mesh.position.set(frame/2,frame/3,-frame);mesh.rotation.y=frame*.05;mesh.scale.set(1+frame/48,1,1);
  c.position.set(frame,5,10);a.update({camera:c});
  const v=values(mesh);if(byFrame.has(frame))assert.deepEqual(v,byFrame.get(frame));else byFrame.set(frame,v);
 }
 assert.deepEqual(byFrame.get(0),byFrame.get(24));
 assert.equal(a.observation().material_samples,3);
 assert.equal(a.observation().material_noise_evaluations,9);
 assert.equal(a.observation().material_lattice_evaluations,72);
 assert.equal(a.observation().material_derivative_evaluations,0);a.dispose();
});

test('camera and mesh parent transforms determine atmosphere distance while coverage stays opaque',()=>{
 const mesh=triangle(),c=camera(),meshParent=new THREE.Object3D(),cameraParent=new THREE.Object3D();
 meshParent.position.z=2;meshParent.add(mesh);cameraParent.position.z=6;c.position.z=4;cameraParent.add(c);
 const original=mesh.material,a=adapter(mesh,{baseColor:[0,0,0],
  atmosphere:{near:0,far:10,density:1,maxAmount:.8,color:[255,255,255]}});
 a.update({camera:c});const amount=.8*(1-Math.exp(-(.8*.8*(3-2*.8))));near(values(mesh)[0],amount);
 const far=values(mesh)[0];cameraParent.position.z=0;a.update({camera:c});assert.ok(values(mesh)[0]<far);
 assert.deepEqual(a.observation().actual_coverage,{opacity:1,transparent:false,depthTest:true,depthWrite:true});
 assert.equal(mesh.material.opacity,original.opacity);a.dispose();
});

test('height attenuation uses world y and sRGB atmosphere colors are converted to linear RGB',()=>{
 const mesh=triangle(),c=camera(),parent=new THREE.Object3D();parent.add(mesh);
 const a=adapter(mesh,{baseColor:[0,0,0],atmosphere:{near:0,far:10,density:2,maxAmount:1,
  baseHeight:0,heightFalloff:1,color:[128,128,128]}});
 c.position.set(0,0,10);a.update({camera:c});const low=values(mesh)[0];
 const gray=new THREE.Color().setRGB(128/255,128/255,128/255,THREE.SRGBColorSpace).r;
 near(low,gray*(1-Math.exp(-2)));
 parent.position.y=5;c.position.y=5;a.update({camera:c});assert.ok(values(mesh)[0]<low/10);a.dispose();
});

test('explicit object transparency and depth flags are independent of fog color mixture',()=>{
 const mesh=triangle([0,0,1],{transparent:true,opacity:.35,depthWrite:false}),a=adapter(mesh,
  {atmosphere:{near:0,far:10,density:4,color:[220,230,240]}});
 a.update({camera:camera()});assert.deepEqual(a.observation().actual_coverage,
  {opacity:.35,transparent:true,depthTest:true,depthWrite:false});
 assert.equal(a.observation().atmosphere_evaluations,3);a.dispose();
 const opaque=triangle([0,0,1],{depthWrite:false});assert.throws(()=>adapter(opaque),/opaque_depth_required/);
 const opaqueWithLowAlpha=triangle([0,0,1],{transparent:false,opacity:.5,depthTest:false,depthWrite:false});
 assert.throws(()=>adapter(opaqueWithLowAlpha),/opaque_depth_required/);
 const dynamic=triangle(),b=adapter(dynamic);
 dynamic.material.transparent=true;dynamic.material.opacity=.25;dynamic.material.depthWrite=false;
 b.update({camera:camera()});assert.equal(b.observation().actual_coverage.opacity,.25);
 dynamic.material.transparent=false;
 assert.throws(()=>b.update({camera:camera()}),/opaque_depth_required/);assert.equal(b.observation().updates,1);b.dispose();
});

test('owned clones preserve original shared resources and dispose is idempotent',()=>{
 const mesh=triangle(),geometry=mesh.geometry,material=mesh.material;let geometryDisposals=0,materialDisposals=0;
 const a=adapter(mesh),ownedGeometry=mesh.geometry,ownedMaterial=mesh.material;
 ownedGeometry.addEventListener('dispose',()=>geometryDisposals++);ownedMaterial.addEventListener('dispose',()=>materialDisposals++);
 assert.notEqual(ownedGeometry,geometry);assert.notEqual(ownedMaterial,material);
 assert.equal(geometry.attributes.color,undefined);assert.equal(material.vertexColors,false);
 a.update({camera:camera()});a.dispose();a.dispose();
 assert.equal(mesh.geometry,geometry);assert.equal(mesh.material,material);
 assert.equal(geometryDisposals,1);assert.equal(materialDisposals,1);
 assert.throws(()=>a.update({camera:camera()}),/adapter_disposed/);
});

test('preexisting vertex colors are cached without reading later shared material or geometry edits',()=>{
 const mesh=triangle([0,0,1],{vertexColors:true}),geometry=mesh.geometry,material=mesh.material;
 geometry.setAttribute('color',new THREE.Float32BufferAttribute([.2,.4,.6,.2,.4,.6,.2,.4,.6],3));
 const a=adapter(mesh),c=camera();a.update({camera:c});const first=values(mesh);
 geometry.attributes.color.array.fill(.9);material.vertexColors=false;
 a.update({camera:c});assert.deepEqual(values(mesh),first);near(first[0],.2);near(first[1],.4);near(first[2],.6);
 assert.equal(a.observation().cached_source_color_bytes,36);a.dispose();
});

test('vertex and byte limits reject before replacing source resources; limits cannot be raised',()=>{
 const mesh=triangle(),geometry=mesh.geometry,material=mesh.material;
 assert.throws(()=>adapter(mesh,{limits:{maxVertices:2}}),/vertex_budget/);
 assert.throws(()=>adapter(mesh,{limits:{maxGeometryBytes:1}}),/geometry_byte_budget/);
 assert.throws(()=>adapter(mesh,{limits:{maxVertices:SPATIAL_THREE_LIMITS.maxVertices+1}}),/limit_invalid/);
 assert.equal(mesh.geometry,geometry);assert.equal(mesh.material,material);
 assert.throws(()=>adapter(mesh,{material:{seed:1}}),/appearance_invalid/);
});

test('numeric memory budget includes original and cloned geometry plus new color and material caches',()=>{
 const mesh=triangle(),expected=72*2+36+48;
 assert.throws(()=>adapter(mesh,{limits:{maxGeometryBytes:expected-1}}),/geometry_byte_budget/);
 const a=adapter(mesh,{limits:{maxGeometryBytes:expected}}),seen=a.observation();
 assert.equal(seen.source_geometry_bytes,72);assert.equal(seen.cloned_geometry_bytes,72);
 assert.equal(seen.owned_color_bytes,36);assert.equal(seen.cached_material_bytes,48);
 assert.equal(seen.accounted_peak_numeric_bytes,expected);a.dispose();
});

test('cumulative update budget rejects repeated seeks without changing colors or refunding work',()=>{
 const mesh=triangle(),a=adapter(mesh,{limits:{maxVertexUpdates:3}}),c=camera();a.update({camera:c});
 const before=values(mesh),observation=a.observation();
 mesh.rotation.y=.8;assert.throws(()=>a.update({camera:c}),/vertex_update_budget/);
 assert.deepEqual(values(mesh),before);assert.equal(a.observation().updates,1);
 assert.equal(a.observation().reserved_vertex_updates,observation.reserved_vertex_updates);a.dispose();
});

test('singular or mutated geometry is rejected rather than shaded with stale normals',()=>{
 const mesh=triangle(),a=adapter(mesh),c=camera();mesh.scale.x=0;
 assert.throws(()=>a.update({camera:c}),/transform_invalid/);assert.equal(a.observation().updates,0);
 mesh.scale.x=1;mesh.geometry.attributes.position.needsUpdate=true;
 assert.throws(()=>a.update({camera:c}),/binding_changed/);a.dispose();
 const bad=triangle([0,0,0]);assert.throws(()=>adapter(bad),/normal_invalid/);
 const skinned=triangle();skinned.isSkinnedMesh=true;assert.throws(()=>adapter(skinned),/mesh_unsupported/);
 const morph=triangle();morph.geometry.morphAttributes.position=[];assert.throws(()=>adapter(morph),/geometry_unsupported/);
});

test('query/fragment imports use the same monotonic authority and cannot mutate its report',async()=>{
 const alternate=await import('../../video_workbench/motion_renderer/spatial_three_adapter.mjs?alternate#fragment'),before=realm();
 const mesh=triangle(),a=alternate.createThreeAppearanceAdapter({THREE,mesh,seed:631907,light,material:constant});
 a.update({camera:camera()});const after=realm();
 assert.equal(after.instances_created,before.instances_created+1);assert.equal(after.material_samples,before.material_samples+3);
 assert.equal(after.lighting_evaluations,before.lighting_evaluations+3);
 assert.deepEqual(alternate.threeAppearanceObservation(),after);
 assert.throws(()=>{after.instances_created=0;},TypeError);assert.throws(()=>{after.limits.maxInstances=10000;},TypeError);
 a.dispose();assert.equal(realm().instances_disposed,before.instances_disposed+1);
 assert.equal(realm().vertices_reserved,before.vertices_reserved+3);
 assert.equal(after.revision,SPATIAL_THREE_ADAPTER_REVISION);
});

test('realm instance reservation survives dispose and cannot be reset by a fragment import',async()=>{
 while(realm().instances_reserved<SPATIAL_THREE_REALM_LIMITS.maxInstances){const a=adapter(triangle());a.dispose();}
 const before=realm(),alternate=await import('../../video_workbench/motion_renderer/spatial_three_adapter.mjs#another');
 assert.throws(()=>alternate.createThreeAppearanceAdapter({THREE,mesh:triangle(),seed:631907,light}),/realm_geometry_budget/);
 assert.equal(realm().instances_reserved,before.instances_reserved);
 assert.equal(realm().material_samples,before.material_samples);
});
