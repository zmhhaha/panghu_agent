import assert from 'node:assert/strict';
import test from 'node:test';
import {APPEARANCE_REVISION,APPEARANCE_LIMITS,evaluateLighting,sampleMaterial,sampleAtmosphere}
 from '../../video_workbench/motion_renderer/scene_space_surface.mjs';

const near=(a,b,epsilon=1e-12)=>assert.ok(Math.abs(a-b)<=epsilon,`${a} != ${b}`);
const lighting=(normal,light,extra={})=>evaluateLighting(normal,{light,...extra});
const material=(position,extra={})=>sampleMaterial(position,{seed:631907,...extra});
const scalarFields=['dot','diffuse','lit','shade','ink_density'];

test('lighting normalizes both directions, flips with the light and keeps pigment independent of coverage',()=>{
 const facing=lighting([1,1,0],[2,2,0]),scaled=lighting([9,9,0],[.25,.25,0]),back=lighting([1,1,0],[-2,-2,0]);
 for(const key of scalarFields)near(facing[key],scaled[key]);
 near(facing.lit,1);near(back.lit,0);near(back.shade,1);near(Math.hypot(...facing.normal),1);
 assert.equal(facing.revision,APPEARANCE_REVISION);assert.equal(facing.mode,'diffuse');
 assert.equal('alpha' in facing,false);assert.equal('coverage' in facing,false);
 const ambient=lighting([1,0,0],[-1,0,0],{ambient:.2,inkRange:[.03,.8]});
 near(ambient.lit,.2);near(ambient.shade,.8);near(ambient.ink_density,.03+.77*.8);
 assert.ok(Object.isFrozen(facing)&&Object.isFrozen(facing.normal)&&Object.isFrozen(facing.light));
});

test('ink response has explicit thresholds and does not alter the supplied form or light',()=>{
 const N=[.3,.4,.5],L=[.55,.52,-.72],beforeN=[...N],beforeL=[...L],options={mode:'ink',lightRange:[-.08,.61],inkRange:[.04,.91]};
 const ink=lighting(N,L,options),diffuse=lighting(N,L),t=Math.max(0,Math.min(1,(ink.dot+.08)/.69));
 near(ink.lit,t*t*(3-2*t));near(ink.shade,1-ink.lit);near(ink.ink_density,.04+.87*ink.shade);
 near(ink.dot,diffuse.dot);assert.deepEqual(ink.normal,diffuse.normal);assert.deepEqual(N,beforeN);assert.deepEqual(L,beforeL);
 assert.notEqual(ink.lit,diffuse.lit);
 near(lighting([1,0,0],[1,0,0],{mode:'ink'}).shade,0);
 near(lighting([1,0,0],[-1,0,0],{mode:'ink'}).shade,1);
});

test('invalid lighting input, singular vectors, unsupported modes and unsafe ranges are rejected',()=>{
 for(const bad of [NaN,Infinity,-Infinity,1e7,true,'1',null]){
  assert.throws(()=>lighting([bad,0,1],[1,0,1]),/scene_surface/);
  assert.throws(()=>lighting([0,0,1],[bad,0,1]),/scene_surface/);
  assert.throws(()=>lighting([0,0,1],[0,0,1],{ambient:bad}),/scene_surface/);
 }
 for(const bad of [[0,0,0],[1,2],new Float32Array([1,0,0])])assert.throws(()=>lighting(bad,[1,0,0]),/scene_surface/);
 for(const extra of [{mode:'pbr'},{lightRange:[0,0]},{lightRange:[-2,1]},{inkRange:[.8,.2]},{inkRange:[0,2]},{alpha:.2}])
  assert.throws(()=>lighting([0,0,1],[0,0,1],extra),/scene_surface/);
 assert.throws(()=>evaluateLighting([0,0,1],{}),/scene_surface/);
});

test('material is coordinate-anchored, query-order independent and reproducible for a fixed seed',()=>{
 const points=[[-4.3,8.2,1.7],[0,0,0],[1.3,-2.8,9.2]],first=points.map(p=>material(p));
 for(let i=points.length-1;i>=0;i--)assert.deepEqual(material(points[i]),first[i]);
 assert.notEqual(material(points[0],{seed:631908}).variation,first[0].variation);
 assert.notEqual(material([points[0][0]+.1,points[0][1],points[0][2]]).variation,first[0].variation);
 for(const result of first){
  assert.equal(result.space,'object');assert.ok(result.value>=0&&result.value<=1);
  assert.ok(result.variation>=-1&&result.variation<=1);assert.ok(result.roughness>=0&&result.roughness<=1);
  assert.ok(result.normal_delta.every(v=>Number.isFinite(v)&&Math.abs(v)<=.1));
  assert.equal('alpha' in result,false);assert.equal('coverage' in result,false);
  assert.ok(Object.isFrozen(result)&&Object.isFrozen(result.normal_delta));
 }
});

test('moving an object preserves local-coordinate material while world anchoring intentionally changes it',()=>{
 const local=[.35,1.25,-.8],beforeTranslation=[10,0,20],afterTranslation=[15,0,20];
 const worldBefore=local.map((v,i)=>v+beforeTranslation[i]),worldAfter=local.map((v,i)=>v+afterTranslation[i]);
 const recoverLocal=(p,translation)=>p.map((v,i)=>v-translation[i]);
 const a=material(recoverLocal(worldBefore,beforeTranslation)),b=material(recoverLocal(worldAfter,afterTranslation));
 near(a.value,b.value);near(a.roughness,b.roughness);
 assert.notEqual(material(worldBefore,{space:'world'}).value,material(worldAfter,{space:'world'}).value);
 assert.equal(material(worldBefore,{space:'world'}).space,'world');
});

test('three frequency bands contribute separately and have continuous lattice boundaries',()=>{
 const P=[.37,.91,1.43],bands=[[1,0,0],[0,1,0],[0,0,1]].map(weights=>material(P,{weights})),combined=material(P);
 near(combined.variation,.5*bands[0].variation+.3*bands[1].variation+.2*bands[2].variation);
 assert.equal(new Set(bands.map(x=>x.variation)).size,3);
 const left=material([1-1e-7,.41,.67]),right=material([1+1e-7,.41,.67]);
 near(left.value,right.value,1e-6);
 const flat=material(P,{amplitude:0,roughnessVariation:0,normalStrength:0});
 assert.equal(flat.value,.5);assert.equal(flat.roughness,.6);assert.deepEqual(flat.normal_delta,[0,0,0]);
 const maximum=material([1e6,-1e6,1e6],{seed:4294967295,scales:[1,32,1024],amplitude:1,normalStrength:1});
 assert.ok([maximum.value,maximum.variation,maximum.roughness,...maximum.normal_delta].every(Number.isFinite));
});

test('material input has finite explicit seed, coordinate and constant three-scale bounds',()=>{
 for(const seed of [-1,1.5,4294967296,NaN,'1',true])assert.throws(()=>material([0,1,2],{seed}),/scene_surface/);
 for(const position of [[0,1,NaN],[0,1,Infinity],[0,1,1e7],[0,1],null])assert.throws(()=>material(position),/scene_surface|TypeError/);
 for(const extra of [{scales:[1,2]},{scales:[1,2,3,4]},{scales:[1,1,2]},{scales:[0,1,2]},
  {scales:[1,2,1025]},{weights:[0,0,0]},{weights:[1,2,3]},{amplitude:2},{normalStrength:-1},{space:'screen'},{time:0}])
  assert.throws(()=>material([0,1,2],extra),/scene_surface/);
 assert.throws(()=>sampleMaterial([0,1,2],{}),/scene_surface/);
 assert.ok(Object.isFrozen(APPEARANCE_LIMITS));assert.equal(APPEARANCE_LIMITS.material_scales,3);
});

test('material normal cues agree with spatial finite differences rather than ambient random direction',()=>{
 const position=[.37,.91,1.43],epsilon=1e-6,strength=.2,sample=material(position,{normalStrength:strength});
 for(let axis=0;axis<3;axis++){
  const lower=[...position],upper=[...position];lower[axis]-=epsilon;upper[axis]+=epsilon;
  const derivative=(material(upper).variation-material(lower).variation)/(2*epsilon);
  near(sample.normal_delta[axis],Math.max(-1,Math.min(1,derivative))*strength,1e-7);
 }
});

test('atmosphere increases monotonically with distance at fixed height and never changes solid coverage',()=>{
 const config={near:5,far:105,density:3,maxAmount:.7,color:[230,235,241]},samples=[0,5,15,30,60,105,500].map(d=>sampleAtmosphere(d,config));
 assert.equal(samples[0].amount,0);assert.equal(samples[1].amount,0);
 for(let i=1;i<samples.length;i++)assert.ok(samples[i].amount>=samples[i-1].amount);
 assert.equal(samples.at(-1).amount,samples.at(-2).amount);
 for(const result of samples){
  assert.ok(result.amount>=0&&result.amount<=.7);near(result.transmittance+result.amount,1);
  assert.deepEqual(result.color,[230,235,241]);assert.equal('alpha' in result,false);assert.equal('coverage' in result,false);
  assert.ok(Object.isFrozen(result)&&Object.isFrozen(result.color));
 }
 const terrain={coverage:1,color:[20,30,40]},haze=sampleAtmosphere(60,config);
 const shaded={...terrain,color:terrain.color.map((v,i)=>v*(1-haze.amount)+haze.color[i]*haze.amount)};
 assert.equal(shaded.coverage,1);assert.ok(shaded.color[0]>terrain.color[0]);
});

test('height attenuates atmosphere separately and zero density/strength are exact bypasses',()=>{
 const options={near:0,far:100,heightFalloff:.3,baseHeight:10},low=sampleAtmosphere(80,{...options,height:10}),high=sampleAtmosphere(80,{...options,height:20});
 assert.ok(high.amount<low.amount);assert.ok(high.height_weight<low.height_weight);
 assert.equal(sampleAtmosphere(80,{...options,height:0}).amount,low.amount);
 for(const extra of [{density:0},{strength:0},{maxAmount:0}])near(sampleAtmosphere(80,extra).amount,0);
 assert.deepEqual(sampleAtmosphere(17.3,options),sampleAtmosphere(17.3,options));
});

test('atmosphere rejects nonfinite distance, reversed ranges and unsafe controls',()=>{
 for(const distance of [NaN,Infinity,-1,1e7,'1',true])assert.throws(()=>sampleAtmosphere(distance),/scene_surface/);
 for(const extra of [{near:5,far:5},{near:10,far:5},{density:65},{density:-1},{strength:2},{maxAmount:-1},
  {height:Infinity},{heightFalloff:65},{color:[256,0,0]},{color:'paper'},{opacity:.2}])
  assert.throws(()=>sampleAtmosphere(20,extra),/scene_surface/);
});

test('appearance queries are pure even if ambient random or wall clocks are unavailable',()=>{
 const random=Math.random,now=Date.now;
 try{
  Math.random=()=>{throw Error('ambient random forbidden');};Date.now=()=>{throw Error('wall clock forbidden');};
  assert.deepEqual(lighting([1,2,3],[3,2,1]),lighting([1,2,3],[3,2,1]));
  assert.deepEqual(material([1.25,2.5,3.75]),material([1.25,2.5,3.75]));
  assert.deepEqual(sampleAtmosphere(63,{height:5}),sampleAtmosphere(63,{height:5}));
 }finally{Math.random=random;Date.now=now;}
});
