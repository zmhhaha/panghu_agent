"""
钟馗伏魔 — FastAPI
输入心事，钟馗判官说几句公道话。
"""
import sys, os, threading, re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from tools import sqlite_client as db

db.init_db("zhongkuifumo_agent")
db.clear_stale_tasks()

app = FastAPI(title="👹 钟馗伏魔 API", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Req(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


class TaskRsp(BaseModel):
    id: str
    text: str
    status: str
    report: str | None = None
    error: str | None = None
    cached: bool = False


def _run(task_id: str, text: str):
    try:
        db.update_task(task_id, status="running")
        from zhongkuifumo_agent.crew import create_zhongkuifumo_crew
        crew = create_zhongkuifumo_crew()
        result = str(crew.kickoff(inputs={"text": text}))
        db.update_task(task_id, status="done", report=result)
        _save_report(task_id, text, result)
    except Exception as e:
        db.update_task(task_id, status="failed", error=str(e))


def _save_report(task_id: str, text: str, report: str):
    try:
        summary = _extract_summary(report)
        keywords = _extract_keywords(text)
        db.save_report(task_id, text, summary, keywords, report)
    except Exception:
        pass


def _find_cached(text: str) -> str | None:
    try:
        rows = db._query(
            f"SELECT content FROM {db.get_service()}_reports "
            f"WHERE topic='{db._esc(text)}' AND content IS NOT NULL "
            f"ORDER BY created_at DESC LIMIT 1"
        )
        if rows and rows[0].get("content"):
            return rows[0]["content"]
    except Exception:
        pass
    return None


@app.get("/zhongkuifumo_agent-health")
def health():
    return {"status": "ok"}


@app.post("/zhongkuifumo_agent", response_model=TaskRsp)
def submit(req: Req):
    cached = _find_cached(req.text)
    if cached:
        return TaskRsp(id="cached", text=req.text, status="done", report=cached, cached=True)
    task_id = db.create_task(req.text)
    threading.Thread(target=_run, args=(task_id, req.text)).start()
    return TaskRsp(id=task_id, text=req.text, status="pending")


@app.get("/zhongkuifumo_agent/{task_id}", response_model=TaskRsp)
def get_task(task_id: str):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskRsp(
        id=task["id"], text=task["topic"], status=task["status"],
        report=task.get("report"), error=task.get("error"),
    )


def _extract_summary(text: str, max_len: int = 150) -> str:
    for line in text.split("\n"):
        s = line.strip()
        if s and not s.startswith("#") and len(s) > 15:
            return s[:max_len] + ("..." if len(s) > max_len else "")
    return text[:max_len]


def _extract_keywords(text: str) -> str:
    import json
    sw = {"的", "与", "及", "和", "在", "了", "是", "有", "之", "为", "等", "中"}
    words = text.replace("、", " ").replace(",", " ").replace("，", " ").split()
    return json.dumps([w for w in words if len(w) >= 2 and w not in sw][:5], ensure_ascii=False)
