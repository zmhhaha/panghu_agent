import { useEffect, useRef, useState } from 'react';
import { api, ApiError, consumeEvents, cursorStore } from './api';
import { activeStatuses, type Job } from './domain';
export function useJob(jobId:string | null | undefined, onSettled:()=>void) {
 const settledRef=useRef(onSettled); settledRef.current=onSettled;
 const [job,setJob]=useState<Job|null>(null); const [connection,setConnection]=useState('');
 const [error,setError]=useState<unknown>(null);
 useEffect(()=>{
  setJob(null); setError(null); if(!jobId)return;
  const controller=new AbortController(); let timer:ReturnType<typeof setTimeout>|undefined; let stopped=false; let finished=false;
  const snapshot=async()=> {const current=await api.job(jobId); if(stopped)return current; setJob(current); cursorStore.write(jobId,current.last_event_seq??0); return current;};
  const run=async()=> {try {
   // Always reconcile first: local cursor never substitutes for the authoritative snapshot.
   let current=await snapshot(); if(stopped)return;
   if(!activeStatuses.has(current.status)){setConnection('');settledRef.current();return;}
   setConnection('实时状态已连接');
   await consumeEvents(jobId,cursorStore.read(jobId),controller.signal,event=>{cursorStore.write(jobId,event.seq);setJob(old=>old?{...old,last_event_seq:event.seq,...(event.payload?.status?{status:event.payload.status}:{}),...(event.payload?.stage?{stage:event.payload.stage}:{})}:old);if(['job.succeeded','job.failed','job.cancelled','job.interrupted'].includes(event.type)){void snapshot().then(current=>{if(stopped)return;if(!activeStatuses.has(current.status)){finished=true;setConnection('');settledRef.current();controller.abort();}}).catch(setError);}});
   if(!stopped&&!finished){current=await snapshot();if(!activeStatuses.has(current.status)){settledRef.current();return;}setConnection('连接已断开，正在恢复');timer=setTimeout(run,2000);}
  }catch(e){if(stopped||finished)return;if(e instanceof ApiError&&e.code==='CURSOR_EXPIRED'){setConnection('事件记录已过期，正在恢复状态');try{await snapshot();}catch(next){setError(next);}}else {setError(e);setConnection('连接中断，正在重新连接');}if(!(e instanceof ApiError&&e.status===401))timer=setTimeout(run,3000);}};
  void run(); return()=>{stopped=true;controller.abort();if(timer)clearTimeout(timer);};
 },[jobId]);
 return {job,setJob,connection,error};
}
