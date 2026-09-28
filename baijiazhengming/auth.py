"""框架级最小鉴权。

**为什么需要**：`baijiazhengming-api` 是 ClusterIP Service，而本 namespace 没有
NetworkPolicy —— 集群里**任何 Pod** 都能直接 POST 任务。提交还会消耗 llm-service
额度，`user_id` 又来自请求体，所以没有这道门时可以：

1. 盗用 LLM 额度；
2. 把 `user_id` 填成别人的值，长期占满对方的「一人一任务」槽位；
3. 靠 `topic` 精确匹配读出别人的缓存回答。

**形状**：一个共享 token，只注入给真正需要调用的两个消费者 —— 共享 UI 与
`selfcheck`（都在 `baijiazhengming` namespace，走同一个 Secret）。凭据不按人格分，
因为它描述的是「谁能调用框架 API」，不是「以谁的身份查 RAG」；后者由
`LLM_TOKEN_<SLUG>` / `RAG_TOKEN_<SLUG>` 负责，两者互不替代。

**失败关闭**：`BAIJIA_API_TOKEN` 未注入时任务端点一律 503，并且 `/health` 报
degraded —— 这样 `deploy.sh` 的第 3 步自检会在切换八个域名之前就停下来。
"""
from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException

ENV_NAME = "BAIJIA_API_TOKEN"


def token() -> str:
    return os.getenv(ENV_NAME, "").strip()


def configured() -> bool:
    return bool(token())


def configuration_error() -> str | None:
    if not configured():
        return f"{ENV_NAME} 未注入：任务端点会拒绝所有请求（/health 仍可访问，供探针使用）"
    return None


def require_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI 依赖：校验 `Authorization: Bearer <token>`。

    用 `secrets.compare_digest` 做定长比较，避免按字节提前返回。
    """
    expected = token()
    if not expected:
        raise HTTPException(503, f"服务未配置 {ENV_NAME}，已拒绝请求")
    scheme, _, value = (authorization or "").partition(" ")
    # 比字节而不是比 str：compare_digest 对含非 ASCII 的 str 会抛 TypeError
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        value.strip().encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(401, "缺少或无效的调用凭据")
