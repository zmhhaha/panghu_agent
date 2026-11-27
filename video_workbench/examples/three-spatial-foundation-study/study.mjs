// Local evidence fixture. This is not an operator runtime admission descriptor.
import * as THREE from '/three.module.min.js';
const deepFreeze=value=>{if(value&&typeof value==='object'){
 for(const item of Object.values(value))deepFreeze(item);Object.freeze(value);}return value;};
const policy=deepFreeze({schema:'video.scene-appearance-policy.v1',
 requested_parameters:{material_quality:'full'},actual_parameters:{material_quality:'full'},
 material_noise_limit:1000000,estimated_material_samples:16384,
 scope:'local_three_study_not_production_admission'});
Object.defineProperty(globalThis,'__trustedSceneAppearancePolicy',
 {value:policy,writable:false,configurable:false});
const {createThreeAppearanceAdapter,threeAppearanceObservation}=await import('/spatial_three_adapter.mjs');
const surface=await import('/scene_space_surface.mjs');
const {MASS_CONFIG,authoredHeight}=await import('/baseline-scene.js');
const plan=await(await fetch('/plan.json')).json(),world=plan.worlds[0],W=1280,H=720;
const renderer=new THREE.WebGLRenderer({alpha:true,antialias:false,preserveDrawingBuffer:true});
renderer.setSize(W,H);renderer.setPixelRatio(1);renderer.outputColorSpace=THREE.SRGBColorSpace;
renderer.toneMapping=THREE.NoToneMapping;renderer.localClippingEnabled=true;
document.body.appendChild(renderer.domElement);
const camera=new THREE.OrthographicCamera(-W/2,W/2,H/2,-H/2,.1,5000);
camera.position.set(W/2,H/2,2000);camera.lookAt(W/2,H/2,0);camera.updateMatrixWorld(true);
const scene=new THREE.Scene(),adapters=[],meshes=[],textures=[],sourceMeshes=[];
const sourceLight=[.55,.52,-.72],depth={paper:-1400,sun:-1300,birds:-1250,'far-ridge':-1100,
 'main-mass':0,'right-mass':-200,'cloud-ribbons':100,bank:200,'pine-a':300,'pine-b':300};
const layerById=new Map(world.layers.map(layer=>[layer.id,layer]));
const ordered=[...world.objects].sort((a,b)=>layerById.get(a.layer_id).order-layerById.get(b.layer_id).order||a.order-b.order);
function authoredCamera(cfg,width,height){const h=cfg.camera.viewHeight,w=h*width/height;
 const result=new THREE.OrthographicCamera(-w/2,w/2,h/2,-h/2,cfg.camera.near,cfg.camera.far);
 result.position.fromArray(cfg.camera.position);result.up.fromArray(cfg.camera.up);result.lookAt(...cfg.camera.target);
 result.updateMatrixWorld(true);return result;}
function terrain(cfg){const positions=[],indices=[],columns=cfg.grid.columns,rows=cfg.grid.rows;
 for(let j=0;j<rows;j++)for(let i=0;i<columns;i++){
  const x=cfg.bounds.x+i*cfg.bounds.width/(columns-1),z=cfg.bounds.z+j*cfg.bounds.depth/(rows-1);
  positions.push(x,authoredHeight(x,z,cfg),z);
 }
 for(let j=0;j<rows-1;j++)for(let i=0;i<columns-1;i++){
  const a=j*columns+i;indices.push(a,a+columns,a+1,a+1,a+columns,a+columns+1);}
 const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
 geometry.setIndex(indices);geometry.computeVertexNormals();return geometry;
}
for(const object of ordered){
 const cfg=MASS_CONFIG[object.id],b=object.bounds,t=object.transform;
 if(cfg){
  const original=authoredCamera(cfg,b.width,b.height),scale=b.height/cfg.camera.viewHeight;
  const geometry=terrain(cfg),material=new THREE.MeshBasicMaterial({color:0xffffff,side:THREE.DoubleSide,
   opacity:1,transparent:false,depthTest:true,depthWrite:true});
  const mesh=new THREE.Mesh(geometry,material);mesh.matrixAutoUpdate=false;
  mesh.matrix.makeTranslation(t.x+b.width/2,H-t.y-b.height/2,depth[object.id]);
  mesh.matrix.multiply(new THREE.Matrix4().makeScale(scale,scale,scale)).multiply(original.matrixWorldInverse);
  mesh.updateMatrixWorld(true);
  material.clippingPlanes=[new THREE.Plane(new THREE.Vector3(0,1,0),-.28).applyMatrix4(mesh.matrixWorld)];
  scene.add(mesh);sourceMeshes.push({id:object.id,vertices:geometry.attributes.position.count,
   indices:geometry.index.count,local_positions:Array.from(geometry.attributes.position.array),
   world_matrix:mesh.matrixWorld.toArray(),authored_camera:cfg.camera});
  const adapter=createThreeAppearanceAdapter({THREE,mesh,seed:cfg.seed,light:{direction:sourceLight,response:'artistic',ambient:.11},
   baseColor:[220,220,206],material:{scales:[.28,1.3,5.4],weights:[.48,.33,.19],base:.82,amplitude:.17,normalStrength:0},
   atmosphere:{near:2000,far:4000,density:.8,strength:.65,maxAmount:.34,baseHeight:0,heightFalloff:0,color:[238,233,220]}});
  adapters.push(adapter);meshes.push(mesh);
 }else{
  const canvas=document.createElement('canvas');canvas.width=b.width;canvas.height=b.height;
  window.__sceneSpaceArtwork.paintObject(canvas.getContext('2d'),structuredClone(object),structuredClone(world),{surface});
  const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;textures.push(texture);
  const material=new THREE.MeshBasicMaterial({map:texture,transparent:object.id!=='paper',
   depthTest:true,depthWrite:object.id==='paper'});
  const mesh=new THREE.Mesh(new THREE.PlaneGeometry(b.width,b.height),material);
  mesh.position.set(t.x+b.width/2,H-t.y-b.height/2,depth[object.id]);scene.add(mesh);
 }
}
function cameraAt(frame){const rows=plan.shots[0].camera.keyframes;
 if(frame<=rows[0].frame)return rows[0];if(frame>=rows.at(-1).frame)return rows.at(-1);
 const index=rows.findIndex(row=>row.frame>frame),a=rows[index-1],b=rows[index];let t=(frame-a.frame)/(b.frame-a.frame);
 if(b.easing==='smoothstep')t=t*t*(3-2*t);
 return Object.fromEntries(['x','y','zoom'].map(key=>[key,a[key]+(b[key]-a[key])*t]));
}
function renderFrame(frame){const state=cameraAt(frame);camera.position.set(state.x,H-state.y,2000);
 camera.zoom=state.zoom;camera.updateProjectionMatrix();camera.updateMatrixWorld(true);
 for(const adapter of adapters)adapter.update({camera});renderer.setClearColor(0xeee9dc,1);renderer.render(scene,camera);
 return {frame,source_world_seed:631907,camera:state,adapters:adapters.map(item=>item.observation()),
  realm:threeAppearanceObservation(),renderer:renderer.info.render};
}
function pixel(x,y){const bytes=new Uint8Array(4);renderer.getContext().readPixels(x,H-y-1,1,1,renderer.getContext().RGBA,
 renderer.getContext().UNSIGNED_BYTE,bytes);return Array.from(bytes);}
function diagnostics(){
 const results={},testScene=new THREE.Scene(),testCamera=new THREE.OrthographicCamera(-2,2,2,-2,.1,100);
 testCamera.position.set(0,0,10);testCamera.lookAt(0,0,0);testCamera.updateMatrixWorld(true);
 const rear=new THREE.Mesh(new THREE.PlaneGeometry(3.2,3.2),new THREE.MeshBasicMaterial({color:0xb91b1b}));
 testScene.add(rear);
 const front=new THREE.Mesh(new THREE.PlaneGeometry(1.8,1.8),new THREE.MeshBasicMaterial({color:0xffffff,side:THREE.DoubleSide}));
 front.position.z=1;testScene.add(front);
 let adapter;
 const draw=()=>{adapter.update({camera:testCamera});renderer.setClearColor(0,0);renderer.render(testScene,testCamera);return pixel(640,360);};
 const create=(extra={})=>createThreeAppearanceAdapter({THREE,mesh:front,seed:631907,
  light:{direction:[0,0,1],ambient:1},baseColor:[230,237,220],
  material:{amplitude:0,base:1,normalStrength:0},...extra});
 adapter=create();const opaqueWithRear=draw();rear.visible=false;const opaqueAlone=draw();rear.visible=true;
 results.pale_solid={with_rear:opaqueWithRear,without_rear:opaqueAlone,
  passed:JSON.stringify(opaqueWithRear)===JSON.stringify(opaqueAlone)&&opaqueWithRear[3]===255};adapter.dispose();
 adapter=create({atmosphere:{near:0,far:20,density:3,strength:1,maxAmount:.8,color:[197,214,240]}});
 const fogWithRear=draw();rear.visible=false;const fogAlone=draw();rear.visible=true;
 results.fog_preserves_coverage={with_rear:fogWithRear,without_rear:fogAlone,
  changed_color:JSON.stringify(fogWithRear)!==JSON.stringify(opaqueWithRear),
  passed:JSON.stringify(fogWithRear)===JSON.stringify(fogAlone)&&fogWithRear[3]===255};adapter.dispose();
 front.material.transparent=true;front.material.opacity=.5;front.material.depthWrite=false;
 adapter=create();const transparentWithRear=draw();rear.visible=false;const transparentAlone=draw();rear.visible=true;
 results.explicit_transparency={with_rear:transparentWithRear,without_rear:transparentAlone,
  passed:transparentWithRear[3]===255&&transparentAlone[3]>=126&&transparentAlone[3]<=129
   &&JSON.stringify(transparentWithRear)!==JSON.stringify(opaqueWithRear)};adapter.dispose();
 front.material.transparent=false;front.material.opacity=1;front.material.depthWrite=true;
 adapter=create({light:{direction:[0,0,1],ambient:0}});
 front.rotation.y=0;const lit=draw();front.rotation.y=Math.PI;const dark=draw();front.rotation.y=0;const relit=draw();
 results.world_light={lit,dark,relit,passed:lit[0]>dark[0]+100&&JSON.stringify(lit)===JSON.stringify(relit)};adapter.dispose();
 const pos=front.geometry.getAttribute('normal');for(let i=0;i<pos.count;i++)pos.setXYZ(i,Math.SQRT1_2,0,Math.SQRT1_2);
 front.scale.set(2,1,.5);front.rotation.y=Math.PI/6;
 adapter=create({light:{direction:[1,0,0],ambient:0},baseColor:[255,255,255]});const transformed=draw();
 const analyticalDot=(.5*Math.cos(Math.PI/6)+2*Math.sin(Math.PI/6))/Math.hypot(2,.5);
 // A pixel check complements the independently derived inverse-transpose facing;
 // output uses the renderer's sRGB conversion, so compare to its exact transfer.
 const expectedSRGB=new THREE.Color(analyticalDot,analyticalDot,analyticalDot).convertLinearToSRGB().r*255;
 results.nonuniform_normal_transform={pixel:transformed,expected_linear_dot:analyticalDot,
  expected_srgb:expectedSRGB,passed:Math.abs(transformed[0]-expectedSRGB)<3};adapter.dispose();
 front.scale.set(1,1,1);front.rotation.y=0;front.geometry.computeVertexNormals();
 adapter=create({material:{scales:[.28,1.3,5.4],weights:[.48,.33,.19],base:.82,amplitude:.17,normalStrength:0}});
 draw();const colors=Array.from(front.geometry.getAttribute('color').array),initialSamples=adapter.observation().material_samples;
 testCamera.position.x=1;testCamera.lookAt(0,0,0);testCamera.updateMatrixWorld(true);draw();
 const movedColors=Array.from(front.geometry.getAttribute('color').array);front.position.x=.25;draw();
 results.fixed_object_material={passed:JSON.stringify(colors)===JSON.stringify(movedColors)
  &&JSON.stringify(colors)===JSON.stringify(Array.from(front.geometry.getAttribute('color').array))
  &&adapter.observation().material_samples===initialSamples,material_samples:initialSamples};adapter.dispose();
 for(const object of [front,rear]){object.geometry.dispose();object.material.dispose();}
 const gl=renderer.getContext(),extension=gl.getExtension('WEBGL_debug_renderer_info');
 results.actual_backend={renderer:String(gl.getParameter(extension?extension.UNMASKED_RENDERER_WEBGL:gl.RENDERER)),
  webgl2:gl instanceof WebGL2RenderingContext,context_lost:gl.isContextLost(),gl_error:gl.getError()};
 renderFrame(0);return results;
}
window.__threeStudy=Object.freeze({renderFrame,diagnostics,sourceMeshes,
 png:()=>renderer.domElement.toDataURL('image/png'),
 jpg:()=>renderer.domElement.toDataURL('image/jpeg',.94),
 observation:()=>({realm:threeAppearanceObservation(),adapters:adapters.map(item=>item.observation())})});
renderFrame(0);
