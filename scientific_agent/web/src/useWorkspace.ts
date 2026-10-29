import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { isActiveJob, type Project, type ProjectSummary } from './domain';

const rememberedProject=()=>{try{return localStorage.getItem('scientific-project')??'';}catch{return '';}};
export function useWorkspace(){
  const [projects,setProjects]=useState<ProjectSummary[]>([]);
  const [project,setProject]=useState<Project|null>(null);
  const [selectedId,setSelectedId]=useState(rememberedProject);
  const [loading,setLoading]=useState(true);const [busy,setBusy]=useState(false);
  const [error,setError]=useState<ApiError|null>(null);const [notice,setNotice]=useState('');
  const selected=useRef(selectedId);selected.current=selectedId;
  const operation=useRef(0);const writing=useRef(false);
  const ingest=useCallback((incoming:Project)=>{
    if(selected.current!==incoming.id)return;
    setProject(previous=>previous?.id===incoming.id&&previous.revision>incoming.revision?previous:incoming);
    setProjects(previous=>[incoming,...previous.filter(item=>item.id!==incoming.id)]);
  },[]);
  const refreshList=useCallback(async()=>{const result=await api.list();setProjects(result.projects);return result.projects;},[]);
  const refresh=useCallback(async()=>{
    const projectId=selected.current;if(!projectId)return;
    const incoming=await api.project(projectId);ingest(incoming);
  },[ingest]);
  useEffect(()=>{let alive=true;(async()=>{try{await api.me();const list=await refreshList();if(alive&&!selected.current&&list[0])setSelectedId(list[0].id);}catch(e){if(alive)setError(e instanceof ApiError?e:new ApiError('network_error'));}finally{if(alive)setLoading(false);}})();return()=>{alive=false;};},[refreshList]);
  useEffect(()=>{
    operation.current++;setProject(null);setNotice('');if(!selectedId)return;
    try{localStorage.setItem('scientific-project',selectedId);}catch{}
    const controller=new AbortController();let alive=true;setLoading(true);
    api.project(selectedId,controller.signal).then(incoming=>{if(alive)ingest(incoming);}).catch(e=>{if(alive)setError(e);}).finally(()=>{if(alive)setLoading(false);});
    return()=>{alive=false;controller.abort();};
  },[selectedId,ingest]);
  const active=project?.jobs?.some(isActiveJob)||Boolean(project?.active_job_id);
  useEffect(()=>{
    if(!selectedId)return;
    let alive=true;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{if(!writing.current&&document.visibilityState!=='hidden'){const incoming=await api.project(selectedId);if(alive)ingest(incoming);}}catch(e){if(alive)setError(e instanceof ApiError?e:new ApiError('network_error'));}finally{if(alive)timer=setTimeout(poll,active?2500:12000);}};
    timer=setTimeout(poll,active?2500:12000);
    return()=>{alive=false;clearTimeout(timer);};
  },[selectedId,active,ingest]);
  const mutate=useCallback(async(task:()=>Promise<Project>,message='已保存'):Promise<Project|null>=>{
    if(writing.current)return null;
    writing.current=true;setBusy(true);setError(null);const epoch=operation.current;
    try{const result=await task();if(epoch===operation.current){ingest(result);setNotice(message);}return result;}
    catch(e){const failure=e instanceof ApiError?e:new ApiError('network_error');if(epoch===operation.current){setError(failure);if(failure.status===409)try{await refresh();}catch{}}return null;}
    finally{writing.current=false;setBusy(false);}
  },[ingest,refresh]);
  async function create(question:string){const result=await mutate(()=>api.create(question),'研究项目已建立');if(result){selected.current=result.id;setSelectedId(result.id);setProject(result);setProjects(items=>[result,...items.filter(item=>item.id!==result.id)]);}return result;}
  return {projects,project,selectedId,select:setSelectedId,loading,busy,error,notice,setError,setNotice,refresh,refreshList,mutate,create};
}
