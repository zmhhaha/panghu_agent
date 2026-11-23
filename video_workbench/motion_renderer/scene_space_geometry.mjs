// Authored shared XYZ geometry -> bounded orthographic visibility/brush guides.
// This does not reconstruct observed 3D or paint normal-derived grey values.
// No Canvas is created. Numeric quotas exclude arbitrary JS/native allocator
// overhead, which remains subject to the original process memory/time limits.
import {reserveGeometryBytes,geometryResourceUsage,MAX_GEOMETRY_RETAINED_BYTES,
 MAX_GEOMETRY_HANDLES} from './scene_space_geometry_budget.mjs';
export {geometryResourceUsage,MAX_GEOMETRY_RETAINED_BYTES,MAX_GEOMETRY_HANDLES};
export const GEOMETRY_REVISION='scene-shared-geometry-1';
export const MAX_GEOMETRY_WORK_BYTES=1024*1024;
export const MAX_GEOMETRY_VERTICES=16384;
export const MAX_GEOMETRY_TRIANGLES=32768;
export const MAX_GEOMETRY_RASTER_SIDE=1024;
export const MAX_GEOMETRY_PIXELS=262144;
export const MAX_GEOMETRY_RASTER_WORK=33554432;
export const GEOMETRY_LIMITS=Object.freeze({vertices:MAX_GEOMETRY_VERTICES,
 triangles:MAX_GEOMETRY_TRIANGLES,raster_side:MAX_GEOMETRY_RASTER_SIDE,
 projection_pixels:MAX_GEOMETRY_PIXELS,raster_pixel_tests:MAX_GEOMETRY_RASTER_WORK,
 retained_numeric_bytes:MAX_GEOMETRY_RETAINED_BYTES,work_numeric_bytes:MAX_GEOMETRY_WORK_BYTES,
 handles:MAX_GEOMETRY_HANDLES});
const meshes=new WeakMap(),projections=new WeakMap(),COORD=1000000,EPS=1e-10;
const check=(ok,code='scene_geometry_invalid')=>{if(!ok)throw Error(code);};
const number=(n,lo=-COORD,hi=COORD)=>{check(typeof n==='number'&&Number.isFinite(n)&&n>=lo&&n<=hi);return n;};
const integer=(n,lo,hi)=>{check(Number.isInteger(n)&&n>=lo&&n<=hi);return n;};
const fields=(o,required,optional=[])=>{check(o!==null&&typeof o==='object'&&!Array.isArray(o));
 const keys=Object.keys(o);check(required.every(k=>keys.includes(k))&&keys.every(k=>required.includes(k)||optional.includes(k)));};
const vector=p=>{check(Array.isArray(p)&&p.length===3);return p.map(v=>number(v));};
const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
const subtract=(a,b)=>a.map((v,i)=>v-b[i]);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit=(a,code)=>{const d=Math.hypot(...a);check(d>1e-12,code);return a.map(v=>v/d||0);};
const frozenVector=p=>Object.freeze([...p]);
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));

function buildMesh(vertexCount,triangleCount,vertexAt,triangleAt,terrain){
 const retainedBytes=vertexCount*24+triangleCount*36;
 const workBytes=vertexCount*6;check(workBytes<=MAX_GEOMETRY_WORK_BYTES,'scene_geometry_work_budget');
 const commit=reserveGeometryBytes('mesh',retainedBytes);
 const vertices=new Float64Array(vertexCount*3),indices=new Uint32Array(triangleCount*3),normals=new Float64Array(triangleCount*3);
 const parent=new Int32Array(vertexCount),rank=new Uint8Array(vertexCount),referenced=new Uint8Array(vertexCount);
 const min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
 for(let i=0;i<vertexCount;i++){const p=vector(vertexAt(i));vertices.set(p,i*3);parent[i]=i;
  for(let k=0;k<3;k++){min[k]=Math.min(min[k],p[k]);max[k]=Math.max(max[k],p[k]);}}
 const find=i=>{while(parent[i]!==i){parent[i]=parent[parent[i]];i=parent[i];}return i;};
 const join=(a,b)=>{a=find(a);b=find(b);if(a===b)return;
  if(rank[a]<rank[b])parent[a]=b;else{parent[b]=a;if(rank[a]===rank[b])rank[a]++;}};
 for(let i=0;i<triangleCount;i++){
  const t=triangleAt(i);check(Array.isArray(t)&&t.length===3);
  const [a,b,c]=t.map(v=>integer(v,0,vertexCount-1));check(a!==b&&b!==c&&a!==c,'scene_geometry_degenerate_triangle');
  const p=[vertices[a*3],vertices[a*3+1],vertices[a*3+2]],q=[vertices[b*3],vertices[b*3+1],vertices[b*3+2]],r=[vertices[c*3],vertices[c*3+1],vertices[c*3+2]];
  const u=subtract(q,p),v=subtract(r,p),n=cross(u,v),length=Math.hypot(...n);
  check(length>1e-12*Math.max(1,Math.hypot(...u)*Math.hypot(...v)),'scene_geometry_degenerate_triangle');
  indices.set([a,b,c],i*3);normals.set(n.map(x=>x/length||0),i*3);
  referenced[a]=referenced[b]=referenced[c]=1;join(a,b);join(b,c);
 }
 let components=0;
 for(let i=0;i<vertexCount;i++){check(referenced[i]===1,'scene_geometry_unused_vertex');if(find(i)===i)components++;}
 const handle=Object.freeze({schema:'video.scene-shared-mesh.v1',revision:GEOMETRY_REVISION,
  vertex_count:vertexCount,triangle_count:triangleCount,numeric_bytes:retainedBytes,
  connected_components:components,component_scope:'shared_vertex_graph_not_closed_manifold',
  bounds:Object.freeze({min:frozenVector(min),max:frozenVector(max)}),
  ...(terrain?{terrain}:{}),scope:'authored_shared_xyz_geometry_not_observed_or_calibrated_3d'});
 meshes.set(handle,{vertices,indices,normals,vertexCount,triangleCount});commit();return handle;
}

export function createMesh(options){
 fields(options,['vertices','triangles']);
 check(Array.isArray(options.vertices)&&Array.isArray(options.triangles));
 const V=integer(options.vertices.length,3,MAX_GEOMETRY_VERTICES),T=integer(options.triangles.length,1,MAX_GEOMETRY_TRIANGLES);
 return buildMesh(V,T,i=>options.vertices[i],i=>options.triangles[i]);
}

export function createTerrainMesh(options){
 fields(options,['bounds','grid','heights']);fields(options.bounds,['x','z','width','depth']);fields(options.grid,['columns','rows']);
 const b={x:number(options.bounds.x),z:number(options.bounds.z),width:number(options.bounds.width,.001),depth:number(options.bounds.depth,.001)};
 number(b.x+b.width);number(b.z+b.depth);
 const columns=integer(options.grid.columns,2,128),rows=integer(options.grid.rows,2,128),V=columns*rows,T=(columns-1)*(rows-1)*2;
 check(V<=MAX_GEOMETRY_VERTICES&&T<=MAX_GEOMETRY_TRIANGLES,'scene_geometry_mesh_budget');
 check(Array.isArray(options.heights)&&options.heights.length===V);
 // Validate the authored input before constructing retained buffers. Heights
 // remain source-owned; only the private vertex copy is retained by the mesh.
 for(const h of options.heights)number(h);
 const vertexAt=i=>[b.x+(i%columns)*b.width/(columns-1),options.heights[i],b.z+Math.floor(i/columns)*b.depth/(rows-1)];
 const triangleAt=i=>{const cell=Math.floor(i/2),a=Math.floor(cell/(columns-1))*columns+cell%(columns-1),right=a+1,down=a+columns;
  return i%2?[right,down,down+1]:[a,down,right];};
 return buildMesh(V,T,vertexAt,triangleAt,Object.freeze({bounds:Object.freeze(b),grid:Object.freeze({columns,rows}),
  scope:'connected_xz_heightfield_surface_not_overhang_volume'}));
}

function cameraFor(value){
 fields(value,['position','target','viewHeight'],['up','near','far']);
 const position=vector(value.position),target=vector(value.target),up=vector(value.up??[0,1,0]);
 const forward=unit(subtract(target,position),'scene_geometry_camera_invalid'),right=unit(cross(forward,up),'scene_geometry_camera_invalid'),vertical=unit(cross(right,forward),'scene_geometry_camera_invalid');
 const viewHeight=number(value.viewHeight,.001),near=number(value.near??.0001,0,10000000),far=number(value.far??10000000,.001,10000000);
 check(far>near,'scene_geometry_camera_invalid');
 return {position,target,up,forward,right,vertical,viewHeight,near,far};
}
function screenPoint(p,camera,width,height){const v=subtract(p,camera.position),scale=height/camera.viewHeight;
 return [width/2+dot(v,camera.right)*scale,height/2-dot(v,camera.vertical)*scale,dot(v,camera.forward)];}
function triangleBox(screen,indices,i,width,height){
 const a=indices[i*3]*3,b=indices[i*3+1]*3,c=indices[i*3+2]*3;
 const ax=screen[a],ay=screen[a+1],bx=screen[b],by=screen[b+1],cx=screen[c],cy=screen[c+1];
 const den=(by-cy)*(ax-cx)+(cx-bx)*(ay-cy);
 if(Math.abs(den)<EPS)return null;
 const x0=Math.max(0,Math.ceil(Math.min(ax,bx,cx)-.5)),x1=Math.min(width-1,Math.floor(Math.max(ax,bx,cx)-.5));
 const y0=Math.max(0,Math.ceil(Math.min(ay,by,cy)-.5)),y1=Math.min(height-1,Math.floor(Math.max(ay,by,cy)-.5));
 return x1>=x0&&y1>=y0?{a,b,c,ax,ay,bx,by,cx,cy,den,x0,x1,y0,y1,tests:(x1-x0+1)*(y1-y0+1)}:null;
}

export function projectMesh(mesh,options){
 const state=meshes.get(mesh);check(state,'scene_geometry_mesh_handle_invalid');
 fields(options,['width','height','camera']);
 const width=integer(options.width,1,MAX_GEOMETRY_RASTER_SIDE),height=integer(options.height,1,MAX_GEOMETRY_RASTER_SIDE),pixels=width*height;
 check(pixels<=MAX_GEOMETRY_PIXELS,'scene_geometry_projection_budget');const camera=cameraFor(options.camera);
 const workBytes=state.vertexCount*24;check(workBytes<=MAX_GEOMETRY_WORK_BYTES,'scene_geometry_work_budget');
 const screen=new Float64Array(state.vertexCount*3);
 for(let i=0;i<state.vertexCount;i++){
  const p=screenPoint([state.vertices[i*3],state.vertices[i*3+1],state.vertices[i*3+2]],camera,width,height);
  check(p.every(Number.isFinite)&&Math.abs(p[0])<=1e10&&Math.abs(p[1])<=1e10,'scene_geometry_projection_range');
  check(p[2]>camera.near&&p[2]<camera.far,'scene_geometry_camera_clip_required');screen.set(p,i*3);
 }
 let tests=0,projectedTriangles=0;
 for(let i=0;i<state.triangleCount;i++){const box=triangleBox(screen,state.indices,i,width,height);if(!box)continue;
  tests+=box.tests;projectedTriangles++;check(tests<=MAX_GEOMETRY_RASTER_WORK,'scene_geometry_raster_work_budget');}
 // Larger per-pixel buffers are not allocated until the complete triangle-box
 // work estimate has passed. The temporary screen array is independently bound.
 const retainedBytes=pixels*12,commit=reserveGeometryBytes('projection',retainedBytes);
 const depths=new Float64Array(pixels),faces=new Int32Array(pixels);depths.fill(Infinity);faces.fill(-1);
 for(let i=0;i<state.triangleCount;i++){
  const b=triangleBox(screen,state.indices,i,width,height);if(!b)continue;
  for(let y=b.y0;y<=b.y1;y++)for(let x=b.x0;x<=b.x1;x++){
   const px=x+.5,py=y+.5,u=((b.by-b.cy)*(px-b.cx)+(b.cx-b.bx)*(py-b.cy))/b.den,
    v=((b.cy-b.ay)*(px-b.cx)+(b.ax-b.cx)*(py-b.cy))/b.den,w=1-u-v;
   if(u<-EPS||v<-EPS||w<-EPS)continue;
   const depth=u*screen[b.a+2]+v*screen[b.b+2]+w*screen[b.c+2],index=y*width+x;
   if(depth<depths[index]-1e-9*Math.max(1,Math.abs(depth))){depths[index]=depth;faces[index]=i;}
  }
 }
 let covered=0,minDepth=Infinity,maxDepth=-Infinity;
 for(let i=0;i<pixels;i++)if(faces[i]>=0){covered++;minDepth=Math.min(minDepth,depths[i]);maxDepth=Math.max(maxDepth,depths[i]);}
 const handle=Object.freeze({schema:'video.scene-mesh-projection.v1',revision:GEOMETRY_REVISION,width,height,
  numeric_bytes:retainedBytes,covered_pixels:covered,min_depth:covered?minDepth:null,max_depth:covered?maxDepth:null,
  raster_pixel_tests:tests,projected_triangles:projectedTriangles,
  camera:Object.freeze({position:frozenVector(camera.position),target:frozenVector(camera.target),up:frozenVector(camera.up),
   viewHeight:camera.viewHeight,near:camera.near,far:camera.far,projection:'orthographic'}),
  scope:'authored_mesh_pixel_center_visibility_not_observed_depth_or_visual_acceptance'});
 projections.set(handle,{mesh:state,camera,depths,faces,width,height});commit();return handle;
}

function boundsFor(projection,options,extra=[]){fields(options,[],['bounds',...extra]);
 const b=options.bounds??{x:0,y:0,width:projection.width,height:projection.height};fields(b,['x','y','width','height']);
 const result={x:number(b.x),y:number(b.y),width:number(b.width,.001),height:number(b.height,.001)};
 number(result.x+result.width);number(result.y+result.height);return result;}
function projectionState(projection){const state=projections.get(projection);check(state,'scene_geometry_projection_handle_invalid');return state;}
function uncovered(){return{covered:false,coverage:0,depth:null,position:null,normal:null,flow:[0,0],flow_valid:false,triangle:null};}

export function sampleProjection(projection,x,y,options={}){
 const s=projectionState(projection),b=boundsFor(projection,options);number(x);number(y);
 const sx=(x-b.x)/b.width*s.width,sy=(y-b.y)/b.height*s.height;
 if(sx<0||sy<0||sx>=s.width||sy>=s.height)return uncovered();
 const col=Math.floor(sx),row=Math.floor(sy),index=row*s.width+col,triangle=s.faces[index];if(triangle<0)return uncovered();
 const depth=s.depths[index],camera=s.camera,scale=camera.viewHeight/s.height,
  horizontal=(col+.5-s.width/2)*scale,vertical=(s.height/2-row-.5)*scale;
 const position=camera.position.map((v,i)=>v+camera.right[i]*horizontal+camera.vertical[i]*vertical+camera.forward[i]*depth),
  normal=Array.from(s.mesh.normals.subarray(triangle*3,triangle*3+3));
 // Gravity projected onto this face, then onto the selected camera plane.
 const tangent=[normal[0]*normal[1],normal[1]*normal[1]-1,normal[2]*normal[1]],
  fx=dot(tangent,camera.right)*b.width/s.width,fy=-dot(tangent,camera.vertical)*b.height/s.height,length=Math.hypot(fx,fy);
 return{covered:true,coverage:1,depth,position,normal,flow:length>1e-12?[fx/length||0,fy/length||0]:[0,0],flow_valid:length>1e-12,triangle};
}

export function projectPoint(projection,point,options={}){
 const s=projectionState(projection),b=boundsFor(projection,options,['depthTolerance']),p=screenPoint(vector(point),s.camera,s.width,s.height),
  tolerance=number(options.depthTolerance??s.camera.viewHeight/s.height*2,0);
 const inView=p[0]>=0&&p[0]<s.width&&p[1]>=0&&p[1]<s.height&&p[2]>s.camera.near&&p[2]<s.camera.far,
  index=inView?Math.floor(p[1])*s.width+Math.floor(p[0]):-1,triangle=inView&&s.faces[index]>=0?s.faces[index]:null;
 return{x:b.x+p[0]/s.width*b.width,y:b.y+p[1]/s.height*b.height,depth:p[2],in_view:inView,
  visible:triangle!==null&&p[2]<=s.depths[index]+tolerance,triangle};
}

export function paintCoverage(ctx,projection,options={}){
 const s=projectionState(projection),b=boundsFor(projection,options);
 check(ctx&&['save','restore','beginPath','rect','fill'].every(k=>typeof ctx[k]==='function'),'scene_geometry_context_invalid');
 let runs=0;ctx.save();try{ctx.fillStyle='#000';ctx.beginPath();
  const dx=b.width/s.width,dy=b.height/s.height;
  for(let row=0;row<s.height;row++){
   let start=-1;
   for(let col=0;col<=s.width;col++){
    if(col<s.width&&s.faces[row*s.width+col]>=0){if(start<0)start=col;}
    else if(start>=0){ctx.rect(b.x+start*dx,b.y+row*dy,(col-start)*dx,dy);runs++;start=-1;}
   }
  }
  // Fill the complete union once. Separate row fills can leave AA alpha seams
  // at noninteger cache densities, or repeatedly apply destination-in.
  ctx.fill();
 }finally{ctx.restore();}
 return{schema:'video.scene-mesh-coverage.v1',runs,rows:s.height,covered_pixels:projection.covered_pixels,
  scope:'pixel_center_silhouette_respects_context_transform_clip_alpha_not_visual_acceptance'};
}
