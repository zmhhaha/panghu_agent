"""Token 计费模块 — 通过 LiteLLM Callback 自动追踪每次 LLM 调用的 Token 消耗。

计费模式：按次结算（后付费）
1. LiteLLM 全局 success_callback → 捕获每次调用的 token 用量
2. contextvars → 将 token 用量关联到当前请求的用户
3. 按定价表计算费用并累计到用户 total_spent（不预扣，事后结算）
"""

import os
import time
import logging
import contextvars
from typing import Optional

from sqlalchemy.orm import Session

from .database import get_session, UsageLog, PricingConfig, User, ApiKey

logger = logging.getLogger("crewai.billing")

# ============================================================
#  Context Variables — 线程安全地关联当前请求的用户
# ============================================================
_current_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_user_id", default=None)
_current_api_key_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_api_key_id", default=None)
_current_topic: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_topic", default=None)

# 存储当前请求累计的 token 消耗
_request_usage: contextvars.ContextVar[dict] = contextvars.ContextVar("request_usage", default={
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "cost": 0.0,
    "models": set(),
})


def set_request_context(user_id: str, api_key_id: str, topic: str = ""):
    """在请求开始时设置上下文（由 auth 中间件调用）。"""
    _current_user_id.set(user_id)
    _current_api_key_id.set(api_key_id)
    _current_topic.set(topic)
    _request_usage.set({
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "models": set(),
    })


def clear_request_context():
    """请求结束时清理上下文。"""
    _current_user_id.set(None)
    _current_api_key_id.set(None)
    _current_topic.set(None)


def get_request_usage() -> dict:
    """获取当前请求的累计用量。"""
    return _request_usage.get()


# ============================================================
#  Pricing lookup
# ============================================================
PRICING_CACHE: dict = {}
PRICING_CACHE_TIME: float = 0.0
PRICING_CACHE_TTL: float = 60.0  # 60 秒刷新一次定价表


def _load_pricing(session: Session) -> dict:
    """加载定价表到内存缓存。"""
    global PRICING_CACHE, PRICING_CACHE_TIME
    now = time.time()
    if now - PRICING_CACHE_TIME < PRICING_CACHE_TTL and PRICING_CACHE:
        return PRICING_CACHE

    pricing = {}
    for row in session.query(PricingConfig).all():
        pricing[row.model_pattern] = {
            "prompt": row.price_per_1k_prompt,
            "completion": row.price_per_1k_completion,
        }
    PRICING_CACHE = pricing
    PRICING_CACHE_TIME = now
    return pricing


def _match_pricing(model: str, pricing: dict) -> Optional[dict]:
    """模糊匹配定价规则。"""
    model_lower = model.lower()
    # 精确匹配优先
    if model_lower in pricing:
        return pricing[model_lower]
    # 模糊匹配
    for pattern, rates in pricing.items():
        if pattern.lower() in model_lower:
            return rates
    # fallback: 使用默认低价
    return {"prompt": 0.50, "completion": 2.00}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """计算单次 LLM 调用的费用（美元）。"""
    with get_session() as session:
        pricing = _load_pricing(session)
    rates = _match_pricing(model, pricing)
    cost = (prompt_tokens / 1000.0 * rates["prompt"]) + (completion_tokens / 1000.0 * rates["completion"])
    return round(cost, 6)


# ============================================================
#  LiteLLM Callbacks
# ============================================================

def track_success(kwargs, response_obj, start_time, end_time):
    """LiteLLM 成功调用回调 — 记录 token 用量并累计消费（后付费）。"""
    user_id = _current_user_id.get()
    if user_id is None:
        return  # 非 API 请求（如 CLI 模式），不追踪

    try:
        model = kwargs.get("model", "unknown")
        usage = getattr(response_obj, "usage", None)

        if usage is None:
            return

        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)

        cost = calculate_cost(model, prompt_tokens, completion_tokens)

        # 更新当前请求累计用量
        req_usage = _request_usage.get()
        req_usage["prompt_tokens"] += prompt_tokens
        req_usage["completion_tokens"] += completion_tokens
        req_usage["total_tokens"] += total_tokens
        req_usage["cost"] += cost
        req_usage["models"].add(model)
        _request_usage.set(req_usage)

        # 写入数据库并累计消费（不预扣余额）
        api_key_id = _current_api_key_id.get()
        topic = _current_topic.get()

        with get_session() as session:
            log = UsageLog(
                user_id=user_id,
                api_key_id=api_key_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost=cost,
                topic=topic,
                status="success",
            )
            session.add(log)

            # 按次结算：累计消费，不扣余额
            user = session.query(User).filter_by(id=user_id).first()
            if user:
                user.total_spent = round((user.total_spent or 0) + cost, 6)

            session.commit()

        logger.info(f"Token tracked: user={user_id[:8]} model={model} tokens={total_tokens} cost=${cost:.6f} total_spent=${user.total_spent if user else 0:.4f}")

    except Exception as e:
        logger.error(f"Error in track_success callback: {e}")


def track_failure(kwargs, response_obj, start_time, end_time):
    """LiteLLM 失败调用回调 — 记录失败但暂不扣费。"""
    user_id = _current_user_id.get()
    if user_id is None:
        return

    try:
        model = kwargs.get("model", "unknown")
        error_msg = str(kwargs.get("exception", getattr(response_obj, "error", "unknown")))

        api_key_id = _current_api_key_id.get()
        topic = _current_topic.get()

        with get_session() as session:
            log = UsageLog(
                user_id=user_id,
                api_key_id=api_key_id,
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cost=0.0,
                topic=topic,
                status="error",
                error_message=error_msg[:500],
            )
            session.add(log)
            session.commit()

    except Exception as e:
        logger.error(f"Error in track_failure callback: {e}")


def install_litellm_callbacks():
    """安装 LiteLLM 全局回调。需要在应用启动时调用一次。"""
    try:
        import litellm
        if track_success not in litellm.success_callback:
            litellm.success_callback.append(track_success)
        if track_failure not in litellm.failure_callback:
            litellm.failure_callback.append(track_failure)
        logger.info("LiteLLM callbacks installed for billing")
    except ImportError:
        logger.warning("litellm not available, billing callbacks not installed")
