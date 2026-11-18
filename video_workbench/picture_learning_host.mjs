// Isolated still capture. Authored scenes draw; this host owns PNG and receipts.
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {readFileSync, writeFileSync, lstatSync} from 'node:fs';
import {extname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const read = (path, max) => {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > max) throw Error('picture_file_invalid');
  return readFileSync(path);
};

export async function capturePicture({chromium, request, resources, engineRuntime, projectRuntime,
  executablePath = '/opt/browser/headless_shell', hyperFramesDist = '/opt/renderer/node_modules/hyperframes/dist'}) {
  const c = request.constraints, engine = request.engine;
  const project = projectRuntime.buildHyperFramesProject(request.project,
    Object.fromEntries([...resources].filter(([n]) => !n.startsWith('/assets/')).map(([n,b]) => [n.slice(1),b])),
    Object.fromEntries([...resources].filter(([n]) => n.startsWith('/assets/')).map(([n,b]) => [n.slice(1),b]))).project;
  const core = projectRuntime.hyperFramesRuntimeArtifact(hyperFramesDist);
  const mime = {'.html':'text/html;charset=utf-8','.js':'text/javascript;charset=utf-8',
    '.mjs':'text/javascript;charset=utf-8','.css':'text/css;charset=utf-8','.ttc':'font/collection','.png':'image/png'};
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
    browser = await chromium.launch({headless:true,executablePath,args:engineRuntime.browserLaunchArgs(engine)});
    const context = await browser.newContext({viewport:{width:c.width,height:c.height},deviceScaleFactor:1,
      serviceWorkers:'block',acceptDownloads:false});
    await engineRuntime.installEngineGuard(context,engine);
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin === origin && resources.has(url.pathname) && !url.search && route.request().method() === 'GET') await route.continue();
      else { if (blocked.length < 16) blocked.push(url.protocol); await route.abort(); }
    });
    if(context.routeWebSocket) await context.routeWebSocket('**/*',socket => { blocked.push('websocket'); socket.close(); });
    const page = await context.newPage(); page.setDefaultTimeout(20000);
    const security = await context.newCDPSession(page); await security.send('Log.enable');
    security.on('Log.entryAdded',({entry}) => { if(entry.source==='security' && entry.level==='error') errors.push('security_policy'); });
    page.on('pageerror',() => { if(errors.length<16) errors.push('scene_error'); });
    page.on('crash',() => errors.push('page_crashed'));
    page.on('popup',popup => { errors.push('popup_forbidden'); popup.close(); });
    page.on('dialog',dialog => dialog.dismiss());
    const ready = async () => {
      await page.goto(origin+'/scene.html',{waitUntil:'networkidle',timeout:30000});
      await projectRuntime.installHyperFramesProject(page,{project,runtimePath:core.runtimePath,legacyBridge:true});
    };
    const capture = async () => {
      await projectRuntime.seekHyperFramesProject(page,0);
      await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all([...document.images].map(image => image.decode()));
        if(document.getAnimations().some(animation=>animation.playState==='running')) throw Error('picture_autonomous_animation');
      });
      const observation = await engineRuntime.verifyPageEngine(page,engine);
      const bytes = await page.screenshot({type:'png',animations:'disabled',timeout:30000});
      if(errors.length || blocked.length) throw Error('picture_scene_failed_or_blocked');
      return {bytes,observation};
    };
    await ready(); const first = await capture(), repeat = await capture();
    await page.reload({waitUntil:'networkidle',timeout:30000});
    await projectRuntime.installHyperFramesProject(page,{project,runtimePath:core.runtimePath,legacyBridge:true});
    const reload = await capture();
    if(!first.bytes.equals(repeat.bytes) || !first.bytes.equals(reload.bytes)) throw Error('picture_repeat_or_reload_mismatch');
    return {bytes:first.bytes,engine_observation:first.observation,
      checks:{repeat_sha256:hash(repeat.bytes),reload_sha256:hash(reload.bytes),seek_seconds:0},
      project_runtime:{...(await page.evaluate(()=>({...window.__trustedHyperFramesProject.state}))),
        core_sha256:core.core_sha256,version:core.version,project_sha256:request.project_sha256}};
  } finally {
    if(browser) await browser.close().catch(()=>{});
    await new Promise(resolve=>server.close(resolve));
  }
}

async function main() {
  const receipt = {schema:'video.picture-learning-render-receipt.v1',status:'starting',evidence_scope:'static_only'};
  try {
    const raw=read('/input/request.json',256*1024), request=JSON.parse(raw);
    if(request.schema!=='video.picture-learning-render-request.v1' || hash(raw)!==process.env.ANIMATION_METHOD_REQUEST_SHA256
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
    for(const [name,sha] of Object.entries(request.assets_sha256)) {
      if(!['gsap.min.js','NotoSansCJK-Regular.ttc','three.module.min.js','three.LICENSE.txt'].includes(name)) throw Error('picture_asset_invalid');
      const bytes=read('/input/scene/assets/'+name,32*1024*1024);
      if(hash(bytes)!==sha) throw Error('picture_asset_changed'); resources.set('/assets/'+name,bytes);
    }
    const engineRuntime=await import('./engine_runtime.mjs'), projectRuntime=await import('./hyperframes_project.mjs');
    const {chromium}=createRequire('/opt/renderer/package.json')('playwright-core');
    const {bytes,...evidence}=await capturePicture({chromium,request,resources,engineRuntime,projectRuntime});
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
