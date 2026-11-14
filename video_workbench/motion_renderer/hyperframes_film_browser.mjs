// Dedicated offline bundle/server/browser adapter. No legacy guard changes.
import {createHash} from 'node:crypto';
import {readFileSync,lstatSync,readdirSync} from 'node:fs';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:http';
import {HYPERFRAMES_CORE_SHA256,hyperFramesRuntimeArtifact} from './hyperframes_project.mjs';
const hash=raw=>createHash('sha256').update(raw).digest('hex');
const check=(value,code)=>{if(!value)throw Error(code);};
const pathSafe=value=>typeof value==='string'&&/^[A-Za-z0-9_./-]+$/.test(value)&&value.length<=240
 &&value.split('/').every(part=>part&&part!=='.'&&part!=='..');
const mime=path=>({html:'text/html; charset=utf-8',mjs:'text/javascript; charset=utf-8',js:'text/javascript; charset=utf-8',
 css:'text/css; charset=utf-8',json:'application/json',svg:'image/svg+xml',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',
 webp:'image/webp',ttc:'font/collection',ttf:'font/ttf',woff:'font/woff',woff2:'font/woff2',txt:'text/plain; charset=utf-8'}
 [path.split('.').at(-1)]||'application/octet-stream');
export function readFilmBrowserBundle(root,expectedBundleSha256){
 root=resolve(root);
 for(let ancestor=root;;ancestor=dirname(ancestor)){
  const stat=lstatSync(ancestor);check(stat.isDirectory()&&!stat.isSymbolicLink(),'film_browser_root_or_ancestor_invalid');
  if(dirname(ancestor)===ancestor)break;
 }
 check(/^[a-f0-9]{64}$/.test(expectedBundleSha256),'film_browser_external_digest_required');
 const read=(path,maximum)=>{check(pathSafe(path),'film_browser_path_invalid');
  const stat=lstatSync(join(root,path));check(stat.isFile()&&!stat.isSymbolicLink()&&stat.size<=maximum,'film_browser_file_invalid');
  return readFileSync(join(root,path));};
 const raw=read('browser-manifest.json',1024*1024);check(hash(raw)===expectedBundleSha256,'film_browser_bundle_digest_mismatch');
 const bundle=JSON.parse(raw);
 check(bundle.schema==='video.hyperframes-film-browser-bundle.v1'&&bundle.execution_status==='not_executed'
  &&bundle.package_verification==='independent_project_recompilation'&&bundle.resources
  &&bundle.root_config?.schema==='video.hyperframes-film-root-config.v1','film_browser_bundle_invalid');
 const resources=new Map();let total=0;
 check(Object.keys(bundle.resources).length<=600,'film_browser_file_budget');
 for(const [path,identity] of Object.entries(bundle.resources)){
  const bytes=read(path,128*1024*1024);total+=bytes.length;
  check(total<=204*1024*1024&&bytes.length===identity.bytes&&hash(bytes)===identity.sha256,'film_browser_resource_changed');
  resources.set('/'+path,bytes);
 }
 const actual=[];
 function walk(relative=''){
  for(const name of readdirSync(join(root,relative))){const path=relative?relative+'/'+name:name;
   const stat=lstatSync(join(root,path));check(!stat.isSymbolicLink(),'film_browser_symlink_not_allowed');
   if(stat.isDirectory())walk(path);else {check(stat.isFile(),'film_browser_file_invalid');actual.push(path);}}
 }
 walk();check(actual.sort().join('\n')===[...Object.keys(bundle.resources),'browser-manifest.json'].sort().join('\n'),
  'film_browser_exact_inventory_required');
 const config=JSON.parse(resources.get('/host/config.json'));
 check(JSON.stringify(config)===JSON.stringify(bundle.root_config)&&config.project_manifest_sha256===bundle.project_manifest_sha256
  &&hash(resources.get('/project/manifest.json'))===config.project_manifest_sha256
  &&config.hyperframes_core_sha256===HYPERFRAMES_CORE_SHA256&&config.root_entry==='host/index.html'
  &&Number.isInteger(config.width)&&config.width>=320&&config.width<=1920
  &&Number.isInteger(config.height)&&config.height>=180&&config.height<=1080
  &&config.realm_protocol==='opaque-sandbox-message-channel-1','film_browser_root_binding_mismatch');
 for(const [name,sha] of Object.entries(config.host_files_sha256))
  check(hash(resources.get('/host/'+name))===sha,'film_browser_host_changed');
 check(config.host_files_sha256['hyperframes_film_browser.mjs']===hash(readFileSync(fileURLToPath(import.meta.url))),
  'film_browser_executor_adapter_changed');
 const project=JSON.parse(resources.get('/project/manifest.json'));
 check(project.schema==='video.hyperframes-film-project.v1'&&project.film_sha256===config.film_sha256
  &&project.execution?.status==='not_executed','film_browser_project_mismatch');
 return {bundle,config,project,resources,expectedBundleSha256};
}
export async function startFilmResourceServer(verified,{hyperFramesDist}={}){
 const artifact=hyperFramesRuntimeArtifact(hyperFramesDist),resources=new Map(verified.resources);
 resources.set('/runtime/hyperframes.js',readFileSync(artifact.runtimePath));
 const project=verified.project,modules=new Map(project.modules.map(module=>[module.module_id,module]));
 const forbidden=path=>path.startsWith('/project/recordings/');
 let origin;const loaded=new Set(),blocked=[];
 const server=createServer((request,response)=>{
  const path=request.url;
  if(request.method!=='GET'||!resources.has(path)||forbidden(path)){
   if(blocked.length<32)blocked.push('resource_not_admitted');response.writeHead(404);response.end();return;
  }
  const module=modules.get(verified.bundle.realms[path.slice(1)]);
  const scriptPaths=module?['/host/hyperframes_film_realm.mjs','/host/hyperframes_film_realm_guard.mjs','/host/hyperframes_film_channel.mjs',
   ...Object.keys(module.files_sha256).filter(name=>/\.(mjs|js)$/.test(name)).map(name=>'/project/'+name),
   ...module.assets.filter(name=>/\.(mjs|js)$/.test(name)).map(name=>'/project/'+name)]:[];
  const assetPaths=module?module.assets.map(name=>origin+'/project/'+name):[];
  const sourcePaths=module?Object.keys(module.files_sha256).map(name=>origin+'/project/'+name):[];
  const csp=module?`default-src 'none'; script-src ${scriptPaths.map(name=>origin+name).join(' ')}; style-src ${origin}/project/modules/${module.namespace}/ 'unsafe-inline'; img-src ${[...assetPaths,...sourcePaths].join(' ')} data:; font-src ${assetPaths.join(' ')||"'none'"}; connect-src ${[...assetPaths,...sourcePaths].join(' ')||"'none'"}; frame-src 'none'`:
   "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'self'";
  response.setHeader('Content-Security-Policy',csp+"; worker-src 'none'; media-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'");
  response.setHeader('Content-Type',mime(path));response.setHeader('X-Content-Type-Options','nosniff');
  response.setHeader('Cache-Control','no-store');response.setHeader('Referrer-Policy','no-referrer');
  if(path.startsWith('/project/modules/')||path.startsWith('/project/assets/')||path==='/host/hyperframes_film_realm.mjs'
   ||path==='/host/hyperframes_film_realm_guard.mjs'||path==='/host/hyperframes_film_channel.mjs')
   response.setHeader('Access-Control-Allow-Origin','*');
  loaded.add(path);response.end(resources.get(path));
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
 origin='http://127.0.0.1:'+server.address().port;
 return {origin,artifact,loaded,blocked,allows:url=>{try{const parsed=new URL(url);
  return parsed.origin===origin&&!parsed.search&&!parsed.hash&&resources.has(parsed.pathname)&&!forbidden(parsed.pathname);
 }catch{return false;}},async close(){await new Promise(resolve=>server.close(resolve));}};
}
export async function probeFilmBrowser({bundleRoot,expectedBundleSha256,chromium,executablePath,hyperFramesDist,
 frames=[0],onFrame}={}){
 const verified=readFilmBrowserBundle(bundleRoot,expectedBundleSha256),server=await startFilmResourceServer(verified,{hyperFramesDist});
 let browser;const errors=[],blocked=[],observations=[];
 try{
  browser=await chromium.launch({headless:true,executablePath,args:['--no-sandbox','--disable-setuid-sandbox',
   '--disable-dev-shm-usage','--disable-background-networking','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const context=await browser.newContext({viewport:{width:verified.config.width,height:verified.config.height},
   deviceScaleFactor:1,acceptDownloads:false});
  // Playwright's serviceWorkers=block helper reads the accessor on opaque
  // documents and itself raises SecurityError. Disable it before any script;
  // opaque origin and worker-src none provide additional independent denial.
  await context.addInitScript(()=>Object.defineProperty(Navigator.prototype,'serviceWorker',{
   value:undefined,writable:false,configurable:false}));
  await context.route('**/*',route=>{
   if(route.request().method()==='GET'&&server.allows(route.request().url()))return route.continue();
   if(blocked.length<32)blocked.push('external_or_undeclared_resource');return route.abort();
  });
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message.slice(0,240)));
  page.on('console',message=>{if(message.type()==='error'&&/Content Security Policy|violates.*directive|Refused to/i.test(message.text())
   &&errors.length<32)errors.push('film_browser_security_policy_error:'+message.text().slice(0,500));});
  page.on('popup',popup=>{errors.push('popup_not_allowed');popup.close();});page.on('dialog',dialog=>dialog.dismiss());
  await page.goto(server.origin+'/host/index.html',{waitUntil:'networkidle',timeout:30000});
  await page.waitForFunction(()=>typeof window.__trustedFilmBrowser?.seekFrame==='function',null,{timeout:30000});
  const serviceWorkerDisabled=await page.evaluate(()=>navigator.serviceWorker===undefined);
  check(serviceWorkerDisabled,'film_browser_service_worker_exposed');
  for(const frame of frames){
   const observation=await page.evaluate(frame=>window.__trustedFilmBrowser.seekFrame(frame),frame);
   check(errors.length===0,'film_browser_page_error:'+JSON.stringify(errors));
   check(blocked.length===0,'film_browser_network_blocked:'+JSON.stringify(blocked));
   const image=await page.screenshot({type:'png',animations:'allow'});
   await page.waitForTimeout(100);
   const idle=await page.screenshot({type:'png',animations:'allow'});
   check(image.equals(idle),'film_browser_autonomous_frame_changed');
   observations.push({...observation,frame_sha256:hash(image)});if(onFrame)await onFrame(frame,image,observation);
  }
  const cleanup=await page.evaluate(()=>window.__trustedFilmBrowser.close());
  check(cleanup.remaining_iframes===0&&cleanup.pending_cleanup_modules===0,'film_browser_cleanup_incomplete');
  check(errors.length===0&&blocked.length===0&&server.blocked.length===0,
   'film_browser_late_failure:'+JSON.stringify({errors,blocked,server:server.blocked}));
  return {schema:'video.hyperframes-film-technical-probe.v1',bundle_sha256:expectedBundleSha256,
   core_sha256:server.artifact.core_sha256,observations,cleanup,errors,blocked,
   service_worker_accessor_disabled:serviceWorkerDisabled,
   loaded_resources:[...server.loaded].sort(),evidence_scope:'real_core_and_technical_fixture_not_agent_visual_or_formal_delivery'};
 }finally{await browser?.close();await server.close();}
}
