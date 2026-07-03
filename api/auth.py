"""API Key 认证 — FastAPI 依赖注入。"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .database import get_session, ApiKey, User, hash_api_key

logger = logging.getLogger("crewai.auth")

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "admin-secret-change-me")
security = HTTPBearer(auto_error=False)


# ============================================================
#  用户认证 — 通过 X-API-Key Header 或 Bearer Token
# ============================================================

async def get_current_user(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> User:
    """从 Header 中提取 API Key，查找对应用户并返回。

    支持两种传参方式：
    - X-API-Key: sk-xxx
    - Authorization: Bearer sk-xxx
    """
    raw_key = x_api_key or (bearer.credentials if bearer else None)

    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API key. Use X-API-Key header or Bearer token.")

    key_hash = hash_api_key(raw_key)

    with get_session() as session:
        api_key = session.query(ApiKey).filter_by(key_hash=key_hash).first()

        if not api_key or not api_key.is_active:
            raise HTTPException(status_code=403, detail="Invalid or inactive API key.")

        if api_key.user and not api_key.user.is_active:
            raise HTTPException(status_code=403, detail="User account is disabled.")

        # 更新最后使用时间
        api_key.last_used_at = datetime.now(timezone.utc)
        session.commit()

        # 返回 user 对象，同时把 api_key_id 也带上（通过 request.state）
        user = api_key.user
        user._current_api_key_id = api_key.id

        return user


async def get_current_user_or_none(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[User]:
    """可选认证 — 没有 API Key 返回 None 而不是报错。"""
    try:
        return await get_current_user(x_api_key, bearer)
    except HTTPException:
        return None


# ============================================================
#  管理员认证
# ============================================================

async def get_admin_user(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
) -> bool:
    """验证管理员密钥。"""
    if not x_admin_key or x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key.")
    return True
