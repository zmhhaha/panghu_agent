import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, consumeEvents, cursorStore } from './api';
import { activeStatuses, type Job } from './domain';

export const JOB_POLL_INTERVAL = 5000;
const transitions = new Set(['job.queued','job.started','stage.changed','job.cancelling','plan.ready','artifact.ready','job.succeeded','job.failed','job.cancelled','job.interrupted']);

export function useJob(jobId:string | null | undefined, onSettled:()=>void) {
 const settledRef=useRef(onSettled);settledRef.current=onSettled;
 const [job,displayJob]=useState<Job|null>(null);
 const [connection,setConnection]=useState('');
 const [error,setError]=useState<unknown>(null);
 const refreshRef=useRef<()=>Promise<Job|null>>(async()=>null);
 const updateRef=useRef<(value:Job|null)=>void>(()=>{});
 const refresh=useCallback(()=>refreshRef.current(),[]);
 const setJob=useCallback((value:Job|null)=>updateRef.current(value),[]);

 useEffect(()=>{
  displayJob(null);setError(null);setConnection('');
  refreshRef.current=async()=>null;updateRef.current=()=>{};
  if(!jobId)return;
  let disposed=false,finished=false,blocked=false,notified=false,refreshAgain=false;
  let current:Job|null=null,observedSeq=0,generation=0;
  let pollTimer:ReturnType<typeof setTimeout>|undefined;
  let reconnectTimer:ReturnType<typeof setTimeout>|undefined;
  let stream:AbortController|undefined;
  let pending:{controller:AbortController;promise:Promise<Job|null>}|undefined;
  const ownsRequest=(controller:AbortController)=>pending?.controller===controller;
  const visible=()=>document.visibilityState!=='hidden';
  const canSync=()=>!disposed&&!finished&&!blocked&&visible();
  const clearPoll=()=>{if(pollTimer)clearTimeout(pollTimer);pollTimer=undefined;};
  const clearReconnect=()=>{if(reconnectTimer)clearTimeout(reconnectTimer);reconnectTimer=undefined;};
  const finish=()=>{
   finished=true;clearPoll();clearReconnect();stream?.abort();setConnection('');
   if(!notified){notified=true;settledRef.current();}
  };
  const apply=(next:Job|null)=>{
   if(disposed||!next||next.id!==jobId)return current;
   const seq=next.last_event_seq??0;
   // An SSE event may have arrived while this GET was in flight. Neither an
   // older snapshot nor a late active response may undo newer/terminal state.
   if(seq<observedSeq||(current&&!activeStatuses.has(current.status)&&activeStatuses.has(next.status)))return current;
   current=next;observedSeq=Math.max(observedSeq,seq);
   cursorStore.write(jobId,observedSeq);displayJob(next);setError(null);
   if(!activeStatuses.has(next.status))finish();
   return next;
  };
  const schedulePoll=()=>{
   clearPoll();
   if(canSync()&&current&&activeStatuses.has(current.status))pollTimer=setTimeout(()=>{void snapshot();},JOB_POLL_INTERVAL);
  };
  const snapshot=(followPending=false):Promise<Job|null>=>{
   if(disposed||!visible()||finished||blocked)return Promise.resolve(current);
   if(pending){refreshAgain=refreshAgain||followPending;return pending.promise;}
   const controller=new AbortController(),version=++generation;
   const operation=(async()=>{
    try {
     const next=await api.job(jobId,controller.signal);
     if(disposed||controller.signal.aborted||version!==generation)return current;
     return apply(next);
    }catch(nextError){
     if(disposed||controller.signal.aborted||version!==generation)return current;
     setError(nextError);
     if(nextError instanceof ApiError&&[401,403,404].includes(nextError.status)){
      blocked=true;clearPoll();clearReconnect();stream?.abort();setConnection('');
     }
     return current;
    }finally{
     if(ownsRequest(controller)){
      pending=undefined;
      const repeat=refreshAgain;refreshAgain=false;
      schedulePoll();
      if(repeat&&canSync())void snapshot();
     }
    }
   })();
   pending={controller,promise:operation};
   return operation;
  };
  const reconnect=(delay:number)=>{
   clearReconnect();
   if(canSync())reconnectTimer=setTimeout(()=>{void connect();},delay);
  };
  const connect=async()=>{
   if(!canSync()||stream)return;
   const initial=await snapshot();
   if(!canSync()||stream)return;
   if(!initial){reconnect(3000);return;}
   const controller=new AbortController();stream=controller;
   setConnection('实时状态已连接');
   try {
    await consumeEvents(jobId,observedSeq,controller.signal,event=>{
     if(!canSync()||controller.signal.aborted||event.seq<=observedSeq)return;
     observedSeq=event.seq;cursorStore.write(jobId,observedSeq);
     // Real events carry stage/error identifiers, not an authoritative status.
     if(transitions.has(event.type))void snapshot(true);
    });
    if(canSync()&&!controller.signal.aborted){await snapshot();if(canSync()){setConnection('连接已断开，正在恢复');reconnect(2000);}}
   }catch(nextError){
    if(!canSync()||controller.signal.aborted)return;
    if(nextError instanceof ApiError&&[401,403,404].includes(nextError.status)){
     blocked=true;clearPoll();setError(nextError);setConnection('');return;
    }
    if(nextError instanceof ApiError&&nextError.code==='CURSOR_EXPIRED'){
     setConnection('事件记录已过期，正在恢复状态');await snapshot();
    }else{setError(nextError);setConnection('连接中断，正在重新连接');}
    reconnect(3000);
   }finally{if(stream===controller)stream=undefined;}
  };
  refreshRef.current=()=>snapshot(true);
  updateRef.current=value=>{apply(value);schedulePoll();};
  const visibility=()=>{
   if(!visible()){
    clearPoll();clearReconnect();generation++;pending?.controller.abort();pending=undefined;refreshAgain=false;
    stream?.abort();stream=undefined;
    if(!finished)setConnection('页面已隐藏，暂停状态同步');
   }else void connect();
  };
  document.addEventListener('visibilitychange',visibility);
  void connect();
  return()=>{
   disposed=true;clearPoll();clearReconnect();generation++;pending?.controller.abort();stream?.abort();
   document.removeEventListener('visibilitychange',visibility);
  };
 },[jobId]);
 return {job,setJob,connection,error,refresh};
}
