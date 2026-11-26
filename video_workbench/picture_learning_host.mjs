// Isolated still capture. Authored scenes draw; this host owns PNG and receipts.
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {readFileSync, writeFileSync, lstatSync} from 'node:fs';
import {extname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {isDeepStrictEqual} from 'node:util';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
// Pinned together with the image-owned vendor module and license.
const INK_LANDSCAPE = {"assets":{"ink-landscape.LICENSE.txt":"b4c356e924703056db7f4392488d1911d3a532d83b752cfa8af9369d2c543108","ink-landscape.mjs":"c6bb3f07c09776beddddf04a7e7d9faae38ec8b6f3499abd8e6722d2115d46f6"},"engines":["canvas"],"entrypoint":"./assets/ink-landscape.mjs","library_id":"ink-landscape","license":"MIT","role":"drawing","schema":"video.learning-drawing-library.v1","source_revision":"b6c96a5e94124bfad8113aaa136ede2298151b5f","version":"0.1.0"};
const INK_ASSETS = ['ink-landscape.mjs', 'ink-landscape.LICENSE.txt'];
export function validateDrawingLibraries(request,resources){
  const libraries=request.drawing_libraries;
  if(libraries===undefined){
    if(INK_ASSETS.some(name=>resources.has('/assets/'+name)||request.assets_sha256?.[name]!==undefined))
      throw Error('learning_drawing_library_not_declared');
    return null;
  }
  const expected={'ink-landscape':INK_LANDSCAPE};
  if(!isDeepStrictEqual(libraries,expected)) throw Error('learning_drawing_library_invalid');
  for(const [name,sha] of Object.entries(INK_LANDSCAPE.assets))
    if(request.assets_sha256?.[name]!==sha||!resources.has('/assets/'+name)
      ||hash(resources.get('/assets/'+name))!==sha) throw Error('learning_drawing_library_asset_changed');
  return libraries;
}
const read = (path, max) => {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > max) throw Error('picture_file_invalid');
  return readFileSync(path);
};

// Measure calls in the exact pinned module via V8, never through scene counters.
export async function collectDrawingExecution(session, origin, previous = []) {
  const captured = await session.send('Profiler.takePreciseCoverage');
  const entries = new Map(previous.map(row => [row.start_offset + ':' + row.end_offset, {...row}]));
  for (const script of captured.result) {
    if (script.url !== origin + '/assets/ink-landscape.mjs') continue;
    const source = await session.send('Debugger.getScriptSource', {scriptId: script.scriptId});
    if (hash(Buffer.from(source.scriptSource, 'utf8')) !== INK_LANDSCAPE.assets['ink-landscape.mjs'])
      throw Error('learning_drawing_coverage_source_changed');
    for (const fn of script.functions) {
      const range = fn.ranges[0];
      if (!fn.functionName || !range || range.count <= 0) continue;
      if (!Number.isSafeInteger(range.count) || fn.functionName.length > 200)
        throw Error('learning_drawing_coverage_invalid');
      const key = range.startOffset + ':' + range.endOffset, prior = entries.get(key);
      entries.set(key, {function_name: fn.functionName, start_offset: range.startOffset,
        end_offset: range.endOffset, call_count: (prior?.call_count || 0) + range.count});
    }
  }
  const functions = [...entries.values()].sort((a,b) => a.start_offset-b.start_offset || a.end_offset-b.end_offset);
  if (functions.length > 1024 || functions.some(row => !Number.isSafeInteger(row.call_count)))
    throw Error('learning_drawing_coverage_invalid');
  return functions;
}
function drawingExecution(functions) {
  return {schema:'video.learning-drawing-api-execution.v1',
    scope:'v8_executed_functions_not_visual_application',
    module_sha256:INK_LANDSCAPE.assets['ink-landscape.mjs'], functions};
}

export async function capturePicture({chromium, request, resources, engineRuntime, projectRuntime,
  executablePath = '/opt/browser/headless_shell', hyperFramesDist = '/opt/renderer/node_modules/hyperframes/dist',
  deadlineAtMs = Date.now() + 3600000}) {
  if (!Number.isFinite(deadlineAtMs)) throw Error('picture_render_deadline_invalid');
  const deadline = performance.now() + Math.min(3600000, deadlineAtMs - Date.now());
  const stage = async (name, cap, action) => {
    const remaining = Math.floor(deadline - performance.now());
    if (remaining <= 0) throw Error('picture_render_deadline_exceeded');
    const timeout = Math.min(cap, remaining);
    let timer;
    try {
      return await Promise.race([
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(Error(remaining <= cap ?
            'picture_render_deadline_exceeded' : 'picture_' + name + '_timeout')), timeout);
        }),
        Promise.resolve().then(() => action(timeout)),
      ]);
    } finally {
      clearTimeout(timer);
    }
  };
  const drawingLibraries = validateDrawingLibraries(request, resources);
  const c = request.constraints, engine = request.engine;
  const generated = request.generated_resources;
  if (generated !== undefined && (!Array.isArray(generated) || generated.length !== 1
    || generated[0]?.schema !== 'video.manim-method-resource.v1'
    || generated[0]?.manifest?.frame_count !== 1 || generated[0]?.manifest?.global_start_frame !== 0))
    throw Error('picture_resource_single_frame_required');
  const project = projectRuntime.buildHyperFramesProject(request.project,
    Object.fromEntries([...resources].filter(([n]) => !n.startsWith('/assets/')).map(([n,b]) => [n.slice(1),b])),
    Object.fromEntries([...resources].filter(([n]) => n.startsWith('/assets/')).map(([n,b]) => [n.slice(1),b]))).project;
  const core = projectRuntime.hyperFramesRuntimeArtifact(hyperFramesDist);
  const mime = {'.html':'text/html;charset=utf-8','.js':'text/javascript;charset=utf-8',
    '.mjs':'text/javascript;charset=utf-8','.css':'text/css;charset=utf-8','.ttc':'font/collection',
    '.png':'image/png','.json':'application/json'};
  const server = createServer((req,res) => {
    const url = new URL(req.url,'http://localhost'), bytes = resources.get(url.pathname);
    res.setHeader('Content-Security-Policy',"default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; media-src 'none'; worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'");
    res.setHeader('X-Content-Type-Options','nosniff');
    if (req.method !== 'GET' || url.search || !bytes) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type',mime[extname(url.pathname)] || 'application/octet-stream'); res.end(bytes);
  });
  let browser;
  await new Promise((resolve,reject) => { server.once('error',reject); server.listen(0,'127.0.0.1',resolve); });
  try {
    const origin = 'http://127.0.0.1:' + server.address().port, errors = [], blocked = [];
    const scripts = new Set(), loadedPNG = new Set();
    browser = await stage('launch',60000,timeout=>chromium.launch({headless:true,executablePath,timeout,
      args:engineRuntime.browserLaunchArgs(engine)}));
    const setup = action => stage('browser_setup',60000,action);
    const context = await setup(()=>browser.newContext({viewport:{width:c.width,height:c.height},deviceScaleFactor:1,
      serviceWorkers:'block',acceptDownloads:false}));
    await setup(()=>engineRuntime.installEngineGuard(context,engine));
    await setup(()=>context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin === origin && resources.has(url.pathname) && !url.search && route.request().method() === 'GET') {
        if (route.request().resourceType() === 'script') scripts.add(url.pathname);
        if (url.pathname.endsWith('.png')) loadedPNG.add(url.pathname);
        await route.continue();
      }
      else { if (blocked.length < 16) blocked.push(url.protocol); await route.abort(); }
    }));
    if(context.routeWebSocket) await setup(()=>context.routeWebSocket('**/*',socket => { blocked.push('websocket'); socket.close(); }));
    const page = await setup(()=>context.newPage());
    const security = await setup(()=>context.newCDPSession(page));
    await setup(()=>security.send('Log.enable'));
    let drawingFunctions = [];
    if (drawingLibraries) await setup(async()=>{
      await security.send('Debugger.enable'); await security.send('Profiler.enable');
      await security.send('Profiler.startPreciseCoverage', {callCount:true,detailed:true});
    });
    security.on('Log.entryAdded',({entry}) => { if(entry.source==='security' && entry.level==='error') errors.push('security_policy'); });
    page.on('pageerror',() => { if(errors.length<16) errors.push('scene_error'); });
    page.on('crash',() => errors.push('page_crashed'));
    page.on('popup',popup => { errors.push('popup_forbidden'); popup.close(); });
    page.on('dialog',dialog => dialog.dismiss());
    const assertScene = () => {
      if (errors.length || blocked.length) throw Error('picture_scene_failed_or_blocked');
    };
    const ready = async () => {
      assertScene();
      // Still capture uses its own budget while the pinned shared HyperFrames
      // installer keeps its existing limits for other renderer callers.
      await stage('project_ready',180000,async timeout=>{
        if (hash(read(core.runtimePath,16*1024*1024))!==core.core_sha256)
          throw Error('hyperframes_runtime_digest_mismatch');
        await page.evaluate(projectRuntime.prepareHyperFramesPage,{project,legacyBridge:true});
        assertScene();
        await page.addScriptTag({path:core.runtimePath});
        await page.waitForFunction(()=>window.__playerReady===true&&window.__renderReady===true
          &&typeof window.__player?.renderSeek==='function',null,{timeout,polling:50});
        await page.evaluate(projectRuntime.bindHyperFramesCore);
        assertScene();
      });
    };
    const capture = async () => {
      await stage('seek',60000,timeout=>projectRuntime.seekHyperFramesProject(page,0,{timeoutMs:timeout}));
      assertScene();
      await stage('scene_assets',120000,()=>page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all([...document.images].map(image => image.decode()));
        if(document.getAnimations().some(animation=>animation.playState==='running')) throw Error('picture_autonomous_animation');
      }));
      const observation = await stage('engine_check',60000,()=>engineRuntime.verifyPageEngine(page,engine));
      const bytes = await stage('screenshot',120000,timeout=>
        page.screenshot({type:'png',animations:'disabled',timeout}));
      assertScene();
      if(generated) {
        const expectedPNG = generated.flatMap(item => Object.keys(item.assets_sha256)
          .filter(name => name.endsWith('.png')).map(name => '/assets/' + name)).sort();
        if (!scripts.has('/assets/manim_frame_layer.mjs') || !scripts.has('/assets/manim_method_frames.mjs')
          || JSON.stringify([...loadedPNG].sort()) !== JSON.stringify(expectedPNG))
          throw Error('picture_manim_resource_not_consumed');
      }
      return {bytes,observation};
    };
    await stage('navigation',180000,timeout=>page.goto(origin+'/scene.html',{waitUntil:'networkidle',timeout}));
    await ready(); const first = await capture(), repeat = await capture();
    if (drawingLibraries) drawingFunctions = await stage('coverage',60000,()=>collectDrawingExecution(security, origin, drawingFunctions));
    await stage('navigation',180000,timeout=>page.reload({waitUntil:'networkidle',timeout}));
    await ready();
    const reload = await capture();
    if (drawingLibraries) drawingFunctions = await stage('coverage',60000,()=>collectDrawingExecution(security, origin, drawingFunctions));
    if(!first.bytes.equals(repeat.bytes) || !first.bytes.equals(reload.bytes)) throw Error('picture_repeat_or_reload_mismatch');
    return {bytes:first.bytes,engine_observation:first.observation,
      ...(drawingLibraries ? {drawing_library_observation:{schema:'video.learning-drawing-library-observation.v1',
        scope:'loaded_scripts_not_api_calls_or_visual_application',drawing_libraries:drawingLibraries,
        api_execution:drawingExecution(drawingFunctions),
        loaded_scripts:[...scripts].sort(),loaded_libraries:scripts.has('/assets/ink-landscape.mjs')?['ink-landscape']:[]}} : {}),
      checks:{repeat_sha256:hash(repeat.bytes),reload_sha256:hash(reload.bytes),seek_seconds:0},
      ...(generated ? {generated_resource_observation:{schema:'video.manim-method-resource-observation.v1',
        scope:'loaded_resources_not_visual_acceptance', helpers_loaded:true,
        resources_sha256:generated.map(item=>item.sha256),loaded_png_paths:[...loadedPNG].sort()}} : {}),
      project_runtime:{...(await stage('engine_check',60000,()=>page.evaluate(()=>({...window.__trustedHyperFramesProject.state})))),
        core_sha256:core.core_sha256,version:core.version,project_sha256:request.project_sha256}};
  } finally {
    if(browser) {
      let timer;
      try {
        await Promise.race([browser.close().catch(()=>{}), new Promise(resolve=>{
          timer = setTimeout(resolve,2000);
        })]);
      } finally { clearTimeout(timer); }
    }
    server.closeAllConnections();
    await new Promise(resolve=>server.close(resolve));
  }
}

async function main() {
  const receipt = {schema:'video.picture-learning-render-receipt.v1',status:'starting',evidence_scope:'static_only'};
  try {
    const raw=read('/input/request.json',256*1024), request=JSON.parse(raw);
    if(!['video.picture-learning-render-request.v1','video.picture-learning-render-request.v2'].includes(request.schema)
      || (request.schema==='video.picture-learning-render-request.v2') !== Array.isArray(request.generated_resources)
      || hash(raw)!==process.env.ANIMATION_METHOD_REQUEST_SHA256
      || request.image_digest!==process.env.ANIMATION_METHOD_IMAGE_DIGEST
      || hash(read(fileURLToPath(import.meta.url),1024*1024))!==request.host_sha256
      || !['html-svg','canvas','three'].includes(request.engine)) throw Error('picture_request_binding_invalid');
    for(const [name,key] of [['engine_runtime.mjs','engine_runtime_sha256'],['hyperframes_project.mjs','project_runtime_sha256']])
      if(hash(read('/opt/renderer/'+name,1024*1024))!==request[key]) throw Error('picture_runtime_changed');
    const c=request.constraints;
    if(!Number.isInteger(c.width)||c.width<320||c.width>1920||!Number.isInteger(c.height)||c.height<180||c.height>1080)
      throw Error('picture_dimensions_invalid');
    const resources=new Map();
    for(const [name,sha] of Object.entries(request.files_sha256)) {
      if(!['scene.html','scene.js','scene.css'].includes(name)) throw Error('picture_scene_path_invalid');
      const bytes=read('/input/scene/'+name,512*1024);
      if(hash(bytes)!==sha) throw Error('picture_source_changed'); resources.set('/'+name,bytes);
    }
    const generatedAssets = new Map((request.generated_resources || []).flatMap(item=>Object.entries(item.assets_sha256)));
    for(const [name,sha] of Object.entries(request.assets_sha256)) {
      if(!['gsap.min.js','NotoSansCJK-Regular.ttc','three.module.min.js','three.LICENSE.txt'].includes(name)
        && !(request.drawing_libraries && INK_ASSETS.includes(name))
        && generatedAssets.get(name)!==sha) throw Error('picture_asset_invalid');
      if(name.startsWith('/') || name.split('/').some(part=>!part || part==='.' || part==='..')
        || /[\\\x00-\x1f]/.test(name)) throw Error('picture_asset_path_invalid');
      const bytes=read('/input/scene/assets/'+name,32*1024*1024);
      if(hash(bytes)!==sha) throw Error('picture_asset_changed'); resources.set('/assets/'+name,bytes);
    }
    const engineRuntime=await import('./engine_runtime.mjs'), projectRuntime=await import('./hyperframes_project.mjs');
    const {chromium}=createRequire('/opt/renderer/package.json')('playwright-core');
    const deadlineAtMs=process.env.ANIMATION_METHOD_DEADLINE_AT_MS===undefined ?
      undefined : Number(process.env.ANIMATION_METHOD_DEADLINE_AT_MS);
    const {bytes,...evidence}=await capturePicture({chromium,request,resources,engineRuntime,projectRuntime,deadlineAtMs});
    if(bytes.length>8*1024*1024) throw Error('picture_output_too_large');
    writeFileSync('/work/output.png',bytes);
    Object.assign(receipt,{status:'rendered',request_identity:request.identity,request_sha256:hash(raw),
      image_digest:request.image_digest,host_sha256:request.host_sha256,engine:request.engine,
      engine_runtime_sha256:request.engine_runtime_sha256,project_runtime_sha256:request.project_runtime_sha256,
      output_sha256:hash(bytes),output_bytes:bytes.length,width:c.width,height:c.height,
      ...evidence,expressive_quality:'not_evaluated'});
  } catch(error) { receipt.status='failed'; receipt.error=String(error).slice(0,500); process.exitCode=1; }
  finally { writeFileSync('/work/render-receipt.json',JSON.stringify(receipt)); }
}
if(process.argv[1]===fileURLToPath(import.meta.url)) await main();
