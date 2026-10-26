import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defaultSettings, type Project } from '../domain';

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
