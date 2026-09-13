"""llm-service 防护日报：拉一次汇总，渲染成一篇 Hublog 文章。

与其它 content agent 同一套骨架（`collect` / `render` / `run_agent`），所以发布、
去重、草稿策略、运行统计都复用 `common/runner.py`，本模块只负责「取数 + 排版」。

两条与本目录其它 bot 不同的地方：

1. **数据来源是集群内的 llm-service，不是公网**。用的是 `LLM_BASE_URL` + `LLM_API_KEY`
   （后者由 `llm-token` Secret 注入本 bot 的调用方令牌，身份 `llm-report`）。
   `/v1/guard/report` 只对 `LLM_GUARD.report_callers` 白名单开放。
2. **报告要写明统计起点**。llm-service 的计数器在进程内存里，Pod 重启归零；
   不写起点的话，「重启后只统计了两小时」会被读成「今天很干净」。

关于可见性：报告走 Hublog 的默认 `visibility=public` —— 它同时也是 llm-service 用量的对外展示。
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from content_agents.common.config import AgentConfig
from content_agents.common.http import HttpClientError, get_json
from content_agents.common.models import Candidate, ContentItem, SourceRef
from content_agents.common.review import assess
from content_agents.common.runner import run_agent

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

BOT_NAME = "llm-guard-report"
SOURCE_NAME = "llm-service"

_SAMPLE_REPORT = {
    "since": "2026-09-13T04:10:00+00:00",
    "generated_at": "2026-09-14T01:17:00+00:00",
    "callers": {
        "zhougongjiemeng": {
            "requests": 42,
            "prompt_tokens": 12000,
            "completion_tokens": 3400,
            "detection_hits": {"override_zh": 3},
            "canary_leaks": 0,
            "rejected": 0,
        }
    },
    "by_alias": {"deepseek-guarded": {"requests": 42, "prompt_tokens": 12000, "completion_tokens": 3400}},
}


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def collect(*, sample: bool = False) -> list[Candidate]:
    """取一次 llm-service 的防护汇总，包成单个 Candidate。

    取不到就返回空 —— `run_agent` 遇到空候选会安静结束，不会发一篇空报告。
    """
    if sample:
        return [
            Candidate(
                external_id="sample",
                title="LLM 接入日报 · 样例",
                summary="离线样例，不访问网络",
                url="",
                source=SOURCE_NAME,
                metadata={"report": _SAMPLE_REPORT, "day": "sample"},
            )
        ]

    logger = logging.getLogger(__name__)
    base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
    token = os.getenv("LLM_API_KEY", "").strip()
    if not base_url or not token:
        logger.warning("LLM_BASE_URL / LLM_API_KEY 未注入；跳过本次日报")
        return []

    try:
        report = get_json(f"{base_url}/guard/report", headers={"Authorization": f"Bearer {token}"})
    except HttpClientError as exc:
        logger.warning("拉取防护汇总失败：%s", exc)
        return []

    if not isinstance(report, dict) or not isinstance(report.get("callers"), dict):
        logger.warning("防护汇总响应格式不符合预期；跳过本次日报")
        return []

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [
        Candidate(
            external_id=day,
            title=f"LLM 接入日报 · {day}",
            summary=f"llm-service 的调用量、检测命中与 canary 泄漏日报（{day}）",
            url="",
            source=SOURCE_NAME,
            metadata={"report": report, "day": day},
        )
    ]


def _render_body(report: dict, day: str) -> str:
    callers = report.get("callers") or {}
    since = report.get("since") or "未知"

    requests = sum(_int(item.get("requests")) for item in callers.values())
    prompt_tokens = sum(_int(item.get("prompt_tokens")) for item in callers.values())
    completion_tokens = sum(_int(item.get("completion_tokens")) for item in callers.values())
    leaks = sum(_int(item.get("canary_leaks")) for item in callers.values())
    rejected = sum(_int(item.get("rejected")) for item in callers.values())

    rule_totals: dict[str, int] = {}
    rule_last_caller: dict[str, str] = {}
    for caller, item in sorted(callers.items()):
        for rule, count in (item.get("detection_hits") or {}).items():
            if _int(count):
                rule_totals[rule] = rule_totals.get(rule, 0) + _int(count)
                rule_last_caller.setdefault(rule, caller)
    detections = sum(rule_totals.values())

    lines: list[str] = [
        f"统计窗口：**自 `{since}`**（llm-service 本次启动）起。",
        "",
        "> 计数器在 llm-service 进程内存里，Pod 重启会归零。上面这个时间戳就是本次统计的真实起点"
        "——如果它离现在很近，说明数据只有很短一段，不代表当天很干净。",
        "",
        "## 总览",
        "",
        f"- 请求 **{requests:,}** 次 · prompt **{prompt_tokens:,}** tokens · completion **{completion_tokens:,}** tokens",
        f"- 检测命中 **{detections}** 次 · canary 泄漏 **{leaks}** 次 · 拦截 **{rejected}** 次",
        "",
    ]

    if callers:
        lines += [
            "## 按调用方",
            "",
            "| 调用方 | 请求 | prompt | completion | 检测命中 | canary |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for caller, item in sorted(callers.items(), key=lambda pair: -_int(pair[1].get("requests"))):
            hits = sum(_int(count) for count in (item.get("detection_hits") or {}).values())
            lines.append(
                f"| `{caller}` | {_int(item.get('requests')):,} "
                f"| {_int(item.get('prompt_tokens')):,} | {_int(item.get('completion_tokens')):,} "
                f"| {hits} | {_int(item.get('canary_leaks'))} |"
            )
        lines.append("")

    lines += ["## 检测命中明细", ""]
    if rule_totals:
        lines += ["| 规则 | 次数 | 最近一次调用方 |", "|---|---:|---|"]
        for rule, count in sorted(rule_totals.items(), key=lambda pair: -pair[1]):
            lines.append(f"| `{rule}` | {count} | `{rule_last_caller.get(rule, '?')}` |")
    else:
        lines.append("本次窗口内没有命中。")
    lines.append("")

    lines += [
        "## 说明",
        "",
        "- 检测命中只代表**文本里出现了这些特征**，不代表一定是攻击；当前是「只记日志」模式，没有拦截。",
        "- spotlight 与 canary 只对 `guarded` 档生效（用户会写 prompt 的那一档）。",
        "- 本报告由 `content_agents/llm_guard_report_agent` 生成，数据来自 llm-service 的 `/v1/guard/report`。",
        "",
    ]
    return "\n".join(lines)


def render(candidate: Candidate, config: AgentConfig) -> ContentItem:
    report = candidate.metadata.get("report") or {}
    day = str(candidate.metadata.get("day") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    body = _render_body(report, day)
    risk, status, notes = assess(body, default_risk="low")
    return ContentItem.create(
        bot_name=config.bot_name,
        bot_version=config.bot_version,
        prompt_version=config.prompt_version,
        title=candidate.title,
        body=body,
        summary=candidate.summary,
        language="zh-CN",
        source_refs=[
            SourceRef(
                name=SOURCE_NAME,
                url="",
                external_id=day,
                published_at="",
                excerpt="llm-service /v1/guard/report 汇总",
            )
        ],
        tags=["llm-service", "日报", "安全"],
        topics=["operations"],
        risk_level=risk,
        review_status=status,
        review_notes=notes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish the daily llm-service guard and usage report")
    parser.add_argument("--sample", action="store_true", help="use an offline sample report")
    args = parser.parse_args()
    config = AgentConfig.from_env(BOT_NAME)
    run_agent(config, lambda: collect(sample=args.sample), render)


if __name__ == "__main__":
    main()
