"""周公解梦 - FastAPI。"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from tools import sqlite_client as db
from tools.llm_config import get_llm_config_error


SERVICE_NAME = "zhougongjiemeng_agent"

db.init_db(SERVICE_NAME)
db.clear_stale_tasks()

app = FastAPI(title="周公解梦 API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Req(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


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
        from zhougongjiemeng_agent.crew import create_zhougongjiemeng_crew

        crew = create_zhougongjiemeng_crew()
        result = str(crew.kickoff(inputs={"text": text}))
        db.update_task(task_id, status="done", report=result)
        _save_report(task_id, text, result)
    except Exception as exc:
        db.update_task(task_id, status="failed", error=str(exc))


def _save_report(task_id: str, text: str, report: str):
    try:
        db.save_report(
            task_id,
            text,
            _extract_summary(report),
            _extract_keywords(text),
            report,
        )
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


@app.get("/zhougongjiemeng_agent-health")
def health():
    config_error = get_llm_config_error("zhougongjiemeng_agent")
    return {
        "status": "degraded" if config_error else "ok",
        "llm_configured": config_error is None,
    }


@app.post("/zhougongjiemeng_agent", response_model=TaskRsp)
def submit(req: Req):
    text = req.text.strip()
    if not text:
        raise HTTPException(422, "Dream description cannot be blank")

    config_error = get_llm_config_error("zhougongjiemeng_agent")
    if config_error:
        raise HTTPException(503, config_error)

    cached = _find_cached(text)
    if cached:
        return TaskRsp(
            id="cached",
            text=text,
            status="done",
            report=cached,
            cached=True,
        )

    task_id = db.create_task(text)
    threading.Thread(target=_run, args=(task_id, text), daemon=True).start()
    return TaskRsp(id=task_id, text=text, status="pending")


@app.get("/zhougongjiemeng_agent/{task_id}", response_model=TaskRsp)
def get_task(task_id: str):
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskRsp(
        id=task["id"],
        text=task["topic"],
        status=task["status"],
        report=task.get("report"),
        error=task.get("error"),
    )


def _extract_summary(text: str, max_len: int = 150) -> str:
    for line in text.split("\n"):
        summary = line.strip()
        if summary and not summary.startswith("#") and len(summary) > 15:
            return summary[:max_len] + ("..." if len(summary) > max_len else "")
    return text[:max_len]


def _extract_keywords(text: str) -> str:
    import json

    stopwords = {"的", "与", "及", "和", "在", "了", "是", "有", "之", "为", "等", "中"}
    words = text.replace("、", " ").replace(",", " ").replace("，", " ").split()
    keywords = [word for word in words if len(word) >= 2 and word not in stopwords][:5]
    return json.dumps(keywords, ensure_ascii=False)
