import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import test from 'node:test';
import {createMesh,createTerrainMesh,projectMesh,sampleProjection,projectPoint,paintCoverage,
 geometryResourceUsage,GEOMETRY_LIMITS,MAX_GEOMETRY_RETAINED_BYTES,MAX_GEOMETRY_WORK_BYTES,
 MAX_GEOMETRY_PIXELS,MAX_GEOMETRY_RASTER_WORK} from '../../video_workbench/motion_renderer/scene_space_geometry.mjs';

const moduleUrl=new URL('../../video_workbench/motion_renderer/scene_space_geometry.mjs',import.meta.url).href;
const near=(a,b,epsilon=1e-8)=>assert.ok(Math.abs(a-b)<=epsilon,`${a} != ${b}`);
const plane=(z=0,s=1)=>({vertices:[[-s,-s,z],[s,-s,z],[s,s,z],[-s,s,z]],triangles:[[0,1,2],[0,2,3]]});
const camera=(extra={})=>({position:[0,0,5],target:[0,0,0],viewHeight:4,...extra});
const project=(mesh,extra={})=>projectMesh(mesh,{width:100,height:100,camera:camera(),...extra});
function fresh(body){const result=spawnSync(process.execPath,['--input-type=module','-e',
 `import assert from 'node:assert/strict';import * as g from ${JSON.stringify(moduleUrl)};${body}`],{encoding:'utf8',timeout:30000});
 assert.equal(result.status,0,result.stdout+result.stderr);return result.stdout;}

test('one terrain shares a connected base across several ridges and intervening shoulders',()=>{
 const columns=41,rows=33,height=(x,z)=>1.2+2.5*Math.exp(-((x+1.8)**2+z*z)/.8)
  +3.2*Math.exp(-((x-1.6)**2+(z-.7)**2)/.9)+1.1*Math.exp(-(z*z/1.2+x*x/8));
 const heights=Array.from({length:columns*rows},(_,i)=>height(-4+(i%columns)*.2,-3+Math.floor(i/columns)*6/(rows-1)));
 const mesh=createTerrainMesh({bounds:{x:-4,z:-3,width:8,depth:6},grid:{columns,rows},heights});
 assert.equal(mesh.connected_components,1);assert.equal(mesh.vertex_count,columns*rows);
 assert.equal(mesh.triangle_count,2*(columns-1)*(rows-1));
 const projection=project(mesh,{width:160,height:120,camera:camera({position:[0,12,0],target:[0,0,0],up:[0,0,-1],viewHeight:8})});
 for(const [x,z]of [[-1.8,0],[0,0],[1.6,.7]]){
  const point=projectPoint(projection,[x,height(x,z),z]);assert.ok(point.in_view&&point.visible);
  const hit=sampleProjection(projection,point.x,point.y);assert.ok(hit.covered&&hit.position[1]>1.2);
 }
 const bridge=projectPoint(projection,[0,height(0,0),0]);assert.ok(sampleProjection(projection,bridge.x,bridge.y).position[1]>2);
});

test('orthographic projection reconstructs analytic world positions and full face normals',()=>{
 const projection=project(createMesh(plane()));
 assert.equal(projection.covered_pixels,2500);
 const hit=sampleProjection(projection,50,50);assert.equal(hit.covered,true);assert.equal(hit.coverage,1);
 near(hit.depth,5);near(hit.position[0],.02);near(hit.position[1],-.02);near(hit.position[2],0);
 assert.deepEqual(hit.normal,[0,0,1]);assert.deepEqual(hit.flow,[0,1]);assert.ok(hit.flow_valid);
 assert.equal(hit.triangle,0);
 assert.deepEqual(sampleProjection(projection,0,0),{covered:false,coverage:0,depth:null,position:null,normal:null,flow:[0,0],flow_valid:false,triangle:null});
});

test('depth buffer chooses a nearer face independent of the triangle draw order',()=>{
 const far=plane(0,1),close=plane(1,.4),vertices=far.vertices.concat(close.vertices),nearTriangles=close.triangles.map(t=>t.map(i=>i+4));
 const mesh=createMesh({vertices,triangles:far.triangles.concat(nearTriangles)});assert.equal(mesh.connected_components,2);
 const projection=project(mesh),reversed=project(createMesh({vertices,triangles:nearTriangles.concat(far.triangles)}));
 const central=sampleProjection(projection,50,50),outer=sampleProjection(projection,30,50);
 near(central.depth,4);near(central.position[2],1);assert.ok(central.triangle>=2);
 near(outer.depth,5);near(outer.position[2],0);assert.ok(outer.triangle<2);
 for(const [x,y]of [[50,50],[30,50],[70,50],[42,42],[1,1]]){
  const a=sampleProjection(projection,x,y),b=sampleProjection(reversed,x,y);
  assert.equal(a.covered,b.covered);assert.deepEqual(a.position,b.position);assert.equal(a.depth,b.depth);
 }
});

test('a reversed view changes which surface is visible without changing authored normals',()=>{
 const far=plane(0),nearPlane=plane(1,.4),mesh=createMesh({vertices:far.vertices.concat(nearPlane.vertices),triangles:far.triangles.concat(nearPlane.triangles.map(t=>t.map(i=>i+4)))});
 const front=project(mesh),back=project(mesh,{camera:camera({position:[0,0,-5]})});
 near(sampleProjection(front,50,50).position[2],1);near(sampleProjection(back,50,50).position[2],0);
 assert.deepEqual(sampleProjection(back,50,50).normal,[0,0,1]);
 assert.ok(sampleProjection(back,50,50).triangle<2);
});

test('flow follows the geometric downhill tangent and flat terrain keeps flow unknown',()=>{
 const mesh=createTerrainMesh({bounds:{x:-1,z:-1,width:2,depth:2},grid:{columns:3,rows:3},heights:Array.from({length:9},(_,i)=>(i%3)*.5)});
 const projection=project(mesh,{camera:camera({position:[0,4,5],target:[0,1,0]})});
 const hit=sampleProjection(projection,50,50);assert.ok(hit.covered&&hit.flow_valid);
 assert.ok(hit.normal[0]<-.4&&hit.normal[1]>.8);assert.ok(hit.flow[0]<-.8&&hit.flow[1]>0);
 near(Math.hypot(...hit.normal),1);near(Math.hypot(...hit.flow),1);
 const scaled=sampleProjection(projection,110,70,{bounds:{x:10,y:20,width:200,height:100}});
 assert.deepEqual(scaled.position,hit.position);near(scaled.flow[0]/scaled.flow[1],hit.flow[0]/hit.flow[1]*2);
 const flat=createTerrainMesh({bounds:{x:-1,z:-1,width:2,depth:2},grid:{columns:2,rows:2},heights:[0,0,0,0]});
 const flatView=project(flat,{camera:camera({position:[0,5,0],up:[0,0,-1]})});
 const top=sampleProjection(flatView,50,50);assert.deepEqual(top.normal,[0,1,0]);assert.deepEqual(top.flow,[0,0]);assert.equal(top.flow_valid,false);
});

test('projected points report actual occlusion and map consistently into local painting bounds',()=>{
 const far=plane(0),nearPlane=plane(1,.4),mesh=createMesh({vertices:far.vertices.concat(nearPlane.vertices),triangles:far.triangles.concat(nearPlane.triangles.map(t=>t.map(i=>i+4)))});
 const projection=project(mesh);
 assert.equal(projectPoint(projection,[0,0,0],{depthTolerance:0}).visible,false);
 assert.equal(projectPoint(projection,[0,0,1],{depthTolerance:0}).visible,true);
 assert.equal(projectPoint(projection,[8,0,0]).in_view,false);
 const point=projectPoint(projection,[0,0,1],{bounds:{x:10,y:20,width:200,height:100}});
 near(point.x,110);near(point.y,70);near(point.depth,4);assert.ok(point.visible);
 const sample=sampleProjection(projection,point.x,point.y,{bounds:{x:10,y:20,width:200,height:100}});near(sample.position[2],1);
});

test('coverage draws only silhouette runs in local coordinates and restores caller paint state',()=>{
 const projection=project(createMesh(plane()),{width:20,height:20}),calls=[],stack=[];let fills=0;
 const ctx={fillStyle:'red',globalAlpha:.37,filter:'blur(1px)',globalCompositeOperation:'source-over',
  save(){stack.push({fillStyle:this.fillStyle,globalAlpha:this.globalAlpha,filter:this.filter,globalCompositeOperation:this.globalCompositeOperation});},
  restore(){Object.assign(this,stack.pop());},beginPath(){calls.length=0;},rect(x,y,w,h){calls.push([x,y,w,h,this.fillStyle,this.globalAlpha]);},fill(){fills++;}};
 const bounds={x:11,y:17,width:100,height:60},result=paintCoverage(ctx,projection,{bounds});
 assert.equal(ctx.fillStyle,'red');assert.equal(ctx.globalAlpha,.37);assert.equal(ctx.filter,'blur(1px)');assert.equal(ctx.globalCompositeOperation,'source-over');
 assert.equal(result.runs,10);assert.equal(result.covered_pixels,100);
 assert.equal(fills,1,'silhouette rows must be one fill to avoid cache-density alpha seams');
 for(let row=0;row<20;row++)for(let col=0;col<20;col++){
  const x=bounds.x+(col+.5)*5,y=bounds.y+(row+.5)*3;
  const covered=calls.some(([xx,yy,w,h])=>x>=xx&&x<xx+w&&y>=yy&&y<yy+h);
  assert.equal(covered,sampleProjection(projection,col+.5,row+.5).covered);
 }
 assert.ok(calls.every(c=>c[4]==='#000'&&c[5]===.37));
});

test('handles are immutable, private data ignores source mutation, and the same input replays',()=>{
 const input=plane(),mesh=createMesh(input),projection=project(mesh),again=project(mesh);
 assert.ok(Object.isFrozen(mesh)&&Object.isFrozen(mesh.bounds.min)&&Object.isFrozen(projection.camera.position));
 assert.equal(mesh.vertices,undefined);assert.equal(projection.depths,undefined);assert.equal(projection.faces,undefined);
 input.vertices[0][0]=-900;input.triangles[0][0]=3;
 const replay=project(mesh);
 for(const [x,y]of [[30,30],[50,50],[70,70],[0,0]]){
  assert.deepEqual(sampleProjection(projection,x,y),sampleProjection(again,x,y));
  assert.deepEqual(sampleProjection(projection,x,y),sampleProjection(replay,x,y));
 }
 const result=sampleProjection(projection,50,50);result.normal[0]=99;
 assert.equal(sampleProjection(projection,50,50).normal[0],0);
 assert.throws(()=>project({...mesh}),/scene_geometry_mesh_handle_invalid/);
 assert.throws(()=>sampleProjection({...projection},1,1),/scene_geometry_projection_handle_invalid/);
 assert.throws(()=>{projection.camera.viewHeight=88;},TypeError);
});

test('camera pan and orthographic scale are evaluated before rasterization',()=>{
 const mesh=createMesh(plane()),normal=project(mesh),zoom=project(mesh,{camera:camera({viewHeight:2})}),pan=project(mesh,{camera:camera({position:[1,0,5],target:[1,0,0]})});
 assert.equal(normal.covered_pixels,2500);assert.equal(zoom.covered_pixels,10000);
 near(projectPoint(normal,[0,0,0]).x,50);near(projectPoint(pan,[0,0,0]).x,25);
 near(sampleProjection(pan,25,50).depth,5);
});

test('maximum grid and actual 570x330 local cache fit the declared numeric limits',()=>{
 fresh(`
  const grid={columns:128,rows:128},heights=Array(grid.columns*grid.rows).fill(0);
  const mesh=g.createTerrainMesh({bounds:{x:-1,z:-1,width:2,depth:2},grid,heights});
  assert.equal(mesh.vertex_count,16384);assert.equal(mesh.triangle_count,32258);assert.equal(mesh.connected_components,1);
  const p=g.projectMesh(mesh,{width:570,height:330,camera:{position:[0,5,3],target:[0,0,0],viewHeight:4}});
  assert.equal(p.numeric_bytes,570*330*12);assert.ok(p.raster_pixel_tests<=g.MAX_GEOMETRY_RASTER_WORK);
  assert.ok(mesh.numeric_bytes+p.numeric_bytes<g.MAX_GEOMETRY_RETAINED_BYTES);
  assert.ok(16384*24<=g.MAX_GEOMETRY_WORK_BYTES);
 `);
 assert.equal(GEOMETRY_LIMITS.projection_pixels,MAX_GEOMETRY_PIXELS);
 assert.equal(GEOMETRY_LIMITS.raster_pixel_tests,MAX_GEOMETRY_RASTER_WORK);
 assert.equal(GEOMETRY_LIMITS.retained_numeric_bytes,MAX_GEOMETRY_RETAINED_BYTES);
 assert.equal(GEOMETRY_LIMITS.work_numeric_bytes,MAX_GEOMETRY_WORK_BYTES);
});

test('invalid topology, numeric values, unsupported fields and bad cameras are rejected',()=>{
 fresh(`
  const valid=()=>({vertices:[[-1,-1,0],[1,-1,0],[0,1,0]],triangles:[[0,1,2]]});
  for(const value of [NaN,Infinity,-Infinity,true,'1',null]){const x=valid();x.vertices[0][0]=value;assert.throws(()=>g.createMesh(x),/scene_geometry/);}
  for(const triangles of [[[0,1,3]],[[0,0,1]],[[0,1]]])assert.throws(()=>g.createMesh({...valid(),triangles}),/scene_geometry/);
  assert.throws(()=>g.createMesh({vertices:[[0,0,0],[1,1,1],[2,2,2]],triangles:[[0,1,2]]}),/scene_geometry_degenerate_triangle/);
  assert.throws(()=>g.createMesh({vertices:[...valid().vertices,[10,10,10]],triangles:[[0,1,2]]}),/scene_geometry_unused_vertex/);
  assert.throws(()=>g.createMesh({...valid(),script:'no'}),/scene_geometry/);
  assert.throws(()=>g.createTerrainMesh({bounds:{x:0,z:0,width:1,depth:1},grid:{columns:129,rows:128},heights:[]}),/scene_geometry/);
  assert.throws(()=>g.createTerrainMesh({bounds:{x:0,z:0,width:1,depth:1},grid:{columns:2,rows:2},heights:[0,0,0]}),/scene_geometry/);
  const mesh=g.createMesh(valid()),camera={position:[0,0,5],target:[0,0,0],viewHeight:4};
  assert.throws(()=>g.projectMesh(mesh,{width:1024,height:1024,camera}),/scene_geometry_projection_budget/);
  assert.throws(()=>g.projectMesh(mesh,{width:1025,height:1,camera}),/scene_geometry/);
  assert.throws(()=>g.projectMesh(mesh,{width:20,height:20,camera:{...camera,target:camera.position}}),/scene_geometry_camera_invalid/);
  assert.throws(()=>g.projectMesh(mesh,{width:20,height:20,camera:{...camera,up:[0,0,1]}}),/scene_geometry_camera_invalid/);
  assert.throws(()=>g.projectMesh(mesh,{width:20,height:20,camera:{...camera,near:6}}),/scene_geometry_camera_clip_required/);
  assert.throws(()=>g.projectMesh(mesh,{width:20,height:20,camera:{...camera,near:6,far:5}}),/scene_geometry_camera_invalid/);
 `);
});

test('raster candidate work is rejected before projection reservation or pixel-buffer allocation',()=>{
 fresh(`
  const mesh=g.createMesh({vertices:[[-2,-2,0],[2,-2,0],[0,2,0]],triangles:Array.from({length:150},()=>[0,1,2])});
  const before=g.geometryResourceUsage();
  assert.throws(()=>g.projectMesh(mesh,{width:512,height:512,camera:{position:[0,0,5],target:[0,0,0],viewHeight:4}}),/scene_geometry_raster_work_budget/);
  assert.deepEqual(g.geometryResourceUsage(),before);
 `);
});

test('retained bytes and handle counts use one canonical quota shared by module fragments',()=>{
 fresh(`
  const a=await import(${JSON.stringify(moduleUrl+'#a')}),b=await import(${JSON.stringify(moduleUrl+'#b')});
  const input=()=>({vertices:[[-1,-1,0],[1,-1,0],[0,1,0]],triangles:[[0,1,2]]});
  for(let i=0;i<32;i++)(i%2?a:b).createMesh(input());
  assert.equal(a.geometryResourceUsage().handles_reserved,32);assert.deepEqual(a.geometryResourceUsage(),b.geometryResourceUsage());
  assert.throws(()=>a.createMesh(input()),/scene_geometry_retained_budget/);
  assert.equal(a.reset,undefined);assert.equal(b.reset,undefined);
 `);
 fresh(`
  const mesh=g.createMesh({vertices:[[-1,-1,0],[1,-1,0],[0,1,0]],triangles:[[0,1,2]]});
  for(let i=0;i<5;i++)g.projectMesh(mesh,{width:512,height:512,camera:{position:[0,0,5],target:[0,0,0],viewHeight:4}});
  assert.throws(()=>g.projectMesh(mesh,{width:512,height:512,camera:{position:[0,0,5],target:[0,0,0],viewHeight:4}}),/scene_geometry_retained_budget/);
  const usage=g.geometryResourceUsage();assert.equal(usage.projections_created,5);assert.equal(usage.handles_reserved,6);
 `);
});

test('failed retained constructions do not refund quota or masquerade as created geometry',()=>{
 fresh(`
  assert.throws(()=>g.createMesh({vertices:[[0,0,0],[1,0,0],[2,0,0]],triangles:[[0,1,2]]}),/scene_geometry_degenerate_triangle/);
  const usage=g.geometryResourceUsage();assert.equal(usage.handles_reserved,1);assert.equal(usage.meshes_created,0);assert.ok(usage.numeric_bytes_reserved>0);
 `);
});
