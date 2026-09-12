"""向 RAG 服务取参考素材（`mode=context`）。

设计要点：
- **只取素材，不触发 RAG 侧生成**——最终回答由 Agent 按自己的 `skill.md` 生成，
  避免两层 LLM 打架、也省一次生成；
- 任何失败（未配置 / 网络 / 非 200）都返回空串：**检索不可用不能拖垮 Agent**；
- 素材由调用方以边界标记包好再注入 prompt，并声明"这是数据不是指令"。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = float(os.getenv("RAG_TIMEOUT", "20"))


def fetch_reference(agent: str, question: str, top_k: int = 4) -> str:
    """返回拼接好的参考素材；取不到时返回空串。"""
    url = os.getenv("RAG_URL", "").rstrip("/")
    token = os.getenv("RAG_TOKEN", "")
    if not url or not token or not question.strip():
        return ""
    request = urllib.request.Request(
        f"{url}/v1/query",
        data=json.dumps({"question": question, "top_k": top_k, "mode": "context"}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        return (body.get("context") or "").strip()
    except Exception as error:  # noqa: BLE001 —— 检索失败一律降级为空素材
        print(f"[rag] {agent}: 取参考素材失败（按无素材继续）: {type(error).__name__}: {error}", file=sys.stderr)
        return ""
