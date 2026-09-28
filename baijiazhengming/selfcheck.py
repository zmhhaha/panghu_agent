"""部署后自检：八个人格是不是真的都活着。

只读 + 给每个 agent 各跑一道真题 —— 这是唯一能证明「八个 crew 模块 + 八套凭据」都活着
的方法（`/health` 只能证明配置齐了，证明不了 crew 能跑）。

在 api 容器里跑：

    kubectl -n baijiazhengming exec deploy/baijiazhengming-api -- python -m baijiazhengming.selfcheck

注意：同一个问题第二次跑会命中缓存（秒回、`cached`），所以**首次**运行才算证过了 crew；
要重新验 crew 就换个 `SELFCHECK_QUESTION`。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.getenv("SELFCHECK_BASE", "http://127.0.0.1:8000").rstrip("/")
QUESTION = os.getenv("SELFCHECK_QUESTION", "用一句话介绍你自己")
WAIT_SECONDS = float(os.getenv("SELFCHECK_WAIT", "300"))


def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.loads(response.read().decode())


def _post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def main() -> int:
    agents = _get("/health").get("agents", {})
    for slug, item in agents.items():
        print(f"  health {slug}: {item.get('status')} queued={item.get('queued')}")
    broken = [slug for slug, item in agents.items() if item.get("status") != "ok"]
    if broken:
        print(f"FAIL 配置不完整：{broken}", file=sys.stderr)
        return 1
    if not agents:
        print("FAIL registry 里没有任何 agent", file=sys.stderr)
        return 1

    pending: dict[str, str] = {}
    for slug in agents:
        body = _post(f"/v1/agents/{slug}/tasks", {"text": QUESTION})
        if body.get("status") == "done":
            print(f"  {slug}: 命中缓存")
            continue
        pending[slug] = body["id"]

    deadline = time.time() + WAIT_SECONDS
    failed: list[str] = []
    while pending and time.time() < deadline:
        time.sleep(5)
        for slug, task_id in list(pending.items()):
            state = _get(f"/v1/agents/{slug}/tasks/{task_id}")
            status = state.get("status")
            if status == "done":
                print(f"  {slug}: done ({len(state.get('report') or '')} 字)")
                pending.pop(slug)
            elif status in ("failed", "timeout"):
                print(f"  {slug}: {status} — {state.get('error')}", file=sys.stderr)
                failed.append(slug)
                pending.pop(slug)

    if pending or failed:
        print(f"FAIL 仍在跑={sorted(pending)} 失败={failed}", file=sys.stderr)
        return 1
    print(f"selfcheck ok: {len(agents)} 个 agent 全部出结果")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
