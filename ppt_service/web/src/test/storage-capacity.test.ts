import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';

let client:typeof import('../api');
beforeEach(async()=>{vi.resetModules();client=await import('../api');});
afterEach(()=>{vi.unstubAllGlobals();});

describe('source upload server disk capacity errors',()=>{
 it.each([
  [507,'storage_capacity_low','服务器可用存储不足'],
  [503,'storage_capacity_unavailable','暂时无法确认服务器存储状态'],
 ] as const)('explains HTTP %s with safe administrator guidance and never retries the upload',async(status,code,message)=>{
  const fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:{code,message:'secret /private/root?token=hidden'}}),{status}));
  vi.stubGlobal('fetch',fetcher);
  const failure=await client.request('/projects/synthetic/assets',{method:'POST',body:new FormData()}).catch(error=>error);
  expect(failure).toMatchObject({code:code.toUpperCase(),status});
  const visible=client.safeError(failure);
  expect(visible).toContain(message);expect(visible).toContain('稍后重试');expect(visible).toContain('管理员');
  expect(visible).not.toMatch(/secret|private|token|额度|资格|费用|清理不再需要的项目/i);
  expect(fetcher).toHaveBeenCalledTimes(1);
 });

 it('maps a non-JSON 507 response to disk guidance without exposing its body',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('<html>secret /private/path</html>',{status:507})));
  const failure=await client.request('/projects/synthetic/assets',{method:'POST',body:new FormData()}).catch(error=>error);
  expect(failure).toMatchObject({code:'STORAGE_CAPACITY_LOW',status:507});
  expect(client.safeError(failure)).toContain('服务器可用存储不足');
  expect(client.safeError(failure)).not.toMatch(/html|secret|private/i);
 });
});
