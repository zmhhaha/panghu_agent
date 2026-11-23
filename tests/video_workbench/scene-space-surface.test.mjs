import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import test from 'node:test';
import {createSurfaceField,sampleSurface,paintSurfaceWash,MAX_FIELD_BYTES,MAX_WORK_BYTES,
 MAX_WASH_ROWS,MAX_WASH_STOPS,SURFACE_LIMITS} from '../../video_workbench/motion_renderer/scene_space_surface.mjs';

const clone=value=>JSON.parse(JSON.stringify(value));
const rectangle=()=>({bounds:{x:0,y:0,width:120,height:120},outline:[[0,0],[120,0],[120,120],[0,120]],
 grid:{columns:61,rows:61},boundaryFade:5});
const mountain=()=>({...rectangle(),ridges:[{id:'main',points:[[60,10,60],[60,110,10]],width:24}]});
const near=(a,b,epsilon=1e-9)=>assert.ok(Math.abs(a-b)<=epsilon,`${a} != ${b}`);

test('flat form has finite upright normals and lighting remains separate from geometry',()=>{
 const field=createSurfaceField(rectangle());
 const up=sampleSurface(field,60,60,{light:[0,0,1]});
 assert.equal(up.coverage,1);assert.equal(up.height,0);assert.deepEqual(up.normal,[0,0,1]);
 assert.deepEqual(up.flow,[0,1]);assert.equal(up.shade,0);
 const side=sampleSurface(field,60,60,{light:[1,0,0]});
 assert.equal(side.shade,1);assert.deepEqual(side.normal,up.normal);
});

test('opposite ridge faces have opposite normal and flow turns and respond to light direction',()=>{
 const field=createSurfaceField(mountain());
 const left=sampleSurface(field,40,60,{light:[-1,0,1]});
 const right=sampleSurface(field,80,60,{light:[-1,0,1]});
 assert.ok(left.normal[0]<-.2);assert.ok(right.normal[0]>.2);
 assert.ok(left.flow[0]<-.1&&right.flow[0]>.1);
 assert.ok(left.flow[1]>.6&&right.flow[1]>.6);
 assert.ok(left.shade<right.shade-.2);
 const reversed=sampleSurface(field,40,60,{light:[1,0,1]});
 assert.ok(reversed.shade>left.shade+.2);
 near(Math.hypot(...left.normal),1);near(Math.hypot(...right.flow),1);
});

test('a valley changes internal form and a branching ridge supplies another stroke direction',()=>{
 const baseline=createSurfaceField(mountain());
 const options=mountain();options.valleys=[{id:'ravine',points:[[48,35,23],[38,100,8]],width:9}];
 options.ridges.push({id:'branch',points:[[60,40,45],[100,92,5]],width:13});
 const varied=createSurfaceField(options);
 assert.ok(sampleSurface(varied,45,55).height<sampleSurface(baseline,45,55).height-5);
 const a=sampleSurface(varied,95,89),b=sampleSurface(baseline,95,89);
 assert.ok(a.flow[0]>b.flow[0]+.2,'branching form should turn the local stroke field');
 assert.ok(Math.hypot(a.normal[0]-b.normal[0],a.normal[1]-b.normal[1])>.1);
});

test('concave coverage, boundary and outside are evaluated against the authored silhouette',()=>{
 const options=rectangle();options.outline=[[0,0],[120,0],[120,40],[40,40],[40,120],[0,120]];
 options.ridges=[{id:'arm',points:[[20,10,30],[20,105,5]],width:12}];
 const field=createSurfaceField(options);
 assert.equal(sampleSurface(field,20,80).coverage,1);
 assert.equal(sampleSurface(field,90,90).coverage,0);
 assert.equal(sampleSurface(field,0,50).coverage,1);
 assert.equal(sampleSurface(field,0,50).height,0);
 assert.deepEqual(sampleSurface(field,-1,50),{coverage:0,height:0,normal:[0,0,1],flow:[0,1],shade:0});
});

test('the same declared form repeats and input/returned handles cannot mutate its buffers',()=>{
 const input=mountain(),before=clone(input),field=createSurfaceField(input),repeat=createSurfaceField(clone(input));
 assert.deepEqual(input,before);assert.ok(Object.isFrozen(field)&&Object.isFrozen(field.outline[0]));
 assert.equal(field.heights,undefined,'mutable private buffers are not given to scene code');
 for(const [x,y] of [[17,13],[40,60],[83,76],[60,100]])assert.deepEqual(sampleSurface(field,x,y),sampleSurface(repeat,x,y));
 input.ridges[0].points[0][2]=0;input.outline[0][0]=50;
 assert.deepEqual(sampleSurface(field,40,60),sampleSurface(repeat,40,60));
 assert.throws(()=>sampleSurface({...field},40,60),/scene_surface_field_invalid/);
 assert.throws(()=>{field.bounds.width=1;},TypeError);
});

test('declared numeric buffers and worst admitted grid are bounded',()=>{
 const options=mountain();options.grid={columns:128,rows:128};
 const field=createSurfaceField(options);
 assert.equal(field.node_count,16384);
 assert.ok(field.numeric_bytes+field.node_count*8<=MAX_FIELD_BYTES);
 assert.ok(MAX_FIELD_BYTES<=MAX_WORK_BYTES);
 assert.equal(SURFACE_LIMITS.work_numeric_bytes,MAX_WORK_BYTES);
 assert.throws(()=>createSurfaceField({...options,grid:{columns:129,rows:128}}),/scene_surface/);
 const many=clone(options);many.outline=Array.from({length:128},(_,i)=>[
  60+59*Math.cos(i*Math.PI/64),60+59*Math.sin(i*Math.PI/64)]);many.ridges=[];
 assert.throws(()=>createSurfaceField(many),/scene_surface_work_budget/);
});

test('self crossings, degenerate outlines, repeated points and exterior features are rejected',()=>{
 for(const outline of [
  [[0,0],[120,120],[0,120],[120,0]],
  [[0,0],[30,0],[60,0]],
  [[0,0],[120,0],[120,120],[0,120],[120,0]],
  [[-1,0],[120,0],[120,120],[0,120]],
 ])assert.throws(()=>createSurfaceField({...rectangle(),outline}),/scene_surface/);
 const repeated=mountain();repeated.ridges[0].points=[[60,10,20],[60,10,10]];
 assert.throws(()=>createSurfaceField(repeated),/scene_surface/);
 const outside=mountain();outside.ridges[0].points[0][0]=-1;
 assert.throws(()=>createSurfaceField(outside),/scene_surface/);
});

test('bad numeric values, extra fields and feature budgets fail before painting',()=>{
 for(const value of [NaN,Infinity,-Infinity,true,'12',null]){
  assert.throws(()=>createSurfaceField({...rectangle(),baseHeight:value}),/scene_surface/);
  assert.throws(()=>sampleSurface(createSurfaceField(rectangle()),value,20),/scene_surface/);
 }
 assert.throws(()=>createSurfaceField({...rectangle(),arbitraryShader:'no'}),/scene_surface/);
 assert.throws(()=>createSurfaceField({...mountain(),ridges:Array.from({length:33},(_,i)=>({id:'r'+i,points:[[20,10,3],[20,100,1]]}))}),/scene_surface/);
 const duplicates=mountain();duplicates.valleys=[clone(duplicates.ridges[0])];
 assert.throws(()=>createSurfaceField(duplicates),/scene_surface/);
 assert.throws(()=>sampleSurface(createSurfaceField(rectangle()),60,60,{light:[0,0,0]}),/scene_surface_light_invalid/);
});

function context({fail=false}={}){
 const events=[],gradients=[],rectangles=[],stack=[];
 const ctx={fillStyle:'original',globalAlpha:.83,transform:[1.7,.2,0,1.2,17,21],
  get canvas(){throw Error('The helper must not access or resize a native Canvas');},
  save(){stack.push({fillStyle:this.fillStyle,globalAlpha:this.globalAlpha,transform:[...this.transform]});events.push('save');},
  restore(){Object.assign(this,stack.pop());events.push('restore');},
  beginPath(){events.push('begin');},moveTo(...p){events.push(['move',...p]);},lineTo(...p){events.push(['line',...p]);},
  closePath(){events.push('close');},clip(){events.push('clip');},
  fillRect(...p){if(fail)throw Error('fixture paint failure');assert.ok(events.includes('clip'));
   events.push(['fill',...p]);rectangles.push({coordinates:p,style:this.fillStyle});},
  createLinearGradient(...p){assert.ok(p.every(Number.isFinite));const stops=[];
   stops.coordinates=p;stops.offsets=[];gradients.push(stops);
   return{addColorStop(offset,color){assert.ok(offset>=0&&offset<=1);assert.match(color,/^rgb\(\d+,\d+,\d+\)$/);
    stops.offsets.push(offset);stops.push(color);}};},
 };
 return{ctx,events,gradients,rectangles};
}

test('wash keeps local transform/alpha and clips opaque graded paint without creating a Canvas',()=>{
 const options=mountain();options.grid={columns:20,rows:24};
 const field=createSurfaceField(options),{ctx,events,gradients}=context();
 const before={fillStyle:ctx.fillStyle,globalAlpha:ctx.globalAlpha,transform:[...ctx.transform]};
 const report=paintSurfaceWash(ctx,field,{paper:[240,234,220],ink:[24,31,26],light:[-1,-.5,1],inkRange:[.03,.5]});
 assert.equal(report.cells,19*23);
 assert.equal(report.strips,120);assert.equal(report.gradient_stops,120*20);
 assert.equal(report.raster_method,'continuous_horizontal_scanlines');
 assert.ok(report.numeric_work_bytes<=MAX_WORK_BYTES);
 assert.deepEqual({fillStyle:ctx.fillStyle,globalAlpha:ctx.globalAlpha,transform:ctx.transform},before);
 assert.equal(events[0],'save');assert.equal(events.at(-1),'restore');
 assert.ok(gradients.some(stops=>stops[0]!==stops[1]),'the wash must contain form-dependent tone gradients');
 assert.ok(new Set(gradients.flat()).size>10);
});

function redOf(color){return Number(/^rgb\((\d+),/.exec(color)[1]);}
function interpolatedRed(stops,t){
 let i=0;while(i<stops.length-2&&stops.offsets[i+1]<t)i++;
 const a=stops.offsets[i],b=stops.offsets[i+1],u=(t-a)/(b-a);
 return redOf(stops[i])*(1-u)+redOf(stops[i+1])*u;
}

test('a mixed saddle-like form is painted as continuous bilinear scanlines across every cell edge',()=>{
 const size=64,columns=4,rows=4,light=[-.7,-.5,.8];
 const field=createSurfaceField({bounds:{x:0,y:0,width:size,height:size},outline:[[0,0],[64,0],[64,64],[0,64]],
  ridges:[{id:'bent',points:[[20,4,40],[40,60,7]],width:13}],
  valleys:[{id:'crossing',points:[[48,4,12],[8,58,6]],width:9}],grid:{columns,rows},boundaryFade:5});
 const nodeTone=Array.from({length:rows},(_,y)=>Array.from({length:columns},(_,x)=>
  sampleSurface(field,x*size/(columns-1),y*size/(rows-1),{light}).shade));
 let mixed=0;
 for(let y=0;y<rows-1;y++)for(let x=0;x<columns-1;x++)mixed=Math.max(mixed,
  Math.abs(nodeTone[y][x]-nodeTone[y][x+1]-nodeTone[y+1][x]+nodeTone[y+1][x+1]));
 assert.ok(mixed>.02,'fixture needs a mixed derivative that a cell-wise linear plane cannot represent');
 const {ctx,gradients,rectangles}=context();
 const report=paintSurfaceWash(ctx,field,{paper:[255,255,255],ink:[0,0,0],light,inkRange:[0,1]});
 assert.equal(report.strips,size);assert.equal(gradients.length,size);assert.equal(rectangles.length,size);
 for(let row=0;row<size;row++){
  const gradient=gradients[row],center=row+.5;
  assert.deepEqual(gradient.coordinates,[0,center,size,center]);
  assert.deepEqual(gradient.offsets,[0,1/3,2/3,1]);
  assert.deepEqual(rectangles[row].coordinates,[0,row,size,1],'no narrow cell fills or overlapping row edges');
  const fy=center*(rows-1)/size,top=Math.min(rows-2,Math.floor(fy)),ty=fy-top;
  for(const x of [8.3,24.2,40.7,61.1]){
   const fx=x*(columns-1)/size,left=Math.min(columns-2,Math.floor(fx)),tx=fx-left;
   const above=nodeTone[top][left]*(1-tx)+nodeTone[top][left+1]*tx;
   const below=nodeTone[top+1][left]*(1-tx)+nodeTone[top+1][left+1]*tx;
   const expected=255*(1-(above*(1-ty)+below*ty));
   near(interpolatedRed(gradient,x/size),expected,.51);
  }
 }
});

test('scanlines align to physical pixel rows under density and fractional translation without altering transform',()=>{
 const field=createSurfaceField({bounds:{x:5,y:7,width:20,height:11},
  outline:[[5,7],[25,7],[25,18],[5,18]],grid:{columns:4,rows:4}});
 for(const d of [2,-2]){
  const {ctx,rectangles}=context(),matrix={a:2,b:0,c:0,d,e:3.4,f:d>0?.3:50.3};
  ctx.getTransform=()=>matrix;
  const before={...matrix};const result=paintSurfaceWash(ctx,field);
  assert.equal(result.strips,23);
  for(let i=0;i<rectangles.length;i++){
   const [x,y,w,h]=rectangles[i].coordinates;
   assert.equal(x,5);assert.equal(w,20);near(h,1/d);
   near(y*d+matrix.f,14+i);near((y+h)*d+matrix.f,15+i);
  }
  assert.deepEqual(matrix,before);
 }
});

test('scanline row and gradient-stop work limits reject expensive transforms before painting',()=>{
 const field=createSurfaceField(rectangle()),large=context();
 large.ctx.getTransform=()=>({a:1,b:0,c:0,d:100,e:0,f:0});
 assert.throws(()=>paintSurfaceWash(large.ctx,field),/scene_surface_wash_work_budget/);
 assert.deepEqual(large.events,[]);
 const many=createSurfaceField({bounds:{x:0,y:0,width:64,height:MAX_WASH_ROWS},
  outline:[[0,0],[64,0],[64,MAX_WASH_ROWS],[0,MAX_WASH_ROWS]],grid:{columns:128,rows:2}});
 assert.ok(MAX_WASH_ROWS*128>MAX_WASH_STOPS);
 const stops=context();assert.throws(()=>paintSurfaceWash(stops.ctx,many),/scene_surface_wash_work_budget/);
 assert.deepEqual(stops.events,[]);
 const singular=context();singular.ctx.getTransform=()=>({a:1,b:0,c:0,d:0,e:0,f:0});
 assert.throws(()=>paintSurfaceWash(singular.ctx,field),/scene_surface_transform_invalid/);
 assert.deepEqual(singular.events,[]);
});

test('wash restores the caller after failure and rejects invalid styles without painting',()=>{
 const field=createSurfaceField(mountain()),{ctx,events}=context({fail:true});
 assert.throws(()=>paintSurfaceWash(ctx,field),/fixture paint failure/);
 assert.equal(events.at(-1),'restore');assert.equal(ctx.fillStyle,'original');
 const untouched=context();
 for(const options of [{inkRange:[.7,.2]},{ink:[0,0,256]},{paper:'white'},{light:[0,0,0]}]){
  assert.throws(()=>paintSurfaceWash(untouched.ctx,field,options),/scene_surface/);
 }
 assert.deepEqual(untouched.events,[]);
});

test('creation and sampling do not use ambient random numbers or wall time',()=>{
 const random=Math.random,now=Date.now;
 try{
  Math.random=()=>{throw Error('ambient random forbidden');};Date.now=()=>{throw Error('wall clock forbidden');};
  const a=createSurfaceField(mountain()),b=createSurfaceField(mountain());
  assert.deepEqual(sampleSurface(a,32,74),sampleSurface(b,32,74));
  paintSurfaceWash(context().ctx,a);
 }finally{Math.random=random;Date.now=now;}
});

function isolatedBudgetProbe(code){
 const helper=new URL('../../video_workbench/motion_renderer/scene_space_surface.mjs',import.meta.url).href;
 const budget=new URL('../../video_workbench/motion_renderer/scene_space_surface_budget.mjs',import.meta.url).href;
 const setup=`import assert from 'node:assert/strict';
const helper=${JSON.stringify(helper)},budget=${JSON.stringify(budget)};
const small={bounds:{x:0,y:0,width:64,height:64},outline:[[0,0],[64,0],[64,64],[0,64]],grid:{columns:2,rows:2}};
`;
 const result=spawnSync(process.execPath,['--input-type=module','-e',setup+code],{encoding:'utf8',timeout:15000});
 assert.equal(result.status,0,result.stderr||result.stdout||String(result.error));
}

test('helper fragment imports share the canonical retained-byte limit and cannot reset it',()=>{
 isolatedBudgetProbe(`
 const first=await import(helper+'#first'),second=await import(helper+'#second');
 assert.equal(first.surfaceResourceUsage,second.surfaceResourceUsage);
 const large={...small,grid:{columns:128,rows:128}};
 const bytes=128*128*6*8,count=Math.floor(first.MAX_TOTAL_FIELD_BYTES/bytes);
 const held=[];
 for(let i=0;i<count;i++)held.push((i%2?first:second).createSurfaceField(large));
 const usage=first.surfaceResourceUsage();
 assert.equal(usage.fields_created,count);assert.equal(usage.fields_reserved,count);
 assert.equal(usage.numeric_bytes_reserved,count*bytes);
 assert.throws(()=>second.createSurfaceField(large),/scene_surface_retained_budget/);
 assert.deepEqual(first.surfaceResourceUsage(),usage);
 assert.throws(()=>{usage.numeric_bytes_reserved=0;},TypeError);
 assert.throws(()=>{first.MAX_TOTAL_FIELD_BYTES=Infinity;},TypeError);
 const unrelated=await import(budget+'#unrelated-authority');
 unrelated.reserveFieldBytes(1)();
 const third=await import(helper+'#fresh-helper');
 assert.throws(()=>third.createSurfaceField(large),/scene_surface_retained_budget/);
 assert.deepEqual(third.surfaceResourceUsage(),usage);
 assert.equal(first.reset,undefined);assert.equal(unrelated.reset,undefined);
 `);
});

test('many small fields exhaust the field count before the byte allowance',()=>{
 isolatedBudgetProbe(`
 const tools=await import(helper);
 for(let i=0;i<tools.MAX_FIELDS;i++)tools.createSurfaceField(small);
 const before=tools.surfaceResourceUsage();
 assert.equal(before.fields_created,64);assert.equal(before.fields_reserved,64);
 assert.ok(before.numeric_bytes_reserved<tools.MAX_TOTAL_FIELD_BYTES);
 assert.throws(()=>tools.createSurfaceField(small),/scene_surface_retained_budget/);
 assert.deepEqual(tools.surfaceResourceUsage(),before);
 `);
});

test('allocation failure keeps its reservation and invalid input consumes no numeric allocation',()=>{
 isolatedBudgetProbe(`
 const tools=await import(helper);
 const start=tools.surfaceResourceUsage();
 assert.throws(()=>tools.createSurfaceField({...small,baseHeight:NaN}),/scene_surface/);
 assert.deepEqual(tools.surfaceResourceUsage(),start);
 const NativeArray=globalThis.Float64Array;
 try{
  globalThis.Float64Array=class {constructor(){throw Error('fixture allocation failure');}};
  assert.throws(()=>tools.createSurfaceField(small),/fixture allocation failure/);
 }finally{globalThis.Float64Array=NativeArray;}
 const reserved=tools.surfaceResourceUsage();
 assert.equal(reserved.fields_created,0);assert.equal(reserved.fields_reserved,1);
 assert.equal(reserved.numeric_bytes_reserved,2*2*6*8);
 tools.createSurfaceField(small);
 const after=tools.surfaceResourceUsage();
 assert.equal(after.fields_created,1);assert.equal(after.fields_reserved,2);
 assert.equal(after.numeric_bytes_reserved,2*reserved.numeric_bytes_reserved);
 `);
});

test('direct budget imports cannot turn invalid primitive reservations into extra allowance',()=>{
 isolatedBudgetProbe(`
 const authority=await import(budget),before=authority.surfaceResourceUsage();
 const original=Number.isSafeInteger;
 try{
  Number.isSafeInteger=()=>true;
  for(const invalid of [NaN,Infinity,-Infinity,-1,0,.5,'1',{},null,true])
   assert.throws(()=>authority.reserveFieldBytes(invalid),/scene_surface_field_reservation_invalid/);
 }finally{Number.isSafeInteger=original;}
 assert.deepEqual(authority.surfaceResourceUsage(),before);
 `);
});
