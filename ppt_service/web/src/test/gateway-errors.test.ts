import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let client:typeof import('../api');
const privateHtml='<html><body>secret-token https://internal.invalid/private?key=secret /work/private</body></html>';
beforeEach(async()=>{vi.resetModules();client=await import('../api');});
afterEach(()=>{vi.unstubAllGlobals();});

describe('safe gateway error messages',()=>{
 it.each([
  [408,'SERVER_RECEIVE_TIMEOUT','服务器接收请求超时'],
  [413,'REQUEST_TOO_LARGE','超过服务入口允许的大小'],
  [502,'UPSTREAM_BAD_GATEWAY','未收到有效的后端响应'],
  [503,'SERVICE_UNAVAILABLE','服务暂不可用'],
  [504,'UPSTREAM_TIMEOUT','等待后端响应超时'],
 ] as const)('explains a non-JSON HTTP %s without exposing its response or retrying the write',async(status,code,message)=>{
  const fetcher=vi.fn().mockResolvedValue(new Response(privateHtml,{status,headers:{'Content-Type':'text/html'}}));
  vi.stubGlobal('fetch',fetcher);
  const failure=await client.request('/projects/private/assets',{method:'POST',body:new FormData()}).catch(error=>error);
  expect(failure).toMatchObject({code,status});
  const visible=client.safeError(failure);
  expect(visible).toContain(message);expect(visible).toContain(`HTTP ${status}`);
  expect(visible).not.toMatch(/html|secret|https?:|internal|\/work|\/private/i);
  expect(fetcher).toHaveBeenCalledTimes(1);
 });

 it('keeps an application JSON code ahead of the HTTP gateway fallback',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:{code:'PROJECT_STORAGE_LIMIT',message:privateHtml}}),{status:413})));
  const failure=await client.request('/projects/private/assets',{method:'POST',body:new FormData()}).catch(error=>error);
  expect(failure).toMatchObject({code:'STORAGE_LIMIT',status:413});
  expect(client.safeError(failure)).toContain('存储空间不足');
  expect(client.safeError(failure)).not.toContain('超过服务入口允许的大小');
  expect(client.safeError(failure)).not.toMatch(/secret|internal|\/work/i);
 });

 it('retains a valid HTTP code on an otherwise generic service error',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(privateHtml,{status:500})));
  const failure=await client.request('/projects/private').catch(error=>error);
  expect(failure).toMatchObject({code:'SERVICE_ERROR',status:500});
  expect(client.safeError(failure)).toContain('SERVICE_ERROR，HTTP 500');
  expect(client.safeError(failure)).not.toMatch(/html|secret|internal|\/private/i);
 });

 it.each([0,NaN,Infinity,999,502.5])('never prints an invalid HTTP error status %s',status=>{
  expect(client.safeError(new client.ApiError('SERVICE_ERROR',status))).not.toContain('HTTP');
 });
});
