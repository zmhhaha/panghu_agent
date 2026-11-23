// Asset custody and V8 evidence logic. Synthetic coverage is not a render claim.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as picture from '../../video_workbench/picture_learning_host.mjs';
import * as video from '../../video_workbench/video_learning_host.mjs';

const vendor=new URL('../../video_workbench/vendor/ink-landscape/',import.meta.url);
const manifest=JSON.parse(readFileSync(new URL('manifest.json',vendor),'utf8'));
const descriptor={schema:'video.learning-drawing-library.v1',library_id:'ink-landscape',
  version:manifest.version,source_revision:manifest.source_revision,license:'MIT',role:'drawing',
  engines:['canvas'],entrypoint:'./assets/ink-landscape.mjs',assets:manifest.assets};
const assets=new Map(Object.keys(descriptor.assets).map(name=>[
  '/assets/'+name,readFileSync(new URL(name,vendor))]));
const request=()=>({assets_sha256:structuredClone(descriptor.assets),
  drawing_libraries:{'ink-landscape':structuredClone(descriptor)}});

for(const [label,host] of [['picture',picture],['video',video]]) {
  test(`${label} host accepts only exact local library and license bytes`,()=>{
    assert.deepEqual(host.validateDrawingLibraries(request(),assets),request().drawing_libraries);
    for(const key of ['version','source_revision','entrypoint']) {
      const changed=request();changed.drawing_libraries['ink-landscape'][key]='changed';
      assert.throws(()=>host.validateDrawingLibraries(changed,assets),/library_invalid/);
    }
    for(const name of Object.keys(descriptor.assets)) {
      const changed=new Map(assets);changed.set('/assets/'+name,Buffer.from('changed'));
      assert.throws(()=>host.validateDrawingLibraries(request(),changed),/asset_changed/);
    }
  });
  test(`${label} frozen tasks without descriptor cannot receive or load library`,()=>{
    assert.equal(host.validateDrawingLibraries({assets_sha256:{}},new Map()),null);
    assert.throws(()=>host.validateDrawingLibraries({assets_sha256:descriptor.assets},assets),/not_declared/);
    assert.throws(()=>host.validateDrawingLibraries({assets_sha256:{}},assets),/not_declared/);
  });
  test(`${label} V8 top-level module evaluation does not count as drawing calls`,async()=>{
    const session={send:async(method)=>method==='Profiler.takePreciseCoverage'?{result:[{
      scriptId:'1',url:'http://localhost/assets/ink-landscape.mjs',functions:[
        {functionName:'',ranges:[{startOffset:0,endOffset:100,count:1}]},
        {functionName:'renderCanvas',ranges:[{startOffset:10,endOffset:90,count:0}]}]}]}:
      {scriptSource:assets.get('/assets/ink-landscape.mjs').toString('utf8')}};
    assert.deepEqual(await host.collectDrawingExecution(session,'http://localhost'),[]);
  });
  test(`${label} V8 calls accumulate across reload and verify actual pinned script source`,async()=>{
    const session={send:async(method)=>method==='Profiler.takePreciseCoverage'?{result:[{
      scriptId:'1',url:'http://localhost/assets/ink-landscape.mjs',functions:[
        {functionName:'renderCanvas',ranges:[{startOffset:10,endOffset:90,count:2}]}]}]}:
      {scriptSource:assets.get('/assets/ink-landscape.mjs').toString('utf8')}};
    const first=await host.collectDrawingExecution(session,'http://localhost');
    assert.deepEqual(first,[{function_name:'renderCanvas',start_offset:10,end_offset:90,call_count:2}]);
    assert.equal((await host.collectDrawingExecution(session,'http://localhost',first))[0].call_count,4);
    const spoof={send:async(method,params)=>method==='Debugger.getScriptSource'?
      {scriptSource:'function renderCanvas(){}'}:session.send(method,params)};
    await assert.rejects(host.collectDrawingExecution(spoof,'http://localhost'),/coverage_source_changed/);
  });
}
