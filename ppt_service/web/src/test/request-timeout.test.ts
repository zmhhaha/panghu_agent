import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defaultSettings, type Project } from '../domain';

let client:typeof import('../api');
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status});
function deferred<T>() {
 let resolve!:(value:T)=>void;
 const promise=new Promise<T>(done=>{resolve=done;});
 return {promise,resolve};
}
beforeEach(async()=>{vi.resetModules();client=await import('../api');vi.useFakeTimers();});
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});

describe('bounded API waits',()=>{
 it('times out a pending response body and aborts the request without replaying the write',async()=>{
  const body=deferred<unknown>();
  const fetcher=vi.fn().mockResolvedValue({ok:true,status:200,json:()=>body.promise} as Response);
  vi.stubGlobal('fetch',fetcher);
  const operation=client.request('/projects',{method:'POST',body:'{}'},50);
  const rejected=expect(operation).rejects.toMatchObject({code:'REQUEST_TIMEOUT'});
  await vi.advanceTimersByTimeAsync(50);await rejected;
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
  body.resolve({saved:true});await vi.advanceTimersByTimeAsync(0);
  expect(fetcher).toHaveBeenCalledTimes(1);
 });

 it('gives uploads five minutes while ordinary requests expire after thirty seconds',async()=>{
  const fetcher=vi.fn().mockImplementation(()=>new Promise(()=>{}));vi.stubGlobal('fetch',fetcher);
  const project:Project={id:'p',title:'fixture',revision:1,stage:'draft',settings:defaultSettings,assets:[],input_snapshot_hash:''};
  const upload=client.api.upload(project,new File(['fixture'],'material.md'));
  const read=client.request('/projects');
  const uploadRejected=expect(upload).rejects.toMatchObject({code:'REQUEST_TIMEOUT'});
  const readRejected=expect(read).rejects.toMatchObject({code:'REQUEST_TIMEOUT'});
  await vi.advanceTimersByTimeAsync(30_000);await readRejected;
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(false);
  await vi.advanceTimersByTimeAsync(270_000);await uploadRejected;
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(2);
 });

 it('retains an uncertain mutation intent for an explicit retry and never retries it automatically',async()=>{
  const body=deferred<unknown>();
  const fetcher=vi.fn().mockResolvedValueOnce({ok:true,status:200,json:()=>body.promise} as Response)
   .mockResolvedValueOnce(response({saved:true}));
  vi.stubGlobal('fetch',fetcher);
  const payload={title:'same explicit intent'};
  const operation=client.mutate('/projects',payload);
  const rejected=expect(operation).rejects.toMatchObject({code:'REQUEST_TIMEOUT'});
  await vi.advanceTimersByTimeAsync(30_000);await rejected;
  expect(fetcher).toHaveBeenCalledTimes(1);
  await expect(client.mutate('/projects',payload)).resolves.toEqual({saved:true});
  expect(fetcher.mock.calls[1][1].headers.get('Idempotency-Key')).toBe(fetcher.mock.calls[0][1].headers.get('Idempotency-Key'));
  body.resolve({saved:true});await vi.advanceTimersByTimeAsync(0);
  expect(fetcher).toHaveBeenCalledTimes(2);
 });

 it('keeps the same signal, intent and total deadline through the single CSRF retry',async()=>{
  const profile=deferred<Response>();const body=deferred<unknown>();
  const fetcher=vi.fn().mockResolvedValueOnce(response({detail:{code:'csrf_rejected'}},403))
   .mockReturnValueOnce(profile.promise)
   .mockResolvedValueOnce({ok:true,status:200,json:()=>body.promise} as Response);
  vi.stubGlobal('fetch',fetcher);
  const operation=client.request('/projects/p',{method:'PATCH',body:'{"title":"same intent"}',headers:{'Idempotency-Key':'fixture-intent'}},100);
  const rejected=expect(operation).rejects.toMatchObject({code:'REQUEST_TIMEOUT'});
  await vi.advanceTimersByTimeAsync(60);
  profile.resolve(response({sub:'fixture',csrf_token:'fresh-token'}));
  await vi.advanceTimersByTimeAsync(0);
  expect(fetcher).toHaveBeenCalledTimes(3);
  const initial=fetcher.mock.calls[0][1];const retry=fetcher.mock.calls[2][1];
  expect(retry.signal).toBe(initial.signal);
  expect(retry.body).toBe(initial.body);
  expect(retry.headers.get('Idempotency-Key')).toBe(initial.headers.get('Idempotency-Key'));
  expect(retry.headers.get('X-CSRF-Token')).toBe('fresh-token');
  await vi.advanceTimersByTimeAsync(40);await rejected;
  expect(retry.signal.aborted).toBe(true);
  body.resolve({saved:true});await vi.advanceTimersByTimeAsync(0);
  expect(fetcher).toHaveBeenCalledTimes(3);
 });

 it('cancels promptly during shared CSRF recovery and never sends a delayed write retry',async()=>{
  const profile=deferred<Response>();const controller=new AbortController();
  const fetcher=vi.fn().mockResolvedValueOnce(response({detail:{code:'csrf_rejected'}},403)).mockReturnValueOnce(profile.promise);
  vi.stubGlobal('fetch',fetcher);
  const operation=client.request('/projects/p',{method:'PATCH',body:'{}',signal:controller.signal},1000);
  const rejected=expect(operation).rejects.toMatchObject({code:'REQUEST_CANCELLED'});
  await vi.advanceTimersByTimeAsync(0);
  expect(fetcher).toHaveBeenCalledTimes(2);
  controller.abort();await rejected;
  profile.resolve(response({sub:'fixture',csrf_token:'fresh-token'}));
  await vi.advanceTimersByTimeAsync(0);
  expect(fetcher).toHaveBeenCalledTimes(2);
 });
});
