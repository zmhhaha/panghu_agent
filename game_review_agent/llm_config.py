"""
试玩评测 agent — LLM 配置。

与 research_agent/crew.py 的四分支保持一致，通过环境变量 PROVIDER 切换：
    PROVIDER=openai | anthropic | deepseek | custom（默认 openai）
供 crew.py（多 agent 流水线）和 runner.py（试玩决策循环）共享。
"""
import os

from crewai import LLM

PROVIDER = os.getenv("PROVIDER", "openai").lower()


def _primary() -> LLM:
    if PROVIDER == "openai":
        return LLM(
            model="openai/gpt-4o-mini",
            base_url="https://api.openai.com",
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.7,
        )
    if PROVIDER == "deepseek":
        return LLM(
            model="deepseek/deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.7,
        )
    if PROVIDER == "custom":
        return LLM(
            model=os.getenv("CUSTOM_MODEL", "gpt-4o-mini"),
            base_url=os.getenv("CUSTOM_API_BASE", "http://localhost:11434/v1"),
            api_key=os.getenv("CUSTOM_API_KEY", ""),
            temperature=0.7,
        )
    # anthropic（默认）
    return LLM(model="anthropic/claude-sonnet-4-6-20250514", temperature=0.7)


def _secondary() -> LLM:
    if PROVIDER == "openai":
        return LLM(
            model="openai/gpt-4o-mini",
            base_url="https://api.openai.com",
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.5,
        )
    if PROVIDER == "deepseek":
        return LLM(
            model="deepseek/deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.5,
        )
    if PROVIDER == "custom":
        return LLM(
            model=os.getenv("CUSTOM_MODEL", "gpt-4o-mini"),
            base_url=os.getenv("CUSTOM_API_BASE", "http://localhost:11434/v1"),
            api_key=os.getenv("CUSTOM_API_KEY", ""),
            temperature=0.5,
        )
    return LLM(model="anthropic/claude-haiku-4-5-20251001", temperature=0.5)


PRIMARY_LLM = _primary()
SECONDARY_LLM = _secondary()
