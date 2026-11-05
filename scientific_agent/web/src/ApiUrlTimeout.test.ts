import {afterEach,describe,expect,it,vi} from 'vitest';
import {api,request} from './api';
import type {Project} from './domain';

const json=(body:unknown)=>new Response(JSON.stringify(body),{headers:{'content-type':'application/json'}});

afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});

describe('official source request deadline',()=>{
  it('allows a 31-second source fetch and preserves revision and idempotency checks',async()=>{
    vi.useFakeTimers();
    const sourceUrl='https://github.com/example/project';
    const p={id:'timeout-project',revision:7} as Project;
    let submitted:RequestInit|undefined;
    vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL,init?:RequestInit)=>{
      if(String(input).endsWith('/me'))return Promise.resolve(json({user_id:'reader',csrf_token:'csrf'}));
      submitted=init;
      return new Promise<Response>((resolve,reject)=>{
        const timer=setTimeout(()=>resolve(json(p)),31_000);
        init?.signal?.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));},{once:true});
      });
    }));
    const result=api.url(p,sourceUrl);
    const resolved=expect(result).resolves.toEqual(p);
    await vi.advanceTimersByTimeAsync(31_000);
    await resolved;
    expect(JSON.parse(String(submitted?.body))).toEqual({expected_revision:7,url:sourceUrl});
    expect(new Headers(submitted?.headers).get('Idempotency-Key')).toBeTruthy();
    expect(new Headers(submitted?.headers).get('X-CSRF-Token')).toBe('csrf');
  });

  it('keeps ordinary JSON reads on their 30-second deadline',async()=>{
    vi.useFakeTimers();
    vi.stubGlobal('fetch',vi.fn((_input:RequestInfo|URL,init?:RequestInit)=>new Promise<Response>((_resolve,reject)=>{
      init?.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
    })));
    const rejected=expect(request('/projects/timeout-project')).rejects.toMatchObject({code:'request_timeout'});
    await vi.advanceTimersByTimeAsync(30_000);
    await rejected;
  });
});
