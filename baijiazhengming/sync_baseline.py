"""启动时把每个 agent 的 knowledge.md 同步进 RAG。

基线知识的同步一直由 initContainer 跑（`scripts/sync_knowledge.py`）。共享运行时里要跑
八次，而且**每次必须用各自的 RAG 凭据** —— rag-service 用凭据决定 collection，拿错
token 就把知识写进别人家了。

失败一律只告警：同步不该阻塞启动（与 `sync_knowledge.py` 自身的策略一致）。
"""
from __future__ import annotations

import os
import subprocess
import sys

from .registry import get_registry

SYNC_SCRIPT = os.getenv("RAG_SYNC_SCRIPT", "/app/scripts/sync_knowledge.py")


def run_one(slug: str) -> None:
    token = os.getenv(f"RAG_TOKEN_{slug.upper()}", "").strip() or os.getenv(
        "RAG_TOKEN", ""
    ).strip()
    if not token:
        print(f"[rag-sync] {slug}: 没有可用的 RAG 凭据，跳过", file=sys.stderr)
        return
    env = dict(os.environ, AGENT_NAME=slug, RAG_TOKEN=token)
    subprocess.run([sys.executable, SYNC_SCRIPT], env=env, check=False)


def main() -> int:
    for definition in get_registry().list_enabled():
        run_one(definition.slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
