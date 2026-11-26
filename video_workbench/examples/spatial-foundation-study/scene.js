import * as ink from './assets/ink-landscape.mjs';
// The host owns the clock and camera; these are frozen source conditions.
const STUDY_OPTIONS = Object.freeze({"lighting":true,"material":true,"atmosphere":true,"diagnostic":"ink"});

const PAPER='#eee9dc',PAPER_RGB=[238,233,220],INK='#242923',ART_MODE='ink';
function randomFor(seed){let a=seed>>>0;return()=>{a+=0x6D2B79F5;let t=a;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296;};}
const sat=t=>Math.max(0,Math.min(1,t));
function makeCanvas(W,H){const c=document.createElement('canvas');c.width=W;c.height=H;return c;}
function polygon(points){const p=new Path2D();points.forEach(([x,y],i)=>i?p.lineTo(x,y):p.moveTo(x,y));p.closePath();return p;}
const MASS_CONFIG={
 'main-mass':{seed:132,bounds:{x:-9,z:-5,width:18,depth:17},grid:{columns:89,rows:77},
  camera:{position:[2.8,7.8,21],target:[0,4.2,2],up:[0,1,0],viewHeight:12.6,near:.1,far:60},
  base:{cx:0,cz:4.1,height:1.45,sx:6.0,sz:5.6},
  ridges:[
   [[-7,0,1.2],[-4.5,2.6,6.5],[-2.2,4.2,3.8],[0,5.3,9.4],[1.6,6.0,4.1],[3.5,6.4,7.2],[6.5,7.5,1.6]],
   [[0,5.3,9.4],[-1.1,7.3,3.2],[-3.7,8.2,2.8],[-6.4,6.8,.9]],
   [[3.5,6.4,7.2],[2.5,8.3,3.8],[.3,10.2,2.1],[-2.3,9.4,1.8],[-3.7,8.2,2.8]],
   [[-4.5,2.6,6.5],[-5.1,4.7,2.6],[-3.7,8.2,2.8]]]},
 'right-mass':{seed:725,bounds:{x:-4.8,z:-3.5,width:9.6,depth:11},grid:{columns:61,rows:57},
  camera:{position:[1.0,5.6,14],target:[0,2.7,1.4],up:[0,1,0],viewHeight:7.9,near:.1,far:45},
  base:{cx:0,cz:2.4,height:.65,sx:3.6,sz:4.4},
  ridges:[
   [[-3.8,0,.5],[-2.2,1.6,2.7],[-1.0,2.4,1.5],[.2,3.1,5.2],[1.4,3.7,2.0],[2.8,4.5,3.0],[4.0,5.3,.5]],
   [[.2,3.1,5.2],[.8,5.4,2.0],[2.8,6.1,.6]]]} };
const MASS_FIELDS=new Map();
function authoredHeight(x,z,cfg){const b=cfg.base;
 const base=b.height*Math.exp(-((x-b.cx)**2/(b.sx*b.sx)+(z-b.cz)**2/(b.sz*b.sz)));
 let ridge=0;
 for(const path of cfg.ridges)for(let i=1;i<path.length;i++){
  const a=path[i-1],b=path[i],dx=b[0]-a[0],dz=b[1]-a[1],t=sat(((x-a[0])*dx+(z-a[1])*dz)/(dx*dx+dz*dz));
  const d=Math.hypot(x-a[0]-dx*t,z-a[1]-dz*t),h=a[2]+(b[2]-a[2])*t;
  const width=.63+.78-h*.043;
  ridge=Math.max(ridge,h*Math.exp(-Math.pow(d/width,1.50)));
 }
 return Math.max(0,base+ridge*(1+Math.sin(x*3.4+z*2.7)*Math.sin(z*3.1-x*.8)*.013));}
function fieldFor(o,tools){if(MASS_FIELDS.has(o.id))return MASS_FIELDS.get(o.id);
 const cfg=MASS_CONFIG[o.id],W=o.bounds.width,H=o.bounds.height,heights=[];
 for(let j=0;j<cfg.grid.rows;j++)for(let i=0;i<cfg.grid.columns;i++){
  const x=cfg.bounds.x+i*cfg.bounds.width/(cfg.grid.columns-1),z=cfg.bounds.z+j*cfg.bounds.depth/(cfg.grid.rows-1);heights.push(authoredHeight(x,z,cfg));}
 const mesh=tools.geometry.createTerrainMesh({bounds:cfg.bounds,grid:cfg.grid,heights});
 if(mesh.connected_components!==1)throw new Error('Mountain mass must be one connected surface');
 const projection=tools.geometry.projectMesh(mesh,{width:W,height:H,camera:cfg.camera});
 const alpha=new Uint8Array(W*H),height=new Float32Array(W*H),depth=new Float32Array(W*H),world=new Float32Array(W*H*3),normals=new Float32Array(W*H*3),flow=new Float32Array(W*H*2);
 const n=ink.createNoise1D(cfg.seed+310),n2=ink.createNoise1D(cfg.seed+710);
 for(let y=0;y<H;y++)for(let x=0;x<W;x++){
  const i=y*W+x,s=tools.geometry.sampleProjection(projection,x+.5,y+.5);if(!s.covered)continue;
  const h=s.position[1],foot=sat((h-.28)/.62),fogTop=H*(.80+n(x*.012+17)*.025)+n2(x*.036+9)*5;
  const fog=sat((H+2-y)/(H+2-fogTop));
  alpha[i]=Math.round(255*foot*(y<fogTop?1:fog*fog*(3-2*fog)));height[i]=h;depth[i]=s.depth;
  world.set(s.position,i*3);normals.set(s.normal,i*3);flow.set(s.flow,i*2);
 }
 const field={cfg,W,H,mesh,projection,alpha,height,depth,world,normals,flow,tools};MASS_FIELDS.set(o.id,field);return field;
}
// The mesh keeps visibility; this field is only a softly grouped brush guide.
function sampleField(f,x,y){const xx=Math.floor(x),yy=Math.floor(y);if(xx<0||xx>=f.W||yy<0||yy>=f.H)return null;
 const i=yy*f.W+xx;if(f.alpha[i]<32)return null;const n=i*3,N=[0,0,0],F=[0,0];let total=0;
 for(let dy=-2;dy<=2;dy+=2)for(let dx=-2;dx<=2;dx+=2){const sx=xx+dx,sy=yy+dy;if(sx<0||sx>=f.W||sy<0||sy>=f.H)continue;
  const j=sy*f.W+sx;if(!f.alpha[j]||Math.abs(f.depth[j]-f.depth[i])>.5)continue;
  const w=dx===0&&dy===0?4:1;total+=w;
  for(let k=0;k<3;k++)N[k]+=f.normals[j*3+k]*w;
  F[0]+=f.flow[j*2]*w;F[1]+=f.flow[j*2+1]*w;}
 const nm=Math.hypot(...N)||1,fm=Math.hypot(...F)||1;
 return{i,x,y,height:f.height[i],depth:f.depth[i],position:[f.world[n],f.world[n+1],f.world[n+2]],normal:N.map(v=>v/nm),flow:F.map(v=>v/fm)};
}
const INK_DESIGN=Object.freeze({lightDirection:Object.freeze([.55,.52,-.72]),
 mainGroups:Object.freeze([[-3.5,5.0,3.0,3.1,.95],[.0,6.0,2.3,2.7,1],[-1.6,8.1,3.4,2.0,.87],[2.6,8.5,2.1,2.3,.92]]),
 farGroups:Object.freeze([[-1.6,2.8,2.1,2.0,.9],[.8,4.8,2.1,2.5,1]])});
const SUN_DIRECTION=INK_DESIGN.lightDirection.map(v=>v/Math.hypot(...INK_DESIGN.lightDirection));
const easeRange=(a,b,v)=>{const t=sat((v-a)/(b-a));return t*t*(3-2*t);};

function guideAt(f,s){
 const key=s.i;if(!f.studyGuides)f.studyGuides=new Map();
 if(f.studyGuides.has(key))return f.studyGuides.get(key);
 const lighting=STUDY_OPTIONS.lighting?f.tools.surface.evaluateLighting(s.normal,{
  light:INK_DESIGN.lightDirection,mode:'ink',ambient:0,lightRange:[-.08,.61]}):null;
 const material=STUDY_OPTIONS.material?f.tools.surface.sampleMaterial(s.position,{
  seed:f.cfg.seed,space:'world',scales:[.28,1.3,5.4],weights:[.48,.33,.19],
  base:.5,amplitude:.26,roughness:.65,roughnessVariation:.14,normalStrength:0}):null;
 const guide={light:lighting?lighting.lit:.5,materialGain:material ? .9+.2*material.value : 1,
  roughness:material?material.roughness:.65};
 f.studyGuides.set(key,guide);return guide;
}
function atmosphereTransmission(tools,id){
 if(!STUDY_OPTIONS.atmosphere)return 1;
 // Explicit authored scene distances, not inferred or reconstructed distances.
 const distance=id==='main-mass'?12:id==='right-mass'?45:80;
 return tools.surface.sampleAtmosphere(distance,{near:0,far:100,density:.9,
  strength:.5,maxAmount:.4,height:0,baseHeight:0,heightFalloff:0,color:PAPER_RGB}).transmittance;
}

function inkZone(f,s){const [x,h,z]=s.position,n=s.normal;
 // One shared light/material evaluation guides the existing ink contacts;
 // it is never rendered as a smooth grey underpainting.
 const guide=guideAt(f,s),light=guide.light,shadow=1-light;
 const step=.32,concavity=(authoredHeight(x-step,z,f.cfg)+authoredHeight(x+step,z,f.cfg)+
  authoredHeight(x,z-step,f.cfg)+authoredHeight(x,z+step,f.cfg))*.25-authoredHeight(x,z,f.cfg),valley=sat(concavity*4);
 const groups=f.W>400?INK_DESIGN.mainGroups:INK_DESIGN.farGroups;
 let group=0,groupIndex=0;groups.forEach(([cx,cz,sx,sz,w],i)=>{const g=w*Math.exp(-((x-cx)**2/(sx*sx)+(z-cz)**2/(sz*sz)));
  if(g>group){group=g;groupIndex=i;}});
 const strength=sat(.10+shadow*.78+valley*.12),deposit=sat(strength*(.36+group*.64)*guide.materialGain);
 return{light,shadow,valley,group,groupIndex,strength,deposit};
}
function traceContact(f,x,y,length,angle,seed){let s=sampleField(f,x,y);if(!s)return[];
 const path=[],r=randomFor(seed),step=1.7,N=Math.max(3,Math.round(length/step)),elbow=.38+r()*.24,bend=(r()<.5?-1:1)*(.14+r()*.28);
 let inertia=s.flow.slice();
 for(let k=0;k<N;k++){
  const q=sampleField(f,x,y);if(!q)break;
  const cp=f.cfg.camera.position,ct=f.cfg.camera.target,cv=cp.map((v,i)=>ct[i]-v),cm=Math.hypot(...cv),
   incidence=Math.abs(q.normal.reduce((sum,v,i)=>sum+v*cv[i]/cm,0));
  if(k&&Math.hypot(...q.position.map((v,i)=>v-s.position[i]))>.50+(1-incidence)*1.25)break;
  const t=k/(N-1),press=(.53+.36*Math.sin(Math.PI*t))*(1-Math.pow(t,2.1));
  path.push([x,y,Math.max(.025,press)]);
  let [dx,dy]=q.flow,m=Math.hypot(dx,dy);if(m<.05){dx=.15;dy=1;m=Math.hypot(dx,dy);}dx/=m;dy/=m;
  dx=inertia[0]*.80+dx*.20;dy=inertia[1]*.80+dy*.20;m=Math.hypot(dx,dy)||1;dx/=m;dy/=m;inertia=[dx,dy];
  const a=angle+Math.sin(t*Math.PI)*bend*.22,c=Math.cos(a),v=Math.sin(a);
  x+=(dx*c-dy*v)*step+(r()-.5)*.25;y+=(dy*c+dx*v)*step+(r()-.5)*.21;s=q;
 }return path;
}

function planInk(f){if(f.inkPlan)return f.inkPlan;
 const r=randomFor(f.cfg.seed+310),points=[],cells=new Map(),cell=6,limit=f.W>400?630:170,base=f.W>400?4.0:3.1;
 for(let attempt=0;attempt<limit*42&&points.length<limit;attempt++){
  const x=r()*f.W,y=r()*f.H,s=sampleField(f,x,y);if(!s)continue;const zone=inkZone(f,s);
  if(r()>.025+.92*Math.pow(zone.deposit,1.65))continue;
  const radius=base*(.68+zone.light*1.75)*(zone.valley>.10?.78:1),cx=Math.floor(x/cell),cy=Math.floor(y/cell);let blocked=false;
  for(let j=-3;j<=3&&!blocked;j++)for(let i=-3;i<=3&&!blocked;i++)for(const q of cells.get((cx+i)+','+(cy+j))??[]){
   if(Math.hypot(x-q.x,y-q.y)<Math.max(radius,q.radius)){blocked=true;break;}}
  if(blocked)continue;const p={x,y,s,zone,radius,seed:f.cfg.seed+attempt*137};points.push(p);
  const key=cx+','+cy;if(!cells.has(key))cells.set(key,[]);cells.get(key).push(p);
 }
 f.inkPlan=points;return points;
}
// Recheck the same tone plan along a route, so a dark contact lifts before it
// crosses into a quiet light face. Width and opacity belong to brush contacts.
function weightedStroke(ctx,f,points,options){let part=[],sum=0;
 const flush=()=>{if(part.length>2){const w=sum/part.length;InkPath.paintInkStroke(ctx,part,{...options,opacity:options.opacity*(.30+.70*w)});}part=[];sum=0;};
 for(const p of points){const s=sampleField(f,p[0],p[1]);if(!s){flush();continue;}const z=inkZone(f,s);
  if(z.light>.82&&z.valley<.04){flush();continue;}
  const guide=guideAt(f,s),w=sat((.15+z.deposit*.85)*guide.materialGain);part.push([p[0],p[1],p[2]*(.42+.58*w)]);sum+=w;}
 flush();
}
function paintWetPass(ctx,f,seeds,scale,color){const r=randomFor(f.cfg.seed+171);
 for(let i=0;i<seeds.length;i++){
  const p=seeds[i];if(i%5!==0||p.zone.deposit<.36)continue;
  const pts=traceContact(f,p.x,p.y,(28+r()*45)*scale,(r()-.5)*.24,p.seed);if(pts.length<5)continue;
  ctx.save();ctx.filter=`blur(${.65*scale}px)`;
  weightedStroke(ctx,f,pts,{seed:p.seed,width:(15+r()*19)*scale,opacity:.12+p.zone.deposit*.07,dryness:.18,noise:.48,ink:color});ctx.restore();
 }
}
function paintTexturePass(ctx,f,seeds,scale,color){const r=randomFor(f.cfg.seed+73);
 for(let i=0;i<seeds.length;i++){
  const p=seeds[i],z=p.zone,material=guideAt(f,p.s),wide=i%3===0,cross=!wide&&i%17===0&&z.shadow>.60;
  const length=(cross?10+r()*16:wide?35+r()*58:27+r()*62)*scale,
   angle=cross?(r()<.5?-1:1)*(1.0+r()*.18):(r()-.5)*.34;
  const pts=traceContact(f,p.x,p.y,length,angle,p.seed);if(pts.length<4)continue;
  weightedStroke(ctx,f,pts,{seed:p.seed,width:(wide?7+r()*11:cross?3+r()*3:.95+r()*3.1)*scale,
   opacity:wide?.35+z.deposit*.35:.30+z.deposit*.40,dryness:sat((wide?.46+r()*.12:.65+r()*.14)+(.65-material.roughness)*.18),noise:.88,ink:color});
  // A small number of broad shoulder rubs bridge the same ink groups, rather
  // than independently filling every upward-facing surface with hatch lines.
  if(i%13===0&&p.s.normal[1]>.43&&z.group>.42){
   const short=traceContact(f,p.x,p.y,(12+r()*17)*scale,(r()<.5?-1:1)*1.08,p.seed+53);
   if(short.length>2)InkNature.paintContact(ctx,short,(11+r()*11)*scale,.28,.48,p.seed+53,color);
  }
 }
}
function pointOnSurface(f,x,z){const h=authoredHeight(x,z,f.cfg),q=f.tools.geometry.projectPoint(f.projection,[x,h+.025,z]);
 if(!q.in_view)return null;
 // A summit may land just outside the sampled pixel. Search a tiny inward
 // neighborhood, choosing a surface point close in depth, without a new view.
 let best=null,score=Infinity;
 for(let dy=-3;dy<=3;dy++)for(let dx=-3;dx<=3;dx++){
  const s=sampleField(f,q.x+dx,q.y+dy);if(!s||Math.abs(s.depth-q.depth)>.24)continue;
  if(!q.visible&&(q.depth-s.depth>f.cfg.camera.viewHeight/f.H*2.1||Math.hypot(s.position[0]-x,s.position[1]-h,s.position[2]-z)>.45))continue;
  const k=dx*dx+dy*dy+Math.abs(s.depth-q.depth)*10;if(k<score){best=s;score=k;}}
 return best;
}
function structuralNodes(f){const nodes=new Map();
 for(const path of f.cfg.ridges)for(let i=0;i<path.length;i++){
  const [x,z,h]=path[i],key=x+','+z;if(!nodes.has(key))nodes.set(key,{x,z,h,count:0,degree:0,peak:false});
  const n=nodes.get(key);n.count++;n.degree+=(i>0?1:0)+(i<path.length-1?1:0);
  if(i>0&&i<path.length-1&&h>path[i-1][2]&&h>path[i+1][2])n.peak=true;
 }
 return [...nodes.values()].filter(n=>n.degree>=3||n.peak).sort((a,b)=>(b.degree*2+b.h)-(a.degree*2+a.h));
}
function paintBonePass(ctx,f,scale,color){const r=randomFor(f.cfg.seed+701);
 for(let p=0;p<f.cfg.ridges.length;p++)for(let i=1;i<f.cfg.ridges[p].length;i++){
  const a=f.cfg.ridges[p][i-1],b=f.cfg.ridges[p][i];if(r()<.40)continue;
  const start=a[2]>b[2]?.06:.70,pts=[];
  for(let k=0;k<10;k++){const t=start+k*.022;if(t>1)break;const s=pointOnSurface(f,a[0]*(1-t)+b[0]*t,a[1]*(1-t)+b[1]*t);if(!s)break;
   pts.push([s.x,s.y,.85-k*.08]);}
  if(pts.length>2)weightedStroke(ctx,f,pts,{seed:f.cfg.seed+p*701+i*173,width:(1.3+r()*1.5)*scale,opacity:.72,dryness:.72,noise:.89,ink:color});
 }
 // Focal dark contacts are selected from authored branching/peak nodes. They
 // are not gated by concave curvature and are never stamped on every vertex.
 let marked=0;const used=[];
 for(const node of structuralNodes(f)){
  const s=pointOnSurface(f,node.x,node.z);if(!s||used.some(q=>Math.hypot(q.x-s.x,q.y-s.y)<22*scale))continue;
  const z=inkZone(f,s);if(z.light>.85&&!node.peak)continue;
  const count=node.peak?2:3;
  for(let j=0;j<count;j++){
   const x=s.x+(r()-.5)*5*scale,y=s.y+(j*.7+r()*2)*scale,w=(2.8+r()*2.5)*scale;
   InkPath.paintInkStroke(ctx,[[x,y,.65],[x+(1+r())*scale,y+(2+r()*2.5)*scale,1],[x+(1+r()*2)*scale,y+(4+r()*2)*scale,.035]],
    {seed:f.cfg.seed+marked*137+j*29,width:w,opacity:.96,dryness:.28,noise:.68,ink:'#111111'});
  }
  used.push(s);if(++marked>=(f.W>400?4:2))break;
 }
}
function coverageFor(f){if(f.brushCoverage)return f.brushCoverage;
 const alpha=new Uint8Array(f.alpha),sites=[],n=ink.createNoise1D(f.cfg.seed+4501);
 for(let y=2;y<f.H-2;y++)for(let x=2;x<f.W-2;x++){
  const i=y*f.W+x;if(f.alpha[i]<190)continue;
  const L=f.alpha[i-1],R=f.alpha[i+1],U=f.alpha[i-f.W],D=f.alpha[i+f.W];
  if(Math.min(L,R,U,D)>100)continue;
  const rough=n(y*.071+x*.013+5),selected=y>f.H*.22&&y<f.H*.90&&rough<.08;
  if(!selected)continue;
  const nx=L-R,ny=U-D,m=Math.hypot(nx,ny)||1,ox=nx/m,oy=ny/m;
  alpha[i]=Math.round(f.alpha[i]*(.63+sat(rough+.4)*.55));
  // Limited pigment fringe, 1..2px only. The opaque interior remains intact.
  for(let k=1;k<=2;k++){const xx=Math.round(x+ox*k),yy=Math.round(y+oy*k),j=yy*f.W+xx;
   if(xx<0||xx>=f.W||yy<0||yy>=f.H||f.alpha[j]>160)continue;
   alpha[j]=Math.max(alpha[j],Math.round(f.alpha[i]*(k===1?.28:.09)));}
  if((x+y)%4===0)sites.push({x,y,nx:ox,ny:oy});
 }
 f.brushCoverage={alpha,sites};return f.brushCoverage;
}
function paintEdgePass(ctx,f,scale,color){const {sites}=coverageFor(f),r=randomFor(f.cfg.seed+4511);
 for(let i=0;i<sites.length;i++){
  const p=sites[i],s=sampleField(f,p.x,p.y);if(!s)continue;const z=inkZone(f,s);
  if(z.light>.78||r()<.45)continue;const length=(4+r()*7)*scale,tx=-p.ny,ty=p.nx;
  InkPath.paintInkStroke(ctx,[[p.x-tx*length*.5,p.y-ty*length*.5,.25],[p.x+p.nx*.7,p.y+p.ny*.7,.85],
   [p.x+tx*length*.5-p.nx,p.y+ty*length*.5-p.ny,.025]],
   {seed:f.cfg.seed+i*31,width:(2+r()*2.8)*scale,opacity:.34+z.shadow*.24,dryness:.88,noise:.96,ink:color});
 }
}

function paintDiagnostic(ctx,f){
 const mode=STUDY_OPTIONS.diagnostic;
 const buffer=makeCanvas(f.W,f.H),pixels=buffer.getContext('2d'),image=pixels.createImageData(f.W,f.H);
 let near=Infinity,far=-Infinity;
 if(mode==='depth')for(let i=0;i<f.alpha.length;i++)if(f.alpha[i]){near=Math.min(near,f.depth[i]);far=Math.max(far,f.depth[i]);}
 const mask=mode==='coverage'?coverageFor(f).alpha:f.alpha;
 for(let y=0;y<f.H;y++)for(let x=0;x<f.W;x++){
  const i=y*f.W+x;if(!mask[i])continue;let rgb;
  if(mode==='coverage')rgb=[mask[i],mask[i],mask[i]];
  else if(mode==='depth'){const v=Math.round(35+195*(1-(f.depth[i]-near)/(far-near||1)));rgb=[v,v,v];}
  else if(mode==='normal')rgb=[0,1,2].map(k=>Math.round((f.normals[i*3+k]*.5+.5)*255));
  else {const s=sampleField(f,x,y);if(!s)continue;
   const light=f.tools.surface.evaluateLighting(s.normal,{light:INK_DESIGN.lightDirection,mode:'ink',lightRange:[-.08,.61]}).lit;
   const v=Math.round(30+210*light);rgb=[v,v,v];}
  const offset=i*4;image.data.set([rgb[0],rgb[1],rgb[2],255],offset);
 }
 pixels.putImageData(image,0,0);ctx.drawImage(buffer,0,0);buffer.width=0;buffer.height=0;
}

function meshInk(ctx,o,tools){const f=fieldFor(o,tools),far=o.id==='right-mass',scale=Math.max(.57,o.bounds.width/570);
 if(STUDY_OPTIONS.diagnostic!=='ink'){paintDiagnostic(ctx,f);return;}
 if(ART_MODE==='clay'){
  for(let y=0;y<f.H;y++)for(let x=0;x<f.W;x++){const i=y*f.W+x;if(!f.alpha[i])continue;
   const n=i*3,t=.12+.56*(1-Math.max(0,f.normals[n]*-.3+f.normals[n+1]*.82+f.normals[n+2]*.45)),c=Math.round(238*(1-t)+40*t);
   ctx.fillStyle=`rgb(${c},${c},${c})`;ctx.fillRect(x,y,1,1);}return;
 }
 ctx.save();ctx.globalAlpha*=(far?.47:1)*atmosphereTransmission(tools,o.id);const color=far?'#697162':'#252c24',seeds=planInk(f);
 paintWetPass(ctx,f,seeds,scale,color);
 paintTexturePass(ctx,f,seeds,scale,color);
 paintBonePass(ctx,f,scale,color);
 paintEdgePass(ctx,f,scale,color);
 ctx.restore();
}
function meshCoverage(ctx,o,tools){const f=fieldFor(o,tools),alpha=coverageFor(f).alpha,paths=Array.from({length:16},()=>new Path2D());
 // Union equal-alpha runs into one fill; preserve cache density/transform.
 for(let y=0;y<f.H;y++){let start=0,last=Math.round(alpha[y*f.W]/17);
  for(let x=1;x<=f.W;x++){const a=x===f.W?-1:Math.round(alpha[y*f.W+x]/17);if(a===last)continue;
   if(last>0)paths[last].rect(start,y,x-start,1);start=x;last=a;}}
 for(let a=1;a<=15;a++){ctx.fillStyle=`rgba(0,0,0,${a/15})`;ctx.fill(paths[a]);}
 return{substrate:PAPER_RGB};
}

function nearBank(ctx,W,H){const buffer=makeCanvas(W,H),c=buffer.getContext('2d');
 InkNature.paintDryBank(c,{width:W,height:H,seed:812});
 // The lower frame crop remains a strong foreground. Soften only the right
 // contact where the shore recedes into cloud, never the whole image bottom.
 const shore=[[481,110],[527,128],[575,146],[616,169],[666,197],[710,224]];
 const yAt=x=>{let i=1;while(i<shore.length-1&&shore[i][0]<x)i++;const a=shore[i-1],b=shore[i],t=sat((x-a[0])/(b[0]-a[0]));return a[1]*(1-t)+b[1]*t;};
 c.globalCompositeOperation='destination-in';const n=ink.createNoise1D(8991);
 for(let x=0;x<W;x+=2){c.save();c.beginPath();c.rect(x,0,2,H);c.clip();
  if(x<530){c.fillStyle='#000';}else{const mix=sat((x-530)/150)*(.42+.58*sat(n(x*.035)+.5)),top=yAt(x)+n(x*.06)*3;
   const g=c.createLinearGradient(0,top-1,0,top+4+mix*4);
   g.addColorStop(0,`rgba(0,0,0,${1-mix*.42})`);g.addColorStop(1,'#000');c.fillStyle=g;}
  c.fillRect(x,0,2,H);c.restore();}
 c.globalCompositeOperation='source-over';ctx.drawImage(buffer,0,0);buffer.width=0;buffer.height=0;
}
function paper(ctx,W,H){ctx.fillStyle=PAPER;ctx.fillRect(0,0,W,H);const r=randomFor(631907);
 for(let i=0;i<22000;i++){ctx.fillStyle=i%4?'rgba(113,93,65,.024)':'rgba(255,255,248,.10)';ctx.fillRect(r()*W,r()*H,.35+r()*.8,.3+r()*.9);}}
function finePaperGrain(ctx,W,H,seed){const r=randomFor(seed);ctx.save();ctx.globalCompositeOperation='destination-out';ctx.strokeStyle='rgba(0,0,0,.065)';ctx.lineWidth=.45;for(let i=0;i<W*H/115;i++){const x=r()*W,y=r()*H;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+1+r()*2.6,y+(r()-.5)*.7);ctx.stroke();}ctx.restore();}
function distant(ctx,W,H,tools){const n=ink.createNoise1D(138),n2=ink.createNoise1D(76),n3=ink.createNoise1D(962),outline=[];
 for(let x=0;x<=W;x+=3){const y=54+n(x*.006+9)*42+n2(x*.023+4)*16+n3(x*.08+2)*3;
  outline.push([x,y]);}
 outline.push([W,H],[0,H]);ctx.save();ctx.globalAlpha*=atmosphereTransmission(tools,'far-ridge');ctx.clip(polygon(outline));ctx.filter='blur(.65px)';
 for(let y=0;y<H;y++){ctx.fillStyle='rgba(76,88,77,'+(.16*sat((H-y)/(H-42)))+')';ctx.fillRect(0,y,W,1);}
 const r=randomFor(909);for(let i=0;i<105;i++){const x=r()*W,y=38+r()*42;
  InkPath.paintInkStroke(ctx,[[x,y,.3],[x-1,y+13,.7],[x-7,y+38,.02]],{width:.6+r()*1.3,opacity:.055,dryness:.5,seed:900+i,ink:'#697465'});}
 ctx.restore();}
function cloudBand(ctx,pts,alpha,blur,thickness){ctx.save();ctx.filter=`blur(${blur}px)`;ctx.globalAlpha*=alpha;ctx.fillStyle=PAPER;ctx.beginPath();
 ctx.moveTo(pts[0][0],pts[0][1]);for(let i=1;i<pts.length;i++){const a=pts[i-1],b=pts[i];ctx.quadraticCurveTo(a[0],a[1],(a[0]+b[0])/2,(a[1]+b[1])/2);}
 const last=pts.at(-1);ctx.lineTo(last[0],last[1]+thickness);for(let i=pts.length-2;i>=0;i--)ctx.lineTo(pts[i][0],pts[i][1]+thickness);ctx.closePath();ctx.fill();ctx.restore();}
function clouds(ctx){
 cloudBand(ctx,[[270,100],[350,77],[421,97],[489,61],[558,81],[632,96],[715,84],[795,107],[870,92]],.55,8,29);
 cloudBand(ctx,[[843,105],[920,78],[980,96],[1040,81],[1110,107],[1190,93],[1280,110]],.72,9,40);
 cloudBand(ctx,[[20,157],[113,139],[209,164],[296,141],[392,160],[491,134],[582,151],[676,137],[790,161]],.55,11,44);
 // Distant cloud shadows are broad and very faint, with no repeated scallops.
 ctx.save();ctx.filter='blur(3px)';ctx.strokeStyle='rgba(101,109,96,.055)';ctx.lineWidth=2;
 for(const p of [[[690,172],[770,151],[850,171],[917,157],[1012,171]],[[430,202],[516,186],[604,206],[697,190],[785,206]]]){
  ctx.beginPath();ctx.moveTo(...p[0]);for(let i=1;i<p.length;i++)ctx.quadraticCurveTo(...p[i-1],(p[i-1][0]+p[i][0])/2,(p[i-1][1]+p[i][1])/2);ctx.stroke();}
 ctx.restore();}
function sun(ctx){ctx.fillStyle='#c5482d';ctx.beginPath();ctx.arc(27,27,23.6,0,Math.PI*2);ctx.fill();const r=randomFor(76);ctx.save();ctx.beginPath();ctx.arc(27,27,23.6,0,Math.PI*2);ctx.clip();
 for(let i=0;i<520;i++){ctx.fillStyle=i%3?'rgba(236,122,79,.12)':'rgba(134,35,22,.08)';ctx.fillRect(r()*54,r()*54,.5+r(),.5+r());}ctx.restore();}
function birds(ctx){const placements=[[12,25,7],[42,19,6],[67,9,7],[92,1,5],[49,44,7]];
 for(const [x,y,w]of placements){InkPath.paintInkStroke(ctx,[[x-w,y,.05],[x-w*.4,y-2,.8],[x,y+1,.5],[x+w*.5,y-1,.8],[x+w,y,.02]],{width:1.25,opacity:.8,dryness:.25,noise:.4,seed:x*191,ink:'#343a30'});}}
window.__sceneSpaceArtwork={paintObject(ctx,o,world,tools){const W=o.bounds.width,H=o.bounds.height;
 if(MASS_CONFIG[o.id])meshInk(ctx,o,tools);else if(o.id==='paper')paper(ctx,W,H);else if(o.id==='far-ridge')distant(ctx,W,H,tools);
 else if(o.id==='bank')nearBank(ctx,W,H);
 else if(o.id==='pine-a')InkNature.paintInkPine(ctx,{x:38,y:123,height:116,seed:93,lean:22});
 else if(o.id==='pine-b')InkNature.paintInkPine(ctx,{x:23,y:89,height:84,seed:552,lean:11});
 else if(o.id==='cloud-ribbons')clouds(ctx);else if(o.id==='sun')sun(ctx);else if(o.id==='birds')birds(ctx);
 if(MASS_CONFIG[o.id]||o.id==='bank'||o.id.startsWith('pine-'))finePaperGrain(ctx,W,H,7123+W);},
 paintCoverage(ctx,o,world,tools){return MASS_CONFIG[o.id]?meshCoverage(ctx,o,tools):null;}};
window.__animationExperiment={ready:true,seek(seconds){return 0;}};

const InkPath=(()=>{
/*
 * Canvas study adapted from the variable-width stroke and point-grid texture
 * ideas in {Shan, Shui}* by Lingdong Huang:
 * https://github.com/LingDong-/shan-shui-inf
 * source revision 9f754d2b2e73495db7883d4d4055a7b0903b0454
 * index.html stroke() / texture().  This is a small Canvas adaptation, not the
 * original scene generator or an assertion of physical brush simulation.
 *
 * MIT License
 * Copyright (c) 2018 Lingdong Huang
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
function randomFor(seed){let a=seed>>>0;return()=>{a+=0x6D2B79F5;let t=a;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296;};}
function noiseFor(seed){const hash=i=>{let h=(seed^Math.imul(i,0x45d9f3b))>>>0;h=Math.imul(h^(h>>>16),0x45d9f3b);return((h^(h>>>16))>>>0)/4294967296;};return t=>{const a=Math.floor(t),f=t-a,u=f*f*(3-2*f);return hash(a)*(1-u)+hash(a+1)*u;};}
function xy(p){return Array.isArray(p)?p:[p.x,p.y,p.pressure];}
function polygonPath(ctx,points){ctx.beginPath();points.forEach((p,i)=>{const [x,y]=xy(p);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.closePath();}

/** One bounded ink contact. Its outline is the footprint of a single stroke,
 * never a separately shaded rock plane. width is the full nominal width.
 * Keep structural lines mostly 1..6px; 8..24px works for sparse rough accents.
 */
function strokePath(ctx,points,options={}){
 if(!Array.isArray(points)||points.length<2)return 0;
 if(points.length>1024)throw Error('shanshui_stroke_point_limit');
 const width=options.width??2,opacity=options.opacity??.65,dry=clamp(options.dryness??.25,0,1),seed=options.seed??1;
 if(!(Number.isFinite(width)&&width>0&&width<=128&&Number.isFinite(opacity)&&opacity>=0&&opacity<=1))throw Error('shanshui_stroke_options');
 const ps=points.map(xy),noise=noiseFor(seed),amount=options.noise??.7;
 const left=[],right=[];let segments=0;
 ctx.save();ctx.fillStyle=options.ink??'#272b27';ctx.globalAlpha*=opacity;
 const flush=()=>{if(left.length<2){left.length=0;right.length=0;return;}
  ctx.beginPath();left.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));
  for(let i=right.length-1;i>=0;i--)ctx.lineTo(...right[i]);ctx.closePath();ctx.fill();
  left.length=0;right.length=0;segments++;};
 try{for(let i=0;i<ps.length;i++){
  const p=ps[i],a=ps[Math.max(0,i-1)],b=ps[Math.min(ps.length-1,i+1)],dx=b[0]-a[0],dy=b[1]-a[1],len=Math.hypot(dx,dy)||1,t=i/(ps.length-1);
  const envelope=options.taper===false?1:Math.pow(Math.max(0,Math.sin(Math.PI*(.015+t*.97))),.62);
  const pressure=Number.isFinite(p[2])?clamp(p[2],0,1):envelope;
  const contact=noise(i*.23+3.9),rough=noise(i*.72+31);
  // Only selected dry contacts break. Wet strokes remain coherent bodies.
  if(dry>.43&&contact<(dry-.35)*.38&&i>1&&i<ps.length-2){flush();continue;}
  const half=width*.5*pressure*((1-amount)+amount*(.34+.86*rough));
  const center=(noise(i*.19+70)-.5)*width*.12;
  const nx=-dy/len,ny=dx/len;
  left.push([p[0]+nx*(half+center),p[1]+ny*(half+center)]);
  right.push([p[0]-nx*(half-center),p[1]-ny*(half-center)]);
 }flush();}finally{ctx.restore();}return segments;
}

/** grid is an array of adjacent downhill guide paths, all with the same number
 * of stations. Each guide runs from top to foot. Author these from the actual
 * silhouette/shoulders/ridges: the grid organizes strokes, it is not drawn.
 */
function paintTexture(ctx,grid,options={}){
 if(!Array.isArray(grid)||grid.length<2||grid.length>128)throw Error('shanshui_texture_grid');
 const stations=grid[0].length;if(stations<3||stations>256||grid.some(g=>g.length!==stations))throw Error('shanshui_texture_stations');
 const count=options.count??300;if(!Number.isInteger(count)||count<0||count>1600)throw Error('shanshui_texture_count');
 const random=randomFor(options.seed??1),noise=noiseFor((options.seed??1)^0x9e3779b9),minWidth=options.minWidth??.8,maxWidth=options.maxWidth??4.6;
 let painted=0;
 for(let i=0;i<count;i++){
  const u=random()*(grid.length-1),strip=Math.min(grid.length-2,Math.floor(u)),f=u-strip;
  const begin=Math.floor(Math.pow(random(),1.48)*stations*.74);
  const length=Math.max(3,Math.floor(stations*(.12+random()*.64)));
  const end=Math.min(stations-1,begin+length),points=[];
  const jitter=(options.jitter??1.3)*(.45+random()*.75),phase=random()*40;
  for(let j=begin;j<=end;j++){
   const a=xy(grid[strip][j]),b=xy(grid[strip+1][j]);
   const x=a[0]*(1-f)+b[0]*f,y=a[1]*(1-f)+b[1]*f;
   points.push([x+(noise(j*.19+phase)-.5)*jitter,y+(noise(j*.29+phase+99)-.5)*jitter*.35]);
  }
  const width=minWidth+(maxWidth-minWidth)*Math.pow(random(),1.7);
  const strength=(options.opacity??.52)*(.46+random()*.54);
  painted+=strokePath(ctx,points,{width,opacity:strength,dryness:options.dryness??.42,noise:.78,seed:(options.seed??1)+i*131,ink:options.ink??'#272b27'});
 }
 return painted;
}

/** Opaque paper/silhouette coverage plus organized longitudinal ink contacts.
 * No normal lighting, face gradient or large semitransparent plane is added.
 * Call within the normal object-local cache context; the function preserves it.
 */
function paintMountain(ctx,{outline,grid,seed=1,paper='#eee9dc',ink='#272b27',count=420,minWidth=.8,maxWidth=5.2,opacity=.66,dryness=.42,edge=true}={}){
 ctx.save();try{
  polygonPath(ctx,outline);ctx.clip();ctx.fillStyle=paper;ctx.fill();
  const painted=paintTexture(ctx,grid,{seed,ink,count,minWidth,maxWidth,opacity,dryness,jitter:1.4});
  if(edge)for(let i=0;i<outline.length-2;i+=3){
   if(i%4===0)continue;
   strokePath(ctx,outline.slice(i,i+4),{width:1.1,opacity:.38,dryness:.42,ink,seed:seed+i*23,taper:false});
  }
  return {contacts:painted,scope:'authored_point_grid_ink_contacts_not_physical_light'};
 }finally{ctx.restore();}
}

// Preferred inline-study names. points may be [x,y] / [x,y,pressure] or
// {x,y,pressure}; pressure is in 0..1, not a local-surface height coordinate.
const paintInkStroke=strokePath;
const paintGridTexture=paintTexture;

return {paintInkStroke,paintGridTexture};})();

const InkNature=(()=>{
// Seeded, object-local ink studies. No gradients, bitmap assets or Canvas allocation.
// Pigment is deposited as many overlapping, ragged contacts along authored paths.
function randomFor(seed){let a=seed>>>0;return()=>{a+=0x6D2B79F5;let t=a;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296;};}
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
function measure(points){const d=[0];for(let i=1;i<points.length;i++)d.push(d.at(-1)+Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]));return d;}
function pointAt(points,distances,d){let i=1;while(i<points.length-1&&distances[i]<d)i++;const a=points[i-1],b=points[i],n=Math.max(.001,distances[i]-distances[i-1]),t=clamp((d-distances[i-1])/n,0,1);return{x:a[0]+(b[0]-a[0])*t,y:a[1]+(b[1]-a[1])*t,nx:-(b[1]-a[1])/n,ny:(b[0]-a[0])/n,p:(a[2]??1)+((b[2]??1)-(a[2]??1))*t};}
function hash(a,b,seed){let h=(Math.imul(a|0,374761393)^Math.imul(b|0,668265263)^seed)>>>0;h=Math.imul(h^(h>>>13),1274126177);return((h^(h>>>16))>>>0)/4294967296;}
function noise(x,y,seed){const ix=Math.floor(x),iy=Math.floor(y),a=x-ix,b=y-iy,u=a*a*(3-2*a),v=b*b*(3-2*b);return(hash(ix,iy,seed)*(1-u)+hash(ix+1,iy,seed)*u)*(1-v)+(hash(ix,iy+1,seed)*(1-u)+hash(ix+1,iy+1,seed)*u)*v;}

function contact(ctx,points,width,alpha,dryness,seed,color='#242923'){
 const rand=randomFor(seed),distances=measure(points),length=distances.at(-1);if(length<.1)return;
 ctx.save();const inherited=ctx.globalAlpha;ctx.fillStyle=color;
 // The fine contacts cohere into one belly; correlated gaps appear only in
 // selected dry groups and follow the route rather than becoming random snow.
 const spacing=width>12?.82:Math.max(.35,width*.12);
 for(let along=0;along<=length;along+=spacing){const s=pointAt(points,distances,along),u=along/length;
  const entry=clamp(along/Math.min(width*.3,length*.13),.08,1),tail=clamp((length-along)/Math.min(width*.22,length*.11),0,1);
  const half=width*.5*Math.pow(Math.max(.02,s.p),.63)*Math.sqrt(entry*tail);
  for(let across=-half;across<=half;across+=spacing){
   const a=across/Math.max(half,.001),structure=noise(along/13,across/3.8,seed),grain=rand();
   const localDry=dryness*(.65+structure*.6),edge=Math.pow(Math.abs(a),4);
   if(grain<localDry*.53+edge*.25||structure<dryness*.32)continue;
   const jitter=(rand()-.5)*spacing,radius=(.72+rand()*.68)*(1-edge*.35);
   const density=Math.min(1,spacing*spacing/(Math.PI*radius*radius*1.25*.8));
   ctx.globalAlpha=inherited*(1-Math.pow(1-alpha,density))*(.75+rand()*.25);
   ctx.beginPath();ctx.ellipse(s.x+s.nx*(across+jitter)+(rand()-.5)*.7,s.y+s.ny*(across+jitter)+(rand()-.5)*.7,
    radius*(1.15+rand()*.55),radius*.8,Math.atan2(-s.nx,s.ny),0,Math.PI*2);ctx.fill();
  }
 }
 ctx.restore();
}

function stroke(ctx,pts,width,alpha,seed,color='#222720'){
 if(pts.length>3){const out=[];
  for(let i=0;i<pts.length-1;i++)for(let j=0;j<8;j++){const t=j/8,a=pts[Math.max(0,i-1)],b=pts[i],c=pts[i+1],d=pts[Math.min(pts.length-1,i+2)];
   out.push([0,1].map(k=>.5*((2*b[k])+(-a[k]+c[k])*t+(2*a[k]-5*b[k]+4*c[k]-d[k])*t*t+(-a[k]+3*b[k]-3*c[k]+d[k])*t*t*t)).concat([(b[2]??1)*(1-t)+(c[2]??1)*t]));}
  out.push(pts.at(-1));pts=out;}

 // Tight small lines retain a complete body. Their contour is subtly uneven,
 // avoiding the uniformly fuzzy hairs of the earlier wide-fiber experiment.
 const r=randomFor(seed),d=measure(pts),length=d.at(-1),left=[],right=[];
 for(let along=0;along<=length+1;along+=Math.max(.65,width*.22)){
  const s=pointAt(pts,d,Math.min(along,length)),t=clamp(along/length,0,1),w=width*.5*Math.pow(Math.max(.015,s.p),.68)*(.9+r()*.2);
  left.push([s.x+s.nx*w,s.y+s.ny*w]);right.push([s.x-s.nx*w,s.y-s.ny*w]);
 }
 ctx.save();ctx.globalAlpha*=alpha;ctx.fillStyle=color;ctx.beginPath();
 [...left,...right.reverse()].forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.closePath();ctx.fill();ctx.restore();
}

// Authored nature refinement of the existing inline study. The containing
// scene retains its {Shan, Shui}* attribution and complete MIT notice above.
// Contact and stroke are retained from the existing inline nature study.
function curvePoints(points,steps=7){const out=[];
 for(let i=0;i<points.length-1;i++)for(let j=0;j<steps;j++){
  const t=j/steps,a=points[Math.max(0,i-1)],b=points[i],c=points[i+1],d=points[Math.min(points.length-1,i+2)];
  out.push([0,1].map(k=>.5*((2*b[k])+(-a[k]+c[k])*t+(2*a[k]-5*b[k]+4*c[k]-d[k])*t*t+(-a[k]+3*b[k]-3*c[k]+d[k])*t*t*t))
   .concat([(b[2]??1)*(1-t)+(c[2]??1)*t]));
 }out.push(points.at(-1));return out;
}

function paintInkPine(ctx,{x=0,y=0,height=120,seed=1,lean=18}={}){
 const r=randomFor(seed),h=height,s=h/120;
 const knots=[[x,y,1],[x+lean*.11-6*s,y-h*.20,.91],[x+lean*.48-7*s,y-h*.42,.73],
  [x+lean*.49+4*s,y-h*.64,.52],[x+lean*.84+1*s,y-h*.82,.29],[x+lean,y-h,.025]];
 const trunk=curvePoints(knots,9);
 // Limb attachment is sampled from the same curved route that is painted.
 const axisAt=t=>{const target=y-h*t;let k=1;while(k<trunk.length-1&&trunk[k][1]>target)k++;
  const a=trunk[k-1],b=trunk[k],u=clamp((target-a[1])/(b[1]-a[1]),0,1);return{x:a[0]+(b[0]-a[0])*u,y:target};};
 const boughs=[
  {t:.245,dir:-1,span:28,up:1,tufts:[.48,.88]},
  {t:.418,dir:1,span:44,up:12,tufts:[.32,.65,.93]},
  {t:.603,dir:-1,span:27,up:7,tufts:[.52,.91]},
  {t:.765,dir:1,span:24,up:6,tufts:[.48,.88]},
  {t:.902,dir:-1,span:15,up:4,tufts:[.71]}];
 const branches=[],clusters=[];
 for(let j=0;j<boughs.length;j++){
  const b=boughs[j],a=axisAt(b.t),ex=a.x+b.dir*b.span*s,ey=a.y-b.up*s;
  const elbowX=a.x+b.dir*b.span*.53*s,elbowY=a.y+(2+r()*3)*s;
  const route=[[a.x,a.y,.80],[elbowX,elbowY,.59],[ex,ey,.035]];
  branches.push({route,width:(2.3-j*.22)*s,id:j});
  b.tufts.forEach((station,c)=>{
   const u=clamp(station+(r()-.5)*.085,.2,.98);
   // The tuft stem reaches the actual limb rather than floating beside it.
   const bx=u<.53?a.x+(elbowX-a.x)*(u/.53):elbowX+(ex-elbowX)*((u-.53)/.47);
   const by=u<.53?a.y+(elbowY-a.y)*(u/.53):elbowY+(ey-elbowY)*((u-.53)/.47);
   const tx=bx+b.dir*(3.4+r()*4.5)*s,ty=by-(2.5+r()*3.4)*s;
   clusters.push({bx,by,tx,ty,dir:b.dir,j,c,id:seed+j*607+c*109,
    size:(.77+r()*.43)*(j>2?.80:1),tilt:(r()-.5)*.58});
  });
 }
 function foliage(q,front){
  const local=randomFor(q.id+(front?1901:937));
  const radius=(6.6+local()*2.0)*s*q.size*1.55,ry=radius*(.52+local()*.20);
  const ox=Math.max(radius+1,q.tx+(front?(local()-.5)*1.4:-q.dir*(1.0+local()*1.8))*s);
  const oy=q.ty+(front?.35:-(1.5+local()*1.6))*s;
  const color=front?'#1b241a':'#6b7665',alpha=front?.86:.37;
  // A few unequal bellies join the needles, with the near underside darker.
  // These remain broken compact marks, not a filled circular leaf sticker.
  const lobeCount=front?5:3;
  for(let l=0;l<lobeCount;l++){
   const cx=ox+(l===0?-.29:l===1?.24:l===2?.04:l===3?-.08:.31)*radius+(local()-.5)*radius*.19;
   const cy=oy+(l===2?.22:l===3?-.31:l===4?.12:-.06)*ry+(local()-.5)*ry*.26;
   const angle=(local()-.5)*1.8,dx=Math.cos(angle)*radius*.29,dy=Math.sin(angle)*ry*.40;
   stroke(ctx,[[cx-dx*.78,cy-dy*.65,.28],[cx,cy-ry*.10,.92],
    [cx+dx,cy+dy,.025]],
    (front?4.1+local()*1.6:3.3+local())*s*q.size,front?.74:.34,q.id+l*131+(front?41:7),color);
  }
  const count=front?27+Math.floor(local()*11):15+Math.floor(local()*6);
  for(let k=0;k<count;k++){
   // Stratified but jittered angles span left/up/right and a few drooping
   // needles. Their starting cores also move, avoiding a repeated fan stamp.
   const a=-3.07+((k+local())/count)*3.66+q.tilt+(front?.08:-.08);
   const reach=.56+Math.pow(local(),.67)*.48;
   const lobe=local()<.5?-.22:.19;
   const sx=ox+radius*lobe+(local()-.5)*radius*.30;
   const sy=oy+(local()-.5)*ry*.40;
   const ex=ox+Math.cos(a)*radius*reach,ey=oy+Math.sin(a)*ry*reach;
   const bend=(local()-.5)*1.3*s;
   stroke(ctx,[[sx,sy,.40+local()*.27],[(sx+ex)*.5+bend,(sy+ey)*.5-bend*.3,.50+local()*.22],
    [ex,ey,.015]],(.43+Math.pow(local(),1.3)*.71)*s*q.size,
    alpha*(.66+local()*.34),q.id+(front?2000:1000)+k*17,color);
  }
  if(front&&q.c%2===0){
   stroke(ctx,[[ox-radius*.21,oy+ry*.20,.45],[ox+radius*.08,oy+ry*.30,.85],
    [ox+radius*.34,oy+ry*.12,.025]],3.8*s*q.size,.85,q.id+4403,'#172116');
  }
 }
 ctx.save();try{
  clusters.forEach(q=>foliage(q,false));
  stroke(ctx,trunk,5.2*s,.93,seed);
  contact(ctx,trunk.slice(0,Math.floor(trunk.length*.82)),2.0*s,.42,.66,seed+11,'#4b4d3d');
  stroke(ctx,[[x,y-3*s,.9],[x-9*s,y+2*s,.5],[x-17*s,y+4*s,.02]],3*s,.88,seed+2);
  stroke(ctx,[[x+1*s,y-3*s,.7],[x+9*s,y+1*s,.45],[x+15*s,y+3*s,.02]],2.5*s,.85,seed+3);
  branches.forEach(b=>stroke(ctx,b.route,b.width,.88,seed+b.id*29));
  clusters.forEach(q=>stroke(ctx,[[q.bx,q.by,.44],[q.tx,q.ty,.02]],.95*s,.72,q.id+73));
  clusters.forEach(q=>foliage(q,true));
 }finally{ctx.restore();}
}

const SHORE=[[0,24],[34,15],[67,13],[103,21],[128,25],[170,29],[218,36],[258,45],[303,60],
 [348,67],[388,80],[436,93],[481,110],[527,128],[575,146],[616,169],[666,197],[710,224]];
function shoreAt(x){let i=1;while(i<SHORE.length-1&&SHORE[i][0]<x)i++;const a=SHORE[i-1],b=SHORE[i],t=clamp((x-a[0])/(b[0]-a[0]),0,1);return a[1]+(b[1]-a[1])*t;}

function paintDryBank(ctx,{width=710,height=210,seed=812}={}){
 const sx=width/710,sy=height/210,r=randomFor(seed);ctx.save();ctx.scale(sx,sy);
 const outline=[];for(let x=0;x<=710;x+=4){const anchored=Math.min(Math.abs(x-128),Math.abs(x-258));
  const rough=(noise(x/12,0,seed)-.5)*8*Math.min(1,anchored/6);outline.push([x,shoreAt(x)+rough]);}
 ctx.beginPath();outline.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.lineTo(710,230);ctx.lineTo(0,230);ctx.closePath();ctx.clip();
 // One broad upper belly joins the entire bank immediately below its crest.
 // It carries the two trees continuously instead of leaving isolated planks.
 // All base deposits use the density-corrected particle brush, never a filled
 // geometric wet polygon. Overlapping belly routes keep middle ink channels.
 const upper=[];
 for(let x=-24;x<=734;x+=10)upper.push([x,shoreAt(clamp(x,0,710))+21+(noise(x/75,2,seed)-.5)*5,
  .82+noise(x/83,1,seed+19)*.15]);
 contact(ctx,upper,77,.72,.12,seed+1100,'#242c22');
 const masses=[
  {p:[[-42,85,.91],[71,92,.98],[192,135,.94],[323,187,.76],[453,237,.30]],w:124,a:.68,d:.13},
  {p:[[-44,171,.94],[64,164,.98],[186,194,.92],[325,240,.47]],w:128,a:.70,d:.16},
  {p:[[169,127,.75],[274,135,.97],[387,174,.93],[496,224,.48]],w:104,a:.65,d:.14},
  {p:[[389,157,.63],[480,168,.94],[584,206,.88],[706,244,.25]],w:80,a:.65,d:.17},
  {p:[[-32,226,.91],[102,217,.98],[239,248,.62]],w:126,a:.67,d:.15}];
 masses.forEach((u,i)=>contact(ctx,curvePoints(u.p,5),u.w,u.a,u.d,seed+1600+i*239,'#252d23'));
 // A few slanting/sideways rubs describe ledges inside the joined ink body.
 // Their uneven extent replaces the repeated upright rock-strip rhythm.
 const ledges=[
  {p:[[7,51,.37],[64,61,.92],[128,91,.65],[169,111,.025]],w:33,a:.54,d:.56},
  {p:[[126,105,.34],[178,118,.87],[242,141,.61],[280,174,.025]],w:36,a:.51,d:.62},
  {p:[[-7,132,.36],[65,144,.92],[132,174,.55],[180,189,.025]],w:40,a:.53,d:.58},
  {p:[[306,157,.32],[363,173,.93],[426,193,.69],[468,216,.025]],w:30,a:.54,d:.60},
  {p:[[455,163,.29],[506,177,.89],[569,202,.08]],w:19,a:.44,d:.67}];
 ledges.forEach((u,i)=>contact(ctx,curvePoints(u.p,5),u.w,u.a,u.d,seed+3100+i*271,'#182317'));
 // Rough crest deposits overlap the upper belly but do not form a separate
 // paper-backed rail. The tree root contacts at x128/258 stay supported.
 for(const [i,a,b] of [[0,0,160],[1,177,386],[2,401,705]]){
  const crest=[];for(let x=a;x<=b;x+=7)crest.push([x,shoreAt(x)+7+(noise(x/24,3,seed)-.5)*3,
   .51+noise(x/39,1,seed+37)*.42]);
  contact(ctx,crest,i===1?18:15,.73,.49,seed+4300+i*193,'#1c271a');
 }
 // Selected short fissures and dark anchors do not repeat across every unit.
 const anchors=[[[47,75,.35],[77,91,.88],[112,101,.025]],
  [[223,148,.32],[254,165,.95],[293,175,.025]],
  [[361,183,.30],[399,196,.88],[439,215,.02]]];
 anchors.forEach((p,i)=>contact(ctx,curvePoints(p,5),9+i*2,.81,.47,seed+5500+i*179,'#172116'));
 // A few broad and narrow grey-paper channels are deposited within the bank,
 // leaving connected dark material around them rather than regular white holes.
 const channels=[
  {p:[[47,102,.22],[83,110,.85],[135,129,.10]],w:16,a:.43},
  {p:[[178,174,.15],[217,166,.73],[253,182,.03]],w:13,a:.38},
  {p:[[322,182,.20],[350,186,.80],[394,207,.02]],w:15,a:.42}];
 channels.forEach((u,i)=>contact(ctx,curvePoints(u.p,5),u.w,u.a,.66,seed+6400+i*191,'#d7dac7'));
 for(const group of [{x:38,y:105,n:4},{x:174,y:142,n:4},{x:317,y:178,n:3},{x:484,y:184,n:3}]){
  for(let k=0;k<group.n;k++){
   const x=group.x+(r()-.5)*27,y=group.y+(r()-.5)*16,len=11+r()*26;
   contact(ctx,[[x,y,.28],[x+len*.52,y+4+(r()-.5)*4,.88],[x+len,y+7+r()*6,.025]],
    2.0+r()*4.2,.54,.73,seed+7400+group.x*13+k*41,'#1a2518');
  }
 }
 ctx.restore();
 // Isolated grass tufts interrupt the shoreline without a uniformly hairy edge.
 for(const group of [{x:59,n:5},{x:205,n:4},{x:451,n:3}]){
  for(let k=0;k<group.n;k++){
   const xx=(group.x+(r()-.5)*20)*sx,yy=shoreAt(xx/sx)*sy,lean=(r()-.5)*13*sx,up=(3+r()*10)*sy;
   stroke(ctx,[[xx,yy+1,.62],[xx+lean*.68,yy-up*.68,.35],[xx+lean,yy-up,.015]],
    .53+r()*.34,.75,seed+8700+group.x*11+k*47);
  }
 }
}

return {paintInkPine,paintDryBank,paintContact:contact};})();
