import * as ink from './assets/ink-landscape.mjs';
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
const SUN_DIRECTION=[.55,.52,-.72].map(v=>v/Math.hypot(.55,.52,-.72)); // upper right, behind the mountain
function inkZone(f,s){const [x,h,z]=s.position,n=s.normal;
 const light=sat((n.reduce((a,v,i)=>a+v*SUN_DIRECTION[i],0)+.12)/.70),shadow=1-light;
 const step=.32,concavity=(authoredHeight(x-step,z,f.cfg)+authoredHeight(x+step,z,f.cfg)+
  authoredHeight(x,z-step,f.cfg)+authoredHeight(x,z+step,f.cfg))*.25-authoredHeight(x,z,f.cfg);
 const valley=sat(concavity*4),patch=sat(.5+.36*Math.sin(x*1.13+z*.72)+.23*Math.sin(z*2.01-x*.57));
 return{light,shadow,valley,patch,strength:sat(.08+shadow*.71+valley*.28)};
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
function contactSeeds(f,seed,limit,spacing){const r=randomFor(seed),cells=new Map(),points=[],cell=6;
 for(let attempt=0;attempt<limit*45&&points.length<limit;attempt++){
  const x=r()*f.W,y=r()*f.H,s=sampleField(f,x,y);if(!s)continue;const zone=inkZone(f,s);
  // Empty faces remain deliberate; dark contacts gather on selected shadow folds.
  if(r()>(.07+zone.strength*.78)*(.18+zone.patch*.82))continue;
  const radius=spacing*(.65+zone.light*1.45)*(zone.valley>.12?.72:1),cx=Math.floor(x/cell),cy=Math.floor(y/cell);
  let blocked=false;const reach=Math.ceil((spacing*2.2)/cell);
  for(let j=-reach;j<=reach&&!blocked;j++)for(let i=-reach;i<=reach&&!blocked;i++){
   for(const q of cells.get((cx+i)+','+(cy+j))??[])if(Math.hypot(x-q.x,y-q.y)<Math.max(radius,q.radius)){blocked=true;break;}}
  if(blocked)continue;const point={x,y,s,zone,radius,seed:seed+attempt*137};points.push(point);
  const key=cx+','+cy;if(!cells.has(key))cells.set(key,[]);cells.get(key).push(point);
 }return points;
}
function meshInk(ctx,o,tools){const f=fieldFor(o,tools),far=o.id==='right-mass',scale=Math.max(.57,o.bounds.width/570),r=randomFor(f.cfg.seed);
 if(ART_MODE==='clay'){
  for(let y=0;y<f.H;y++)for(let x=0;x<f.W;x++){const i=y*f.W+x;if(!f.alpha[i])continue;
   const n=i*3,t=.12+.56*(1-Math.max(0,f.normals[n]*-.3+f.normals[n+1]*.82+f.normals[n+2]*.45)),c=Math.round(238*(1-t)+40*t);
   ctx.fillStyle=`rgb(${c},${c},${c})`;ctx.fillRect(x,y,1,1);}return;
 }
 ctx.save();ctx.globalAlpha*=far?.56:1;const color=far?'#66705f':'#252c24';
 // Wet ink occurs as a few local contacts within dark groups, not a full-body
 // grey image. Its footprint and density come from a brush, not Lambert shading.
 for(const p of contactSeeds(f,f.cfg.seed+171,far?16:48,11*scale)){
  if(p.zone.shadow<.4)continue;const pts=traceContact(f,p.x,p.y,(22+r()*40)*scale,(r()-.5)*.3,p.seed);
  if(pts.length<4)continue;ctx.save();ctx.filter=`blur(${.7*scale}px)`;
  InkPath.paintInkStroke(ctx,pts,{seed:p.seed,width:(12+r()*20)*scale,opacity:.075+p.zone.shadow*.065,dryness:.16,noise:.48,ink:color});ctx.restore();
 }
 // Coherent broad dry contacts unite the rock walls. They are far fewer than
 // the earlier 420-contact carpet and carry their own pressing and lifting.
 for(const p of contactSeeds(f,f.cfg.seed+73,far?50:185,6.2*scale)){
  const z=p.zone;if(z.light>.78&&r()<.67)continue;
  const pts=traceContact(f,p.x,p.y,(32+r()*62)*scale,(r()-.5)*.35,p.seed);if(pts.length<5)continue;
  InkPath.paintInkStroke(ctx,pts,{seed:p.seed,width:(7+r()*12)*scale,opacity:.24+z.strength*.40,dryness:.40+r()*.13,noise:.87,ink:color});
 }
 // Grouped longitudinal folds retain the original rock-wall language, leaving
 // quiet regions between groups rather than replacing it with disconnected dots.
 const folds=contactSeeds(f,f.cfg.seed+310,far?150:540,4.1*scale);
 for(const p of folds){const z=p.zone;if(z.light>.72&&r()<.57)continue;
  const cross=r()<.075&&z.shadow>.50,angle=cross?(r()<.5?-1:1)*(.95+r()*.25):(r()-.5)*.40;
  const pts=traceContact(f,p.x,p.y,(cross?9+r()*17:26+r()*72)*scale,angle,p.seed);if(pts.length<4)continue;
  InkPath.paintInkStroke(ctx,pts,{seed:p.seed,width:(cross?2+r()*3:.85+r()*3.6)*scale,
   opacity:.23+z.strength*.40,dryness:.64+r()*.17,noise:.91,ink:color});
 }
 // Sparse sideways rubs link only selected saddles. They never form a repeated
 // horizontal lattice over all upward-facing triangles.
 for(const p of contactSeeds(f,f.cfg.seed+911,far?28:110,8*scale)){
  if(p.s.normal[1]<.47||r()>.70)continue;
  const pts=traceContact(f,p.x,p.y,(9+r()*17)*scale,(r()<.5?-1:1)*(1.00+r()*.3),p.seed);
  if(pts.length>2)InkNature.paintContact(ctx,pts,(10+r()*13)*scale,.21+p.zone.shadow*.22,.38,p.seed,color);
 }
 // Pressing and lifting ridge fragments: visibility is still computed by the
 // original depth buffer; no triangle edges or complete outline are drawn.
 for(let p=0;p<f.cfg.ridges.length;p++)for(let i=1;i<f.cfg.ridges[p].length;i++){
  const a=f.cfg.ridges[p][i-1],b=f.cfg.ridges[p][i];
  for(let g=0;g<3;g++){
   if(r()<.38)continue;const start=g===0?(a[2]>b[2]?.01:.79):r()*.80,points=[];
   for(let k=0;k<8;k++){const t=start+k*.025;if(t>1)break;const x=a[0]*(1-t)+b[0]*t,z=a[1]*(1-t)+b[1]*t,h=authoredHeight(x,z,f.cfg);
    const q=tools.geometry.projectPoint(f.projection,[x,h+.03,z]);if(!q.visible)break;
    if(!sampleField(f,q.x,q.y))break;points.push([q.x,q.y,(.76-k*.09)*(.75+r()*.25)]);}
   if(points.length>2)InkPath.paintInkStroke(ctx,points,{seed:f.cfg.seed+p*701+i*173+g*31,width:(1.0+r()*1.6)*scale,opacity:.48,dryness:.76,noise:.9,ink:color});
  }
 }
 // Dark accents are concentrated on a small subset of shadow creases.
 for(const p of folds){if(p.zone.valley<.055||p.zone.light>.55||r()>.12)continue;
  const d=2+r()*4;InkNature.paintContact(ctx,[[p.x,p.y,.8],[p.x+d*.35,p.y+d,.04]],(3+r()*4)*scale,.84,.37,p.seed+773,color);}
 ctx.restore();
}

function meshCoverage(ctx,o,tools){const f=fieldFor(o,tools);
 // Quantized runs carry the projected silhouette and local cloud alpha while
 // respecting the host's current object transform/clip. No final-canvas erasure.
 for(let y=0;y<f.H;y++){let start=0,last=Math.round(f.alpha[y*f.W]/17);
  for(let x=1;x<=f.W;x++){const a=x===f.W?-1:Math.round(f.alpha[y*f.W+x]/17);if(a===last)continue;
   if(last>0){ctx.fillStyle=`rgba(0,0,0,${last/15})`;ctx.fillRect(start,y,x-start,1);}start=x;last=a;}}
 return{substrate:PAPER_RGB};}

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
function distant(ctx,W,H){const n=ink.createNoise1D(138),n2=ink.createNoise1D(76),n3=ink.createNoise1D(962),outline=[];
 for(let x=0;x<=W;x+=3){const y=54+n(x*.006+9)*42+n2(x*.023+4)*16+n3(x*.08+2)*3;
  outline.push([x,y]);}
 outline.push([W,H],[0,H]);ctx.save();ctx.clip(polygon(outline));ctx.filter='blur(.65px)';
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
 if(MASS_CONFIG[o.id])meshInk(ctx,o,tools);else if(o.id==='paper')paper(ctx,W,H);else if(o.id==='far-ridge')distant(ctx,W,H);
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

function paintInkPine(ctx,{x=0,y=0,height=120,seed=1,lean=18}={}){
 const r=randomFor(seed),h=height,s=h/120;
 const trunk=[[x,y,1],[x+lean*.11-6*s,y-h*.20,.91],[x+lean*.48-7*s,y-h*.42,.73],
  [x+lean*.49+4*s,y-h*.64,.52],[x+lean*.84+1*s,y-h*.82,.29],[x+lean,y-h,.025]];
 const trunkX=t=>{let k=1;while(k<trunk.length-1&&(y-trunk[k][1])/h<t)k++;const a=trunk[k-1],b=trunk[k],v=(y-h*t-a[1])/(b[1]-a[1]);return a[0]+(b[0]-a[0])*v;};
 // Branch endpoints, roots and crown envelope stay with the established tree.
 const boughs=[{t:.26,dir:-1,span:35,up:4},{t:.43,dir:1,span:43,up:13},
  {t:.60,dir:-1,span:31,up:5},{t:.72,dir:1,span:26,up:10},{t:.87,dir:-1,span:19,up:3}];
 const branches=[],clusters=[];
 for(let j=0;j<boughs.length;j++){
  const b=boughs[j],cx=trunkX(b.t),cy=y-h*b.t;
  const ex=cx+b.dir*b.span*s,ey=cy-b.up*s;
  branches.push({points:[[cx,cy,.78],[cx+b.dir*b.span*.44*s,cy+(r()-.65)*5*s,.59],[ex,ey,.025]],width:(2.3-j*.2)*s,id:j});
  const count=3+(j<2?1:0);
  for(let c=0;c<count;c++){
   const local=randomFor(seed+j*607+c*79+41),u=.23+c*.22+(local()-.5)*.10;
   const bx=cx+(ex-cx)*u,by=cy+(ey-cy)*u-2*s;
   const tx=bx+b.dir*(6+local()*5)*s,ty=by-(3+local()*3.5)*s;
   clusters.push({bx,by,tx,ty,dir:b.dir,j,c,id:seed+j*607+c*79});
  }
 }
 function foliage(q,front){
  const local=randomFor(q.id+(front?1901:937)),scale=q.j>2?.79:1;
  // Rear tufts sit slightly higher/sideways. Their grey remains visible through
  // a few irregular openings; the darker near core and needles overlap them.
  const ox=q.tx+(front?(local()-.5)*2.6:-q.dir*(1.5+local()*2.2))*s;
  const oy=q.ty+(front?(local()-.5)*1.8:-(2+local()*2))*s;
  const base=-1.58+q.dir*(.12+local()*.24),bias=local()>.45?.63:.36;
  const color=front?'#20271f':'#717768',alpha=front?.82:.40;
  // Unequal, offset short contacts bind the tuft; no symmetric two-fan stamp.
  const reach=(front?7.4:6.2)*s*scale;
  contact(ctx,[[ox-reach*.58,oy+1.1*s,.42],[ox-reach*.05,oy-1.3*s,.95],
   [ox+reach*.43,oy-.15*s,.70],[ox+reach*.72,oy+.6*s,.04]],
   (front?4.8:3.8)*s,alpha,front?.28:.39,q.id+(front?53:11),color);
  if(front&&q.c%2===0)contact(ctx,[[ox-2*s,oy-.6*s,.35],[ox+.6*s,oy-2.9*s,.8],
   [ox+3.5*s,oy-1.6*s,.03]],2.3*s,.64,.36,q.id+124,color);
  const count=front?18+Math.floor(local()*10):10+Math.floor(local()*6);
  const gap=base+(local()-.5)*.65;
  for(let k=0;k<count;k++){
   const lobe=local()<bias?-.48:.43;
   let a=base+lobe+(local()-.5)*.78;
   // The opening is local to this tuft, not a repeated missing wedge.
   if(k%4===1&&Math.abs(a-gap)<.16)continue;
   const short=local()<.28;
   const len=(short?2.8+local()*2.2:4.5+Math.pow(local(),.8)*5.5)*s*scale;
   const sx=ox+(local()-.5)*(front?5.4:4.0)*s,sy=oy+(local()-.5)*2.7*s;
   const dx=Math.cos(a)*len,dy=Math.sin(a)*len,bend=(local()-.5)*1.8*s;
   stroke(ctx,[[sx,sy,.40+local()*.25],[sx+dx*.50+Math.sin(a)*bend,sy+dy*.52,.55+local()*.24],
    [sx+dx,sy+dy,.015]],(.45+Math.pow(local(),1.25)*.77)*s,
    front?.65+local()*.28:.32+local()*.20,q.id+(front?2000:1000)+k*17,color);
  }
 }
 ctx.save();
 try{
  // Fore/background drawing order supplies real overlap within this flat tree.
  clusters.forEach(q=>foliage(q,false));
  stroke(ctx,trunk,5.2*s,.93,seed);
  contact(ctx,trunk.slice(0,5),2.2*s,.48,.63,seed+11,'#4b4d3d');
  stroke(ctx,[[x,y-3*s,.9],[x-9*s,y+2*s,.5],[x-17*s,y+4*s,.02]],3*s,.88,seed+2);
  stroke(ctx,[[x+1*s,y-3*s,.7],[x+9*s,y+1*s,.45],[x+15*s,y+3*s,.02]],2.5*s,.85,seed+3);
  branches.forEach(b=>stroke(ctx,b.points,b.width,.91,seed+b.id*29));
  clusters.forEach(q=>stroke(ctx,[[q.bx,q.by,.5],[q.tx,q.ty,.02]],1.1*s,.82,q.id+73));
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
 // Rock planes are authored contact routes with different bearings, extent
 // and gaps. No smooth light/shade base is laid beneath these ink marks.
 const units=[
  {p:[[4,34,.8],[34,52,1],[25,105,.5],[6,139,.08]],w:53},
  {p:[[59,31,.7],[86,60,1],[63,112,.65],[39,151,.05]],w:47},
  {p:[[109,38,.85],[124,67,1],[112,121,.6],[87,173,.06]],w:45},
  {p:[[158,48,.7],[184,77,.95],[160,119,.7],[149,170,.03]],w:50},
  {p:[[227,58,.7],[243,98,.9],[221,141,.7],[202,193,.02]],w:47},
  {p:[[286,77,.8],[300,106,1],[277,156,.7],[258,207,.05]],w:55},
  {p:[[339,84,.7],[361,117,.95],[335,154,.68],[320,211,.05]],w:46},
  {p:[[397,99,.8],[419,139,.9],[398,178,.6],[390,219,.03]],w:49},
  {p:[[456,119,.7],[480,152,.9],[466,188,.6],[465,222,.03]],w:44},
  {p:[[520,146,.7],[540,172,.95],[532,214,.06]],w:47},
  {p:[[578,164,.6],[605,195,.9],[607,226,.03]],w:45}];
 // Broken cross contacts bind separate rock heads into a continuous bank.
 const bind=[[[0,84,.9],[58,103,1],[112,114,.3]],[[99,121,.7],[157,145,.9],[211,152,.05]],
  [[206,159,.7],[260,178,.95],[321,198,.1]],[[330,178,.7],[383,196,.9],[435,219,.08]]];
 const belly=[[[0,60,.95],[72,65,1],[154,88,.9],[236,121,.2]],
  [[0,132,.85],[101,156,.95],[218,184,.8],[335,220,.25]],
  [[211,122,.8],[293,144,.95],[386,175,.9],[486,217,.15]],
  [[419,177,.65],[503,209,.95],[592,239,.2]],
  [[-25,193,.95],[72,213,1],[171,240,.45]]];
 belly.forEach((p,i)=>contact(ctx,p,74+i%2*12,.53,.31,seed+7200+i*179));
 bind.forEach((p,i)=>contact(ctx,p,40,.53,.35,seed+i*109));
 // Three broken crest routes join the rock heads into one shore. Two broad
 // interruptions remain at deliberately chosen ledges, not at every unit.
 const crestRanges=[[0,154],[174,365],[379,703]];
 crestRanges.forEach(([a,b],i)=>{
  const points=[[a,shoreAt(a)+13,.55]];
  for(let x=a+9;x<b-4;x+=11)points.push([x,shoreAt(x)+13+(noise(x/18,1,seed+17)-.5)*2.6,.88]);
  points.push([b,shoreAt(b)+13,.06]);
  contact(ctx,points,29+i%2*3,.82,.36,seed+7500+i*73);
 });
 for(const [a,b] of [[0,185],[199,431],[442,705]]){
  const crest=[];for(let x=a;x<=b;x+=5)crest.push([x,shoreAt(x)+7,.55+noise(x/23,3,seed)*.4]);
  contact(ctx,crest,18,.79,.31,seed+a*31);
 }
 units.forEach((u,i)=>{
  if([1,4,8].includes(i))stroke(ctx,u.p,u.w*.44,.48,seed+i*811);
  contact(ctx,u.p,u.w,.57+r()*.14,.36+r()*.24,seed+i*193);
  const a=u.p[1],b=u.p[2];
  contact(ctx,[[a[0]-4,a[1]-8,.9],[a[0]-11,a[1]+16,.8],[b[0]-12,b[1]+18,.03]],
   13+r()*8,.74,.61,seed+i*197+7,'#1d241d');
 });
 // Short dry crests and selected fissures: generous unmarked channels stay.
 for(let i=1;i<SHORE.length-2;i+=2){const a=SHORE[i],b=SHORE[i+1];
  contact(ctx,[[a[0]-7,a[1]+2,.7],[a[0]+8,a[1]+6,.85],[b[0],b[1]+5,.08]],8+r()*7,.77,.55,seed+i*271);}
 for(let i=0;i<65;i++){
  const x=r()*660,y=shoreAt(x)+4+r()*13;
  if(r()<.35)continue;
  contact(ctx,[[x,y,.65],[x+2+r()*4,y+1+r()*3,.04]],2+r()*4,.75,.33,seed+4000+i);
 }
 // A handful of exposed-paper cracks run with selected rock planes; these
 // are local bank marks, never erasure of an already-composited landscape.
 const white=[[[54,77,.2],[49,88,.7],[41,104,.04]],[[170,139,.2],[161,157,.5],[156,170,.1]],
  [[286,153,.4],[275,170,.15]],[[398,188,.2],[386,210,.04]]];
 white.forEach((p,i)=>contact(ctx,p,10.5+i%2*4.8,.30,.61,seed+8100+i*17,'#eee9dc'));
 ctx.restore();
 // Sparse grasses break the silhouette outside its clipping region. These
 // are grouped near two ledges, not a uniformly fuzzy border.
 for(const group of [{x:78,n:6},{x:285,n:5},{x:439,n:4}]){
  for(let k=0;k<group.n;k++){const x=(group.x+(r()-.5)*18)*sx,y=shoreAt(x/sx)*sy;
   const lean=(r()-.5)*10*sx,up=(3+r()*10)*sy;
   stroke(ctx,[[x,y+1,.65],[x+lean*.7,y-up*.7,.4],[x+lean,y-up,.02]],.55+.35*r(),.77,seed+9000+group.x+k);}
 }
}

return {paintInkPine,paintDryBank,paintContact:contact};})();
