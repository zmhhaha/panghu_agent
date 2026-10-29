import { describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
import { emptyRequirements, type Project } from './domain';

describe('API delivery semantics',()=>{
  it('does not automatically replay an uncertain mutation and reuses its intent key for explicit retry',async()=>{
    const headers:Headers[]=[];let writes=0;
    vi.stubGlobal('fetch',vi.fn(async(_url:string,init:RequestInit={})=>{
      if(!init.method)return new Response(JSON.stringify({user_id:'u',csrf_token:'csrf'}),{headers:{'content-type':'application/json'}});
      headers.push(new Headers(init.headers));writes++;if(writes===1)throw new TypeError('connection reset');
      return new Response(JSON.stringify({id:'p',requirements:emptyRequirements,jobs:[],versions:[],materials:[],search_batches:[]}),{headers:{'content-type':'application/json'}});
    }));await api.me();
    const p={id:'p',revision:9} as Project;
    await expect(api.action(p,'plan')).rejects.toBeInstanceOf(ApiError);expect(writes).toBe(1);
    await api.action(p,'plan');expect(writes).toBe(2);expect(headers[0].get('Idempotency-Key')).toBe(headers[1].get('Idempotency-Key'));expect(headers[1].get('X-CSRF-Token')).toBe('csrf');
  });
  it('turns an HTML login redirect into authentication recovery rather than parsing it as project data',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>new Response('<html>Sign in</html>',{headers:{'content-type':'text/html'}})));
    await expect(api.project('p')).rejects.toMatchObject({code:'authentication_required',status:401});
  });
  it('normalizes persisted backend job states and material identities without inventing progress',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({id:'p',requirements:emptyRequirements,versions:[],search_batches:[],materials:[{material_id:'m',status:'metadata_only'}],jobs:[{id:'job',state:'admission_uncertain',error_code:null}]}),{headers:{'content-type':'application/json'}})));
    const p=await api.project('p');expect(p.jobs[0].status).toBe('admission_uncertain');expect(p.jobs[0].progress).toBeUndefined();expect(p.materials[0].id).toBe('m');expect(p.materials[0].status).toBe('metadata_only');
  });
});
