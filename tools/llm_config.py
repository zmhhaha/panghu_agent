"""共享 LLM 配置校验。

只检查 provider 与凭据是否存在，不导入 CrewAI，方便 API 在创建任务前快速失败。
"""
from __future__ import annotations

import os


REQUIRED_API_KEYS = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
SUPPORTED_PROVIDERS = frozenset((*REQUIRED_API_KEYS, "custom"))


def get_provider() -> str:
    return os.getenv("PROVIDER", "openai").strip().lower()


def get_llm_config_error(service_name: str = "LLM") -> str | None:
    """返回可直接展示给用户/运维的配置错误；配置正常时返回 None。"""
    provider = get_provider()
    if provider not in SUPPORTED_PROVIDERS:
        choices = "、".join(sorted(SUPPORTED_PROVIDERS))
        return f"{service_name} 配置错误：不支持 PROVIDER={provider}，可选值为 {choices}。"

    key_name = REQUIRED_API_KEYS.get(provider)
    if key_name and not os.getenv(key_name, "").strip():
        return (
            f"{service_name} 配置不完整：PROVIDER={provider}，但 {key_name} 未注入 API Pod。"
            f"请检查对应 agent-secret 是否已从 Vault 同步，并重启 API Pod。"
        )
    return None


def require_llm_config(service_name: str = "LLM") -> str:
    """校验配置并返回 provider，供 Crew/CLI 在构造模型前调用。"""
    error = get_llm_config_error(service_name)
    if error:
        raise RuntimeError(error)
    return get_provider()
