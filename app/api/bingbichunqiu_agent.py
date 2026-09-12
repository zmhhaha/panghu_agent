import sys, os, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from tools import sqlite_client as db


def llm_service_config_error() -> str | None:
    """模型调用统一走集群内 llm-service：本服务不再持有 provider 凭据。"""
    if not os.getenv("LLM_BASE_URL", "").strip():
        return "bingbichunqiu_agent 配置不完整：LLM_BASE_URL 未注入（应指向集群内 llm-service）。"
    if not os.getenv("LLM_SERVICE_TOKEN", "").strip():
        return "bingbichunqiu_agent 配置不完整：LLM_SERVICE_TOKEN 未注入，请检查 ExternalSecret 是否已同步。"
    return None
db.init_db("bingbichunqiu_agent"); db.clear_stale_tasks()
app=FastAPI(title="📜 秉笔春秋 API",version="3.0"); app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
class Req(BaseModel): text:str=Field(...,min_length=1,max_length=2000)
class TaskRsp(BaseModel): id:str; text:str; status:str; report:str|None=None; error:str|None=None; cached:bool=False
def _run(i,t):
 try:
  db.update_task(i,status="running"); from bingbichunqiu_agent.crew import create_bingbichunqiu_crew
  from tools.rag_client import fetch_reference
  r=str(create_bingbichunqiu_crew().kickoff(inputs={"text":t,"reference":fetch_reference("bingbichunqiu",t)})); db.update_task(i,status="done",report=r); db.save_report(i,t,r[:150],"",r)
 except Exception as e: db.update_task(i,status="failed",error=str(e))
@app.get("/bingbichunqiu_agent-health")
def health():
 e=llm_service_config_error(); return {"status":"degraded" if e else "ok","llm_configured":e is None}
@app.post("/bingbichunqiu_agent",response_model=TaskRsp)
def submit(req:Req):
 e=llm_service_config_error()
 if e: raise HTTPException(503,detail=e)
 i=db.create_task(req.text); threading.Thread(target=_run,args=(i,req.text),daemon=True).start(); return TaskRsp(id=i,text=req.text,status="pending")
@app.get("/bingbichunqiu_agent/{task_id}",response_model=TaskRsp)
def get_task(task_id):
 t=db.get_task(task_id)
 if not t: raise HTTPException(404,"Task not found")
 return TaskRsp(id=t["id"],text=t["topic"],status=t["status"],report=t.get("report"),error=t.get("error"))
