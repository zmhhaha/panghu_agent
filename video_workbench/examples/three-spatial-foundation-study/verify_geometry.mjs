// Verify native Three input against the retained authored heightfield, without
// rendering again or importing its unrelated brush/DOM initialization.
import {readFileSync,writeFileSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname,join,resolve} from 'node:path';
import {createHash} from 'node:crypto';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const root=dirname(fileURLToPath(import.meta.url)),out=resolve(process.argv[2]);
assert(process.argv[2]&&existsSync(join(out,'source-meshes.json')));
const raw=readFileSync(join(root,'../spatial-foundation-study/baseline-source.json'));
const baseline=JSON.parse(raw),source=baseline.files['scene.js'];
assert.equal(baseline.source_sha256,'1b6693b620f576dacf2b328367999a00458b8440f3f86ff825e996b153734be0');
const a=source.indexOf('const MASS_CONFIG='),b=source.indexOf('const MASS_FIELDS=new Map();',a);
const c=source.indexOf('function authoredHeight('),d=source.indexOf('function fieldFor(',c);
assert(a>=0&&b>a&&c>b&&d>c);
const sandbox={};vm.runInNewContext('const sat=t=>Math.max(0,Math.min(1,t));\n'
 +source.slice(a,b)+'\n'+source.slice(c,d)+'\nthis.config=MASS_CONFIG;this.height=authoredHeight;',sandbox,{timeout:1000});
const meshes=JSON.parse(readFileSync(join(out,'source-meshes.json'))),results=[];
assert.deepEqual(meshes.map(mesh=>mesh.id).sort(),Object.keys(sandbox.config).sort());
for(const mesh of meshes){const cfg=sandbox.config[mesh.id],C=cfg.grid.columns,R=cfg.grid.rows;
 assert.equal(mesh.vertices,C*R);assert.equal(mesh.indices,(C-1)*(R-1)*6);
 assert.equal(mesh.local_positions.length,C*R*3);let maximumRoundingError=0;
 for(let j=0;j<R;j++)for(let i=0;i<C;i++){
  const x=cfg.bounds.x+i*cfg.bounds.width/(C-1),z=cfg.bounds.z+j*cfg.bounds.depth/(R-1);
  const wanted=[x,sandbox.height(x,z,cfg),z],offset=(j*C+i)*3;
  for(let k=0;k<3;k++){assert.equal(mesh.local_positions[offset+k],Math.fround(wanted[k]));
   maximumRoundingError=Math.max(maximumRoundingError,Math.abs(mesh.local_positions[offset+k]-wanted[k]));}
 }
 results.push({id:mesh.id,vertices:mesh.vertices,indices:mesh.indices,
  every_native_position_matches_authored_float32:true,maximum_float32_rounding_error:maximumRoundingError,
  position_sha256:createHash('sha256').update(Buffer.from(Float32Array.from(mesh.local_positions).buffer)).digest('hex')});
}
const result={schema:'video.three-retained-geometry-comparison.v1',passed:true,
 baseline_source_sha256:baseline.source_sha256,baseline_file_sha256:createHash('sha256').update(raw).digest('hex'),
 source_meshes_file_sha256:createHash('sha256').update(readFileSync(join(out,'source-meshes.json'))).digest('hex'),
 scope:'all_native_input_vertices_and_triangle_counts_against_retained_heightfields_not_artistic_equivalence',
 meshes:results,render_calls:0,model_calls:0};
const target=join(out,'geometry-comparison.json');assert(!existsSync(target));writeFileSync(target,JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result));
