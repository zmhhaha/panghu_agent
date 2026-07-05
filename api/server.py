"""CrewAI 研究助手 — FastAPI 服务（付费 API 层）。

启动方式：
    cd panghu_agent
    python -m uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload

或者：
    python api/server.py
"""

import os
import sys
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

# 把 crewai/ 目录加入 sys.path，确保 from crew import ... 能正确导入
_CREWAI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crewai")
sys.path.insert(0, _CREWAI_DIR)

from dotenv import load_dotenv
# API 模块使用自己独立的 .env（位于 api/ 目录下）
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .database import init_db, get_session, User, ApiKey, UsageLog, PricingConfig, hash_api_key
from .auth import get_current_user, get_admin_user
from .billing import (
    set_request_context,
    clear_request_context,
    get_request_usage,
    install_litellm_callbacks,
)

# ============================================================
#  Logging
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("crewai.server")

# ============================================================
#  FastAPI App
# ============================================================
app = FastAPI(
    title="CrewAI Research Agent API",
    description="多 Agent 协作研究助手 — 提供深度调研、分析、报告生成能力",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
#  Startup Event
# ============================================================
@app.on_event("startup")
def on_startup():
    """初始化数据库 + 安装 LiteLLM 计费回调。"""
    init_db()
    install_litellm_callbacks()
    logger.info("CrewAI API Server started.")


# ============================================================
#  Pydantic Schemas
# ============================================================

class ResearchRequest(BaseModel):
    topic: str = Field(..., description="调研主题", min_length=1, max_length=500)
    language: str = Field(default="zh", description="报告语言 (zh/en)")


class ResearchResponse(BaseModel):
    success: bool
    topic: str
    report: str
    usage: dict
    cost_this_time: float          # 本次调用费用
    total_spent: float             # 累计消费


class UserInfoResponse(BaseModel):
    id: str
    username: str
    email: Optional[str]
    total_spent: float             # 累计消费（美元）
    total_donated: float           # 累计赞助（美元）
    is_active: bool
    created_at: str


class UsageRecord(BaseModel):
    id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float
    topic: Optional[str]
    status: str
    created_at: str


class UsageResponse(BaseModel):
    user: UserInfoResponse
    total_calls: int
    total_tokens: int
    total_cost: float
    records: list[UsageRecord]


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    email: Optional[str] = None


class CreateKeyResponse(BaseModel):
    user_id: str
    api_key: str
    prefix: str
    message: str


class PricingEntry(BaseModel):
    model_pattern: str
    price_per_1k_prompt: float
    price_per_1k_completion: float


# ============================================================
#  赞助页面 — 小额扫码赞助（1毛 / 2毛 / 5毛）
# ============================================================

from fastapi.responses import HTMLResponse

@app.get("/donate", response_class=HTMLResponse)
def donate_page():
    """小额赞助页面 — 移动端友好的二维码扫码赞助。"""
    donate_html = os.path.join(os.path.dirname(__file__), "donate.html")
    if os.path.exists(donate_html):
        with open(donate_html, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h2>donate.html 未找到</h2>", status_code=404)


# ============================================================
#  公开端点
# ============================================================

@app.get("/")
def root():
    return {"service": "CrewAI Research Agent", "version": "1.0.0", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ============================================================
#  用户端点
# ============================================================

@app.get("/v1/me", response_model=UserInfoResponse)
def get_me(user: User = Depends(get_current_user)):
    """查询当前用户信息（含累计消费 + 赞助）。"""
    with get_session() as session:
        fresh = session.query(User).filter_by(id=user.id).first()
        return {
            "id": fresh.id,
            "username": fresh.username,
            "email": fresh.email,
            "total_spent": fresh.total_spent or 0.0,
            "total_donated": fresh.total_donated or 0.0,
            "is_active": fresh.is_active,
            "created_at": fresh.created_at.isoformat() if fresh.created_at else "",
        }


@app.get("/v1/usage", response_model=UsageResponse)
def get_my_usage(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
):
    """查询当前用户的调用记录。"""
    with get_session() as session:
        total = session.query(UsageLog).filter_by(user_id=user.id).count()
        records = (
            session.query(UsageLog)
            .filter_by(user_id=user.id)
            .order_by(UsageLog.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        total_tokens = sum(r.total_tokens for r in records)
        total_cost = sum(r.cost for r in records)

        return {
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "total_spent": user.total_spent or 0.0,
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat() if user.created_at else "",
            },
            "total_calls": total,
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
            "records": [
                {
                    "id": r.id,
                    "model": r.model,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "cost": r.cost,
                    "topic": r.topic,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                }
                for r in records
            ],
        }


# ============================================================
#  核心端点：执行调研
# ============================================================

@app.post("/v1/research", response_model=ResearchResponse)
def run_research(
    req: ResearchRequest,
    user: User = Depends(get_current_user),
):
    """执行一次深度调研。

    需要 X-API-Key Header。按次结算（后付费），调用结束后自动记录费用到累计消费。
    """
    # 1. 设置计费上下文
    api_key_id = getattr(user, "_current_api_key_id", "unknown")
    set_request_context(user_id=user.id, api_key_id=api_key_id, topic=req.topic)

    try:
        # 2. 运行 CrewAI
        from crew import create_research_crew

        logger.info(f"Starting research: topic='{req.topic}' user={user.username}")
        crew = create_research_crew()
        result = crew.kickoff(inputs={"topic": req.topic})

        # 3. 获取用量统计
        usage = get_request_usage()
        cost_this_time = round(usage["cost"], 6)
        usage_summary = {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"],
            "cost": cost_this_time,
            "models": list(usage["models"]),
        }

        # 4. 读取生成的报告（位于 crewai/ 目录下）
        report_content = ""
        report_path = os.path.join(_CREWAI_DIR, "report.md")
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                report_content = f.read()

        if not report_content:
            report_content = str(result)

        # 5. 获取累计消费
        with get_session() as session:
            fresh_user = session.query(User).filter_by(id=user.id).first()
            total_spent = fresh_user.total_spent if fresh_user else 0.0

        logger.info(f"Research completed: topic='{req.topic}' tokens={usage_summary['total_tokens']} cost=${cost_this_time:.4f} total_spent=${total_spent:.4f}")

        return ResearchResponse(
            success=True,
            topic=req.topic,
            report=report_content,
            usage=usage_summary,
            cost_this_time=cost_this_time,
            total_spent=round(total_spent, 6),
        )

    except Exception as e:
        logger.error(f"Research failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Research failed: {str(e)}")

    finally:
        clear_request_context()


# ============================================================
#  管理端点
# ============================================================

@app.post("/admin/users", response_model=dict)
def admin_create_user(
    req: CreateUserRequest,
    _: bool = Depends(get_admin_user),
):
    """创建用户并返回 API Key。"""
    import secrets

    with get_session() as session:
        if session.query(User).filter_by(username=req.username).first():
            raise HTTPException(status_code=409, detail="Username already exists.")

        user = User(
            username=req.username,
            email=req.email,
            total_spent=0.0,
        )
        session.add(user)
        session.flush()

        raw_key = "sk-" + secrets.token_hex(24)
        api_key = ApiKey(
            user_id=user.id,
            key_hash=hash_api_key(raw_key),
            prefix=raw_key[:15],
            name="default",
        )
        session.add(api_key)
        session.commit()

        return {
            "user_id": user.id,
            "username": user.username,
            "total_spent": 0.0,
            "api_key": raw_key,
            "message": "User created. Save the API key — it won't be shown again.",
        }


@app.get("/admin/users", response_model=list[dict])
def admin_list_users(
    _: bool = Depends(get_admin_user),
):
    """列出所有用户。"""
    with get_session() as session:
        users = session.query(User).order_by(User.created_at.desc()).all()
        return [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "total_spent": u.total_spent or 0.0,
                "total_donated": u.total_donated or 0.0,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else "",
            }
            for u in users
        ]


@app.get("/admin/users/{user_id}", response_model=dict)
def admin_get_user(
    user_id: str,
    _: bool = Depends(get_admin_user),
):
    """查看某个用户的详情和用量。"""
    with get_session() as session:
        user = session.query(User).filter_by(id=user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        total_tokens = sum(r.total_tokens or 0 for r in user.usage_logs)
        total_cost = sum(r.cost or 0 for r in user.usage_logs)

        total_donated = sum(d.amount or 0 for d in user.donations)

        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "total_spent": user.total_spent or 0.0,
            "total_donated": total_donated,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat() if user.created_at else "",
            "stats": {
                "total_calls": len(user.usage_logs),
                "total_tokens": total_tokens,
                "total_cost": round(total_cost, 6),
                "total_donations": len(user.donations),
                "total_donated": round(total_donated, 2),
            },
            "api_keys": [
                {"id": k.id, "prefix": k.prefix, "name": k.name, "is_active": k.is_active, "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None}
                for k in user.api_keys
            ],
        }


@app.post("/admin/users/{user_id}/keys", response_model=CreateKeyResponse)
def admin_create_key(
    user_id: str,
    _: bool = Depends(get_admin_user),
):
    """为用户生成新的 API Key。"""
    import secrets

    with get_session() as session:
        user = session.query(User).filter_by(id=user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        raw_key = "sk-" + secrets.token_hex(24)
        api_key = ApiKey(
            user_id=user.id,
            key_hash=hash_api_key(raw_key),
            prefix=raw_key[:15],
            name="default",
        )
        session.add(api_key)
        session.commit()

        return CreateKeyResponse(
            user_id=user.id,
            api_key=raw_key,
            prefix=raw_key[:15],
            message="New API key generated. Save it — it won't be shown again.",
        )


@app.post("/admin/users/{user_id}/keys/{key_id}/revoke")
def admin_revoke_key(
    user_id: str,
    key_id: str,
    _: bool = Depends(get_admin_user),
):
    """吊销用户的 API Key。"""
    with get_session() as session:
        api_key = session.query(ApiKey).filter_by(id=key_id, user_id=user_id).first()
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found.")
        api_key.is_active = False
        session.commit()
        return {"message": "API key revoked."}


@app.get("/admin/pricing", response_model=list[PricingEntry])
def admin_get_pricing(
    _: bool = Depends(get_admin_user),
):
    """查看当前定价配置。"""
    with get_session() as session:
        rows = session.query(PricingConfig).all()
        return [
            PricingEntry(
                model_pattern=r.model_pattern,
                price_per_1k_prompt=r.price_per_1k_prompt,
                price_per_1k_completion=r.price_per_1k_completion,
            )
            for r in rows
        ]


@app.post("/admin/pricing")
def admin_set_pricing(
    entry: PricingEntry,
    _: bool = Depends(get_admin_user),
):
    """新增或更新定价规则。"""
    with get_session() as session:
        existing = session.query(PricingConfig).filter_by(model_pattern=entry.model_pattern).first()
        if existing:
            existing.price_per_1k_prompt = entry.price_per_1k_prompt
            existing.price_per_1k_completion = entry.price_per_1k_completion
        else:
            session.add(PricingConfig(
                model_pattern=entry.model_pattern,
                price_per_1k_prompt=entry.price_per_1k_prompt,
                price_per_1k_completion=entry.price_per_1k_completion,
            ))
        session.commit()
        return {"message": f"Pricing for '{entry.model_pattern}' updated."}


# ============================================================
#  直接运行
# ============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", "8000"))
    uvicorn.run("api.server:app", host="0.0.0.0", port=port, reload=True)
