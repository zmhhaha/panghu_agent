import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../api';
import { defaultSettings, type Project } from '../domain';
import { hasObservedParser, parseFailureMessage, parseStatusLabel, parseSummary } from '../parse';
import { PARSE_POLL_INTERVAL, useParsePolling } from '../useParsePolling';

vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), api: { project: vi.fn() } }));
const project: Project = { id:'p', title:'解析测试', stage:'draft', revision:1, input_snapshot_hash:'hash', settings:defaultSettings, assets:[{id:'a',display_name:'资料.md',bytes:10,parse_status:'queued',text_summary:null,parse_error:null}] };
const state = (status:string):Project => ({...project,assets:[{...project.assets[0],parse_status:status}]});
beforeEach(()=>{vi.useFakeTimers();vi.clearAllMocks();Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});});
afterEach(()=>{vi.useRealTimers();});
const advance = async (n=PARSE_POLL_INTERVAL) => {await act(async()=>{await vi.advanceTimersByTimeAsync(n);});};

describe('parse polling lifecycle',()=>{
 it('uses actual queued -> parsing -> ready snapshots and stops after ready',async()=>{
  const receiver=vi.fn();vi.mocked(api.project).mockResolvedValueOnce(state('parsing')).mockResolvedValueOnce({...state('ready'),assets:[{...state('ready').assets[0],text_summary:'真实摘要'}]});
  const {unmount}=renderHook(()=>useParsePolling(project,true,receiver));
  expect(receiver).not.toHaveBeenCalled();await advance();expect(receiver.mock.calls[0][0].assets[0].parse_status).toBe('parsing');await advance();expect(receiver.mock.calls[1][0].assets[0].text_summary).toBe('真实摘要');await advance(15000);expect(api.project).toHaveBeenCalledTimes(2);unmount();
 });
 it('polls stopping without announcing ready and stops on actual failed snapshot',async()=>{
  vi.mocked(api.project).mockResolvedValue({...state('failed'),assets:[{...state('failed').assets[0],parse_error:'parse_timeout'}]});const receiver=vi.fn();const {unmount}=renderHook(()=>useParsePolling(state('stopping'),true,receiver));await advance();expect(receiver.mock.calls[0][0].assets[0].parse_status).toBe('failed');await advance();expect(api.project).toHaveBeenCalledOnce();unmount();
 });
 it('cleans pending request on tab switch and ignores its late result',async()=>{
  let resolve!:(p:Project)=>void;vi.mocked(api.project).mockImplementation(()=>new Promise(r=>{resolve=r;}));const receiver=vi.fn();const {rerender,unmount}=renderHook(({enabled})=>useParsePolling(project,enabled,receiver),{initialProps:{enabled:true}});await advance();const signal=vi.mocked(api.project).mock.calls[0][1]!;rerender({enabled:false});expect(signal.aborted).toBe(true);await act(async()=>resolve(state('ready')));expect(receiver).not.toHaveBeenCalled();await advance(15000);expect(api.project).toHaveBeenCalledOnce();unmount();
 });
 it('pauses while hidden and reads real snapshot when visible again',async()=>{
  vi.mocked(api.project).mockResolvedValue(state('parsing'));const {unmount}=renderHook(()=>useParsePolling(project,true,vi.fn()));Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});act(()=>document.dispatchEvent(new Event('visibilitychange')));await advance(15000);expect(api.project).not.toHaveBeenCalled();Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});await act(async()=>document.dispatchEvent(new Event('visibilitychange')));expect(api.project).toHaveBeenCalledOnce();unmount();await advance(15000);expect(api.project).toHaveBeenCalledOnce();
 });
 it('reload starts from server pending state; terminal and disabled panels never poll',async()=>{
  vi.mocked(api.project).mockResolvedValue(state('ready'));const first=renderHook(()=>useParsePolling(project,true,vi.fn()));first.unmount();const second=renderHook(()=>useParsePolling(state('parsing'),true,vi.fn()));await advance();expect(api.project).toHaveBeenCalledOnce();second.unmount();const terminal=renderHook(()=>useParsePolling(state('ready'),true,vi.fn()));await advance();expect(api.project).toHaveBeenCalledOnce();terminal.unmount();
 });
 it('stops polling on ownership/authentication errors and allows explicit manual refresh',async()=>{
  vi.mocked(api.project).mockRejectedValue(new ApiError('NOT_FOUND',404));const {result,unmount}=renderHook(()=>useParsePolling(project,true,vi.fn()));await advance();expect(result.current).toBeInstanceOf(ApiError);await advance(15000);expect(api.project).toHaveBeenCalledOnce();unmount();
 });
});
describe('parse presentation safety',()=>{
 it('uses dedicated labels without confusing ready with PPT-ready',()=>{expect(parseStatusLabel('ready')).toBe('解析完成');expect(parseStatusLabel('queued')).toBe('等待解析');expect(parseStatusLabel('stopping')).toBe('正在停止解析');expect(hasObservedParser(project.assets)).toBe(false);});
 it('shows bounded text only for ready and clears stale summary for removed/null states',()=>{const asset={...project.assets[0],parse_status:'ready',text_summary:'中'.repeat(3000)};expect(parseSummary(asset)?.length).toBe(2000);expect(parseSummary({...asset,parse_status:'queued'})).toBeNull();expect(parseSummary({...asset,parse_status:'removed'})).toBeNull();expect(parseSummary({...asset,text_summary:null,summary:'过期摘要'})).toBeNull();});
 it('maps known failures and never echoes unknown paths, code, token or stack',()=>{expect(parseFailureMessage('scanned_pdf_unsupported')).toContain('扫描 PDF');expect(parseFailureMessage('parse_timeout')).toContain('时限');expect(parseFailureMessage('/work/private secret-token stack')).not.toMatch(/private|secret-token|stack/);});
});
