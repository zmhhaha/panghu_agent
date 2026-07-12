"""
研究助手 Agent — FastAPI（内部服务，不对外开放）
所有数据持久化到共享 SQLite 服务，容器本地不留数据。
POST /research          → 提交任务，返回 task_id
GET  /research/{id}     → 查询任务状态 & 结果
GET  /reports           → 检索已完成的报告
GET  /download/{id}     → 下载报告全文
"""
import sys, os, threading, re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from tools import sqlite_client as db

# ── 初始化 research 表 ──
db.init_db("research")

# ── 启动时清理：将上次未完成的任务标记为 failed（服务重启导致后台线程丢失）──
db.clear_stale_tasks()

app = FastAPI(title="🐯 研究助手 API (internal)", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ============================================================
#  Schemas
# ============================================================

class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    email: str = Field(default="", max_length=200)
    user_id: str = Field(default="", max_length=200)


class TaskResponse(BaseModel):
    id: str
    topic: str
    status: str
    user_id: str | None = None
    report: str | None = None
    error: str | None = None


# ============================================================
#  后台研究线程
# ============================================================

def _run_research(task_id: str, topic: str, email: str = ""):
    try:
        db.update_task(task_id, status="running")

        from research_agent.crew import create_research_crew
        crew = create_research_crew()
        result = str(crew.kickoff(inputs={"topic": topic}))

        db.update_task(task_id, status="done", report=result)

        # 报告存入共享 SQLite（检索 + 下载用）
        try:
            summary = _extract_summary(result)
            keywords = _extract_keywords(topic)
            db.save_report(task_id, topic, summary, keywords, result)
        except Exception:
            pass  # 报告存储失败不影响主流程

        # 发送完成邮件通知
        if email:
            from tools.email_client import send_email
            send_email(
                to=email,
                subject=f"🐯 调研完成: {topic[:30]}",
                body=f"""<h2>调研完成</h2>
<p><b>主题:</b> {topic}</p>
<p><b>报告长度:</b> {len(result)} 字符</p>
<p>请访问 <a href=\"https://research-agent.panghuer.top\">研究助手</a> 搜索报告 ID <code>{task_id}</code> 查看或下载报告。</p>""",
            )

    except Exception as e:
        db.update_task(task_id, status="failed", error=str(e))


# ============================================================
#  API 端点
# ============================================================

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/research", response_model=TaskResponse)
def submit_research(req: ResearchRequest):
    """提交调研任务，立即返回 task_id，后台异步执行"""
    if req.user_id:
        running = db.get_running_task_by_user(req.user_id)
        if running:
            raise HTTPException(429, detail=f"您已有任务正在执行（{running['id'][:8]}），请等待完成后再提交")
    task_id = db.create_task(req.topic, req.user_id)
    threading.Thread(target=_run_research, args=(task_id, req.topic, req.email)).start()
    return TaskResponse(id=task_id, topic=req.topic, status="pending", user_id=req.user_id)


@app.get("/research/{task_id}", response_model=TaskResponse)
def get_task(task_id: str):
    """查询任务状态和结果"""
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskResponse(
        id=task["id"], topic=task["topic"], status=task["status"],
        report=task.get("report"), error=task.get("error"),
    )


# ---- 报告检索 ----

class ReportItem(BaseModel):
    id: str
    topic: str
    summary: str | None = None
    keywords: str | None = None
    model_used: str | None = None
    created_at: str | None = None


@app.get("/reports", response_model=list[ReportItem])
def search_reports(
    q: str = Query(default="", description="搜索关键词"),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
):
    """检索已完成的报告"""
    rows = db.search_reports(q, limit, offset)
    return [
        ReportItem(
            id=r["id"], topic=r["topic"], summary=r.get("summary"),
            keywords=r.get("keywords"), model_used=r.get("model_used"),
            created_at=r.get("created_at"),
        )
        for r in rows
    ]


@app.get("/reports/{report_id}")
def get_report(report_id: str):
    """获取单篇报告全文"""
    r = db.get_report(report_id)
    if not r:
        raise HTTPException(404, "Report not found")
    return {k: r.get(k) for k in ("id", "topic", "summary", "keywords", "content", "model_used", "created_at")}


# ---- 下载 ----

@app.get("/download/{task_id}")
def download_report(task_id: str):
    """下载报告全文（Markdown 文件）"""
    from fastapi.responses import Response

    r = db.get_report_by_task(task_id)
    if not r or not r.get("content"):
        raise HTTPException(404, "Report not found")

    filename = _safe_filename(r.get("topic", "report"))
    return Response(
        content=r["content"].encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============================================================
#  工具函数
# ============================================================

def _safe_filename(topic: str) -> str:
    safe = re.sub(r'[^\x00-\x7F]+', '', topic)
    safe = re.sub(r'[^\w\s-]', '', safe).strip()
    return (safe[:40].replace(' ', '_') or 'report') + '.md'


def _extract_summary(text: str, max_len: int = 200) -> str:
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and len(stripped) > 20:
            return stripped[:max_len] + ("..." if len(stripped) > max_len else "")
    return text[:max_len] + "..." if len(text) > max_len else text


def _extract_keywords(topic: str) -> str:
    import json as _json
    stopwords = {"的", "与", "及", "和", "在", "了", "是", "有", "之", "为", "等", "中"}
    words = topic.replace("、", " ").replace(",", " ").replace("，", " ").split()
    return _json.dumps([w for w in words if len(w) >= 2 and w not in stopwords][:5], ensure_ascii=False)
