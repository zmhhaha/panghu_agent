import * as THREE from './assets/three.module.min.js';

const data = __PAYLOAD__;
const width = 1280, height = 720, duration = data.timeline.duration_seconds;
const lerp = (a,b,t) => a+(b-a)*t;
const clamp = value => Math.max(0,Math.min(1,value));
const smooth = value => {const t=clamp(value);return t*t*(3-2*t);};
const phaseProgress = (time,start,end) => smooth((time/duration-start)/(end-start));
const state = window.__videoMotion;

try {
  if (THREE.REVISION !== '170') throw Error('需要本地锁定的 Three.js r170');
  if (!window.gsap) throw Error('本地 GSAP 未加载');
  const canvas = document.getElementById('three-scene');
  const renderer = new THREE.WebGLRenderer({canvas,antialias:true,alpha:false,
    preserveDrawingBuffer:true,powerPreference:'low-power'});
  renderer.setPixelRatio(1); renderer.setSize(width,height,false);
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.setClearColor('#0c1523',1);
  const gl = renderer.getContext();
  if (!renderer.capabilities.isWebGL2 || gl.isContextLost()) throw Error('浏览器未提供可用的 WebGL2 上下文');
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32,width/height,.1,100);
  const cameraTarget = new THREE.Vector3(0,0,0);
  scene.add(new THREE.AmbientLight('#d2e1ff',2.3));
  const keyLight = new THREE.DirectionalLight('#ffffff',3.1);keyLight.position.set(4,7,6);scene.add(keyLight);
  const rimLight = new THREE.DirectionalLight('#638de8',1.6);rimLight.position.set(-5,2,-3);scene.add(rimLight);
  const geometry = new THREE.BoxGeometry(3.6,.22,2.25);
  const layers=data.plan.layers.map((row,index)=>{
    const material=new THREE.MeshStandardMaterial({color:row.color,metalness:.12,roughness:.55});
    const mesh=new THREE.Mesh(geometry,material);mesh.name=row.id;
    const outline=new THREE.LineSegments(new THREE.EdgesGeometry(geometry),
      new THREE.LineBasicMaterial({color:row.color,transparent:true,opacity:.5}));
    mesh.add(outline);scene.add(mesh);
    return {row,index,mesh,material,key:document.querySelector('[data-layer="'+row.id+'"]')};
  });
  let current=0;
  const unitCount=layers.length;
  const pixels=[[32,160],[640,360],[640,400],[560,400],[720,400],[640,450],[580,320],[700,320]];

  function renderAt(time) {
    current=Math.max(0,Math.min(duration,Number(time)));
    const explode=phaseProgress(current,.12,.30)*(1-phaseProgress(current,.80,.96));
    const focus=phaseProgress(current,.32,.72);
    const candidate=data.variant==='candidate';
    const focusStrength=candidate ? Math.sin(Math.PI*focus) : 0;
    const focusIndex=Math.min(unitCount-1,Math.floor(focus*unitCount));
    for(const layer of layers) {
      const ordinal=(unitCount-1)/2-layer.index;
      layer.mesh.position.set(-.85+ordinal*.1*explode,ordinal*lerp(.30,.86,explode),ordinal*.42*explode);
      layer.mesh.rotation.set(0,0,0);
      layer.mesh.scale.set(1,1,1);
      layer.material.emissive.set(layer.row.color);
      layer.material.emissiveIntensity=candidate&&layer.index===focusIndex ? .12*focusStrength : 0;
      layer.material.opacity=1;layer.mesh.visible=true;
      layer.key.classList.toggle('active',candidate&&layer.index===focusIndex&&focusStrength>.05);
    }
    const angle=.64+(candidate ? .28*focus : 0);
    camera.position.set(Math.sin(angle)*13.2,6.5+focusStrength*.6,Math.cos(angle)*13.2);
    cameraTarget.set(-.1,candidate ? (((unitCount-1)/2-focus*(unitCount-1))*.13*focusStrength) : 0,0);
    camera.lookAt(cameraTarget);camera.updateMatrixWorld(true);scene.updateMatrixWorld(true);
    const phase=document.getElementById('phase');
    phase.textContent=explode<.03 ? '完整服务：各层共同工作' : focusStrength>.1&&candidate
      ? data.plan.layers[focusIndex].label+'：'+data.plan.layers[focusIndex].detail : '拆开来看：每层负责一件事';
    renderer.render(scene,camera);
    if(gl.isContextLost()) throw Error('WebGL 上下文已丢失');
  }

  function projectedBounds(mesh) {
    const points=[];
    for(const x of [-1.8,1.8])for(const y of [-.11,.11])for(const z of [-1.125,1.125]) {
      const point=new THREE.Vector3(x,y,z).applyMatrix4(mesh.matrixWorld).project(camera);
      points.push([(point.x+1)*width/2,(1-point.y)*height/2]);
    }
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    return {x:Math.min(...xs),y:Math.min(...ys),width:Math.max(...xs)-Math.min(...xs),height:Math.max(...ys)-Math.min(...ys)};
  }

  state.snapshot=()=>({engine:'three',time:current,three_revision:THREE.REVISION,
    webgl:{version:gl.getParameter(gl.VERSION),context_lost:gl.isContextLost(),
      draw_calls:renderer.info.render.calls,triangles:renderer.info.render.triangles,width,height},
    camera:{position:camera.position.toArray(),target:cameraTarget.toArray(),
      matrix_world:camera.matrixWorld.toArray(),projection_matrix:camera.projectionMatrix.toArray()},
    objects:layers.map(({row,mesh,material})=>({id:row.id,position:mesh.position.toArray(),
      rotation:[mesh.rotation.x,mesh.rotation.y,mesh.rotation.z],scale:mesh.scale.toArray(),
      matrix_world:mesh.matrixWorld.toArray(),bounds:projectedBounds(mesh),visible:mesh.visible,opacity:material.opacity})),
    bitmap_samples:pixels.map(([x,y])=>{const pixel=new Uint8Array(4);gl.readPixels(x,height-1-y,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);return {x,y,rgba:Array.from(pixel)};})});
  state.renderAt=renderAt;
  const clock={time:0};
  const timeline=gsap.timeline({paused:true});
  timeline.to(clock,{time:duration,duration,ease:'none',onUpdate:()=>renderAt(clock.time)},0);
  window.__timelines=window.__timelines||{};window.__timelines['video-motion']=timeline;
  canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();state.fail('WebGL 上下文已丢失');});
  await document.fonts.load('16px VideoNoto');await document.fonts.ready;
  if(!document.fonts.check('16px VideoNoto'))throw Error('指定字体未加载');
  renderAt(0);state.resolve();
} catch(error) {state.fail(error?.message||String(error));}
