import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { api, mutate, request } from '../api';
import { defaultSettings, type Project } from '../domain';
const project:Project={id:'p',title:'CSRF测试',stage:'draft',revision:1,input_snapshot_hash:'snapshot',settings:defaultSettings,assets:[]};
beforeEach(async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('{}')));await api.me();});
afterEach(()=>vi.unstubAllGlobals());
describe('real profile CSRF contract',()=>{
 it('keeps me token in memory and attaches it to all mutations, including multipart upload',async()=>{
  const fetcher=vi.fn().mockImplementation(async(url:string,_init?:RequestInit)=>new Response(JSON.stringify(url.endsWith('/me')?{sub:'u',csrf_token:'fixture-profile-token'}:{})));vi.stubGlobal('fetch',fetcher);const profile=await api.me();expect(profile).not.toHaveProperty('csrf_token');
  await mutate('/projects',{title:'新项目'});await api.upload(project,new File(['资料'],'material.md'));await api.remove(project);await api.cancel('job');await api.confirm({...project,plan:{revision:'7',stage:'directions',input_snapshot_hash:'snapshot'}},{direction_id:'evidence-handbook'});
  const writes=fetcher.mock.calls.filter(([,init])=>init?.method&&init.method!=='GET');expect(writes).toHaveLength(5);for(const [,init] of writes)expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('fixture-profile-token');expect(fetcher.mock.calls[0][1].headers.get('X-CSRF-Token')).toBeNull();expect(localStorage.length).toBe(0);
 });
 it('clears stale token on 401 and does not invent one until a later successful me',async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce(new Response('{"csrf_token":"old-fixture"}')).mockResolvedValueOnce(new Response('{}',{status:401})).mockResolvedValueOnce(new Response('{}')).mockResolvedValueOnce(new Response('{"csrf_token":"new-fixture"}')).mockResolvedValueOnce(new Response('{}'));vi.stubGlobal('fetch',fetcher);await api.me();await expect(request('/projects')).rejects.toMatchObject({status:401});await mutate('/projects',{title:'无token'});expect(fetcher.mock.calls[2][1].headers.get('X-CSRF-Token')).toBeNull();await api.me();await mutate('/projects',{title:'登录后'});expect(fetcher.mock.calls[4][1].headers.get('X-CSRF-Token')).toBe('new-fixture');
 });
 it('does not accept a caller-supplied fake CSRF header and does not attach write tokens to GET',async()=>{
  const fetcher=vi.fn().mockImplementation(async()=>new Response('{}'));vi.stubGlobal('fetch',fetcher);await request('/projects',{method:'POST',headers:{'X-CSRF-Token':'forged'},body:'{}'});expect(fetcher.mock.calls[0][1].headers.get('X-CSRF-Token')).toBeNull();fetcher.mockResolvedValueOnce(new Response('{"csrf_token":"verified-fixture"}'));await api.me();await request('/projects',{method:'POST',headers:{'x-csrf-token':'forged'},body:'{}'});expect(fetcher.mock.calls[2][1].headers.get('X-CSRF-Token')).toBe('verified-fixture');await request('/projects');expect(fetcher.mock.calls[3][1].headers.get('X-CSRF-Token')).toBeNull();
 });
});
