"""
试玩评测 agent — LLM 配置。

模型调用统一走集群内 llm-service：本服务**不再持有 provider 凭据**，
只需要入口地址、模型别名与内部令牌（provider 密钥由 llm-service 持有）。

    LLM_BASE_URL / LLM_MODEL / LLM_SERVICE_TOKEN
"""
import os

from crewai import LLM

_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")
_LLM_TOKEN = os.getenv("LLM_SERVICE_TOKEN", "").strip()
LLM_ALIAS = os.getenv("LLM_MODEL", "deepseek-trusted")

if not _LLM_BASE_URL or not _LLM_TOKEN:
    raise RuntimeError(
        "game_review_agent 未配置 llm-service：需要 LLM_BASE_URL 与 LLM_SERVICE_TOKEN"
        "（见 k8s/api-deployment.yaml 与 vault/inventory/llm-token-externalsecret.yaml）"
    )


def _make(temperature: float) -> LLM:
    # CrewAI 必须显式给 provider：`openai/<别名>` 会落到未安装的 litellm 分支并报错
    return LLM(model=LLM_ALIAS, provider="openai", base_url=_LLM_BASE_URL, api_key=_LLM_TOKEN, temperature=temperature)


PRIMARY_LLM = _make(0.7)
SECONDARY_LLM = _make(0.5)
