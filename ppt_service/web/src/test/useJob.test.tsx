import { act,renderHook,waitFor } from '@testing-library/react';
import { afterEach,beforeEach,describe,it,expect,vi } from 'vitest';
import { api,ApiError,consumeEvents,cursorStore } from '../api';
import { JOB_POLL_INTERVAL,useJob } from '../useJob';
vi.mock('../api',async original=>({...await original<typeof import('../api')>(),api:{job:vi.fn()},consumeEvents:vi.fn()}));
const snapshot={id:'j',project_id:'p',kind:'generate',status:'running',stage:'generating',last_event_seq:12};
beforeEach(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});});
afterEach(()=>{vi.clearAllMocks();vi.useRealTimers();});
describe('job restoration',()=>{
 it('reads authoritative snapshot before reconnecting with server sequence',async()=>{localStorage.setItem('ppt:cursor:j','4');vi.mocked(api.job).mockResolvedValue(snapshot);vi.mocked(consumeEvents).mockImplementation(()=>new Promise(()=>{}));const done=vi.fn();const {result,unmount}=renderHook(()=>useJob('j',done));await waitFor(()=>expect(consumeEvents).toHaveBeenCalled());expect(result.current.job?.status).toBe('running');expect(vi.mocked(consumeEvents).mock.calls[0][1]).toBe(12);expect(cursorStore.read('j')).toBe(12);unmount();});
 it('cancel stays cancelling until a real terminal snapshot arrives',async()=>{vi.mocked(api.job).mockResolvedValue({...snapshot,status:'cancelling'});vi.mocked(consumeEvents).mockImplementation(()=>new Promise(()=>{}));const {result,unmount}=renderHook(()=>useJob('j',vi.fn()));await waitFor(()=>expect(result.current.job?.status).toBe('cancelling'));expect(result.current.job?.status).not.toBe('cancelled');unmount();});
 it('expired event cursor reconciles using fresh server snapshot',async()=>{vi.mocked(api.job).mockResolvedValueOnce(snapshot).mockResolvedValue({...snapshot,last_event_seq:19});vi.mocked(consumeEvents).mockRejectedValueOnce(new ApiError('CURSOR_EXPIRED',410));const {result,unmount}=renderHook(()=>useJob('j',vi.fn()));await waitFor(()=>expect(result.current.job?.last_event_seq).toBe(19));expect(cursorStore.read('j')).toBe(19);unmount();});
 it('reconnects after expired cursor with the refreshed actual event sequence',async()=>{vi.useFakeTimers();vi.mocked(api.job).mockResolvedValueOnce(snapshot).mockResolvedValue({...snapshot,last_event_seq:21});vi.mocked(consumeEvents).mockRejectedValueOnce(new ApiError('CURSOR_EXPIRED',410)).mockImplementationOnce(()=>new Promise(()=>{}));const {unmount}=renderHook(()=>useJob('j',vi.fn()));await act(async()=>{await vi.advanceTimersByTimeAsync(3100);});expect(consumeEvents).toHaveBeenCalledTimes(2);expect(vi.mocked(consumeEvents).mock.calls[1][1]).toBe(21);unmount();});
 it('terminal snapshot completes without opening another event stream',async()=>{vi.mocked(api.job).mockResolvedValue({...snapshot,status:'cancelled'});const done=vi.fn();const {result}=renderHook(()=>useJob('j',done));await waitFor(()=>expect(done).toHaveBeenCalledOnce());expect(result.current.job?.status).toBe('cancelled');expect(consumeEvents).not.toHaveBeenCalled();});
 it('reconciles real stage-only started events and polls transitions without a new event',async()=>{
  vi.useFakeTimers();
  vi.mocked(api.job).mockResolvedValueOnce({...snapshot,status:'queued',stage:'plan_initial',last_event_seq:1}).mockResolvedValueOnce({...snapshot,status:'starting',stage:'plan_initial',last_event_seq:2}).mockResolvedValue({...snapshot,status:'running',stage:'plan_initial',last_event_seq:2});
  vi.mocked(consumeEvents).mockImplementation(async(_id,_cursor,_signal,event)=>{await new Promise<void>(()=>{event({seq:2,type:'job.started',payload:{stage:'plan_initial'}});});});
  const {result,unmount}=renderHook(()=>useJob('j',vi.fn()));
  await act(async()=>{});expect(result.current.job?.status).toBe('starting');expect(cursorStore.read('j')).toBe(2);
  await act(async()=>{await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL);});
  expect(result.current.job?.status).toBe('running');expect(api.job).toHaveBeenCalledTimes(3);unmount();
 });

 it('exposes an authoritative refresh for the same unchanged job ID',async()=>{
  vi.mocked(api.job).mockResolvedValueOnce({...snapshot,status:'queued'}).mockResolvedValue(snapshot);
  vi.mocked(consumeEvents).mockImplementation(()=>new Promise(()=>{}));
  const {result,unmount}=renderHook(()=>useJob('j',vi.fn()));await waitFor(()=>expect(result.current.job?.status).toBe('queued'));
  await act(async()=>{await result.current.refresh();});expect(result.current.job?.status).toBe('running');expect(api.job).toHaveBeenCalledTimes(2);unmount();
 });

 it('keeps newer event cursors when an older GET resolves and settles only from the next snapshot',async()=>{
  vi.useFakeTimers();let old!:(value:typeof snapshot)=>void;let latest!:(value:typeof snapshot)=>void;let event!:Parameters<typeof consumeEvents>[3];
  vi.mocked(api.job).mockResolvedValueOnce(snapshot).mockImplementationOnce(()=>new Promise(resolve=>{old=resolve;})).mockImplementationOnce(()=>new Promise(resolve=>{latest=resolve;}));
  vi.mocked(consumeEvents).mockImplementation((_id,_cursor,_signal,receiver)=>{event=receiver;return new Promise(()=>{});});
  const done=vi.fn();const {result,unmount}=renderHook(()=>useJob('j',done));await act(async()=>{});
  act(()=>{void result.current.refresh();});
  act(()=>event({seq:15,type:'job.succeeded',payload:{}}));
  await act(async()=>old({...snapshot,status:'starting'}));
  expect(result.current.job?.status).toBe('running');expect(cursorStore.read('j')).toBe(15);expect(done).not.toHaveBeenCalled();
  await act(async()=>latest({...snapshot,status:'succeeded',last_event_seq:15}));
  expect(result.current.job?.status).toBe('succeeded');expect(done).toHaveBeenCalledOnce();expect(cursorStore.read('j')).toBe(15);
  await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});expect(api.job).toHaveBeenCalledTimes(3);unmount();
 });

 it('does not overwrite a terminal cancellation with a late active response at the same sequence',async()=>{
  let resolve!:(value:typeof snapshot)=>void;
  vi.mocked(api.job).mockResolvedValueOnce(snapshot).mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
  vi.mocked(consumeEvents).mockImplementation(()=>new Promise(()=>{}));
  const done=vi.fn();const {result,unmount}=renderHook(()=>useJob('j',done));await waitFor(()=>expect(result.current.job?.status).toBe('running'));
  act(()=>{void result.current.refresh();});
  act(()=>result.current.setJob({...snapshot,status:'cancelled',last_event_seq:13}));
  await act(async()=>resolve({...snapshot,status:'running',last_event_seq:13}));
  expect(result.current.job?.status).toBe('cancelled');expect(done).toHaveBeenCalledOnce();expect(cursorStore.read('j')).toBe(13);unmount();
 });

 it('pauses hidden-page polling and ignores an aborted response after visibility resumes',async()=>{
  vi.useFakeTimers();let late!:(value:typeof snapshot)=>void;
  vi.mocked(api.job).mockResolvedValueOnce(snapshot).mockImplementationOnce(()=>new Promise(resolve=>{late=resolve;})).mockResolvedValue({...snapshot,last_event_seq:14});
  vi.mocked(consumeEvents).mockImplementation(()=>new Promise(()=>{}));
  const done=vi.fn();const {result,unmount}=renderHook(()=>useJob('j',done));await act(async()=>{});
  await act(async()=>{await vi.advanceTimersByTimeAsync(JOB_POLL_INTERVAL);});
  const signal=vi.mocked(api.job).mock.calls[1][1]!;
  Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});act(()=>document.dispatchEvent(new Event('visibilitychange')));
  expect(signal.aborted).toBe(true);await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});expect(api.job).toHaveBeenCalledTimes(2);
  Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
  expect(result.current.job?.last_event_seq).toBe(14);
  await act(async()=>late({...snapshot,status:'succeeded',last_event_seq:99}));
  expect(result.current.job?.status).toBe('running');expect(cursorStore.read('j')).toBe(14);expect(done).not.toHaveBeenCalled();unmount();
 });

 it('does not poll absent or terminal jobs',async()=>{
  vi.useFakeTimers();vi.mocked(api.job).mockResolvedValue({...snapshot,status:'succeeded'});
  const absent=renderHook(()=>useJob(null,vi.fn()));await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});expect(api.job).not.toHaveBeenCalled();absent.unmount();
  const terminal=renderHook(()=>useJob('j',vi.fn()));await act(async()=>{});await act(async()=>{await vi.advanceTimersByTimeAsync(15000);});expect(api.job).toHaveBeenCalledOnce();expect(consumeEvents).not.toHaveBeenCalled();terminal.unmount();
 });
});
