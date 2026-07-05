"""
研究助手 Agent — FastAPI（内部服务，不对外开放）
POST /research          → 提交任务，返回 task_id
GET  /research/{id}     → 查询任务状态 & 结果
GET  /reports           → 检索已完成的报告（关键词 / 全文搜索）
"""
import sys, os, threading, json, re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crewai"))


def _safe_filename(topic: str) -> str:
    """生成纯 ASCII 文件名（HTTP Content-Disposition 只能用 latin-1）"""
    safe = re.sub(r'[^\x00-\x7F]+', '', topic)  # 去除非 ASCII 字符
    safe = re.sub(r'[^\w\s-]', '', safe).strip()  # 只保留字母数字空格连字符
    return (safe[:40].replace(' ', '_') or 'report') + '.md'

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.database import get_session, ResearchTask, ResearchReport, engine, Base

# 自动建表
Base.metadata.create_all(engine)

app = FastAPI(title="🐯 研究助手 API (internal)", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)


class TaskResponse(BaseModel):
    id: str
    topic: str
    status: str
    report: str | None = None
    error: str | None = None


def _run_research(task_id: str, topic: str):
    """后台线程执行调研，完成后写入数据库"""
    db = get_session()
    try:
        task = db.query(ResearchTask).filter_by(id=task_id).first()
        if task:
            task.status = "running"
            db.commit()

        from crew import create_research_crew
        crew = create_research_crew()
        result = str(crew.kickoff(inputs={"topic": topic}))

        task = db.query(ResearchTask).filter_by(id=task_id).first()
        if task:
            task.status = "done"
            task.report = result
            db.commit()

        # 1) 报告持久化到本地 research_reports 表
        try:
            summary = ResearchReport.extract_summary(result)
            keywords = json.dumps(ResearchReport.extract_keywords(topic), ensure_ascii=False)
            report_record = ResearchReport(
                id=task_id,
                task_id=task_id,
                topic=topic,
                summary=summary,
                keywords=keywords,
                content=result,
            )
            db.add(report_record)
            db.commit()
        except Exception:
            pass  # 本地存储失败不影响主流程

        # 2) 报告也同步到共享 SQLite 服务
        try:
            import urllib.request
            summary = ResearchReport.extract_summary(result)
            keywords = json.dumps(ResearchReport.extract_keywords(topic), ensure_ascii=False)
            sql = (
                f"INSERT OR REPLACE INTO research_reports (id, task_id, topic, summary, keywords, content, created_at) "
                f"VALUES ('{task_id}', '{task_id}', '{topic.replace(chr(39), chr(39)+chr(39))}', "
                f"'{summary.replace(chr(39), chr(39)+chr(39))}', '{keywords.replace(chr(39), chr(39)+chr(39))}', "
                f"'{result.replace(chr(39), chr(39)+chr(39))}', datetime('now'))"
            )
            data = json.dumps({"sql": sql}).encode("utf-8")
            rq = urllib.request.Request(
                "http://sqlite.data.svc.cluster.local:8000/execute",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(rq, timeout=10)
        except Exception:
            pass  # 共享存储失败不影响主流程
    except Exception as e:
        task = db.query(ResearchTask).filter_by(id=task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            db.commit()
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/research", response_model=TaskResponse)
def submit_research(req: ResearchRequest):
    """提交调研任务，立即返回 task_id，后台异步执行"""
    db = get_session()
    task = ResearchTask(topic=req.topic, status="pending")
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()

    # 后台线程执行
    t = threading.Thread(target=_run_research, args=(task_id, req.topic))
    t.start()

    return TaskResponse(id=task_id, topic=req.topic, status="pending")


@app.get("/research/{task_id}", response_model=TaskResponse)
def get_task(task_id: str):
    """查询任务状态和结果"""
    db = get_session()
    task = db.query(ResearchTask).filter_by(id=task_id).first()
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskResponse(
        id=task.id, topic=task.topic, status=task.status,
        report=task.report, error=task.error,
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
    """检索已完成的报告：支持全文搜索 topic + summary + content"""
    db = get_session()
    query = db.query(ResearchReport)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.filter(
            (ResearchReport.topic.ilike(pattern)) |
            (ResearchReport.summary.ilike(pattern)) |
            (ResearchReport.content.ilike(pattern))
        )
    rows = query.order_by(ResearchReport.created_at.desc()).offset(offset).limit(limit).all()
    return [
        ReportItem(
            id=r.id, topic=r.topic, summary=r.summary, keywords=r.keywords,
            model_used=r.model_used, created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@app.get("/reports/{report_id}")
def get_report(report_id: str):
    """获取单篇报告全文"""
    db = get_session()
    r = db.query(ResearchReport).filter_by(id=report_id).first()
    if not r:
        raise HTTPException(404, "Report not found")
    return {
        "id": r.id, "topic": r.topic, "summary": r.summary,
        "keywords": r.keywords, "content": r.content,
        "model_used": r.model_used,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@app.get("/download/{task_id}")
def download_report(task_id: str):
    """下载报告全文（Markdown 文件）—— 优先本地，fallback 共享 SQLite"""
    from fastapi.responses import Response

    # 1) 先尝试从本地 DB 获取
    db = get_session()
    try:
        r = db.query(ResearchReport).filter_by(task_id=task_id).first()
        if r and r.content:
            topic = r.topic or "report"
            filename = _safe_filename(topic)
            return Response(
                content=r.content.encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
    finally:
        db.close()

    # 2) 本地没有则从共享 SQLite 获取
    import urllib.request
    try:
        data = json.dumps({"sql": f"SELECT topic, content FROM research_reports WHERE task_id='{task_id}'"}).encode("utf-8")
        rq = urllib.request.Request(
            "http://sqlite.data.svc.cluster.local:8000/query",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(rq, timeout=10)
        rows = json.loads(resp.read().decode("utf-8")).get("rows", [])
        if not rows:
            raise HTTPException(404, "Report not found")
        content = rows[0]["content"]
        topic = rows[0]["topic"]
        filename = _safe_filename(topic)
        return Response(
            content=content.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch report: {e}")
