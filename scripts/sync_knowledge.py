#!/usr/bin/env python3
"""Agent 启动时把 knowledge.md 同步到 RAG。

设计要点：
- 整份 `knowledge.md` 原样交给 RAG，**转换规则只有服务端一份**，避免各 Agent 各写一套；
- 任何失败只告警、不返回非零——重启同步不能阻塞 Agent 启动（proposal 明确要求）；
- 幂等：RAG 侧按 checksum 判断，内容没变时不会重复写。

由 k8s 的 initContainer 调用；也可手工执行：
    AGENT_NAME=daofaziran RAG_URL=http://rag-service.data.svc.cluster.local:8080 \
    RAG_TOKEN=... python scripts/sync_knowledge.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

# 默认公版；相声作品与圣经/古兰经的现代译本需另行判断
COPYRIGHT_DEFAULT = {
    "xiaotanrenjian": "copyrighted",
    "yimaneili": "mixed",
    "zhenzhuzhida": "mixed",
}


def main() -> int:
    agent = os.getenv("AGENT_NAME", "").strip()
    url = os.getenv("RAG_URL", "").strip().rstrip("/")
    token = os.getenv("RAG_TOKEN", "").strip()

    if not agent:
        print("[rag-sync] 跳过：未设置 AGENT_NAME", file=sys.stderr)
        return 0
    if not url or not token:
        print(f"[rag-sync] {agent}: 未配置 RAG_URL / RAG_TOKEN，跳过同步", file=sys.stderr)
        return 0

    path = Path(os.getenv("KNOWLEDGE_PATH", f"/app/{agent}_agent/knowledge.md"))
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"[rag-sync] {agent}: 读不到 {path}（{error}），跳过", file=sys.stderr)
        return 0

    payload = {
        "source_id": "knowledge.md",
        "doc_type": "knowledge",
        "content": content,
        "metadata": {"copyright_status": COPYRIGHT_DEFAULT.get(agent, "public-domain")},
    }
    request = urllib.request.Request(
        f"{url}/v1/ingest",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        print(f"[rag-sync] {agent}: 已同步 {body.get('chunk_count')} 个分块 -> {body.get('collection')}")
    except Exception as error:  # noqa: BLE001 —— 任何失败都不阻塞 Agent 启动
        print(f"[rag-sync] {agent}: 同步失败（不影响启动）: {type(error).__name__}: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
