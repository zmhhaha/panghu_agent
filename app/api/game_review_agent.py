"""
通用游戏试玩评价 agent — FastAPI（内部服务，不对外开放）

POST /game_review            → 提交试玩评测任务（game_url + comment_targets），返回 task_id
GET  /game_review/{task_id}  → 查询任务状态 & 结果
GET  /reports                → 检索已完成的报告
GET  /download/{task_id}     → 下载报告全文
GET  /health                 → 健康检查

后台线程里：
1. 启动 Playwright 浏览器
2. 用通用浏览器工具创建 crew 的试玩员工具
3. crew.kickoff() 三段式流水线（试玩→评测→撰写）
4. 结果存 SQLite，浏览器 close
"""
import sys
import os
import threading
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from tools import sqlite_client as db

# ── 初始化 game_review_agent 表 ──
db.init_db("game_review_agent")
# ── 启动时清理：上次进程遗留的未完成任务标为 failed ──
db.clear_stale_tasks()

app = FastAPI(title="🎮 游戏试玩评价 API (internal)", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 输出目录（容器内临时目录，报告全文会同步存 SQLite）
OUT_DIR = os.getenv("GAME_OUT_DIR", "/tmp/game_review_agent")
os.makedirs(OUT_DIR, exist_ok=True)


# ============================================================
#  Schemas
# ============================================================

class GameReviewRequest(BaseModel):
    game_url: str = Field(..., min_length=4, max_length=500, description="要试玩的游戏 URL")
    comment_targets: str = Field(default="", max_length=500, description="评测关注点（可选）")
    user_id: str = Field(default="", max_length=200)


class TaskResponse(BaseModel):
    id: str
    game_url: str
    comment_targets: str
    status: str
    user_id: str | None = None
    report: str | None = None
    error: str | None = None


# ============================================================
#  后台试玩评测线程
# ============================================================

def _run_review(task_id: str, game_url: str, comment_targets: str):
    browser = None
    try:
        db.update_task(task_id, status="running")

        from tools.game_play.browser import GameBrowser
        from tools.game_play.tools import make_game_tools
        from game_review_agent.crew import create_game_review_crew

        task_out_dir = os.path.join(OUT_DIR, task_id)
        os.makedirs(task_out_dir, exist_ok=True)

        # 启动浏览器，为试玩员创建工具
        browser = GameBrowser()
        browser.start()
        page = browser.page
        tools = make_game_tools(page, task_out_dir)

        crew = create_game_review_crew(
            game_url=game_url,
            comment_targets=comment_targets,
            browser_tools=tools,
            out_dir=task_out_dir,
        )
        # Playwright 会在本线程注册 running event loop，导致 crew.kickoff() 走
        # async 路径报 "invoked synchronously from within a running event loop"。
        # 把 kickoff 放到一个干净的线程池线程里跑（那里 is_inside_event_loop() 为
        # false，同步执行成功），Playwright 的 page 对象跨线程串行访问是安全的。
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = str(pool.submit(crew.kickoff, {
                "game_url": game_url,
                "comment_targets": comment_targets,
            }).result())

        db.update_task(task_id, status="done", report=result)

        # 报告存入共享 SQLite（检索 + 下载用）
        try:
            summary = _extract_summary(result)
            keywords = _extract_keywords(comment_targets or game_url)
            db.save_report(task_id, game_url, summary, keywords, result)
        except Exception:
            pass  # 报告存储失败不影响主流程

    except Exception as e:
        db.update_task(task_id, status="failed", error=str(e))
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


# ============================================================
#  API 端点
# ============================================================

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/game_review", response_model=TaskResponse)
def submit_review(req: GameReviewRequest):
    """提交试玩评测任务，立即返回 task_id，后台异步执行"""
    if req.user_id:
        running = db.get_running_task_by_user(req.user_id)
        if running:
            raise HTTPException(429, detail=f"您已有任务正在执行（{running['id'][:8]}），请等待完成后再提交")
    task_id = db.create_task(f"{req.game_url} | {req.comment_targets[:30]}", req.user_id)
    threading.Thread(target=_run_review, args=(task_id, req.game_url, req.comment_targets)).start()
    return TaskResponse(
        id=task_id, game_url=req.game_url, comment_targets=req.comment_targets,
        status="pending", user_id=req.user_id,
    )


@app.get("/game_review/{task_id}", response_model=TaskResponse)
def get_task(task_id: str):
    """查询任务状态和结果"""
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskResponse(
        id=task["id"],
        game_url=task.get("topic", ""),
        comment_targets="",
        status=task["status"],
        report=task.get("report"),
        error=task.get("error"),
    )


# ---- 报告检索 ----

class ReportItem(BaseModel):
    id: str
    topic: str
    summary: str | None = None
    keywords: str | None = None
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
            keywords=r.get("keywords"), created_at=r.get("created_at"),
        )
        for r in rows
    ]


@app.get("/reports/{report_id}")
def get_report(report_id: str):
    """获取单篇报告全文"""
    r = db.get_report(report_id)
    if not r:
        raise HTTPException(404, "Report not found")
    return {k: r.get(k) for k in ("id", "topic", "summary", "keywords", "content", "created_at")}


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
