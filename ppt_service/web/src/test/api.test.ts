import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defaultSettings, type Project } from '../domain';
import { syntheticSnapshot } from '../acceptance/fixtures';

let client:typeof import('../api');
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status});
function deferred<T>() {
 let resolve!:(value:T)=>void;
 const promise=new Promise<T>(done=>{resolve=done;});
 return {promise,resolve};
}
beforeEach(async()=>{vi.resetModules();client=await import('../api');});
afterEach(()=>{vi.unstubAllGlobals();});

describe('profile and CSRF recovery lifecycle',()=>{
 it('binds review-only revision to the explicit draft and current saved review without a project PATCH',async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce(response({csrf_token:'fixture-token'})).mockResolvedValueOnce(response({job_id:'new-review-job'},202));
  vi.stubGlobal('fetch',fetcher);await client.api.me();
  const project:Project={id:'p',title:'fixture',stage:'awaiting_review',revision:7,input_snapshot_hash:'snapshot',settings:defaultSettings,assets:[],external_model_ack:true};
  const binding={...syntheticSnapshot('review-1').binding,project_id:'p'};
  await expect(client.api.reviewRevise(project,'review-1',[2],'修正标题重叠',binding,3)).resolves.toEqual({job_id:'new-review-job'});
  expect(fetcher.mock.calls.map(([url])=>url)).toEqual(['/api/v1/me','/api/v1/projects/p/review-revisions']);
  const request=fetcher.mock.calls[1][1];expect(request.method).toBe('POST');
  expect(JSON.parse(request.body)).toEqual({expected_revision:7,base_version_id:'review-1',pages:[2],feedback:'修正标题重叠',review_binding:binding,review_revision:3});
  expect(request.headers.get('X-CSRF-Token')).toBe('fixture-token');expect(request.headers.get('Idempotency-Key')).toBeTruthy();
 });

 it.each([false,undefined])('never sends a review revision when the project has no saved external-model consent (%s)',async(external_model_ack)=>{
  const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
  const project:Project={id:'p',title:'fixture',stage:'awaiting_review',revision:7,input_snapshot_hash:'snapshot',settings:defaultSettings,assets:[],external_model_ack};
  await expect(client.api.reviewRevise(project,'review-1',[],'调整布局',syntheticSnapshot('review-1').binding,0)).rejects.toMatchObject({code:'CONSENT_REQUIRED'});
  expect(fetcher).not.toHaveBeenCalled();
 });

 it('keeps a stale review error safe and never retries a write or substitutes a newer review binding',async()=>{
  const fetcher=vi.fn().mockResolvedValue(response({detail:{code:'review_records_changed'}},409));vi.stubGlobal('fetch',fetcher);
  const project:Project={id:'p',title:'fixture',stage:'awaiting_review',revision:7,input_snapshot_hash:'snapshot',settings:defaultSettings,assets:[],external_model_ack:true};
  await expect(client.api.reviewRevise(project,'review-1',[],'调整布局',syntheticSnapshot('review-1').binding,2)).rejects.toMatchObject({code:'REVIEW_RECORDS_CHANGED',status:409});
  expect(fetcher).toHaveBeenCalledOnce();expect(client.safeError(new client.ApiError('review_records_changed',409))).toContain('重新读取记录');
 });

 it('shares only pending profile reads and fetches the next identity afresh',async()=>{
  const pending=deferred<Response>();
  const fetcher=vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response({sub:'second-user',csrf_token:'second-fixture'}));
  vi.stubGlobal('fetch',fetcher);
  const first=client.api.me();const concurrent=client.api.me();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(concurrent).toBe(first);
  pending.resolve(response({sub:'first-user',csrf_token:'first-fixture'}));
  expect(await first).toMatchObject({subject:'first-user'});
  expect(await concurrent).not.toHaveProperty('csrf_token');
  expect(await client.api.me()).toMatchObject({subject:'second-user'});
  expect(fetcher).toHaveBeenCalledTimes(2);
 });

 it('does not restore an old identity or header when a pending me settles after 401',async()=>{
  const stale=deferred<Response>();const current=deferred<Response>();
  const fetcher=vi.fn().mockReturnValueOnce(stale.promise).mockResolvedValueOnce(response({},401)).mockReturnValueOnce(current.promise).mockResolvedValueOnce(response({}));
  vi.stubGlobal('fetch',fetcher);
  const beforeExpiry=client.api.me();
  await expect(client.request('/projects')).rejects.toMatchObject({status:401});
  const afterExpiry=client.api.me();
  stale.resolve(response({sub:'stale-user',csrf_token:'stale-fixture'}));
  await expect(beforeExpiry).rejects.toMatchObject({code:'UNAUTHENTICATED',status:401});
  expect(client.api.me()).toBe(afterExpiry);
  current.resolve(response({sub:'current-user',csrf_token:'current-fixture'}));
  expect(await afterExpiry).toMatchObject({subject:'current-user'});
  await client.mutate('/projects',{title:'Current identity'});
  expect(new Headers(fetcher.mock.calls[3][1].headers).get('X-CSRF-Token')).toBe('current-fixture');
 });

 it('refreshes once after exact CSRF rejection and preserves the JSON intent',async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce(response({sub:'owner',csrf_token:'old-fixture'}))
   .mockResolvedValueOnce(response({detail:{code:'csrf_rejected'}},403))
   .mockResolvedValueOnce(response({sub:'owner',csrf_token:'fresh-fixture'}))
   .mockResolvedValueOnce(response({saved:true}));
  vi.stubGlobal('fetch',fetcher);
  await client.api.me();
  const result=await client.mutate('/projects/p',{expected_revision:7,title:'Same intent'},'PATCH');
  expect(result).toEqual({saved:true});
  expect(fetcher.mock.calls.map(([url])=>url)).toEqual(['/api/v1/me','/api/v1/projects/p','/api/v1/me','/api/v1/projects/p']);
  const first=fetcher.mock.calls[1][1];const retry=fetcher.mock.calls[3][1];
  expect(first.method).toBe('PATCH');expect(retry.method).toBe('PATCH');
  expect(first.body).toBe(retry.body);
  expect(first.headers.get('Idempotency-Key')).toBe(retry.headers.get('Idempotency-Key'));
  expect(first.headers.get('X-CSRF-Token')).toBe('old-fixture');
  expect(retry.headers.get('X-CSRF-Token')).toBe('fresh-fixture');
 });

 it('replays the same multipart file and upload intent after CSRF recovery',async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce(response({csrf_token:'old-fixture'}))
   .mockResolvedValueOnce(response({detail:{code:'csrf_rejected'}},403))
   .mockResolvedValueOnce(response({csrf_token:'fresh-fixture'}))
   .mockResolvedValueOnce(response({id:'asset'}));
  vi.stubGlobal('fetch',fetcher);await client.api.me();
  const project:Project={id:'p',title:'Synthetic',stage:'draft',revision:7,input_snapshot_hash:'snapshot',settings:defaultSettings,assets:[]};
  const file=new File(['Synthetic only'],'material.md',{type:'text/markdown'});
  await client.api.upload(project,file);
  const first=fetcher.mock.calls[1][1];const retry=fetcher.mock.calls[3][1];
  expect(first.body).toBeInstanceOf(FormData);
  expect(retry.body).toBe(first.body);
  expect((retry.body as FormData).get('file')).toBe(file);
  expect(first.headers.get('Idempotency-Key')).toBe(retry.headers.get('Idempotency-Key'));
  expect(retry.headers.get('Content-Type')).toBeNull();
  expect(retry.headers.get('X-CSRF-Token')).toBe('fresh-fixture');
 });

 it('removes only the selected asset at the expected project revision and preserves its DELETE intent on CSRF recovery',async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce(response({csrf_token:'old-fixture'}))
   .mockResolvedValueOnce(response({detail:{code:'csrf_rejected'}},403))
   .mockResolvedValueOnce(response({csrf_token:'fresh-fixture'}))
   .mockResolvedValueOnce(response({revision:8}));
  vi.stubGlobal('fetch',fetcher);await client.api.me();
  const project:Project={id:'project / fixture',title:'fixture',stage:'draft',revision:7,input_snapshot_hash:'snapshot',settings:defaultSettings,assets:[]};
  await client.api.removeAsset(project,'asset / fixture');
  const expected='/api/v1/projects/project%20%2F%20fixture/assets/asset%20%2F%20fixture?expected_revision=7';
  expect(fetcher.mock.calls.map(([url])=>url)).toEqual(['/api/v1/me',expected,'/api/v1/me',expected]);
  const first=fetcher.mock.calls[1][1];const retry=fetcher.mock.calls[3][1];
  expect(first.method).toBe('DELETE');expect(retry.method).toBe('DELETE');
  expect(retry.body).toBe(first.body);
  expect(retry.headers.get('Idempotency-Key')).toBe(first.headers.get('Idempotency-Key'));
  expect(retry.headers.get('X-CSRF-Token')).toBe('fresh-fixture');
 });

 it('reparses only the selected asset with the expected revision and preserves its POST intent through CSRF recovery',async()=>{
  const queued={revision:8,asset_id:'asset / fixture',parse_status:'queued'};
  const fetcher=vi.fn().mockResolvedValueOnce(response({csrf_token:'old-fixture'}))
   .mockResolvedValueOnce(response({detail:{code:'csrf_rejected'}},403))
   .mockResolvedValueOnce(response({csrf_token:'fresh-fixture'}))
   .mockResolvedValueOnce(response(queued));
  vi.stubGlobal('fetch',fetcher);await client.api.me();
  const project:Project={id:'project / fixture',title:'fixture',stage:'draft',revision:7,input_snapshot_hash:'snapshot',settings:defaultSettings,assets:[]};
  await expect(client.api.reparseAsset(project,'asset / fixture')).resolves.toEqual(queued);
  const expected='/api/v1/projects/project%20%2F%20fixture/assets/asset%20%2F%20fixture/reparse';
  expect(fetcher.mock.calls.map(([url])=>url)).toEqual(['/api/v1/me',expected,'/api/v1/me',expected]);
  const first=fetcher.mock.calls[1][1];const retry=fetcher.mock.calls[3][1];
  expect(first.method).toBe('POST');expect(retry.method).toBe('POST');
  expect(JSON.parse(first.body)).toEqual({expected_revision:7});expect(retry.body).toBe(first.body);
  expect(first.headers.get('Idempotency-Key')).toBeTruthy();
  expect(retry.headers.get('Idempotency-Key')).toBe(first.headers.get('Idempotency-Key'));
  expect(first.headers.get('X-CSRF-Token')).toBe('old-fixture');
  expect(retry.headers.get('X-CSRF-Token')).toBe('fresh-fixture');
  expect(retry.headers.get('Content-Type')).toBe('application/json');
 });

 it.each([
  ['asset_parse_not_failed','此文件当前不是解析失败状态'],
  ['asset_parse_busy','此文件仍有解析任务正在处理'],
 ])('shows a safe actionable message for a %s reparse rejection without replaying it',async(code,message)=>{
  const fetcher=vi.fn().mockResolvedValue(response({detail:{code,message:'/work/private secret-token https://internal.invalid'}},409));
  vi.stubGlobal('fetch',fetcher);
  const project:Project={id:'fixture',title:'fixture',stage:'draft',revision:7,input_snapshot_hash:'snapshot',settings:defaultSettings,assets:[]};
  const failure=await client.api.reparseAsset(project,'source').catch(error=>error);
  expect(failure).toMatchObject({code:code.toUpperCase(),status:409});
  expect(client.safeError(failure)).toContain(message);
  expect(client.safeError(failure)).not.toMatch(/secret-token|\/work|https?:|internal/);
  expect(fetcher).toHaveBeenCalledTimes(1);
 });

 it('stops after a second CSRF rejection and shows a specific recovery message',async()=>{
  const fetcher=vi.fn().mockImplementation(async(url:string)=>url.endsWith('/me')?response({csrf_token:'fixture'}):response({detail:{code:'csrf_rejected'}},403));
  vi.stubGlobal('fetch',fetcher);
  try {await client.mutate('/projects',{title:'Synthetic'});throw new Error('Expected rejection');}
  catch(error) {
   expect(error).toMatchObject({code:'CSRF_REJECTED',status:403});
   expect(client.safeError(error)).toContain('页面安全校验已失效');
  }
  expect(fetcher.mock.calls.map(([url])=>url)).toEqual(['/api/v1/projects','/api/v1/me','/api/v1/projects']);
 });

 it.each([
  ['GET',403,{detail:{code:'csrf_rejected'}}],
  ['POST',403,{detail:{code:'origin_rejected'}}],
  ['POST',403,{error:{code:'csrf_rejected'}}],
  ['POST',401,{detail:{code:'authentication_required'}}],
  ['POST',500,{detail:{code:'csrf_rejected'}}],
 ] as const)('does not replay %s after status %s without exact mutation CSRF rejection',async(method,status,body)=>{
  const fetcher=vi.fn().mockResolvedValue(response(body,status));vi.stubGlobal('fetch',fetcher);
  await expect(client.request('/projects',{method,...(method==='GET'?{}:{body:'{}'})})).rejects.toMatchObject({status});
  expect(fetcher).toHaveBeenCalledTimes(1);
 });
});
