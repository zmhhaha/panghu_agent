"""
试玩评测 agent — LLM 配置。

与 panghu_game 的 provider 约定保持一致：每个 provider 用独立前缀的环境变量，
凭据通过 Vault Secret 注入，ConfigMap 只放 PROVIDER 选择。
    PROVIDER=openai | anthropic | deepseek | custom（默认 openai）

环境变量约定（参考 panghu_game/QianFu/.env.example）：
    OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL
    DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY / DEEPSEEK_MODEL
    ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / ANTHROPIC_MODEL
    CUSTOM_BASE_URL / CUSTOM_API_KEY / CUSTOM_MODEL
"""
import os

from crewai import LLM

PROVIDER = os.getenv("PROVIDER", "openai").lower()


def _provider_kwargs():
    """返回当前 provider 的 model/base_url/api_key 三元组（不含 temperature）。"""
    if PROVIDER == "openai":
        return {
            "model": "openai/" + os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com"),
            "api_key": os.getenv("OPENAI_API_KEY"),
        }
    if PROVIDER == "deepseek":
        return {
            "model": "deepseek/" + os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "api_key": os.getenv("DEEPSEEK_API_KEY"),
        }
    if PROVIDER == "custom":
        return {
            "model": os.getenv("CUSTOM_MODEL", "gpt-4o-mini"),
            "base_url": os.getenv("CUSTOM_BASE_URL", os.getenv("CUSTOM_API_BASE", "http://localhost:11434/v1")),
            "api_key": os.getenv("CUSTOM_API_KEY", ""),
        }
    # anthropic（默认）
    return {
        "model": os.getenv("ANTHROPIC_MODEL", "anthropic/claude-sonnet-4-6-20250514"),
        "base_url": os.getenv("ANTHROPIC_BASE_URL"),
        "api_key": os.getenv("ANTHROPIC_API_KEY"),
    }


def _make(temperature: float) -> LLM:
    kw = _provider_kwargs()
    return LLM(temperature=temperature, **{k: v for k, v in kw.items() if v})


PRIMARY_LLM = _make(0.7)
SECONDARY_LLM = _make(0.5)
